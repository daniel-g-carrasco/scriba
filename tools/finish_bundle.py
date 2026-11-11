#!/usr/bin/env python3
"""The PyInstaller bundle made whole and checked, the same way in the CI
and on a developer's computer (under MSYS2 UCRT64, after pyinstaller):

    python tools/finish_bundle.py [dist/Scriba]

1. What GTK reads at run time and PyInstaller does not take: the compiled
   GLib schemas, the Adwaita and hicolor icon themes (without the legacy
   theme and the cursors), their caches made again (without them GTK reads
   thousands of icon files at start, and the copied caches do not know the
   app's own icon: the template's docs/08-trappole.md).
2. The translations other than Italian and English taken out, and
   gdk-pixbuf's image loaders that GTK does not need for the app's icons
   (SVG, PNG and the common ones stay): PyInstaller takes them all, and
   HEIF, AVIF, JPEG XL and TIFF bring along video encoders and decoders
   (x265, which is GPL-2.0, and AV1's four), with the libraries only they
   used, and their lines in loaders.cache. Out too: GIO's TLS module
   (GnuTLS and its five libraries: the app's HTTPS is Python's own). Not
   cairo's script interpreter, which brings LZO (GPL-2.0-or-later): GTK
   itself imports it (found by the check below, 11/11/2026).
3. The licences of what is given away, in `_internal/licenses`: the app's
   own and those in LICENSES/, and for every MSYS2 package a file of the
   bundle comes from (pacman -Qo) its licence files and a line in
   THIRD-PARTY.md (package, version, licence, home page).
4. The files that must be there checked one by one: the app, its
   engines, GStreamer's plugins, the sample, the licences; and every DLL
   any program or library of the bundle imports, found in the bundle or in
   Windows' own System32 (what `ntldd -R` would say, read with pefile).
   Anything missing fails the step.
5. The weight: the whole folder and the ten heaviest files.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = Path(sys.prefix)                       # MSYS2: C:/msys64/ucrt64
PACKAGE_PREFIX = "mingw-w64-ucrt-x86_64-"
KEEP_LANGUAGES = ("it", "en")
KEEP_LOADERS = ("png", "svg", "jpeg", "gif", "ico", "bmp")     # gdk-pixbuf's, by the format in the file's name
# modules nothing of the app loads (see the docstring), beside the loaders
UNUSED_MODULES = ("gio_modules/libgiognutls.dll",)
# what only those left out use: taken out when nothing else imports them
LOADERS_ONLY = ("libgnutls-30.dll", "libnettle-9.dll", "libhogweed-7.dll", "libgmp-10.dll", "libtasn1-6.dll",
                "libidn2-0.dll", "libunistring-5.dll", "libp11-kit-0.dll",
                "libheif.dll", "libx265-217.dll", "libaom.dll", "libdav1d-7.dll", "libde265-0.dll", "librav1e.dll",
                "libSvtAv1Enc-4.dll", "libavif-16.dll", "libjxl.dll", "libjxl_cms.dll", "libjxl_threads.dll",
                "libhwy.dll", "libtiff-6.dll", "libjbig-0.dll", "libLerc.dll", "libdeflate.dll", "libyuv.dll",
                "libsharpyuv-0.dll", "libwebp-7.dll", "libwebpdemux-2.dll", "libwmf-0-2-7.dll", "libwmflite-0-2-7.dll",
                "liblzma-5.dll", "libzstd.dll")
REQUIRED = (
    "Scriba.exe", "scriba-cli.exe",
    "_internal/gi_typelibs/Gtk-4.0.typelib", "_internal/gi_typelibs/Adw-1.typelib",
    "_internal/gi_typelibs/Gst-1.0.typelib", "_internal/gdbus.exe", "_internal/certs/ca-bundle.crt",
    "_internal/share/locale/it/LC_MESSAGES/scriba.mo",
    "_internal/share/icons/hicolor/scalable/apps/io.github.daniel_g_carrasco.scriba.svg",
    "_internal/share/glib-2.0/schemas/gschemas.compiled",
    "_internal/scriba/samples/sample-it.flac", "_internal/scriba/samples/sample-it.txt",
    "_internal/scriba/samples/SOURCES.md", "_internal/scriba/gui/style.css",
    "_internal/whisper-cli.exe", "_internal/whisper-bench.exe", "_internal/ggml-vulkan.dll",
    "_internal/ggml-cpu-x64.dll", "_internal/ffmpeg.exe", "_internal/ffprobe.exe", "_internal/onnxruntime.dll",
    "_internal/licenses/THIRD-PARTY.md", "_internal/licenses/Scriba-MIT.txt",
    "_internal/licenses/diarize-Apache-2.0.txt", "_internal/licenses/LGPL-2.1.txt",
    # the LGPL ffmpeg (tools/build_ffmpeg.sh), never MSYS2's GPL one
    "_internal/licenses/ffmpeg/BUILD.txt", "_internal/licenses/ffmpeg/COPYING.LGPLv2.1",
    *(f"_internal/gst_plugins/libgst{p}.dll" for p in (
        "coreelements", "wasapi2", "audioconvert", "audioresample", "audiorate", "level", "flac", "playback",
        "typefindfunctions", "isomp4", "matroska", "audioparsers", "mediafoundation", "gio", "d3d12", "audiofx")),
)


def run(*argv: str) -> str:
    environ = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    return subprocess.run(argv, capture_output=True, text=True, env=environ, check=False).stdout


def themes(internal: Path) -> None:
    schemas = internal / "share" / "glib-2.0" / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    if not (schemas / "gschemas.compiled").is_file():
        shutil.copy2(PREFIX / "share" / "glib-2.0" / "schemas" / "gschemas.compiled", schemas)
    icons = internal / "share" / "icons"
    for theme in ("Adwaita", "hicolor"):
        shutil.copytree(PREFIX / "share" / "icons" / theme, icons / theme, dirs_exist_ok=True)
    shutil.rmtree(icons / "AdwaitaLegacy", ignore_errors=True)
    shutil.rmtree(icons / "Adwaita" / "cursors", ignore_errors=True)
    for theme in ("Adwaita", "hicolor"):
        subprocess.run(["gtk4-update-icon-cache", "-q", "-t", "-f", str(icons / theme)], check=True)


def languages(internal: Path) -> None:
    locale = internal / "share" / "locale"
    if locale.is_dir():
        for folder in locale.iterdir():
            if folder.is_dir() and not folder.name.startswith(KEEP_LANGUAGES):
                shutil.rmtree(folder)


def loaders(internal: Path) -> list[str]:
    """gdk-pixbuf's loaders other than KEEP_LOADERS taken out, with their
    lines in loaders.cache (blocks of lines, a blank line between, the
    first naming the module), then the libraries only they imported."""
    folder = internal / "lib" / "gdk-pixbuf" / "loaders"
    gone = []
    for module in sorted(folder.glob("*.dll")):
        if not any(kind in module.name.lower() for kind in KEEP_LOADERS):
            module.unlink()
            gone.append(module.name)
    for name in UNUSED_MODULES:
        if (internal / name).is_file():
            (internal / name).unlink()
            gone.append(name)
    cache = internal / "lib" / "gdk-pixbuf" / "loaders.cache"
    if cache.is_file():
        blocks = cache.read_text(encoding="utf-8").split("\n\n")
        kept = [b for b in blocks if not any(name in b for name in gone)]
        cache.write_text("\n\n".join(kept), encoding="utf-8")
    while True:
        imported = {name.lower() for names in imports(internal).values() for name in names}
        unused = [internal / n for n in LOADERS_ONLY if (internal / n).is_file() and n.lower() not in imported]
        if not unused:
            return gone
        for path in unused:
            path.unlink()
            gone.append(path.name)


def imports(internal: Path) -> dict[Path, list[str]]:
    """Every program and library of the bundle and the DLLs it imports."""
    import pefile
    found = {}
    for path in internal.parent.rglob("*"):
        if path.suffix.lower() not in (".dll", ".exe", ".pyd") or not path.is_file():
            continue
        try:
            pe = pefile.PE(str(path), fast_load=True)
            pe.parse_data_directories([pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        except pefile.PEFormatError:
            continue
        found[path] = [entry.dll.decode("ascii", "replace") for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        pe.close()
    return found


def unresolved(internal: Path) -> list[str]:
    """Imports that neither the bundle nor Windows has: a DLL forgotten
    (or one taken out that something still needs)."""
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    here = {p.name.lower() for p in internal.parent.rglob("*.dll")}
    out = []
    for path, names in imports(internal).items():
        for name in names:
            low = name.lower()
            if low.startswith(("api-ms-win-", "ext-ms-")) or low in here or (system / name).is_file():
                continue
            out.append(f"{path.relative_to(internal.parent)} imports {name}")
    return out


def packages_of(internal: Path) -> dict[str, str]:
    """The MSYS2 packages the bundle's programs and libraries come from:
    {package: version}, from where MSYS2 keeps a file of the same name."""
    places = [PREFIX / "bin", PREFIX / "lib" / "gstreamer-1.0"]
    sources = []
    for path in internal.rglob("*"):
        if path.name.lower() in ("ffmpeg.exe", "ffprobe.exe") and (internal / "licenses" / "ffmpeg").is_dir():
            continue        # the LGPL build of tools/build_ffmpeg.sh, not MSYS2's of the same name
        if path.suffix.lower() in (".dll", ".exe", ".pyd"):
            for place in places:
                if (place / path.name).is_file():
                    sources.append(str(place / path.name))
                    break
    owners: set[str] = set()
    for start in range(0, len(sources), 100):
        owners.update(run("pacman", "-Qqo", *sources[start:start + 100]).split())
    # the Python packages, whose modules are in the bundle's archive
    owners.update({f"{PACKAGE_PREFIX}{n}" for n in ("python", "python-numpy", "python-scipy", "python-scikit-learn",
                                                     "python-joblib", "python-threadpoolctl", "python-gobject",
                                                     "python-cairo")})
    versions = {}
    for line in run("pacman", "-Q", *sorted(owners)).splitlines():
        name, _sep, version = line.partition(" ")
        versions[name] = version
    return versions


def licences(internal: Path) -> None:
    out = internal / "licenses"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy2(ROOT / "LICENSE", out / "Scriba-MIT.txt")
    for text in (ROOT / "LICENSES").iterdir():
        shutil.copy2(text, out / text.name)
    # the bundle's ffmpeg is the LGPL build when it is that very file: its
    # sources, its configure line and the licence's text go with it
    own = ROOT / "build" / "ffmpeg-lgpl"
    if (internal / "ffmpeg.exe").is_file() and (own / "bin" / "ffmpeg.exe").is_file() and \
            (internal / "ffmpeg.exe").read_bytes() == (own / "bin" / "ffmpeg.exe").read_bytes():
        (out / "ffmpeg").mkdir()
        for name in ("BUILD.txt", "COPYING.LGPLv2.1"):
            shutil.copy2(own / name, out / "ffmpeg" / name)
    rows = []
    for package, version in sorted(packages_of(internal).items()):
        info = run("pacman", "-Qi", package)
        fields = {k.strip(): v.strip() for k, _s, v in (line.partition(":") for line in info.splitlines()) if v}
        short = package.removeprefix(PACKAGE_PREFIX)
        shipped = PREFIX / "share" / "licenses" / short
        if shipped.is_dir():
            shutil.copytree(shipped, out / short, dirs_exist_ok=True)
        rows.append(f"| {short} | {version} | {fields.get('Licenses', '?')} | {fields.get('URL', '')} | "
                    f"{'licenses/' + short if shipped.is_dir() else 'the licence named (texts in this folder)'} |")
    ffmpeg_build = out / "ffmpeg" / "BUILD.txt"
    head = ["# Third-party software in Scriba for Windows", "",
            "Every library and program below is given away with Scriba as its authors' licence allows; the "
            "licence files are in this folder. The packages are MSYS2's (https://packages.msys2.org), whose "
            "sources are at https://github.com/msys2/MINGW-packages.", ""]
    if ffmpeg_build.is_file():
        head += ["ffmpeg and ffprobe are Scriba's own build of FFmpeg's release sources, under the LGPL 2.1 or "
                 "later: how they were built, and where the sources are, is in licenses/ffmpeg/BUILD.txt.", ""]
    head += ["| Package | Version | Licence | Home page | Licence files |", "|---|---|---|---|---|"]
    (out / "THIRD-PARTY.md").write_text("\n".join(head + rows) + "\n", encoding="utf-8")


def check(bundle: Path) -> list[str]:
    return [name for name in REQUIRED if not (bundle / name).is_file()]


def weight(bundle: Path) -> None:
    files = [p for p in bundle.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    print(f"bundle: {total / 2**20:.0f} MiB in {len(files)} files")
    for path in sorted(files, key=lambda p: p.stat().st_size, reverse=True)[:10]:
        print(f"  {path.stat().st_size / 2**20:7.1f} MiB  {path.relative_to(bundle)}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    bundle = Path(argv[0]) if argv else ROOT / "dist" / "Scriba"
    internal = bundle / "_internal"
    themes(internal)
    languages(internal)
    print("taken out:", ", ".join(loaders(internal)))
    licences(internal)
    missing = check(bundle) + unresolved(internal)
    weight(bundle)
    for name in missing:
        print(f"MISSING {name}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
