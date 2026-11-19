#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bind an inventory to exact source archives and verify the separate download."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from package_inventory import relative_path, sha256, verify_bundle

INDEX = "sources-manifest.json"
INVENTORY = "inventory.json"
CHUNK = 1 << 20
MAX_METADATA = 1 << 20
CHECKSUMS = {"sha256sums": "sha256", "sha512sums": "sha512", "b2sums": "blake2b",
             "sha384sums": "sha384", "sha224sums": "sha224", "sha1sums": "sha1", "md5sums": "md5"}


class SourceError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def stream_digest(stream, algorithm="sha256"):
    return hashlib.file_digest(stream, algorithm).hexdigest()


@contextlib.contextmanager
def tar_reader(path, zstd):
    if path.suffix != ".zst":
        with tarfile.open(path, "r:*") as archive:
            yield archive
        return
    if hasattr(tarfile.TarFile, "zstopen"):
        with tarfile.open(path, "r|zst") as archive:
            yield archive
        return
    with subprocess.Popen([zstd, "-dc", "--", str(path)], stdout=subprocess.PIPE) as decoder:
        with tarfile.open(fileobj=decoder.stdout, mode="r|") as archive:
            yield archive
        while decoder.stdout.read(CHUNK):
            pass
        if decoder.wait():
            raise SourceError(f"zstd could not decode {path.name}")


def members(path, names, zstd, algorithms=("sha256",), metadata=()):
    """Only read requested members. Never extract paths or links."""
    result, text = {}, {}
    with tar_reader(path, zstd) as archive:
        for entry in archive:
            if entry.isdir() and PurePosixPath(entry.name) == PurePosixPath("."):
                continue
            name = str(relative_path(entry.name))
            if name not in names:
                continue
            if name in result or not entry.isfile():
                raise SourceError(f"duplicate or non-file archive member: {name}")
            with archive.extractfile(entry) as stream:
                if name in metadata:
                    if entry.size > MAX_METADATA:
                        raise SourceError(f"metadata too large: {name}")
                    reported = stream.read()
                    text[name] = reported.decode("utf-8")
                    result[name] = {"sha256": digest(reported)}
                else:
                    hashes = {algorithm: hashlib.new(algorithm) for algorithm in algorithms}
                    while chunk := stream.read(CHUNK):
                        for hasher in hashes.values():
                            hasher.update(chunk)
                    result[name] = {algorithm: hasher.hexdigest() for algorithm, hasher in hashes.items()}
    missing = names - result.keys()
    if missing:
        raise SourceError(f"missing archive members in {path.name}: {', '.join(sorted(missing))}")
    return result, text


def fields(text):
    parsed = {}
    for line in text.splitlines():
        key, equals, value = line.strip().partition(" = ")
        if equals and not key.startswith("#"):
            parsed.setdefault(key, []).append(value)
    return parsed


def only(metadata, key):
    choices = metadata.get(key, [])
    if len(choices) != 1:
        raise SourceError(f"expected one {key} in package metadata")
    return choices[0]


def verify_signature(file, signature, keyring, gpgv):
    if keyring is None or not keyring.is_file():
        raise SourceError("a trusted MSYS2 public keyring is required")
    with tempfile.TemporaryDirectory(prefix="source-signature-") as home:
        result = subprocess.run([gpgv, "--homedir", home, "--keyring", str(keyring.resolve()),
                                 str(signature), str(file)], capture_output=True)
    if result.returncode:
        raise SourceError(f"signature rejected for {file.name}")


def verify_msys2(component, rows, directory, receipt, keyring, gpgv, zstd):
    binary = directory / relative_path(receipt["binary"])
    source = directory / relative_path(receipt["source"])
    binary_sig = directory / relative_path(receipt["binary_signature"])
    source_sig = directory / relative_path(receipt["source_signature"])
    verify_signature(binary, binary_sig, keyring, gpgv)
    verify_signature(source, source_sig, keyring, gpgv)

    metadata = {".PKGINFO", ".BUILDINFO"}
    hashes, info = members(binary, metadata | {row["source"] for row in rows}, zstd, metadata=metadata)
    pkg, build = fields(info[".PKGINFO"]), fields(info[".BUILDINFO"])
    exact = {"pkgname": component["name"], "pkgver": component["version"], "pkgbase": component["base"]}
    if any(only(table, key) != value for table in (pkg, build) for key, value in exact.items()):
        raise SourceError(f"binary package name, base or version differs: {component['name']}")
    for row in rows:
        if hashes[row["source"]]["sha256"] != row["sha256"]:
            raise SourceError(f"binary package checksum differs: {row['target']}")
    recipe_hash = only(build, "pkgbuild_sha256sum")
    base = relative_path(component["base"])
    recipe, srcinfo = f"{base}/PKGBUILD", f"{base}/.SRCINFO"
    hashes, text = members(source, {recipe, srcinfo}, zstd, metadata={srcinfo})
    if hashes[recipe]["sha256"] != recipe_hash:
        raise SourceError("PKGBUILD checksum differs from binary BUILDINFO")
    src = fields(text[srcinfo])
    epoch = src.get("epoch", ["0"])[0]
    version = (f"{epoch}:" if epoch != "0" else "") + only(src, "pkgver") + "-" + only(src, "pkgrel")
    if only(src, "pkgbase") != str(base) or version != component["version"]:
        raise SourceError("source package base or version differs from binary")

    expected = {}
    for suffix in ("", "_x86_64"):
        declared = src.get("source" + suffix, [])
        sums = {algorithm: src[key + suffix] for key, algorithm in CHECKSUMS.items() if key + suffix in src}
        if any(len(values) != len(declared) for values in sums.values()):
            raise SourceError("source and checksum counts differ")
        for index, name in enumerate(declared):
            alias, sep, url = name.partition("::")
            filename = alias if sep else unquote(PurePosixPath(urlsplit(name).path).name)
            path = f"{base}/{relative_path(filename)}"
            pins = {key: values[index] for key, values in sums.items() if values[index] != "SKIP"}
            if not pins or path in expected:
                raise SourceError(f"duplicate or unpinned source member: {path}")
            expected[path] = pins
    if not expected:
        raise SourceError("no declared sources")
    found, _ = members(source, set(expected), zstd, set(CHECKSUMS.values()))
    for name, pins in expected.items():
        if any(found[name][key] != wanted.lower() for key, wanted in pins.items()):
            raise SourceError(f"source checksum differs: {name}")
    evidence = {"binary_sha256": sha256(binary), "binary_signature_sha256": sha256(binary_sig),
                "recipe_sha256": recipe_hash, "metadata": info, "members": sorted(expected)}
    return source, source_sig, evidence


def verify_adapter(component, rows, directory, receipt, zstd):
    """A trusted build supplies the matching inputs for a wheel, Linux or its own build."""
    source = directory / relative_path(receipt["source"])
    if sha256(source) != receipt["source_sha256"]:
        raise SourceError(f"source archive checksum differs: {component['name']}")
    binary_pins = receipt["build_inputs"]
    if any(binary_pins.get(row["source"]) != row["sha256"] for row in rows):
        raise SourceError(f"build receipt does not cover corresponding inputs: {component['name']}")
    required = receipt["required_members"]
    if not required:
        raise SourceError(f"receipt has no source members: {component['name']}")
    if source.suffix == ".zip":
        with ZipFile(source) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise SourceError("duplicate zip members")
            found = {}
            for name in required:
                relative_path(name)
                with archive.open(name) as stream:
                    found[name] = {"sha256": stream_digest(stream)}
    else:
        found, _ = members(source, set(required), zstd)
    if any(found[name]["sha256"] != checksum for name, checksum in required.items()):
        raise SourceError(f"source member checksum differs: {component['name']}")
    return source, None, {"members": sorted(required)}


def wanted_components(inventory):
    return {key: value for key, value in inventory["components"].items() if value["kind"] != "generated"}


def collect(bundle, inventory_path, inputs_path, output, *, keyring=None, gpgv="gpgv", zstd="zstd"):
    raw_inventory = inventory_path.read_bytes()
    inventory = json.loads(raw_inventory)
    verify_bundle(bundle, inventory, strict=True)
    receipts = json.loads(inputs_path.read_text(encoding="utf-8"))
    wanted = wanted_components(inventory)
    if not wanted or receipts.get("schema_version") != 1 or set(receipts["components"]) != set(wanted):
        raise SourceError("source receipts must cover every inventory component")
    record = {"schema_version": 1, "inventory_sha256": digest(raw_inventory),
              "platform": inventory["platform"], "components": {}}
    files = {}
    for name, component in sorted(wanted.items()):
        receipt = receipts["components"][name]
        if receipt["version"] != component["version"]:
            raise SourceError(f"source receipt version differs: {name}")
        rows = [row for row in inventory["files"] if row["component"] == name]
        if component["kind"] == "msys2":
            source, signature, detail = verify_msys2(component, rows, inputs_path.parent, receipt, keyring, gpgv, zstd)
        elif component["kind"] in ("linux", "project", "wheel", "custom"):
            source, signature, detail = verify_adapter(component, rows, inputs_path.parent, receipt, zstd)
        else:
            raise SourceError(f"unsupported source adapter: {component['kind']}")
        folder = relative_path(component["kind"] + "/" + component["name"])
        extras = {}
        for path in (source, signature):
            if path is not None:
                entry = str(folder / relative_path(path.name))
                files[entry] = path
                extras[entry] = sha256(path)
        record["components"][name] = {"version": component["version"], "files": extras, **detail}
    with ZipFile(output, "x") as archive:
        archive.writestr(INVENTORY, raw_inventory)
        archive.writestr(INDEX, json.dumps(record, indent=2) + "\n")
        for name, path in files.items():
            archive.write(path, name)
    return verify_archive(output, expected_inventory=digest(raw_inventory))


def verify_archive(path, *, expected_inventory=None):
    with ZipFile(path) as archive:
        inventory = archive.read(INVENTORY)
        index = json.loads(archive.read(INDEX))
        checksum = digest(inventory)
        if index.get("schema_version") != 1 or index["inventory_sha256"] != checksum or (
                expected_inventory and checksum != expected_inventory):
            raise SourceError("inventory hash differs in source archive")
        data = json.loads(inventory)
        if data.get("schema_version") != 1 or data["issues"] or index["platform"] != data["platform"]:
            raise SourceError("invalid or unresolved source inventory")
        wanted = wanted_components(data)
        if not wanted or set(wanted) != set(index["components"]):
            raise SourceError("source archive does not cover the inventory")
        expected = {INDEX, INVENTORY}
        for key, data in index["components"].items():
            if data["version"] != wanted[key]["version"] or not data["files"]:
                raise SourceError(f"incomplete source record: {key}")
            if wanted[key]["kind"] == "msys2" and (len(data["files"]) != 2
                    or not any(member.endswith(".sig") for member in data["files"]) or not data.get("members")):
                raise SourceError(f"incomplete signed package sources: {key}")
            for member, checksum in data["files"].items():
                relative_path(member)
                if member in expected:
                    raise SourceError(f"duplicate source record: {member}")
                expected.add(member)
                with archive.open(member) as stream:
                    if stream_digest(stream) != checksum:
                        raise SourceError(f"source archive checksum differs: {member}")
        if sorted(archive.namelist()) != sorted(expected):
            raise SourceError("source archive contents differ from its index")
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, help="verify an existing source archive")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inputs", type=Path, help="trusted source receipts and relative artifact paths")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--keyring", type=Path)
    parser.add_argument("--gpgv", default="gpgv")
    parser.add_argument("--zstd", default="zstd")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            expected = sha256(args.inventory) if args.inventory else None
            result = verify_archive(args.verify, expected_inventory=expected)
            artifact = args.verify
        elif args.bundle and args.inputs and args.output:
            inventory = args.inventory or args.bundle / "_internal/licenses/inventory.json"
            result = collect(args.bundle, inventory, args.inputs, args.output, keyring=args.keyring,
                             gpgv=args.gpgv, zstd=args.zstd)
            artifact = args.output
        else:
            parser.error("use --verify or give --bundle, --inputs and --output")
    except (OSError, ValueError, KeyError, tarfile.TarError, BadZipFile) as exc:
        print(f"Sources rejected: {exc}")
        return 1
    print(f"{len(result['components'])} components verified, SHA-256 {sha256(artifact)}")
    return 0


if __name__ == "__main__":
    flags = {arg.partition("=")[0] for arg in sys.argv[1:]}
    if "--inventory" in flags and not flags.intersection({"--inputs", "--verify"}):
        from collect_scriba_sources import main as scriba_main

        raise SystemExit(scriba_main())
    raise SystemExit(main())
