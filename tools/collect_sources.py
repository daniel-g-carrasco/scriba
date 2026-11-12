#!/usr/bin/env python3
"""Freeze corresponding MSYS2 sources without executing package recipes.

Reads a bundle's PACKAGES.json, verifies signed binary/source packages, ties
PKGBUILD to binary BUILDINFO, checks declared source hashes and records VCS/SKIP
inputs explicitly. Archives are streamed, never extracted into the filesystem.
Output must be outside this checkout. The result is build evidence, not a claim
of legal compliance or a byte-identical rebuild.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

try:
    import gstplay_receipt
except ModuleNotFoundError:
    from tools import gstplay_receipt

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "mingw-w64-ucrt-x86_64-"
ALGORITHMS = {"sha256sums": "sha256", "sha512sums": "sha512", "b2sums": "blake2b",
              "sha1sums": "sha1", "md5sums": "md5", "sha224sums": "sha224", "sha384sums": "sha384"}
MAX_METADATA = 4 << 20
MAX_UNPACKED = 32 << 30


class SourceError(Exception):
    """Missing or unverifiable input: collection must not substitute another version."""


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(name):
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name or ":" in name:
        raise SourceError("unsafe archive or bundle path")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
        raise SourceError("unsafe archive or bundle path")
    clean = str(PurePosixPath(name))
    if clean == ".":
        raise SourceError("empty archive or bundle path")
    return clean


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.~@-]*", name):
        raise SourceError("invalid package name or version")
    return name


def safe_version(version):
    if not isinstance(version, str):
        raise SourceError("invalid package version")
    plain = version
    if ":" in version:
        epoch, plain = version.split(":", 1)
        if not epoch.isdecimal():
            raise SourceError("invalid package epoch")
    safe_name(plain)
    return version


def archive_version(version):
    safe_version(version)
    if ":" in version:
        epoch, version = version.split(":", 1)
        if not epoch.isdecimal() or not version:
            raise SourceError("invalid package epoch")
    return version


def evidence_token(package, version):
    return urllib.parse.quote(safe_name(package) + "-" + safe_version(version), safe="+_.~-@")


def package_repositories(package):
    if package.startswith(PREFIX):
        return "https://repo.msys2.org/mingw/ucrt64", "https://repo.msys2.org/mingw/sources", "any"
    if package.startswith("mingw-"):
        raise SourceError("unsupported MinGW environment: expected UCRT64")
    return "https://repo.msys2.org/msys/x86_64", "https://repo.msys2.org/msys/sources", "x86_64"


def require_external_output(path):
    path = Path(path).resolve()
    if path == ROOT or ROOT in path.parents:
        raise SourceError("output must be outside the source repository")
    return path


def bundle_path(bundle, relative):
    relative = safe_member(relative)
    root = Path(bundle).resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise SourceError(f"bundle file missing or outside bundle: {relative}")
    return path


def fields(text):
    result = {}
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, separator, value = line.strip().partition(" = ")
            if separator:
                result.setdefault(key, []).append(value)
    return result


def single(data, key):
    values = data.get(key, [])
    if len(values) != 1:
        raise SourceError(f"metadata needs exactly one {key}")
    return values[0]


def load_inventory(path):
    path = Path(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("packages"), list):
            raise SourceError("unsupported inventory schema")
        kind = "json"
    else:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("| "):
                cells = [cell.strip() for cell in line.split("|")[1:-1]]
                if len(cells) >= 2 and cells[0] not in ("Package", "---"):
                    rows.append({"package": PREFIX + cells[0], "version": cells[1], "files": []})
        data, kind = {"schema_version": 1, "packages": rows}, "markdown-incomplete"
    if not data["packages"]:
        raise SourceError("inventory contains no packages")
    seen = set()
    for entry in data["packages"]:
        name = safe_name(entry["package"])
        safe_version(entry["version"])
        if name in seen:
            raise SourceError(f"duplicate inventory package: {name}")
        seen.add(name)
        for file in entry.get("files", []):
            safe_member(file)
        for file, origin in entry.get("origins", {}).items():
            safe_member(file)
            safe_member(origin["package_file"])
        for origin in entry.get("module_origins", {}).values():
            safe_member(origin["package_file"])
    for generated in data.get("generated_files", []):
        safe_member(generated["file"])
        for origin in generated.get("project_inputs", []):
            safe_member(origin["project_file"])
        for origin in generated.get("inputs", []):
            safe_name(origin["package"])
            safe_member(origin["package_file"])
    custom = data.get("custom_components", [])
    if len(custom) > 1 or any(item.get("component") != "gstplay" for item in custom):
        raise SourceError("unsupported custom component inventory")
    package_files = {file for entry in data["packages"]
                     for file in [*entry.get("files", []), *entry.get("origins", {})]}
    for component in custom:
        if component.get("files") != ["_internal/libgstplay-1.0-0.dll"]:
            raise SourceError("unexpected custom GstPlay file mapping")
        if package_files.intersection(component["files"]):
            raise SourceError("custom GstPlay cannot claim a pacman file origin")
    data["inventory_kind"] = kind
    return data


@contextlib.contextmanager
def archive_reader(path, zstd=None):
    path = Path(path)
    process = None
    with tempfile.TemporaryFile() as errors:
        try:
            if path.suffix == ".zst":
                executable = zstd or shutil.which("zstd")
                if not executable:
                    raise SourceError("zstd is required to read .zst archives")
                process = subprocess.Popen([str(executable), "-q", "-d", "-c", str(path)],
                                           stdout=subprocess.PIPE, stderr=errors)
                stream = process.stdout
            else:
                stream = path.open("rb")
            try:
                with tarfile.open(fileobj=stream, mode="r|*") as archive:
                    yield archive
            finally:
                stream.close()
            if process is not None and process.wait() != 0:
                raise SourceError(f"cannot decompress {path.name}")
        except (tarfile.TarError, EOFError) as error:
            raise SourceError(f"invalid archive {path.name}: {type(error).__name__}") from error
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait()


def scan_archive(path, wanted=(), source=False, zstd=None):
    """Read metadata and selected file hashes; never materialise tar members."""
    records, texts, algorithms, total = {}, {}, ({"sha256", *ALGORITHMS.values()} if source else {"sha256"}), 0
    with archive_reader(path, zstd) as archive:
        for member in archive:
            name = safe_member(member.name)
            if name in records:
                raise SourceError(f"duplicate archive member: {name}")
            total += member.size
            if total > MAX_UNPACKED:
                raise SourceError("archive exceeds uncompressed size limit")
            record = {"size": member.size, "type": "directory" if member.isdir() else "other"}
            records[name] = record
            if member.issym() or member.islnk():
                target = member.linkname
                if "\\" in target or target.startswith("/") or re.match(r"^[A-Za-z]:", target):
                    raise SourceError("unsafe archive link")
                combined = (str(PurePosixPath(name).parent / target) if member.issym() else target)
                safe_member(posixpath.normpath(combined))
                record.update(type="link", target=target)
                continue
            if not member.isfile():
                if not member.isdir():
                    raise SourceError("unsupported special archive member")
                continue
            record["type"] = "file"
            metadata = PurePosixPath(name).name in (".PKGINFO", ".BUILDINFO", ".SRCINFO", "PKGBUILD")
            hashed = metadata or name in wanted or source
            if not hashed:
                continue
            if metadata and member.size > MAX_METADATA:
                raise SourceError("archive metadata exceeds size limit")
            digest = {algorithm: hashlib.new(algorithm) for algorithm in algorithms}
            pieces = []
            stream = archive.extractfile(member)
            if stream is None:
                raise SourceError("missing archive member data")
            with stream:
                while chunk := stream.read(1 << 20):
                    for hasher in digest.values():
                        hasher.update(chunk)
                    if metadata:
                        pieces.append(chunk)
            record["hashes"] = {key: value.hexdigest() for key, value in digest.items()}
            if metadata:
                texts[name] = b"".join(pieces)
                if name.endswith("/.SRCINFO"):
                    info = fields(texts[name].decode("utf-8"))
                    algorithms.update(ALGORITHMS[key] for key in ALGORITHMS if key in info)
    return records, texts


def source_filename(value):
    alias, separator, url = value.partition("::")
    if separator:
        name = safe_member(alias)
    else:
        url = value
        parsed = urllib.parse.urlsplit(url)
        name = safe_member(urllib.parse.unquote(PurePosixPath(parsed.path).name) if parsed.scheme else value)
    return name, url


def inspect_source(path, base, recipe_hash, zstd=None):
    safe_name(base)
    records, texts = scan_archive(path, source=True, zstd=zstd)
    recipe = texts.get(f"{base}/PKGBUILD")
    srcinfo = texts.get(f"{base}/.SRCINFO")
    if recipe is None or srcinfo is None:
        raise SourceError("source archive is missing PKGBUILD or SRCINFO")
    actual = hashlib.sha256(recipe).hexdigest()
    if actual != recipe_hash:
        raise SourceError("PKGBUILD checksum differs from binary BUILDINFO")
    info = fields(srcinfo.decode("utf-8"))
    if single(info, "pkgbase") != base:
        raise SourceError("source pkgbase differs from binary")
    payloads = []
    for suffix in ("", "_x86_64"):
        declared = info.get("source" + suffix, [])
        checksums = {algorithm: info[key + suffix] for key, algorithm in ALGORITHMS.items() if key + suffix in info}
        if any(len(values) != len(declared) for values in checksums.values()):
            raise SourceError("source and checksum counts differ")
        for index, value in enumerate(declared):
            filename, url = source_filename(value)
            expected = {algorithm: values[index] for algorithm, values in checksums.items()}
            if not expected:
                raise SourceError("source has no declared checksum")
            vcs = urllib.parse.urlsplit(url).scheme.split("+", 1)[0] in ("git", "hg", "svn", "bzr", "fossil")
            candidates = [f"{base}/{filename}"]
            if vcs:
                candidates += [f"{base}/{filename.removesuffix('.git')}", f"{base}/{filename}.git"]
                material = [name for name in records if any(name == candidate or name.startswith(candidate + "/")
                                                           for candidate in candidates)]
                if not material:
                    raise SourceError(f"missing VCS material: {filename}")
                fragment = urllib.parse.parse_qs(urllib.parse.urlsplit(url).fragment)
                revision = next(iter(fragment.get("commit", fragment.get("tag", fragment.get("branch", [])))), "")
                payloads.append({"source": value, "status": "vcs_unchecked", "revision": revision,
                                 "members": material[:20], "member_count": len(material), "checksums": expected})
                continue
            record = records.get(candidates[0])
            if record is None or record.get("type") != "file":
                raise SourceError(f"missing declared source: {filename}")
            if not record.get("hashes"):
                raise SourceError(f"source checksum unavailable for nested member: {filename}")
            checked = {}
            for algorithm, checksum in expected.items():
                if checksum == "SKIP":
                    continue
                got = record["hashes"].get(algorithm)
                if got is None or got != checksum:
                    raise SourceError(f"source checksum mismatch: {filename} ({algorithm})")
                checked[algorithm] = got
            payloads.append({"source": value, "file": candidates[0], "status": "verified" if checked else "skipped",
                             "checksums": expected, "verified_hashes": checked, "sha256": record["hashes"]["sha256"]})
    if not payloads:
        raise SourceError("SRCINFO declares no source payloads")
    return {"base": base, "recipe_sha256": actual, "payloads": payloads,
            "unhashed_sources": sum(payload["status"] != "verified" for payload in payloads)}


def inspect_binary(path, entry, bundle, zstd=None):
    origins = entry.get("origins", {})
    module_origins = entry.get("module_origins", {})
    generated_inputs = entry.get("_generated_inputs", [])
    wanted = {safe_member(value["package_file"])
              for value in [*origins.values(), *module_origins.values(), *generated_inputs]}
    records, texts = scan_archive(path, wanted=wanted, zstd=zstd)
    pkg = texts.get(".PKGINFO", texts.get("./.PKGINFO"))
    build = texts.get(".BUILDINFO", texts.get("./.BUILDINFO"))
    if pkg is None or build is None:
        raise SourceError("binary package is missing PKGINFO or BUILDINFO")
    info, build_info = fields(pkg.decode("utf-8")), fields(build.decode("utf-8"))
    if single(info, "pkgname") != entry["package"] or single(build_info, "pkgname") != entry["package"]:
        raise SourceError("binary package name differs from inventory")
    if single(info, "pkgver") != entry["version"] or single(build_info, "pkgver") != entry["version"]:
        raise SourceError("binary package version differs from inventory")
    base = safe_name(single(info, "pkgbase"))
    if entry.get("base", base) != base or single(build_info, "pkgbase") != base:
        raise SourceError("binary pkgbase differs from inventory")
    recipe_hash = single(build_info, "pkgbuild_sha256sum")
    if not re.fullmatch(r"[0-9a-f]{64}", recipe_hash):
        raise SourceError("invalid binary PKGBUILD hash")
    files = []
    for relative in entry.get("files", []):
        actual = sha256(bundle_path(bundle, relative))
        origin = origins.get(relative)
        if origin is None:
            raise SourceError(f"bundle file has no package origin: {relative}")
        source = records.get(safe_member(origin["package_file"]), {})
        if actual != origin["sha256"] or actual != source.get("hashes", {}).get("sha256"):
            raise SourceError(f"bundle file checksum differs from binary package: {relative}")
        files.append({"file": relative, "package_file": origin["package_file"], "sha256": actual, "status": "verified"})
    modules = []
    for module, origin in module_origins.items():
        source = records.get(safe_member(origin["package_file"]), {})
        if origin["sha256"] != source.get("hashes", {}).get("sha256"):
            raise SourceError(f"module input checksum differs from binary package: {module}")
        modules.append({"module": module, **origin, "status": "input_verified"})
    generated = []
    for origin in generated_inputs:
        source = records.get(safe_member(origin["package_file"]), {})
        if origin["sha256"] != source.get("hashes", {}).get("sha256"):
            raise SourceError("generated file input checksum differs from binary package")
        generated.append({**origin, "status": "input_verified"})
    return {"base": base, "recipe_sha256": recipe_hash, "files": files, "module_inputs": modules,
            "generated_inputs": generated,
            "pkginfo_text": pkg.decode("utf-8"), "buildinfo_text": build.decode("utf-8")}


def signature_fingerprint(output, returncode):
    bad = re.search(r"\[GNUPG:\] (?:BADSIG|ERRSIG|REVKEYSIG|EXPSIG|EXPKEYSIG|NO_PUBKEY)\b", output)
    valid = re.findall(r"^\[GNUPG:\] VALIDSIG ([0-9A-Fa-f]{40,64})\b", output, re.MULTILINE)
    if returncode or bad or not valid:
        raise SourceError("archive signature could not be verified")
    return valid[0].upper()


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise SourceError(f"existing evidence differs: {path.name}")
        return
    with path.open("xb") as stream:
        stream.write(data)


def copy_new(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(source) != sha256(destination):
            raise SourceError(f"existing evidence differs: {destination.name}")
        return
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1 << 20)


def public_keyring(data):
    if b"PRIVATE KEY" in data:
        raise SourceError("a public keyring is required")
    if b"-----BEGIN PGP PUBLIC KEY BLOCK-----" not in data:
        return data
    packets = []
    for block in re.findall(rb"-----BEGIN PGP PUBLIC KEY BLOCK-----(.*?)-----END PGP PUBLIC KEY BLOCK-----", data, re.DOTALL):
        body = []
        for line in block.splitlines():
            line = line.strip()
            if line and b":" not in line and not line.startswith(b"="):
                body.append(line)
        packets.append(base64.b64decode(b"".join(body), validate=True))
    if not packets:
        raise SourceError("empty public keyring")
    return b"".join(packets)


def verify_signature(archive, signature, work, gpgv):
    done = subprocess.run([str(gpgv), "--homedir", ".", "--keyring", "./msys2-public.gpg", "--status-fd", "1",
                           str(Path(signature).resolve()), str(Path(archive).resolve())], cwd=work,
                          capture_output=True, check=False, text=True, errors="replace")
    fingerprint = signature_fingerprint(done.stdout, done.returncode)
    return {"fingerprint": fingerprint, "archive_sha256": sha256(archive), "signature_sha256": sha256(signature)}


def download(url, destination, max_bytes=4 << 30):
    destination = Path(destination)
    if destination.is_file():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Scriba-source-collector"})
    fd, partial = tempfile.mkstemp(prefix="source-download-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:
            total = 0
            while chunk := response.read(1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    raise SourceError("download exceeds size limit")
                out.write(chunk)
        # A hard link claims a new name atomically; never replace an existing file.
        try:
            os.link(partial, destination)
        except FileExistsError:
            pass
    finally:
        Path(partial).unlink(missing_ok=True)
    return destination


def exact_archive(stem, suffix, repository, folder, caches=()):
    safe_name(stem)
    for extension in ("zst", "xz", "gz"):
        filename = f"{stem}.{suffix}.tar.{extension}"
        for cache in (folder, *caches):
            path = Path(cache) / filename
            if path.is_file() and path.with_name(filename + ".sig").is_file():
                if path.parent != folder:
                    copy_new(path, Path(folder) / filename)
                    write_new(Path(folder) / (filename + ".sig"), path.with_name(filename + ".sig").read_bytes())
                return Path(folder) / filename, repository.rstrip("/") + "/" + urllib.parse.quote(filename)
    for extension in ("zst", "xz", "gz"):
        filename = f"{stem}.{suffix}.tar.{extension}"
        url = repository.rstrip("/") + "/" + urllib.parse.quote(filename)
        try:
            archive = download(url, Path(folder) / filename)
            download(url + ".sig", Path(folder) / (filename + ".sig"))
            return archive, url
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    raise SourceError(f"exact archive unavailable: {stem}.{suffix}.tar.*")


def installed_version(pacman_root, package):
    local = Path(pacman_root) / "var/lib/pacman/local"
    for path in local.glob(package + "-*/desc"):
        data = path.read_text(encoding="utf-8")
        name = re.search(r"%NAME%\n([^\n]+)", data)
        version = re.search(r"%VERSION%\n([^\n]+)", data)
        if name and name.group(1) == package and version:
            return version.group(1)
    raise SourceError(f"installed build package not found: {package}")


def own_ffmpeg(args, output, bundle):
    if not all((args.ffmpeg_build, args.ffmpeg_source, args.ffmpeg_script)):
        raise SourceError("own FFmpeg needs BUILD.txt, source tarball and build script")
    text = args.ffmpeg_build.read_text(encoding="utf-8")
    expected = re.search(r"^SHA-256 of the sources: ([0-9a-f]{64})$", text, re.MULTILINE)
    url = re.search(r"^Sources: (https://\S+)$", text, re.MULTILINE)
    if not expected or not url or sha256(args.ffmpeg_source) != expected.group(1):
        raise SourceError("own FFmpeg source checksum differs from BUILD.txt")
    directory = output / "own-ffmpeg"
    for path in (args.ffmpeg_build, args.ffmpeg_source, args.ffmpeg_script):
        copy_new(path, directory / path.name)
    binaries = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        built = args.ffmpeg_build.parent / "bin" / name
        shipped = bundle / "_internal" / name
        if not built.is_file() or not shipped.is_file() or sha256(built) != sha256(shipped):
            raise SourceError(f"own FFmpeg program differs from build: {name}")
        binaries.append({"file": "_internal/" + name, "sha256": sha256(shipped)})
    return {"kind": "own_ffmpeg", "url": url.group(1), "source_sha256": expected.group(1),
            "inputs": [{"file": "own-ffmpeg/" + p.name, "sha256": sha256(p)}
                       for p in (args.ffmpeg_build, args.ffmpeg_source, args.ffmpeg_script)], "programs": binaries}


def own_gstplay(receipt_path, output, bundle, component, *, project_root=ROOT):
    receipt_path = Path(receipt_path)
    receipt = gstplay_receipt.read_receipt(receipt_path, project_root=project_root)
    if (receipt != component["receipt"] or component["files"] != ["_internal/libgstplay-1.0-0.dll"]
            or receipt["outputs"][0]["file"] != "bin/libgstplay-1.0-0.dll"):
        raise SourceError("GstPlay receipt differs from the bundle inventory")
    program = {"file": component["files"][0], "sha256": receipt["outputs"][0]["sha256"]}
    if sha256(bundle_path(bundle, program["file"])) != program["sha256"]:
        raise SourceError("GstPlay library differs from the bundle source pair")
    notices = Path(bundle) / "_internal/licenses/gstplay"
    if sha256(notices / "BUILD.json") != sha256(receipt_path):
        raise SourceError("GstPlay bundle receipt differs from the build")
    inputs = [receipt_path, receipt_path.parent / receipt["source"]["file"],
              receipt_path.parent / receipt["patch"]["file"],
              *[receipt_path.parent / item["file"] for item in receipt["licence_files"]],
              gstplay_receipt.relative_file(project_root, receipt["recipe"]["project_file"]),
              gstplay_receipt.relative_file(project_root, "tools/gstplay_receipt.py")]
    for item in [receipt["patch"], *receipt["licence_files"]]:
        if sha256(notices / item["file"]) != sha256(receipt_path.parent / item["file"]):
            raise SourceError("GstPlay bundle notice differs from the source pair")
    directory = Path(output) / "own-gstplay"
    for path in inputs:
        copy_new(path, directory / path.name)
    return {"kind": "own_gstplay", "receipt": receipt,
            "inputs": [{"file": "own-gstplay/" + path.name, "sha256": sha256(path)} for path in inputs],
            "programs": [program]}


def collect(args):
    output = require_external_output(args.output)
    if (output / "manifest.json").exists():
        raise SourceError("final manifest exists; choose a new output revision")
    inventory = load_inventory(args.inventory)
    entries = [dict(entry) for entry in inventory["packages"]]
    if (args.bundle / "_internal/licenses/gstplay/BUILD.json").is_file() != bool(inventory.get("custom_components")):
        raise SourceError("GstPlay custom inventory and bundle receipt differ")
    for generated in inventory.get("generated_files", []):
        if sha256(bundle_path(args.bundle, generated["file"])) != generated["sha256"]:
            raise SourceError("generated bundle file checksum differs from inventory")
        for origin in generated.get("project_inputs", []):
            if sha256(bundle_path(ROOT, origin["project_file"])) != origin["sha256"]:
                raise SourceError("project input checksum differs from inventory")
        for origin in generated.get("inputs", []):
            entry = next((item for item in entries if item["package"] == origin["package"]), None)
            if entry is None:
                raise SourceError("generated file input package is absent from inventory")
            entry.setdefault("_generated_inputs", []).append(origin)
    if args.include_pyinstaller and not any(e["package"] == PREFIX + "pyinstaller" for e in entries):
        entries.append({"package": PREFIX + "pyinstaller", "version": installed_version(args.pacman_root, PREFIX + "pyinstaller"),
                        "files": [], "role": "bootloader-build-tool"})
    for package in args.extra_package:
        name, separator, version = package.partition("=")
        safe_name(name)
        version = version if separator else installed_version(args.pacman_root, name)
        if not any(entry["package"] == name for entry in entries):
            entries.append({"package": name, "version": safe_version(version), "files": [], "role": "extra-build-tool"})
    if args.select:
        selected = set(args.select)
        entries = [entry for entry in entries if entry["package"] in selected or entry["package"].removeprefix(PREFIX) in selected]
        if not entries:
            raise SourceError("no selected package is in the inventory")
    output.mkdir(parents=True, exist_ok=True)
    work = output / "verification"
    work.mkdir(exist_ok=True)
    key_data = args.keyring.read_bytes()
    write_new(work / "msys2-public.gpg", public_keyring(key_data))
    write_new(output / "inventory.json", args.inventory.read_bytes())
    cache = require_external_output(args.cache or (output.parent / "binary-download-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "inventory_kind": inventory["inventory_kind"],
                "inventory_sha256": sha256(args.inventory), "subset": bool(args.select),
                "keyring_sha256": hashlib.sha256(key_data).hexdigest(),
                "verification_keyring_sha256": sha256(work / "msys2-public.gpg"),
                "generated_files": inventory.get("generated_files", []),
                "packages": [], "errors": [], "own_sources": []}
    for number, entry in enumerate(entries, 1):
        print(f"[{number}/{len(entries)}] {entry['package']} {entry['version']}", flush=True)
        try:
            binary_repo, source_repo, architecture = package_repositories(entry["package"])
            binary_repo, source_repo = args.binary_repo or binary_repo, args.source_repo or source_repo
            stem = safe_name(entry["package"] + "-" + archive_version(entry["version"]) + "-" + architecture)
            # The installed package cache is read only. Downloads go to our separate cache.
            binary, binary_url = exact_archive(stem, "pkg", binary_repo, cache, (args.package_cache,))
            binary_signature = verify_signature(binary, binary.with_name(binary.name + ".sig"), work, args.gpgv)
            checked = inspect_binary(binary, entry, args.bundle, args.zstd)
            source_stem = safe_name(checked["base"] + "-" + archive_version(entry["version"]))
            archive, source_url = exact_archive(source_stem, "src", source_repo, output / "archives", args.source_cache)
            signature = verify_signature(archive, archive.with_name(archive.name + ".sig"), work, args.gpgv)
            source = inspect_source(archive, checked["base"], checked["recipe_sha256"], args.zstd)
            token = evidence_token(entry["package"], entry["version"])
            for field, suffix in (("pkginfo_text", "PKGINFO"), ("buildinfo_text", "BUILDINFO")):
                write_new(output / "metadata" / (token + "." + suffix), checked.pop(field).encode("utf-8"))
            manifest["packages"].append({"package": entry["package"], "version": entry["version"],
                                         "binary_url": binary_url, "binary": binary_signature, **checked,
                                         "source_url": source_url, "archive": "archives/" + archive.name,
                                         "source_signature": signature, "signature": "archives/" + archive.name + ".sig",
                                         "archive_bytes": archive.stat().st_size, "source": source})
            print(f"  {archive.stat().st_size / 2**20:.1f} MiB; recipe matched; {source['unhashed_sources']} unchecked inputs", flush=True)
        except (SourceError, OSError, ValueError, subprocess.SubprocessError) as error:
            detail = str(error) if isinstance(error, SourceError) else type(error).__name__
            manifest["errors"].append({"package": entry["package"], "version": entry["version"], "error": detail})
            print(f"  BLOCKED: {detail}", flush=True)
        # An append-only journal survives interruption without replacing prior evidence.
        with (output / "journal.jsonl").open("a", encoding="utf-8") as journal:
            journal.write(json.dumps({"package": entry["package"], "collected": len(manifest["packages"]),
                                      "blocked": len(manifest["errors"])}) + "\n")
    if (args.ffmpeg_build or args.ffmpeg_source or args.ffmpeg_script
            or (not args.select and (args.bundle / "_internal/licenses/ffmpeg/BUILD.txt").is_file())):
        try:
            manifest["own_sources"].append(own_ffmpeg(args, output, args.bundle))
        except SourceError as error:
            manifest["errors"].append({"package": "own-ffmpeg", "error": str(error)})
    for component in inventory.get("custom_components", []):
        try:
            path = getattr(args, "gstplay_build", None) or ROOT / "build/gstplay/BUILD.json"
            manifest["own_sources"].append(own_gstplay(path, output, args.bundle, component))
        except (SourceError, OSError, ValueError, KeyError, tarfile.TarError) as error:
            manifest["errors"].append({"package": "own-gstplay",
                                       "error": str(error) if isinstance(error, ValueError) else type(error).__name__})
    manifest["collection_complete"] = not manifest["errors"] and not args.select and inventory["inventory_kind"] == "json"
    manifest["unhashed_sources"] = sum(p["source"]["unhashed_sources"] for p in manifest["packages"])
    manifest["manual_review_required"] = bool(manifest["unhashed_sources"])
    write_new(output / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    print(f"Collected {len(manifest['packages'])}; blocked {len(manifest['errors'])}; unchecked inputs {manifest['unhashed_sources']}")
    return 1 if manifest["errors"] else 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inventory", type=Path, required=True)
    p.add_argument("--bundle", type=Path, required=True, help="bundle root, containing _internal")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--pacman-root", type=Path, default=Path("C:/msys64") if os.name == "nt" else Path("/"))
    p.add_argument("--package-cache", type=Path)
    p.add_argument("--cache", type=Path)
    p.add_argument("--source-cache", type=Path, action="append", default=[])
    p.add_argument("--binary-repo", help="override the official package repository")
    p.add_argument("--source-repo", help="override the official source repository")
    p.add_argument("--keyring", type=Path)
    p.add_argument("--gpgv", default=shutil.which("gpgv"))
    p.add_argument("--zstd", default=shutil.which("zstd"))
    p.add_argument("--select", action="append", default=[])
    p.add_argument("--extra-package", action="append", default=[])
    p.add_argument("--include-pyinstaller", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--gstplay-build", type=Path, help="custom GstPlay BUILD.json; defaults to build/gstplay/BUILD.json")
    p.add_argument("--ffmpeg-build", type=Path)
    p.add_argument("--ffmpeg-source", type=Path)
    p.add_argument("--ffmpeg-script", type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    args.package_cache = args.package_cache or args.pacman_root / "var/cache/pacman/pkg"
    args.keyring = args.keyring or args.pacman_root / "usr/share/pacman/keyrings/msys2.gpg"
    try:
        if not args.gpgv:
            raise SourceError("gpgv is required")
        return collect(args)
    except (SourceError, OSError, ValueError) as error:
        print(f"Source collection failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
