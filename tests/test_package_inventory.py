# SPDX-License-Identifier: GPL-3.0-or-later
"""A miniature pacman database and a PyInstaller table of contents."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

import _setup

spec = importlib.util.spec_from_file_location("package_inventory", _setup.ROOT / "tools/package_inventory.py")
package_inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_inventory)


class PackageInventoryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.msys = self.root / "msys"
        self.project = self.root / "project"
        self.work = self.project / "build"
        self.owner = "mingw-w64-ucrt-x86_64-foo"
        self.binary = "ucrt64/bin/libfoo.dll"
        self.license_one = "ucrt64/share/licenses/foo/LICENSE"
        self.license_two = "ucrt64/share/licenses/foo/NOTICE"
        db = self.msys / "var/lib/pacman/local" / (self.owner + "-2-3")
        db.mkdir(parents=True)
        (db / "desc").write_text(f"%NAME%\n{self.owner}\n\n%VERSION%\n1:2-3\n\n%LICENSE%\nMIT\n", encoding="utf-8")
        (db / "files").write_text("%FILES%\n" + "\n".join((self.binary, self.license_one, self.license_two)),
                                  encoding="utf-8")
        for name in (self.binary, self.license_one, self.license_two):
            path = self.msys / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.project.mkdir()
        (self.project / "LICENSE").write_bytes(b"project license")
        self.toc = [("libfoo.dll", str(self.msys / self.binary), "BINARY")]

    def test_license_texts_and_notice_match_the_bundle(self):
        manifest, datas = package_inventory.inventory(self.toc, self.msys, self.project, self.work)
        package = manifest["components"]["msys2:" + self.owner]
        self.assertEqual(package["version"], "1:2-3")
        self.assertEqual(len(package["license_files"]), 2)
        bundle = self.root / "bundle"
        for name, source, _ in self.toc + datas:
            dest = bundle / "_internal" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        package_inventory.verify_bundle(bundle, manifest, strict=True)
        (bundle / package["license_files"][0]).write_bytes(b"different")
        with self.assertRaisesRegex(package_inventory.InventoryError, "differs"):
            package_inventory.verify_bundle(bundle, manifest)

    def test_unknown_file_is_explicit_and_the_database_is_required(self):
        unknown = self.msys / "ucrt64/bin/unknown.dll"
        unknown.write_bytes(b"dll")
        manifest, _ = package_inventory.inventory(
            [("unknown.dll", str(unknown), "BINARY")], self.msys, self.project, self.work)
        self.assertEqual(manifest["issues"], [{"target": "_internal/unknown.dll", "reason": "no recorded origin"}])
        with self.assertRaisesRegex(package_inventory.InventoryError, "database is absent"):
            package_inventory.inventory(self.toc, self.root / "absent", self.project, self.work)

    def test_different_installed_revision_stops_the_build(self):
        with self.assertRaisesRegex(package_inventory.InventoryError, "installed 1:2-3, expected 1:2-4"):
            package_inventory.inventory(self.toc, self.msys, self.project, self.work,
                                        expected_versions={self.owner: "1:2-4"})
