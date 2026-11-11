"""A newer version, from the GitHub releases (Windows; on Linux the Flatpak
brings it, from GNOME Software).

The latest release from GitHub's API, standard library only, at most once a
day at start and whenever asked. The installed build downloads the
release's installer, checks it against the SHA-256 GitHub publishes for it
and against the project's own signature (minisign.py: the CI signs it with
a key GitHub does not hold, so an installer replaced in the release with
its digest is still refused), and starts it: Setup asks the running app to
quit, replaces the files and starts it again. The portable build and a run
from the sources open the release page.

What an update never does: start an installer that is not the one named
for the release's own version (an answer that pairs a newer tag with an
older, duly signed installer would otherwise install that older one), read
more than the release says, or leave the verified file where another
process of the user could swap it before Setup reads it (hold()).

The module knows no app: whose releases, and which key signs them, is a
Project the caller gives (app/services.py makes this app's).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scriba.i18n import _

from . import minisign

SIGNATURE_SUFFIX = ".minisig"
SIGNATURE_MAX_BYTES = 4096
# what is read at most: GitHub's answer about a release (a few tens of KB),
# an installer whose size the release does not give
RELEASE_MAX_BYTES = 2**20
INSTALLER_MAX_BYTES = 512 * 2**20
CHUNK = 256 * 1024


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Project:
    """Whose releases these are: what an app tells this module about itself."""
    repo: str               # "owner/name" on GitHub
    slug: str               # the file names of a release: <slug>-setup-v1.2.3-windows-x64.exe
    exe_name: str           # the Windows executable's name: the User-Agent of the requests
    version: str            # the version running
    public_key: str         # the releases' signing key (minisign), its public half

    @property
    def releases_url(self) -> str:
        return f"https://github.com/{self.repo}/releases"

    @property
    def latest_release_api(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases/latest"

    @property
    def downloads(self) -> str:
        return f"https://github.com/{self.repo}/releases/download/"

    @property
    def download_folder_prefix(self) -> str:
        return f"{self.slug}-update-"

    @property
    def dev_release_variable(self) -> str:
        """Developer aid: the environment variable naming a release described
        in a local file, to try the dialog without publishing one."""
        return f"{self.slug.upper().replace('-', '_')}_DEV_RELEASE_API"

    def installer_name(self, version: str) -> str:
        return f"{self.slug}-setup-v{version.strip().lstrip('vV')}-windows-x64.exe"

    def is_installer(self, name: str) -> bool:
        """A released version's installer by its name: never the "dev" builds of the CI."""
        return bool(re.match(rf"^{re.escape(self.slug)}-setup-v\d+(\.\d+){{1,3}}-windows-x64\.exe$", name, re.IGNORECASE))


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str = ""            # from GitHub's "digest", when it gives one
    signature_url: str = ""     # its .minisig among the release's files, when there is one


@dataclass
class Release:
    version: str
    url: str                    # the release's page
    notes: str
    assets: list[Asset] = field(default_factory=list)


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.0' -> (1, 2, 0); what is not a number counts as 0."""
    parts = []
    for piece in text.strip().lstrip("vV").split("."):
        m = re.match(r"\d+", piece)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def installed_build() -> bool:
    """The Inno Setup installation (its uninstaller next to the exe), not
    the portable zip nor the sources."""
    return (sys.platform == "win32" and bool(getattr(sys, "frozen", False))
            and (Path(sys.executable).parent / "unins000.exe").is_file())


def ssl_context():
    """The frozen Windows build ships a CA bundle: MSYS2's OpenSSL does not
    always see the Windows certificate store."""
    import ssl
    context = ssl.create_default_context()
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "certs" / "ca-bundle.crt"
    if getattr(sys, "frozen", False) and bundled.is_file():
        try:
            context.load_verify_locations(cafile=str(bundled))
        except (ssl.SSLError, OSError):
            pass
    return context


def _get(project: Project, url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": f"{project.exe_name}/{project.version}",
                                                   "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())


def parse_release(project: Project, data: dict) -> Release:
    tag = str(data.get("tag_name") or "")
    if not tag:
        raise UpdateError(_("unexpected answer from GitHub"))
    listed = data.get("assets") or []
    signatures = {str(a.get("name") or ""): str(a.get("browser_download_url") or "") for a in listed}
    assets = []
    for a in listed:
        name, digest = str(a.get("name") or ""), str(a.get("digest") or "")
        assets.append(Asset(name, str(a.get("browser_download_url") or ""), int(a.get("size") or 0),
                            digest[7:] if digest.startswith("sha256:") else "",
                            signatures.get(name + SIGNATURE_SUFFIX, "")))
    return Release(tag.lstrip("vV"), str(data.get("html_url") or project.releases_url), str(data.get("body") or ""),
                   assets)


def latest_release(project: Project, timeout: float = 15.0) -> Release:
    # developer aid: a release described in a local file, to try the dialog
    # without publishing one; never in the built app
    url = project.latest_release_api
    if not getattr(sys, "frozen", False):
        url = os.environ.get(project.dev_release_variable) or url
    try:
        with _get(project, url, timeout) as response:
            raw = response.read(RELEASE_MAX_BYTES + 1)
        if len(raw) > RELEASE_MAX_BYTES:
            raise UpdateError(_("unexpected answer from GitHub"))
        data = json.loads(raw)
    except urllib.error.HTTPError as e:
        raise UpdateError(_("GitHub answered {code}").format(code=e.code)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(str(getattr(e, "reason", e))) from None
    return parse_release(project, data)


def installer_of(project: Project, release: Release) -> Asset | None:
    """The release's installer: the one named for the release's own version,
    from the project's own releases, with the SHA-256 GitHub gives for it
    and its signature. Else None: the dialog then opens the release page."""
    wanted = project.installer_name(release.version).lower()
    return next((a for a in release.assets
                 if project.is_installer(a.name) and a.name.lower() == wanted and a.url.startswith(project.downloads)
                 and a.sha256 and a.signature_url.startswith(project.downloads)), None)


def per_user_install() -> bool:
    """Installed for this user only (under the profile's local app data), as
    Setup does by default: an update then never needs to elevate."""
    local = os.environ.get("LOCALAPPDATA", "")
    return bool(local) and os.path.normcase(os.path.abspath(sys.executable)).startswith(
        os.path.normcase(os.path.abspath(local)) + os.sep)


_held: list = []        # the handles of the installers kept open (see hold)


def hold(path: Path) -> None:
    """Windows: the verified installer kept open, with no write or delete
    sharing, until the app exits: no other process of the user can replace
    or rename it between the check and the moment Setup reads it (and
    reads it again, elevated, when the app is installed for all users: the
    file of a process of the user would then run as an administrator).
    Setup only needs to read it. Elsewhere nothing to do."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    generic_read, share_read, open_existing = 0x80000000, 0x1, 3
    handle = kernel32.CreateFileW(str(path), generic_read, share_read, None, open_existing, 0, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), "the installer could not be kept open")
    _held.append(handle)


def release_held() -> None:
    """The installers kept open, let go (at quit, and in the tests)."""
    if sys.platform != "win32":
        return
    import ctypes
    while _held:
        ctypes.windll.kernel32.CloseHandle(_held.pop())


def download_folder(project: Project) -> Path:
    """A new folder of its own for an installer, in the temporary folder."""
    return Path(tempfile.mkdtemp(prefix=project.download_folder_prefix))


def clean_downloads(project: Project) -> None:
    """The installers of earlier updates, gone (Setup has finished with them
    by the time the app starts again)."""
    for folder in Path(tempfile.gettempdir()).glob(project.download_folder_prefix + "*"):
        shutil.rmtree(folder, ignore_errors=True)


def download(project: Project, asset: Asset, dest: Path, progress: Callable[[int, int], None] | None = None,
             cancelled: threading.Event | None = None) -> Path:
    """The asset, in dest, checked against its size, its SHA-256 and its
    signature: a file that does not match is removed; without a SHA-256 or
    a signature nothing is downloaded."""
    if not asset.sha256:
        raise UpdateError(_("the release gives no checksum for the installer: not downloaded"))
    if not asset.signature_url:
        raise UpdateError(_("the release gives no signature for the installer: not downloaded"))
    part = dest.with_name(dest.name + ".part")
    digest, signed = hashlib.sha256(), minisign.blake2b()
    try:
        with _get(project, asset.signature_url, 30) as response:
            signature = response.read(SIGNATURE_MAX_BYTES).decode("utf-8", "replace")
        with _get(project, asset.url, 30) as response, open(part, "wb") as out:
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            limit = asset.size or INSTALLER_MAX_BYTES
            done = 0
            while True:
                if cancelled is not None and cancelled.is_set():
                    raise UpdateError(_("cancelled"))
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                if done + len(chunk) > limit:       # more than the release says: no disk filled with it
                    raise UpdateError(_("the file downloaded does not match the release: not started"))
                out.write(chunk)
                digest.update(chunk)
                signed.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        # a connection cut short ends the reading without an error
        if (asset.size and done != asset.size) or digest.hexdigest().lower() != asset.sha256.lower():
            raise UpdateError(_("the file downloaded does not match the release: not started"))
        try:
            trusted = minisign.verify(signed.digest(), signature, project.public_key)
        except minisign.SignatureError:
            trusted = ""
        # the comment names the file: another release's installer, signed
        # too, is not this one
        if f"file:{asset.name}" not in trusted.split():
            raise UpdateError(_("the installer is not signed by the project: not started"))
        part.replace(dest)
        hold(dest)
        return dest
    except urllib.error.HTTPError as e:
        raise UpdateError(_("download failed ({code})").format(code=e.code)) from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError(str(getattr(e, "reason", e))) from None
    finally:
        try:
            part.unlink()
        except OSError:
            pass
