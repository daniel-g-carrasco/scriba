"""Custom GstPlay provenance is checked without building or executing sources."""
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

SPEC = importlib.util.spec_from_file_location("gstplay_receipt", _setup.ROOT / "tools/gstplay_receipt.py")
receipt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receipt)


class ReceiptTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.project = self.root / "project"
        self.build = self.root / "build"
        self.build.mkdir()
        for name in ("tools/build_gstplay.py", "build-aux/gstplay/gstplay-dispose.patch"):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"never execute this input")
        self.source = self.build / "gst-plugins-bad-1.26.11.tar.xz"
        with tarfile.open(self.source, "w:xz") as archive:
            info = tarfile.TarInfo("gst-plugins-bad-1.26.11/COPYING")
            info.size = 7
            archive.addfile(info, io.BytesIO(b"licence"))
        (self.build / "gstplay-dispose.patch").write_bytes(b"never execute this input")
        (self.build / "COPYING").write_bytes(b"licence")
        (self.build / "lib").mkdir()
        (self.build / "lib/libgstplay-1.0.so.0").write_bytes(b"custom library")
        sha = receipt.sha256
        self.data = {"schema_version": 1, "component": "gstplay", "version": "1.26.11",
            "source": {"file": self.source.name, "url": receipt.source_url("1.26.11"), "sha256": sha(self.source)},
            "patch": {"file": "gstplay-dispose.patch", "url": receipt.PATCH_URL,
                      "sha256": sha(self.build / "gstplay-dispose.patch")},
            "recipe": {"project_file": "tools/build_gstplay.py",
                       "sha256": sha(self.project / "tools/build_gstplay.py")},
            "outputs": [{"file": "lib/libgstplay-1.0.so.0", "sha256": sha(self.build / "lib/libgstplay-1.0.so.0")}],
            "licence_files": [{"file": "COPYING", "sha256": sha(self.build / "COPYING")}]}
        self.enterContext(mock.patch.dict(receipt.SOURCE_SHA256, {"1.26.11": sha(self.source)}))
        self.enterContext(mock.patch.object(receipt, "PATCH_SHA256", self.data["patch"]["sha256"]))
        self.path = self.build / "BUILD.json"

    def read(self):
        self.path.write_text(json.dumps(self.data))
        return receipt.read_receipt(self.path, project_root=self.project)

    def test_exact_source_patch_recipe_notice_and_output_are_verified_without_private_paths(self):
        self.assertEqual(self.read(), self.data)
        self.assertNotIn(str(self.root), json.dumps(self.read()))

    def test_changed_input_output_recipe_and_notice_fail(self):
        for path in (self.source, self.build / "gstplay-dispose.patch", self.build / "COPYING",
                     self.build / "lib/libgstplay-1.0.so.0", self.project / "tools/build_gstplay.py"):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                with self.assertRaises(ValueError):
                    self.read()
                path.write_bytes(original)

    def test_unknown_version_paths_extra_outputs_and_untrusted_patch_are_refused(self):
        for key, value in (("version", "9.99"), ("outputs", [{"file": "../secret", "sha256": "0" * 64}])):
            original = self.data[key]
            self.data[key] = value
            with self.assertRaises(ValueError):
                self.read()
            self.data[key] = original
        self.data["patch"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.read()

    def test_missing_materials_and_unrecognised_metadata_cannot_expose_private_paths(self):
        self.data["private_path"] = str(self.root)
        with self.assertRaises(ValueError):
            self.read()
        del self.data["private_path"]
        self.path.write_text(json.dumps(self.data))
        self.path.unlink()
        with self.assertRaises(ValueError) as failed:
            receipt.read_receipt(self.path, project_root=self.project)
        self.assertNotIn(str(self.root), str(failed.exception))

    def test_a_self_consistent_notice_hash_cannot_replace_the_upstream_notice(self):
        notice = self.build / "COPYING"
        notice.write_bytes(b"different notice")
        self.data["licence_files"][0]["sha256"] = receipt.sha256(notice)
        with self.assertRaisesRegex(ValueError, "notice differs"):
            self.read()
