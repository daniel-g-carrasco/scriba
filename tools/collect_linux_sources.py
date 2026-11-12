#!/usr/bin/env python3
"""Collect pinned Linux sources and verify their association with the current build.

Recipes and archives are data, never executed or extracted. Strict mode needs
an application archive supplied by the public CI checkout; this tool never
archives the working repository. --acquire-only is explicitly non-publishable.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import signal
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

try:
    import gstplay_receipt
except ModuleNotFoundError:
    from tools import gstplay_receipt

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "build-aux/flatpak/sources/linux-corresponding-sources.json"
CHUNK = 1 << 20
RESERVED = {"metadata", "application", "manifest.json", "catalog.json", "readme.md"}


class SourceError(Exception):
    """Acquisition or association failed; the resulting collection cannot be published."""


def relative_path(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise SourceError("unsafe relative path")
    return value


def target_path(root, relative):
    root = Path(root).resolve()
    path = root / relative_path(relative)
    if path.is_symlink() or root not in path.resolve().parents:
        raise SourceError("path leaves its designated directory")
    return path


def https_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise SourceError("only HTTPS URLs without credentials are accepted")
        _ = parsed.port  # Validate the authority before a transfer starts.
        return parsed
    except (ValueError, TypeError) as error:
        raise SourceError("invalid HTTPS URL") from error


def validate_catalog(catalog):
    if catalog.get("schema_version") != 1 or not catalog.get("artifacts"):
        raise SourceError("unsupported or empty source catalogue")
    ids, destinations = set(), set()
    for item in catalog["artifacts"]:
        ident = item.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]*", ident) or ident in ids:
            raise SourceError("invalid or duplicate source identifier")
        ids.add(ident)
        name = relative_path(item["destination"])
        if name.split("/", 1)[0].casefold() in RESERVED or name.casefold() in destinations:
            raise SourceError("reserved or duplicate source destination")
        destinations.add(name.casefold())
        https_url(item["url"])
        if type(item.get("size")) is not int or item["size"] <= 0:
            raise SourceError("source size must be a positive byte count")
        for algorithm, length in (("sha256", 64), ("sha512", 128)):
            if algorithm == "sha256" or algorithm in item:
                if not isinstance(item.get(algorithm), str) or not re.fullmatch(r"[a-fA-F0-9]{" + str(length) + "}", item[algorithm]):
                    raise SourceError("source needs a declared cryptographic hash")
        for key, value in item.get("request_headers", {}).items():
            if key.lower() != "accept" or not isinstance(value, str) or "\r" in value or "\n" in value:
                raise SourceError("only a public Accept header is allowed in the catalogue")
    def references(values):
        if not values or any(value not in ids for value in values):
            raise SourceError("source reference is absent from the catalogue")
    for entry in catalog.get("binary_mappings", []):
        relative_path(entry["binary_path"])
        references(entry["source_artifacts"])
    ort = catalog.get("onnxruntime_binary")
    if ort:
        relative_path(ort["path"])
        references([ort["source_artifact"]])
    for entry in catalog.get("python_sources", []):
        references([entry["source_artifact"]])
    for entry in catalog.get("openblas_builds", []):
        references(entry["artifacts"])
    for entry in catalog.get("resolved_ports", []):
        recipe = entry["recipe"]
        relative_path(recipe["path"])
        if "artifact" in recipe:
            references([recipe["artifact"]])
        elif "git_tree" not in recipe or not any(name.startswith(recipe["path"].casefold() + "/") for name in destinations):
            raise SourceError("version override has no preserved recipe files")
    return catalog


def check(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise SourceError("cancelled")


def verify_file(path, item, cancelled=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item["size"]:
        raise SourceError("source size differs from the catalogue")
    hashes = {key: hashlib.new(key) for key in ("sha256", "sha512") if key in item}
    total = 0
    with path.open("rb") as incoming:
        while chunk := incoming.read(CHUNK):
            check(cancelled)
            total += len(chunk)
            if total > item["size"]:
                raise SourceError("source exceeds its declared size")
            for digest in hashes.values():
                digest.update(chunk)
    if total != item["size"]:
        raise SourceError("source was truncated")
    actual = {key: digest.hexdigest() for key, digest in hashes.items()}
    for key, digest in actual.items():
        if digest != item[key].lower():
            raise SourceError(f"source {key} differs from the catalogue")
    return actual


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        old, new = https_url(request.full_url), https_url(newurl)
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None and (old.hostname, old.port) != (new.hostname, new.port):
            redirected.remove_header("Authorization")
        return redirected


def _public_url(url):
    parsed = https_url(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")), bool(parsed.query)


def _seeds(item, directories):
    names = {PurePosixPath(item["destination"]).name,
             urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(item["url"]).path).name)}
    for directory in directories:
        directory = Path(directory)
        if not directory.is_dir():
            continue
        candidates = [directory / item["destination"]]
        candidates.extend(directory / name for name in names)
        candidates.extend(directory / item["sha256"] / name for name in names)
        candidates.extend(path for path in directory.iterdir() if path.is_file() and not path.is_symlink()
                          and path.stat().st_size == item["size"])
        seen = set()
        for path in candidates:
            if path not in seen:
                seen.add(path)
                yield path


def _publish(partial, destination, item, cancelled=None):
    check(cancelled)
    verify_file(partial, item, cancelled)
    try:
        os.link(partial, destination)  # Atomic new name; never replace existing evidence.
    except FileExistsError:
        verify_file(destination, item, cancelled)


def _copy_verified(source, destination, item, cancelled=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return verify_file(destination, item, cancelled)
    fd, temporary = tempfile.mkstemp(prefix=".source-", suffix=".part", dir=destination.parent)
    partial = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as outgoing, Path(source).open("rb") as incoming:
            copied = 0
            while chunk := incoming.read(CHUNK):
                check(cancelled)
                copied += len(chunk)
                if copied > item["size"]:
                    raise SourceError("source exceeds its declared size")
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        _publish(partial, destination, item, cancelled)
    finally:
        partial.unlink(missing_ok=True)
    return verify_file(destination, item, cancelled)


def acquire(item, cache, *, seeds=(), opener=None, cancelled=None, token="", progress=None):
    https_url(item["url"])
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    destination = target_path(cache, item["sha256"].lower() + ".source")
    if destination.exists():
        verified = verify_file(destination, item, cancelled)
        return destination, {"acquisition": "cache", "effective_url": None, "verified_hashes": verified}
    for seed in _seeds(item, seeds):
        try:
            verify_file(seed, item, cancelled)
        except (SourceError, OSError):
            check(cancelled)
            continue
        verified = _copy_verified(seed, destination, item, cancelled)
        return destination, {"acquisition": "seed", "effective_url": None, "verified_hashes": verified}
    request = urllib.request.Request(item["url"], headers={"User-Agent": "Scriba source collector",
                                                           **item.get("request_headers", {})})
    if token and urllib.parse.urlsplit(item["url"]).hostname == "api.github.com":
        request.add_unredirected_header("Authorization", "Bearer " + token)
    opener = opener or urllib.request.build_opener(HTTPSRedirect())
    fd, temporary = tempfile.mkstemp(prefix=".source-", suffix=".part", dir=cache)
    partial = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as outgoing:
            check(cancelled)
            with opener.open(request, timeout=30) as response:
                effective, redacted = _public_url(response.geturl())
                length = response.headers.get("Content-Length", "")
                if length.isdecimal() and int(length) > item["size"]:
                    raise SourceError("server exceeds the declared source size")
                done = 0
                while True:
                    check(cancelled)
                    chunk = response.read(min(CHUNK, item["size"] - done + 1))
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > item["size"]:
                        raise SourceError("server exceeds the declared source size")
                    outgoing.write(chunk)
                    if progress:
                        progress(done, item["size"])
                outgoing.flush()
                os.fsync(outgoing.fileno())
        _publish(partial, destination, item, cancelled)
        return destination, {"acquisition": "download", "effective_url": effective,
                             "effective_url_query_redacted": redacted,
                             "verified_hashes": verify_file(destination, item, cancelled)}
    except (urllib.error.URLError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError):
            code = error.code
            error.close()
            raise SourceError(f"source server returned HTTP {code}") from None
        raise SourceError(f"source transfer failed ({type(error).__name__})") from None
    finally:
        partial.unlink(missing_ok=True)


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_binary(mapping, build_root):
    relative = mapping.get("binary_path", mapping.get("path"))
    expected = mapping.get("binary_sha256", mapping.get("sha256"))
    path = target_path(build_root, relative)
    if not path.is_file() or file_hash(path) != expected:
        raise SourceError(f"current binary differs from the audited source mapping: {relative}")
    return {"matched": True, "sha256": expected, "size": path.stat().st_size}


def verify_application(archive, commit, build_root, *, project_inputs=None):
    if not re.fullmatch(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})", commit):
        raise SourceError("application source needs its exact public checkout commit")
    count, seen, compared = 0, set(), set()
    project_inputs = project_inputs or {}
    pending = {"scriba/" + relative_path(name): digest for name, digest in project_inputs.items()}
    prefix = "scriba/src/scriba/"
    try:
        with tarfile.open(archive, "r|*") as source:
            for member in source:
                relative_path(member.name.rstrip("/"))
                if member.name in seen:
                    raise SourceError("duplicate application archive path")
                seen.add(member.name)
                if member.issym() or member.islnk():
                    # Source archives are retained, never followed or extracted.
                    continue
                if member.name in pending:
                    if not member.isfile():
                        raise SourceError("custom build input is not a regular application source member")
                    incoming = source.extractfile(member)
                    with incoming:
                        digest = hashlib.file_digest(incoming, "sha256").hexdigest()
                    if digest != pending.pop(member.name):
                        raise SourceError("custom build input differs from the public application archive")
                if member.isfile() and member.name.startswith(prefix):
                    path = target_path(build_root, "share/scriba/scriba/" + member.name[len(prefix):])
                    incoming = source.extractfile(member)
                    if incoming is None or not path.is_file():
                        raise SourceError("application source is absent from the current build")
                    with incoming:
                        digest = hashlib.file_digest(incoming, "sha256").hexdigest()
                    if digest != file_hash(path):
                        raise SourceError("application archive differs from the current build source")
                    compared.add(member.name[len(prefix):])
                    count += 1
    except tarfile.TarError as error:
        raise SourceError("invalid application source archive") from error
    if pending:
        raise SourceError("application archive omits a custom build input")
    if not count:
        raise SourceError("application archive has no source files to compare")
    installed = target_path(build_root, "share/scriba/scriba")
    expected = {path.relative_to(installed).as_posix() for path in installed.rglob("*.py")}
    if not expected.issubset(compared):
        raise SourceError("application archive omits installed source files")
    return {"status": "source_files_match", "commit": commit, "archive_sha256": file_hash(archive),
            "archive_size": Path(archive).stat().st_size, "source_files_verified": count}


def _modules(manifest):
    found = {}

    def visit(entries):
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("name") in found:
                raise SourceError("use an expanded Flatpak manifest with unique module names")
            found[entry["name"]] = entry
            visit(entry.get("modules", []))
    visit(manifest.get("modules", []))
    return found


def _normalise(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def verify_inputs(catalog, manifest, builder_state):
    modules = _modules(manifest)
    for item in catalog["artifacts"]:
        if item.get("role") == "engine_source":
            module = modules.get(item["module"], {})
            if not any(source.get("sha256") == item["sha256"] for source in module.get("sources", [])):
                raise SourceError("compiled engine source pin differs from the source catalogue")
    distributions = {_normalise(entry["name"]): entry for entry in catalog["python_sources"]}
    result, seen = [], set()
    inputs = []
    for module in modules.values():
        for source in module.get("sources", []):
            url = source.get("url", "")
            if url.endswith(".whl"):
                filename = PurePosixPath(urllib.parse.urlsplit(url).path).name
                parts = filename.split("-")
                name, version = _normalise(parts[0]), parts[1]
                entry = distributions.get(name)
                if entry is None or version != entry["version"]:
                    raise SourceError("Python wheel has no matching pinned source distribution")
                seen.add(name)
                inputs.append((source, [entry["source_artifact"]], "wheel"))
            elif module["name"] == "onnxruntime":
                inputs.append((source, [catalog["onnxruntime_binary"]["source_artifact"]], "binary_archive"))
    if seen != set(distributions):
        raise SourceError("Flatpak Python wheel/source coverage differs from the catalogue")
    if not any(kind == "binary_archive" for _source, _refs, kind in inputs):
        raise SourceError("Flatpak manifest has no ONNX Runtime binary input")
    for source, references, kind in inputs:
        https_url(source["url"])
        expected = source.get("sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise SourceError("binary build input has no pinned SHA-256")
        filename = PurePosixPath(urllib.parse.urlsplit(source["url"]).path).name
        path = target_path(builder_state, "downloads/" + expected + "/" + filename)
        if not path.is_file() or file_hash(path) != expected:
            raise SourceError(f"binary build input differs from its manifest pin: {filename}")
        result.append({"type": kind, "filename": filename, "url": source["url"], "sha256": expected,
                       "size": path.stat().st_size, "verified": True, "source_artifacts": references})
    return result


def verify_gstplay(catalog, manifest, build_root, output, *, project_root=ROOT):
    modules = _modules(manifest)
    config = catalog.get("gstplay")
    if not config:
        if ("gstplay" in modules or (build_root and (
                (Path(build_root) / "lib/libgstplay-1.0.so.0").exists() or
                (Path(build_root) / "share/licenses/gstplay/BUILD.json").exists()))):
            raise SourceError("GstPlay module is absent from the source catalogue")
        return None, {}
    if config != {"module": "gstplay", "receipt": "share/licenses/gstplay/BUILD.json",
                  "source_artifact": config.get("source_artifact")}:
        raise SourceError("unexpected GstPlay catalogue mapping")
    module = modules.get("gstplay")
    if module is None:
        raise SourceError("GstPlay source catalogue has no manifest module")
    path = target_path(build_root, config["receipt"])
    receipt = gstplay_receipt.read_receipt(path, output_root=build_root, project_root=project_root)
    item = next((entry for entry in catalog["artifacts"] if entry["id"] == config["source_artifact"]), None)
    source = receipt["source"]
    if item is None or (item["url"], item["sha256"]) != (source["url"], source["sha256"]):
        raise SourceError("GstPlay receipt source differs from the catalogue")
    if not any((entry.get("url"), entry.get("sha256")) == (source["url"], source["sha256"])
               for entry in module.get("sources", [])):
        raise SourceError("GstPlay source differs from the Flatpak manifest")
    required = {receipt["recipe"]["project_file"]: receipt["recipe"]["sha256"],
                gstplay_receipt.PATCH_PROJECT_FILE: receipt["patch"]["sha256"],
                "tools/gstplay_receipt.py": file_hash(Path(project_root) / "tools/gstplay_receipt.py")}
    for name, digest in required.items():
        expected = gstplay_receipt.relative_file(project_root, name)
        matches = []
        for entry in module.get("sources", []):
            if not entry.get("path"):
                continue
            supplied = Path(entry["path"])
            if not supplied.is_absolute():
                supplied = Path(project_root) / "build-aux/flatpak" / supplied
            if supplied.resolve() == expected.resolve() and supplied.is_file() and file_hash(supplied) == digest:
                matches.append(entry)
        if len(matches) != 1:
            raise SourceError("GstPlay patch or recipe differs from the Flatpak manifest")
    retained = {"BUILD.json": path, receipt["patch"]["file"]: path.parent / receipt["patch"]["file"],
                **{entry["file"]: path.parent / entry["file"] for entry in receipt["licence_files"]},
                "build_gstplay.py": gstplay_receipt.relative_file(project_root, receipt["recipe"]["project_file"]),
                "gstplay_receipt.py": gstplay_receipt.relative_file(project_root, "tools/gstplay_receipt.py")}
    for name, origin in retained.items():
        write_new(Path(output) / "metadata/gstplay" / name, origin.read_bytes())
    return {"receipt": receipt, "source_artifact": item["id"],
            "inputs": [{"file": "metadata/gstplay/" + name, "sha256": file_hash(origin)}
                       for name, origin in retained.items()],
            "verified_outputs": receipt["outputs"]}, required


def external(path):
    path = Path(path).resolve()
    if path == ROOT or ROOT in path.parents:
        raise SourceError("source evidence and cache must be outside the checkout")
    return path


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise SourceError("existing evidence differs; choose a new output revision")
        return
    with path.open("xb") as stream:
        stream.write(data)


def write_json(path, value):
    write_new(path, (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode())


def _public_manifest(value):
    if isinstance(value, dict):
        return {key: ("<local source path omitted>" if key == "path" and isinstance(child, str)
                      and (child.startswith("/") or re.match(r"^[A-Za-z]:", child)) else _public_manifest(child))
                for key, child in value.items()}
    if isinstance(value, list):
        return [_public_manifest(child) for child in value]
    return value


def collect(args, cancelled):
    output, cache = external(args.output), external(args.cache)
    if (output / "manifest.json").exists():
        raise SourceError("final manifest already exists; choose a new output revision")
    catalog = validate_catalog(json.loads(args.catalog.read_text(encoding="utf-8")))
    if not args.acquire_only and not all((args.app_archive, args.app_commit, args.build_root,
                                         args.bundle, args.resolved_manifest, args.runtime_commit, args.sdk_commit)):
        raise SourceError("strict publication needs app archive/commit, build root, bundle, manifest and runtime/SDK commits")
    output.mkdir(parents=True, exist_ok=True)
    write_new(output / "catalog.json", args.catalog.read_bytes())
    report = {"schema_version": 1, "status": catalog["status"], "catalog_sha256": file_hash(args.catalog),
              "acquisition_only": args.acquire_only, "artifacts": [], "binary_inputs": [], "errors": [],
              "application_source": {"status": "not_supplied"}, "bundle": None,
              "sources_complete": False, "binary_association_verified": False, "publishable": False}
    for key in ("resolved_ports", "binary_mappings", "onnxruntime_binary", "openblas_builds", "recipe_assumptions",
                "python_sources", "provenance", "scope"):
        if key in catalog:
            report[key] = catalog[key]
    try:
        binary_verified = []
        custom_project_inputs = {}
        if args.build_root:
            for mapping in catalog.get("binary_mappings", []):
                binary_verified.append({**mapping, "verification": verify_binary(mapping, args.build_root)})
            ort = catalog.get("onnxruntime_binary")
            if ort:
                report["onnxruntime_binary"] = {**ort, "verification": verify_binary(ort, args.build_root)}
            report["binary_mappings"] = binary_verified
        if args.resolved_manifest:
            manifest = json.loads(args.resolved_manifest.read_text(encoding="utf-8"))
            write_json(output / "metadata/flatpak-manifest.json", _public_manifest(manifest))
            report["flatpak_manifest"] = {"input_sha256": file_hash(args.resolved_manifest),
                                          "retained_sha256": file_hash(output / "metadata/flatpak-manifest.json"),
                                          "runtime": manifest.get("runtime"), "sdk": manifest.get("sdk"),
                                          "runtime_version": manifest.get("runtime-version"),
                                          "app_id": manifest.get("app-id", manifest.get("id"))}
            report["binary_inputs"] = verify_inputs(catalog, manifest, args.builder_state)
            custom, custom_project_inputs = verify_gstplay(catalog, manifest, args.build_root, output)
            if custom is not None:
                report["gstplay"] = custom
        for name in ("runtime_commit", "sdk_commit"):
            value = getattr(args, name)
            if value and not re.fullmatch(r"[a-f0-9]{64}", value):
                raise SourceError("runtime/SDK metadata needs exact OSTree commits")
            report[name] = value or None
        if args.build_root:
            metadata = args.build_root.parent / "metadata"
            if metadata.is_file():
                config = configparser.ConfigParser(interpolation=None)
                config.read(metadata, encoding="utf-8")
                report["flatpak_build"] = {"metadata_sha256": file_hash(metadata),
                                           "application": dict(config["Application"])}
            elif not args.acquire_only:
                raise SourceError("Flatpak build metadata is missing")
        if args.bundle:
            report["bundle"] = {"filename": args.bundle.name, "sha256": file_hash(args.bundle),
                                 "size": args.bundle.stat().st_size}
        if args.app_archive:
            if not args.app_commit or not args.build_root:
                raise SourceError("application archive needs its commit and current build to compare")
            identity = verify_application(args.app_archive, args.app_commit, args.build_root,
                                          project_inputs=custom_project_inputs)
            path = output / "application/app-source.tar"
            _copy_verified(args.app_archive, path, {"size": identity["archive_size"],
                                                    "sha256": identity["archive_sha256"]}, cancelled)
            report["application_source"] = {**identity, "file": "application/app-source.tar"}
        report["binary_association_verified"] = bool(args.build_root and args.resolved_manifest
                                                      and report["binary_inputs"] and binary_verified)
    except (SourceError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        report["errors"].append({"phase": "build_identity", "error": str(error)})
    if not report["errors"] or args.acquire_only:
        token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
        for number, item in enumerate(catalog["artifacts"], 1):
            print(f"[{number}/{len(catalog['artifacts'])}] {item['id']} ({item['size']} bytes)", flush=True)
            last = [time.monotonic()]

            def progress(done, total, ident=item["id"], last=last):
                if time.monotonic() - last[0] >= 10:
                    print(f"  {ident}: {done}/{total} bytes", flush=True)
                    last[0] = time.monotonic()
            try:
                check(cancelled)
                source, verified = acquire(item, cache, seeds=args.seed_dir, cancelled=cancelled,
                                           token=token, progress=progress)
                _copy_verified(source, target_path(output, item["destination"]), item, cancelled)
                report["artifacts"].append({**item, **verified, "verified": True})
            except (SourceError, OSError) as error:
                report["errors"].append({"phase": "acquisition", "id": item["id"], "error": str(error)})
                print(f"  BLOCKED: {type(error).__name__}: {error}", flush=True)
                if cancelled.is_set():
                    break
    report["sources_complete"] = len(report["artifacts"]) == len(catalog["artifacts"])
    report["publishable"] = bool(not args.acquire_only and not report["errors"] and report["sources_complete"]
                                  and report["binary_association_verified"] and report["bundle"]
                                  and report["application_source"]["status"] == "source_files_match")
    write_json(output / "manifest.json", report)
    readme = ("Verified source acquisition and recipe-derived provenance\n\n"
              + catalog["status"] + "\n\n"
              "Declared licences and recipe assumptions retain their original limits.\n"
              "Cache and seed files are hash checked; their URLs are not contacted again.\n"
              "effective_url is null for these offline acquisitions. No recipes were executed.\n"
              f"Sources collected: {len(report['artifacts'])}/{len(catalog['artifacts'])}\n"
              f"Current binary association verified: {report['binary_association_verified']}\n"
              f"Publishable collection: {report['publishable']}\n")
    write_new(output / "README.md", readme.encode())
    print(f"Collected {len(report['artifacts'])}; errors {len(report['errors'])}; publishable {report['publishable']}", flush=True)
    return 0 if not report["errors"] and (report["publishable"] or args.acquire_only) else 1


def verify_pair(archive, bundle, commit):
    """Read the signed source report without extraction and bind it to a release."""
    if not isinstance(commit, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit):
        raise SourceError("pair verification needs the exact application commit")
    report = None
    try:
        with tarfile.open(archive, "r|*") as stream:
            for member in stream:
                if member.name.removeprefix("./") != "manifest.json":
                    continue
                if report is not None or not member.isfile() or member.size > 16 << 20:
                    raise SourceError("source archive needs one regular manifest at its root")
                payload = stream.extractfile(member)
                if payload is None:
                    raise SourceError("source archive manifest is unreadable")
                with payload:
                    report = json.load(payload)
    except (tarfile.TarError, ValueError) as error:
        raise SourceError("invalid source archive or manifest") from error
    if not isinstance(report, dict) or any(report.get(key) is not True for key in
                                           ("publishable", "sources_complete", "binary_association_verified")):
        raise SourceError("source archive is not a publishable verified collection")
    if report.get("errors") != [] or report.get("acquisition_only", False):
        raise SourceError("source archive reports incomplete acquisition")
    source = report.get("application_source", {})
    if source.get("status") != "source_files_match" or source.get("commit") != commit:
        raise SourceError("source archive does not match this application commit")
    identity = report.get("bundle", {})
    bundle = Path(bundle)
    if identity.get("size") != bundle.stat().st_size or identity.get("sha256") != file_hash(bundle):
        raise SourceError("source archive does not match this bundle")
    return {"bundle_sha256": identity["sha256"], "app_commit": commit,
            "source_archive_sha256": file_hash(archive), "verified": True}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    result.add_argument("--output", type=Path)
    result.add_argument("--cache", type=Path)
    result.add_argument("--seed-dir", type=Path, action="append", default=[])
    result.add_argument("--resolved-manifest", type=Path)
    result.add_argument("--build-root", type=Path, help="Flatpak build-dir/files")
    result.add_argument("--builder-state", type=Path, default=Path(".flatpak-builder"))
    result.add_argument("--bundle", type=Path)
    result.add_argument("--app-archive", type=Path, help="git archive of the public checkout, produced by CI")
    result.add_argument("--app-commit")
    result.add_argument("--runtime-commit")
    result.add_argument("--sdk-commit")
    result.add_argument("--verify-pair", type=Path, help="verify the source tar manifest against --bundle and --app-commit")
    result.add_argument("--acquire-only", action="store_true", help="acquire sources without claiming a publishable collection")
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.verify_pair:
        if not args.bundle or not args.app_commit:
            arguments.error("--verify-pair needs --bundle and --app-commit")
        try:
            print(json.dumps(verify_pair(args.verify_pair, args.bundle, args.app_commit)), flush=True)
            return 0
        except (SourceError, OSError) as error:
            print(f"Source pair verification failed: {error}", flush=True)
            return 1
    if not args.output or not args.cache:
        arguments.error("collection needs --output and --cache")
    cancelled = threading.Event()
    handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            handlers[signum] = signal.signal(signum, lambda _signal, _frame: cancelled.set())
    try:
        return collect(args, cancelled)
    except (SourceError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        print(f"Source collection failed: {error}", flush=True)
        return 1
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
