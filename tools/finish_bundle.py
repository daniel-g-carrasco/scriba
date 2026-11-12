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
3. The licences in `_internal/licenses`: the app's own, LICENSES/, and
   package-owned licence texts identified through the local pacman database.
   PACKAGES.json accounts for the actual TOC inputs, Python modules and GTK
   assets. THIRD-PARTY.md lists the installed metadata and missing texts.
4. The files that must be there checked one by one: the app, its
   engines, GStreamer's plugins, the sample, the licences; and every DLL
   any program or library of the bundle imports, found in the bundle or in
   Windows' own System32 (what `ntldd -R` would say, read with pefile).
   Anything missing fails the step.
5. The weight: the whole folder and the ten heaviest files.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote

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
    "_internal/licenses/THIRD-PARTY.md", "_internal/licenses/PACKAGES.json",
    "_internal/licenses/Scriba-GPL-3.0-or-later.txt", "_internal/licenses/COPYRIGHT.txt",
    "_internal/licenses/diarize-Apache-2.0.txt", "_internal/licenses/LGPL-2.1.txt",
    # releases retain the smaller static FFmpeg from tools/build_ffmpeg.sh
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
            gio_modules = internal / "gio_modules"
            if gio_modules.is_dir():
                subprocess.run([str(PREFIX / "bin" / "gio-querymodules.exe"), str(gio_modules)], check=True)
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


def pacman_fields(path: Path) -> dict[str, list[str]]:
    """Read pacman's local database format without locale-dependent output."""
    fields: dict[str, list[str]] = {}
    key = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("%") and line.endswith("%"):
            key = line.strip("%")
            fields[key] = []
        elif line and key:
            fields[key].append(line)
    return fields


def installed_packages(prefix: Path) -> tuple[dict, dict]:
    packages, owners = {}, {}
    database = prefix.parent / "var" / "lib" / "pacman" / "local"
    for folder in sorted(database.iterdir()):
        if not (folder / "desc").is_file() or not (folder / "files").is_file():
            continue
        fields = pacman_fields(folder / "desc")
        name = fields["NAME"][0]
        packages[name] = {"package": name, "version": fields["VERSION"][0],
                          "base": (fields.get("BASE") or [name])[0],
                          "licences": fields.get("LICENSE", []), "url": (fields.get("URL") or [""])[0]}
        members = pacman_fields(folder / "files").get("FILES", [])
        packages[name]["licence_files"] = [member for member in members if
                                            member.startswith((prefix.name + "/share/licenses/", "usr/share/licenses/")) and
                                            not member.endswith("/")]
        for member in members:
            if member.endswith("/"):
                continue
            key = member.casefold()
            if key in owners and owners[key][0] != name:
                raise ValueError(f"ambiguous pacman owner: {member}")
            owners[key] = (name, member)
    if not packages:
        raise ValueError(f"no installed package database in {database}")
    return packages, owners


def local_path(value: str) -> Path:
    """A Windows TOC can also be inspected read-only from WSL."""
    value = value.replace(chr(92), "/")
    if os.name != "nt" and len(value) > 2 and value[1:3] == ":/":
        return Path("/mnt") / value[0].lower() / value[3:]
    return Path(value)


def toc_entries(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and all(isinstance(item, str) for item in value):
            yield tuple(value)
        else:
            for item in value:
                yield from toc_entries(item)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(internal: Path, *, prefix: Path = PREFIX, root: Path = ROOT, toc_dir: Path | None = None) -> dict:
    """Account for the actual build inputs, rejecting missing or changed origins.

    Only paths relative to MSYS2 and the bundle leave this function. Generated
    caches have input records rather than a fictitious byte-for-byte owner.
    """
    internal, prefix, root = internal.resolve(), prefix.resolve(), root.resolve()
    if toc_dir is None:
        toc_dir = next((p for p in (root / "build" / "scriba", root / "build" / "pyinstaller" / "scriba")
                        if (p / "COLLECT-00.toc").is_file()), root / "build" / "scriba")
    toc_dir = toc_dir.resolve()
    tocs = {}
    for path in sorted(toc_dir.glob("*.toc")):
        try:
            tocs[path.name] = ast.literal_eval(path.read_text(encoding="utf-8"))
        except (ValueError, SyntaxError) as error:
            raise ValueError(f"invalid literal TOC: {path.name}") from error
    for kind in ("Analysis", "PYZ", "COLLECT", "EXE"):
        if not any(name.startswith(kind + "-") for name in tocs):
            raise ValueError(f"missing {kind} TOC in {toc_dir}")
    metadata, owners = installed_packages(prefix)
    used, accounted, generated, copied_sources = {}, set(), [], {}
    msys = prefix.parent

    def owner(source):
        try:
            member = source.relative_to(msys).as_posix()
        except ValueError as error:
            raise ValueError(f"source outside MSYS2: {source.name}") from error
        found = owners.get(member.casefold())
        if found is None:
            raise ValueError(f"no pacman owner: {member}")
        package, member = found
        if not source.is_file():
            raise ValueError(f"missing installed input: {member}")
        if package not in used:
            used[package] = {**metadata[package], "files": set(), "modules": set(), "origins": {},
                             "module_origins": {}}
        return package, member

    def copied(path, source):
        package, member = owner(source)
        digest = sha256(path)
        if digest != sha256(source):
            raise ValueError(f"hash mismatch: {path.relative_to(internal.parent).as_posix()}")
        name = path.relative_to(internal.parent).as_posix()
        used[package]["files"].add(name)
        used[package]["origins"][name] = {"package_file": member, "sha256": digest}
        accounted.add(path)
        copied_sources[path] = source

    def generated_file(path, sources):
        inputs = []
        for source in sorted(set(sources)):
            package, member = owner(source)
            inputs.append({"package": package, "package_file": member, "sha256": sha256(source)})
        if not inputs:
            raise ValueError(f"no generated inputs: {path.relative_to(internal.parent).as_posix()}")
        generated.append({"file": path.relative_to(internal.parent).as_posix(), "sha256": sha256(path),
                          "inputs": inputs})
        accounted.add(path)

    entries = {name: list(toc_entries(value)) for name, value in tocs.items()}
    collected = [entry for name, values in entries.items() if name.startswith("COLLECT-") for entry in values]
    exe_outputs = {local_path(value[0]).resolve() for name, value in tocs.items()
                   if name.startswith("EXE-") and isinstance(value, (list, tuple)) and
                   value and isinstance(value[0], str)}
    bootstrap = next((local_path(origin) for values in entries.values() for module, origin, kind in values
                      if module == "pyiboot01_bootstrap" and kind == "PYSOURCE"), None)
    # Analysis names the original Python inputs, including stdlib bootstrap
    # dependencies that EXE/PKG refer to as freshly compiled localpycs files.
    analysis_sources = {}
    for name, values in entries.items():
        if name.startswith("Analysis-"):
            for module, origin, kind in values:
                if kind == "PYMODULE" and origin != "-":
                    analysis_sources.setdefault(module, set()).add(local_path(origin).resolve())
    # Analysis contains modules from base_library.zip, PYZ contains the other
    # pure Python modules. EXE/PKG also record PyInstaller's embedded runtime.
    for values in entries.values():
        for module, origin, kind in values:
            if kind == "PYMODULE" and origin == "-":
                continue  # A namespace package contains no source file.
            source, compiled = local_path(origin), None
            if kind == "PYMODULE" and source.is_relative_to(toc_dir / "localpycs"):
                compiled = source
                candidates = analysis_sources.get(module, set())
                if len(candidates) > 1:
                    raise ValueError(f"ambiguous compiled Python origin: {module}")
                if candidates:
                    source = next(iter(candidates))
                elif module.startswith("pyimod") and bootstrap is not None:
                    source = bootstrap.parent / (module + ".py")
                else:
                    raise ValueError(f"unresolved compiled Python origin: {module}")
                if not compiled.is_file():
                    raise ValueError(f"missing compiled Python input: {module}")
            if kind in ("PYMODULE", "PYSOURCE") and source.is_relative_to(msys):
                package, member = owner(source)
                used[package]["modules"].add(module)
                origin_record = {"package_file": member, "sha256": sha256(source)}
                existing = used[package]["module_origins"].setdefault(module, {})
                if compiled is not None:
                    digest = sha256(compiled)
                    if existing.get("compiled_sha256", digest) != digest:
                        raise ValueError(f"inconsistent compiled Python input: {module}")
                    origin_record["compiled_sha256"] = digest
                existing.update(origin_record)
            elif kind in ("PYMODULE", "PYSOURCE") and not source.is_relative_to(root):
                raise ValueError(f"unresolved Python origin: {module}")
            elif kind == "EXECUTABLE" and source.is_relative_to(msys):
                package, member = owner(source)
                module = "PyInstaller.bootloader." + source.stem
                used[package]["modules"].add(module)
                used[package]["module_origins"][module] = {"package_file": member, "sha256": sha256(source)}

    # Hook search paths are recorded as (directory, priority) by Analysis.
    # Their owners matter even though the hook code itself is not copied.
    def hook_directories(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                yield local_path(value[0])
            else:
                for item in value:
                    yield from hook_directories(item)

    for name, value in tocs.items():
        if name.startswith("Analysis-"):
            for folder in hook_directories(value):
                if folder.is_relative_to(msys):
                    members = [msys / member for _package, member in owners.values() if
                               member.startswith(folder.relative_to(msys).as_posix().casefold() + "/")]
                    if not members:
                        raise ValueError(f"no pacman owner for hooks: {folder.name}")
                    for source in members:
                        owner(source)

    for destination, origin, kind in collected:
        relative = PurePosixPath(destination.replace(chr(92), "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe TOC destination")
        path = (internal.parent if kind == "EXECUTABLE" else internal) / Path(relative)
        if not path.is_file():
            continue  # Deliberately removed loaders, languages and their DLLs.
        source = local_path(origin)
        if destination == "certs/ca-bundle.crt":
            if sha256(path) != sha256(source):
                raise ValueError("hash mismatch: generated certificate bundle")
            inputs = [prefix / "bin" / "update-ca-trust", prefix / "bin" / "p11-kit.exe"]
            for trust_root in (prefix / "share/pki/ca-trust-source", prefix / "etc/pki/ca-trust/source"):
                inputs.extend(p for p in trust_root.rglob("*") if p.is_file())
            generated_file(path, inputs)
        elif path.name == "icon-theme.cache" and relative.parts[:2] == ("share", "icons"):
            continue  # Rebuilt by themes(); handled with the added assets below.
        elif path.name == "gschemas.compiled":
            if sha256(path) != sha256(source):
                raise ValueError("hash mismatch: compiled schema cache")
            schema_dir = source.parent if source.parent.name == "_pyi_gschema_compilation" else prefix / relative.parent
            inputs = []
            for xml in sorted(schema_dir.glob("*.xml")):
                installed = prefix / "share" / "glib-2.0" / "schemas" / xml.name
                if sha256(xml) != sha256(installed):
                    raise ValueError(f"hash mismatch: schema input {xml.name}")
                inputs.append(installed)
            generated_file(path, inputs)
        elif path.name == "loaders.cache":
            inputs = [local_path(src) for dest, src, _kind in collected
                      if dest.startswith("lib/gdk-pixbuf/loaders/") and (internal / dest).is_file()]
            generated_file(path, inputs)
        elif path.name == "giomodule.cache":
            inputs = [local_path(src) for dest, src, _kind in collected
                      if dest.startswith("gio_modules/") and dest.lower().endswith(".dll") and
                      (internal / dest).is_file()]
            generated_file(path, [prefix / "bin" / "gio-querymodules.exe", *inputs])
        elif source.parent == toc_dir and (
                (kind == "EXECUTABLE" and source.resolve() in exe_outputs) or
                (kind == "DATA" and destination == "base_library.zip" and source.name == "base_library.zip")):
            # The build directory may be outside the checkout. Only these
            # explicit generated outputs are admitted, with identical bytes.
            if sha256(path) != sha256(source):
                raise ValueError(f"hash mismatch: build output {destination}")
            accounted.add(path)
        elif source.is_relative_to(msys):
            copied(path, source)
        elif source.is_relative_to(root / "build" / "ffmpeg-lgpl" / "bin"):
            if path.name not in ("ffmpeg.exe", "ffprobe.exe") or sha256(path) != sha256(source):
                raise ValueError(f"hash mismatch: own FFmpeg {path.name}")
            accounted.add(path)
        elif source.is_relative_to(root):
            # App data and build products have their own attribution. Unknown
            # third-party inputs are never exempted just for sharing a name.
            project_dirs = ("src", "data", "locale", "build-aux")
            if any(source.is_relative_to(root / folder) for folder in project_dirs):
                if sha256(path) != sha256(source):
                    raise ValueError(f"hash mismatch: app data {destination}")
                accounted.add(path)
                copied_sources[path] = source
            elif path.name == "loaders.cache":
                accounted.add(path)
            else:
                raise ValueError(f"unresolved build origin: {destination}")
        else:
            raise ValueError(f"unresolved build origin: {destination}")

    # Assets added by themes(), absent from COLLECT, still have exact inputs.
    for path in sorted(internal.parent.rglob("*")):
        if not path.is_file() or path in accounted or path.is_relative_to(internal / "licenses"):
            continue
        if not path.is_relative_to(internal):
            raise ValueError(f"no build origin: {path.name}")
        relative = path.relative_to(internal)
        if path.name == "icon-theme.cache" and relative.parts[:2] == ("share", "icons"):
            theme = path.parent
            inputs, project_inputs = [], []
            for asset in theme.rglob("*"):
                if not asset.is_file() or asset.name == "icon-theme.cache":
                    continue
                source = copied_sources.get(asset, prefix / asset.relative_to(internal))
                if source.is_relative_to(msys):
                    inputs.append(source)
                elif source.is_relative_to(root):
                    project_inputs.append({"project_file": source.relative_to(root).as_posix(),
                                           "sha256": sha256(source)})
                else:
                    raise ValueError(f"no icon cache input: {asset.name}")
            generated_file(path, inputs)
            if project_inputs:
                generated[-1]["project_inputs"] = sorted(project_inputs, key=lambda r: r["project_file"])
        elif relative.parts[:2] == ("share", "icons"):
            copied(path, prefix / relative)
        elif path.name == "gschemas.compiled":
            generated_file(path, list((prefix / relative.parent).glob("*.xml")))
        else:
            raise ValueError(f"no build origin: {relative.as_posix()}")
    packages = []
    for package in sorted(used):
        record = used[package]
        record["files"] = sorted(record["files"])
        record["modules"] = sorted(record["modules"])
        record["origins"] = dict(sorted(record["origins"].items()))
        record["module_origins"] = dict(sorted(record["module_origins"].items()))
        packages.append(record)
    return {"schema_version": 1, "packages": packages, "generated_files": sorted(generated, key=lambda r: r["file"])}


def packages_of(internal: Path) -> dict[str, str]:
    """Compatibility summary; the JSON inventory carries the provenance."""
    return {package["package"]: package["version"] for package in inventory(internal)["packages"]}


def upstream_notices(record: dict, root: Path) -> list[Path]:
    """Validate source-derived notices before copying or replacing any output."""
    short = record["package"].removeprefix(PACKAGE_PREFIX)
    folder = root / "LICENSES" / "upstream" / short
    if not folder.is_dir():
        return []
    # Retain older verified supplements for local builds, while allowing
    # explicitly verified rolling-package versions to coexist. Never select
    # a latest version or accept a nearby version's notice as equivalent.
    token = quote(record["version"], safe="")
    if token in ("", ".", ".."):
        raise ValueError(f"unsafe upstream notice version: {short}")
    versioned = folder / "versions" / token
    if versioned.is_dir():
        folder = versioned
    provenance = folder / "PROVENANCE.json"
    evidence = json.loads(provenance.read_text(encoding="utf-8"))
    if (evidence.get("schema_version"), evidence.get("package"), evidence.get("version")) != \
            (1, record["package"], record["version"]):
        raise ValueError(f"upstream notice package/version mismatch: {short}")
    paths = []
    for entry in evidence.get("files", []):
        relative = PurePosixPath(entry["file"])
        if relative.is_absolute() or ".." in relative.parts or chr(92) in entry["file"]:
            raise ValueError(f"unsafe upstream notice path: {short}")
        path = folder / Path(relative)
        if not path.is_file() or sha256(path) != entry["sha256"]:
            raise ValueError(f"upstream notice hash mismatch: {short}/{relative}")
        paths.append(path)
    if not paths:
        raise ValueError(f"no upstream notice texts: {short}")
    return [provenance, *paths]


def licences(internal: Path, *, prefix: Path = PREFIX, root: Path = ROOT, toc_dir: Path | None = None,
             out_dir: Path | None = None, licence_root: Path | None = None) -> None:
    """Write notices; separate roots allow review against an earlier bundle."""
    licence_root = root if licence_root is None else licence_root
    data = inventory(internal, prefix=prefix, root=root, toc_dir=toc_dir)
    supplements = {record["package"]: upstream_notices(record, licence_root) for record in data["packages"]}
    out = out_dir if out_dir is not None else internal / "licenses"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy2(licence_root / "LICENSE", out / "Scriba-GPL-3.0-or-later.txt")
    shutil.copy2(licence_root / "COPYRIGHT", out / "COPYRIGHT.txt")
    for text in (licence_root / "LICENSES").iterdir():
        if text.is_dir():
            if text.name != "upstream":
                shutil.copytree(text, out / text.name)
        else:
            shutil.copy2(text, out / text.name)
    # the bundle's ffmpeg is the LGPL build when it is that very file: its
    # sources, its configure line and the licence's text go with it
    own = root / "build" / "ffmpeg-lgpl"
    if (internal / "ffmpeg.exe").is_file() and (own / "bin" / "ffmpeg.exe").is_file() and \
            (internal / "ffmpeg.exe").read_bytes() == (own / "bin" / "ffmpeg.exe").read_bytes():
        (out / "ffmpeg").mkdir()
        for name in ("BUILD.txt", "COPYING.LGPLv2.1"):
            shutil.copy2(own / name, out / "ffmpeg" / name)
    rows, missing, without_installed = [], [], []
    for record in data["packages"]:
        package, version = record["package"], record["version"]
        short = package.removeprefix(PACKAGE_PREFIX)
        for member in record["licence_files"]:
            source = prefix.parent / member
            relative = PurePosixPath(*PurePosixPath(member).parts[3:])
            destination = out / short / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        upstream_files = []
        for source in supplements[package]:
            relative = source.relative_to(licence_root / "LICENSES")
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            upstream_files.append(relative.as_posix())
        if upstream_files:
            record["upstream_licence_files"] = upstream_files
        statuses = []
        if record["licence_files"]:
            statuses.append(f"[package licence files]({short}/)")
        else:
            without_installed.append(package)
        if upstream_files:
            notice_folder = PurePosixPath(upstream_files[0]).parent.as_posix()
            statuses.append(f"[upstream notices and provenance]({notice_folder}/)")
        if not statuses:
            missing.append(package)
            statuses.append("No package licence files installed; source notices need review")
        status = "; ".join(statuses)
        rows.append(f"| {short} | {version} | {'; '.join(record['licences']) or '?'} | {record['url']} | {status} |")
    data["missing_licence_files"] = missing
    data["packages_without_installed_licence_files"] = without_installed
    (out / "PACKAGES.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ffmpeg_build = out / "ffmpeg" / "BUILD.txt"
    head = ["# Third-party software in Scriba for Windows", "",
            ("PACKAGES.json records the installed MSYS2 packages identified from PyInstaller's build inputs "
            "and the GTK assets copied into this bundle. Versions and licence labels are from the local "
            "package database. File origins and SHA-256 digests identify copied files; generated caches "
            "list their inputs separately. Python modules inside the executable's archives are listed by name."), "",
            ("Package recipes: https://github.com/msys2/MINGW-packages. "
            "The table identifies the licence files actually copied. A missing package licence directory "
            "does not establish that its notices are covered by another text in this folder."), ""]
    if ffmpeg_build.is_file():
        head += [("ffmpeg and ffprobe are Scriba's own build of FFmpeg's release sources, under the LGPL 2.1 or "
                  "later: how they were built, and where the sources are, is in ffmpeg/BUILD.txt."), ""]
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
