#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble the verified source download, or publish a complete draft."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from collect_sources import INVENTORY, verify_archive
from package_inventory import sha256, verify_portable

INDEX = "release-sources.json"
NOTES = "release-notes.it.md"
PLATFORMS = {"windows": "windows-x64", "linux": "flatpak"}


def names(slug, tag):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", slug) or not re.fullmatch(r"v\d+(?:\.\d+){1,3}", tag):
        raise ValueError("invalid product slug or stable tag")
    return {
        "installer": f"{slug}-setup-{tag}-windows-x64.exe",
        "portable": f"{slug}-{tag}-windows-x64-portable.zip",
        "flatpak": f"{slug}.flatpak",
        "sources": f"{slug}-{tag}-sources.zip",
    }


def assemble(slug, tag, binaries, archives, output):
    wanted = names(slug, tag)
    if output.name != wanted["sources"]:
        raise ValueError(f"the source archive must be named {wanted['sources']}")
    for role, file in binaries.items():
        if file.name != wanted[role]:
            raise ValueError(f"expected {wanted[role]}")
    for platform, archive in archives.items():
        if verify_archive(archive)["platform"] != PLATFORMS[platform]:
            raise ValueError(f"wrong source inventory platform: {platform}")
    with ZipFile(archives["windows"]) as archive:
        verify_portable(binaries["portable"], archive.read(INVENTORY))
    record = {"schema_version": 1, "slug": slug, "tag": tag,
              "binaries": {path.name: sha256(path) for path in binaries.values()},
              "platforms": {platform + ".zip": sha256(path) for platform, path in archives.items()}}
    with ZipFile(output, "x") as result:
        result.writestr(INDEX, json.dumps(record, indent=2) + "\n")
        for platform, file in archives.items():
            result.write(file, platform + ".zip")
    return verify_download(output, slug, tag)


def verify_download(path, slug, tag):
    expected = names(slug, tag)
    with ZipFile(path) as source, tempfile.TemporaryDirectory(prefix="release-sources-") as work:
        record = json.loads(source.read(INDEX))
        files = {platform + ".zip" for platform in PLATFORMS}
        binaries = set(expected.values()) - {expected["sources"]}
        if (record.get("schema_version") != 1 or record.get("slug") != slug or record.get("tag") != tag
                or set(record["binaries"]) != binaries or set(record["platforms"]) != files
                or sorted(source.namelist()) != sorted(files | {INDEX})):
            raise ValueError("source download does not cover the complete release")
        for platform, format_name in PLATFORMS.items():
            member = platform + ".zip"
            local = Path(work) / member
            with source.open(member) as stream, local.open("xb") as output:
                shutil.copyfileobj(stream, output)
            if sha256(local) != record["platforms"][member] or verify_archive(local)["platform"] != format_name:
                raise ValueError(f"invalid corresponding sources: {platform}")
    return record


def finish(client, slug, tag):
    """Evaluate the draft, returning a message and editing it only when complete."""
    release = client.view()
    if not release.get("draft", False):
        return "already published"
    expected = names(slug, tag)
    binaries = set(expected.values()) - {expected["sources"]}
    mandatory = binaries | {name + ".minisig" for name in binaries} | {NOTES, expected["sources"]}
    assets = {asset["name"]: asset for asset in release["assets"] if asset.get("state", "uploaded") == "uploaded"}
    missing = mandatory - assets.keys()
    if missing:
        return "the release stays a draft, still missing " + ", ".join(sorted(missing))
    try:
        with tempfile.TemporaryDirectory(prefix="release-draft-") as work:
            sources, notes = (client.download(name, Path(work)) for name in (expected["sources"], NOTES))
            manifest = verify_download(sources, slug, tag)
            for name, checksum in manifest["binaries"].items():
                if assets[name].get("digest") != "sha256:" + checksum:
                    raise ValueError(f"release checksum differs from sources for {name}")
            text = notes.read_text(encoding="utf-8")
            header, _, body = text.partition("\n")
            if header != f"<!-- release-notes: it {tag} -->" or not re.search(r"^[-*] +\S", body, re.M):
                raise ValueError("Italian notes do not match the release")
    except (OSError, ValueError, KeyError, BadZipFile, subprocess.CalledProcessError) as error:
        return f"the release stays a draft, {error}"
    client.publish()
    return f"release {tag} published"


class GitHub:
    def __init__(self, repo, tag):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("invalid GitHub repository")
        self.repo, self.tag = repo, tag

    def view(self):
        return json.loads(subprocess.check_output(["gh", "api", f"repos/{self.repo}/releases/tags/{quote(self.tag)}"]))

    def download(self, name, folder):
        subprocess.run(["gh", "release", "download", self.tag, "--repo", self.repo,
                        "--pattern", name, "--dir", str(folder)], check=True)
        return folder / name

    def publish(self):
        subprocess.run(["gh", "release", "edit", self.tag, "--repo", self.repo,
                        "--draft=false", "--latest"], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    collect = modes.add_parser("assemble", help="combine already verified platform source archives and binary digests")
    publish = modes.add_parser("publish-if-complete")
    for mode in (collect, publish):
        mode.add_argument("--slug", required=True)
        mode.add_argument("--tag", required=True)
    for key in ("windows", "linux", "installer", "portable", "flatpak", "output"):
        collect.add_argument("--" + key, type=Path, required=True)
    publish.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    try:
        names(args.slug, args.tag)
        if args.command == "assemble":
            binaries = {key: getattr(args, key) for key in ("installer", "portable", "flatpak")}
            archives = {key: getattr(args, key) for key in PLATFORMS}
            assemble(args.slug, args.tag, binaries, archives, args.output)
            print(f"Source download SHA-256 {sha256(args.output)}")
        else:
            print(finish(GitHub(args.repo, args.tag), args.slug, args.tag))
    except (OSError, ValueError, KeyError, BadZipFile, subprocess.CalledProcessError) as error:
        print(f"Release check failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
