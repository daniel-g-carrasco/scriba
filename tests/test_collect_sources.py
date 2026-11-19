# SPDX-License-Identifier: GPL-3.0-or-later
"""Correspondence fixtures using the two historical PDF DLL names."""

import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zipfile import ZipFile

import _setup

with mock.patch.object(sys, "path", [str(_setup.ROOT / "tools"), *sys.path]):
    import collect_sources as cs


def tar(path, contents):
    with tarfile.open(path, "w") as archive:
        for name, data in contents.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))


class SourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bundle = self.root / "bundle"
        (self.bundle / "_internal").mkdir(parents=True)
        self.inventory = self.root / "inventory.json"
        self.inputs = self.root / "inputs.json"
        self.archive = self.root / "sources.zip"
        self.items = (
            ("mingw-w64-jbigkit", "2.1-6", "libjbig-0.dll"),
            ("mingw-w64-lzo2", "2.10-3", "liblzo2-2.dll"),
        )
        data = {"schema_version": 1, "platform": "windows-x64", "components": {},
                "files": [], "notices": {}, "issues": []}
        receipts = {}
        for base, version, dll in self.items:
            owner = base.replace("mingw-w64-", "mingw-w64-ucrt-x86_64-")
            key = f"msys2:{owner}"
            data["components"][key] = {"kind": "msys2", "name": owner, "base": base, "version": version}
            shipped = ("fixture bytes for " + dll).encode()
            target = f"_internal/{dll}"
            (self.bundle / target).write_bytes(shipped)
            origin = f"ucrt64/bin/{dll}"
            data["files"].append({"target": target, "component": key, "source": origin, "sha256": cs.digest(shipped)})
            receipts[key] = {"version": version, "binary": base + ".pkg.tar", "binary_signature": base + ".pkg.tar.sig",
                             "source": base + ".src.tar", "source_signature": base + ".src.tar.sig"}
            version_upstream, release = version.rsplit("-", 1)
            recipe, upstream, patch = b"recipe", b"upstream source", b"patch"
            info = f"pkgname = {owner}\npkgbase = {base}\npkgver = {version}\n"
            build = info + f"pkgbuild_sha256sum = {cs.digest(recipe)}\n"
            binary = {".PKGINFO": info.encode(), ".BUILDINFO": build.encode(), origin: shipped}
            tar(self.root / receipts[key]["binary"], binary)
            srcinfo = (f"pkgbase = {base}\npkgver = {version_upstream}\npkgrel = {release}\n"
                       f"source = upstream.tar\nsource = local.patch\nsha256sums = {cs.digest(upstream)}\n"
                       f"sha256sums = {cs.digest(patch)}\n")
            entries = {f"{base}/PKGBUILD": recipe, f"{base}/.SRCINFO": srcinfo.encode(),
                       f"{base}/upstream.tar": upstream, f"{base}/local.patch": patch}
            tar(self.root / receipts[key]["source"], entries)
            (self.root / receipts[key]["source_signature"]).write_bytes(b"test signature")
            (self.root / receipts[key]["binary_signature"]).write_bytes(b"test signature")
        self.inventory.write_text(json.dumps(data), encoding="utf-8")
        self.inputs.write_text(json.dumps({"schema_version": 1, "components": receipts}), encoding="utf-8")
        self.enterContext(mock.patch.object(cs, "verify_signature"))

    def collect(self):
        return cs.collect(self.bundle, self.inventory, self.inputs, self.archive)

    def test_verified_sources_archive_and_incomplete_download(self):
        index = self.collect()
        self.assertEqual(len(index["components"]), 2)
        self.assertEqual(cs.verify_archive(self.archive), index)
        print("Source fixture SHA-256", cs.sha256(self.archive))
        for item in index["components"].values():
            self.assertEqual(len(item["members"]), 2)
        broken = self.root / "incomplete.zip"
        with ZipFile(self.archive) as good, ZipFile(broken, "x") as bad:
            for name in good.namelist():
                if not name.endswith("lzo2.src.tar"):
                    bad.writestr(name, good.read(name))
        with self.assertRaises(KeyError):
            cs.verify_archive(broken)

    def test_binary_mismatch_rejects_the_pair_even_if_the_inventory_matches_the_shipped_dll(self):
        target = "_internal/libjbig-0.dll"
        changed = b"a different build of JBIG"
        (self.bundle / target).write_bytes(changed)
        inventory = json.loads(self.inventory.read_text(encoding="utf-8"))
        inventory["files"][0]["sha256"] = cs.digest(changed)
        self.inventory.write_text(json.dumps(inventory), encoding="utf-8")
        with self.assertRaisesRegex(cs.SourceError, "checksum differs.*libjbig-0.dll"):
            self.collect()
        self.assertFalse(self.archive.exists())

    def test_missing_patch_rejects_the_lzo_source(self):
        base = self.items[1][0]
        path = self.root / (base + ".src.tar")
        with tarfile.open(path) as original:
            kept = {item.name: original.extractfile(item).read() for item in original
                    if not item.name.endswith("local.patch")}
        tar(path, kept)
        with self.assertRaisesRegex(cs.SourceError, "missing archive members.*local.patch"):
            self.collect()
        self.assertFalse(self.archive.exists())

    def test_linux_build_adapter_binds_the_archive_to_the_build_inputs(self):
        source = self.root / "linux-source.tar"
        source_sha = cs.digest(b"corresponding source")
        tar(source, {"lib.c": b"corresponding source"})
        component = {"kind": "linux", "name": "runtime"}
        row = {"source": "lib.so", "sha256": cs.digest(b"binary")}
        receipt = {"source": source.name, "source_sha256": cs.sha256(source),
                   "build_inputs": {"lib.so": row["sha256"]}, "required_members": {"lib.c": source_sha}}
        found, signature, meta = cs.verify_adapter(component, [row], self.root, receipt, "zstd")
        self.assertEqual((found, signature, meta["members"]), (source, None, ["lib.c"]))
        with self.assertRaisesRegex(cs.SourceError, "does not cover"):
            cs.verify_adapter(component, [{**row, "sha256": "0" * 64}], self.root, receipt, "zstd")
