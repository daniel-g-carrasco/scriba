"""Scriba's web fallback when GitHub rate-limits release API requests."""

import http.client
import re
import urllib.error
import urllib.request

from scriba.i18n import _

from . import updates
from .updates import RELEASE_MAX_BYTES, SIGNATURE_SUFFIX, Asset, Project, Release, UpdateError


def _get_web(project: Project, url: str, timeout: float):
    request = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": project.exe_name})
    return urllib.request.urlopen(request, timeout=timeout, context=updates.ssl_context())





def _latest_web_release(project: Project, timeout: float) -> Release:
    """Resolve /latest and read the installer's digest from GitHub's asset list."""
    with _get_web(project, project.releases_url + "/latest", timeout) as response:
        url = response.geturl()
    match = re.fullmatch(re.escape(project.releases_url) + r"/tag/([vV]?[0-9]+(?:\.[0-9]+){1,3})", url)
    if match is None:
        raise UpdateError(_("unexpected answer from GitHub"))
    tag = match[1]
    release = Release(tag.lstrip("vV"), url, "", tag=tag)
    name = project.installer_name(release.version)
    download = f"{project.downloads}{tag}/{name}"
    path = download.removeprefix("https://github.com").encode()
    with _get_web(project, f"{project.releases_url}/expanded_assets/{tag}", timeout) as response:
        listing = response.read(RELEASE_MAX_BYTES + 1)
    if len(listing) > RELEASE_MAX_BYTES:
        raise UpdateError(_("unexpected answer from GitHub"))
    notes_path = f"{project.downloads}{tag}/{updates.ITALIAN_NOTES}"
    notes_href = notes_path.removeprefix("https://github.com").encode()
    digest, signed = None, False
    for row in re.finditer(rb"<li\b[^>]*>(.*?)</li>", listing, re.DOTALL):
        if b'href="' + path + b'"' in row[1]:
            digest = re.search(rb"sha256:([0-9a-fA-F]{64})(?![0-9a-fA-F])", row[1])
        if b'href="' + path + SIGNATURE_SUFFIX.encode() + b'"' in row[1]:
            signed = True
        if b'href="' + notes_href + b'"' in row[1]:
            checksum = re.search(rb"sha256:([0-9a-fA-F]{64})(?![0-9a-fA-F])", row[1])
            release.assets.append(Asset(updates.ITALIAN_NOTES, notes_path, 0,
                                        checksum[1].decode() if checksum else ""))
    if digest is not None and signed:
        release.assets.append(Asset(name, download, 0, digest[1].decode(), download + SIGNATURE_SUFFIX))
    return release


def latest_release(project: Project, timeout: float = 15.0) -> Release:
    try:
        return updates.latest_release(project, timeout)
    except UpdateError as error:
        response = error.__context__
        if not isinstance(response, urllib.error.HTTPError):
            raise
        response.close()
        if response.code not in (403, 429) or updates.release_source(project)[0] != project.latest_release_api:
            raise
    try:
        return _latest_web_release(project, timeout)
    except urllib.error.HTTPError as error:
        error.close()
        raise UpdateError(_("GitHub answered {code}").format(code=error.code), http_response=True) from None
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise UpdateError(str(getattr(error, "reason", error)), http_response=True) from None
