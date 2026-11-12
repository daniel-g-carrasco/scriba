"""Corresponding-source collection never executes recipes or trusts unverified inputs."""

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _setup

SPEC = importlib.util.spec_from_file_location("collect_sources", _setup.ROOT / "tools" / "collect_sources.py")
sources = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sources
SPEC.loader.exec_module(sources)


class SourceCollectionTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.recipe = b"pkgver=1\npkgrel=1\ntouch THIS_MUST_NOT_RUN\n"
        self.recipe_hash = hashlib.sha256(self.recipe).hexdigest()

    def archive(self, members, name="source.tar.gz"):
        path = self.root / name
        with tarfile.open(path, "w:gz") as archive:
            for member_name, data in members:
                info = tarfile.TarInfo(member_name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return path

    def source(self, payload=b"source bytes", expected=None, recipe=None, source="upstream.tar.gz", checksum=None):
        expected = expected or hashlib.sha256(b"source bytes").hexdigest()
        info = (f"pkgbase = mingw-w64-example\n\tpkgver = 1\n\tpkgrel = 1\n"
                f"\tsource = {source}\n\tsha256sums = {checksum or expected}\n")
        return self.archive([("mingw-w64-example/.SRCINFO", info.encode()),
                             ("mingw-w64-example/PKGBUILD", recipe or self.recipe),
                             ("mingw-w64-example/upstream.tar.gz", payload)])

    def test_all_payload_hashes_and_the_binary_recipe_hash_are_checked(self):
        archive = self.source()
        with (mock.patch.object(sources.subprocess, "run", side_effect=AssertionError("recipe executed")),
              mock.patch.object(sources.subprocess, "Popen", side_effect=AssertionError("recipe executed"))):
            result = sources.inspect_source(archive, "mingw-w64-example", self.recipe_hash)
        self.assertEqual(result["recipe_sha256"], self.recipe_hash)
        self.assertEqual(result["payloads"][0]["status"], "verified")
        self.assertFalse((self.root / "THIS_MUST_NOT_RUN").exists())

    def test_a_changed_payload_is_refused(self):
        with self.assertRaisesRegex(sources.SourceError, "checksum"):
            sources.inspect_source(self.source(payload=b"changed"), "mingw-w64-example", self.recipe_hash)

    def test_a_different_recipe_is_refused_even_when_the_payload_matches(self):
        with self.assertRaisesRegex(sources.SourceError, "PKGBUILD"):
            sources.inspect_source(self.source(recipe=b"different"), "mingw-w64-example", self.recipe_hash)

    def test_skip_is_recorded_explicitly_rather_than_passed_off_as_verified(self):
        result = sources.inspect_source(self.source(checksum="SKIP"), "mingw-w64-example", self.recipe_hash)
        self.assertEqual(result["payloads"][0]["status"], "skipped")
        self.assertEqual(result["unhashed_sources"], 1)

    def test_vcs_material_and_the_pinned_commit_are_preserved_as_unchecked(self):
        commit = "a" * 40
        info = ("pkgbase = mingw-w64-example\n\tpkgver = 1\n\tpkgrel = 1\n"
                f"\tsource = repo::git+https://example.invalid/repo#commit={commit}\n\tsha256sums = SKIP\n")
        archive = self.archive([("mingw-w64-example/.SRCINFO", info.encode()),
                                ("mingw-w64-example/PKGBUILD", self.recipe),
                                ("mingw-w64-example/repo/HEAD", b"a git snapshot")])
        payload = sources.inspect_source(archive, "mingw-w64-example", self.recipe_hash)["payloads"][0]
        self.assertEqual(payload["status"], "vcs_unchecked")
        self.assertEqual(payload["revision"], commit)
        self.assertTrue(payload["members"])

    def test_absolute_parent_and_windows_paths_are_refused(self):
        for path in ("../../escape", "/absolute", "C:/absolute", "x\\escape", "x/../escape", "x:stream", "folder/x:stream"):
            with self.subTest(path=path), self.assertRaises(sources.SourceError):
                sources.safe_member(path)
        bad = self.archive([("../../outside", b"bad")])
        with self.assertRaises(sources.SourceError):
            sources.inspect_source(bad, "mingw-w64-example", self.recipe_hash)
        self.assertFalse((self.root.parent / "outside").exists())

    def test_duplicate_metadata_cannot_replace_an_earlier_recipe(self):
        archive = self.archive([("mingw-w64-example/PKGBUILD", self.recipe),
                                ("mingw-w64-example/PKGBUILD", b"replacement")])
        with self.assertRaisesRegex(sources.SourceError, "duplicate"):
            sources.inspect_source(archive, "mingw-w64-example", self.recipe_hash)

    def test_inventory_paths_and_package_names_are_validated_before_access(self):
        path = self.root / "packages.json"
        path.write_text(json.dumps({"schema_version": 1, "packages": [
            {"package": "mingw-w64-ucrt-x86_64-example", "version": "1-1", "files": ["../../secret"]}]}))
        with self.assertRaises(sources.SourceError):
            sources.load_inventory(path)
        path.write_text(json.dumps({"schema_version": 1, "packages": [
            {"package": "example;touch marker", "version": "1-1", "files": []}]}))
        with self.assertRaises(sources.SourceError):
            sources.load_inventory(path)

    def test_a_signature_needs_success_and_a_valid_fingerprint(self):
        valid = "[GNUPG:] GOODSIG key test\n[GNUPG:] VALIDSIG " + "A" * 40 + " 2026-01-01 0 0\n"
        self.assertEqual(sources.signature_fingerprint(valid, 0), "A" * 40)
        for output, status in ((valid, 1), ("[GNUPG:] GOODSIG key test", 0),
                               (valid + "[GNUPG:] REVKEYSIG key test", 0)):
            with self.subTest(status=status, output=output), self.assertRaises(sources.SourceError):
                sources.signature_fingerprint(output, status)

    def test_binary_metadata_and_bundle_origin_are_checked(self):
        bundle = self.root / "bundle"
        (bundle / "_internal").mkdir(parents=True)
        data = b"a DLL"
        (bundle / "_internal/example.dll").write_bytes(data)
        package = "mingw-w64-ucrt-x86_64-example"
        archive = self.archive([(".PKGINFO", f"pkgname = {package}\npkgbase = mingw-w64-example\npkgver = 1-1\n".encode()),
                                (".BUILDINFO", f"pkgname = {package}\npkgbase = mingw-w64-example\npkgver = 1-1\npkgbuild_sha256sum = {self.recipe_hash}\n".encode()),
                                ("ucrt64/bin/example.dll", data)], "binary.tar.gz")
        entry = {"package": package, "version": "1-1", "files": ["_internal/example.dll"],
                 "origins": {"_internal/example.dll": {"package_file": "ucrt64/bin/example.dll",
                                                        "sha256": hashlib.sha256(data).hexdigest()}}}
        result = sources.inspect_binary(archive, entry, bundle)
        self.assertEqual(result["base"], "mingw-w64-example")
        self.assertEqual(result["files"][0]["status"], "verified")
        (bundle / "_internal/example.dll").write_bytes(b"tampered")
        with self.assertRaisesRegex(sources.SourceError, "bundle"):
            sources.inspect_binary(archive, entry, bundle)
        entry["version"] = "2-1"
        with self.assertRaisesRegex(sources.SourceError, "version"):
            sources.inspect_binary(archive, entry, bundle)

    def test_a_missing_declared_source_is_not_silently_accepted(self):
        with self.assertRaisesRegex(sources.SourceError, "missing"):
            sources.inspect_source(self.source(source="missing.tar.gz"), "mingw-w64-example", self.recipe_hash)

    def test_output_inside_the_source_repository_is_refused(self):
        with self.assertRaises(sources.SourceError):
            sources.require_external_output(_setup.ROOT / "build" / "sources")
        self.assertEqual(sources.require_external_output(self.root / "out"), (self.root / "out").resolve())

    def test_source_order_and_architecture_specific_hashes_are_independent(self):
        payload = b"source bytes"
        info = ("pkgbase = mingw-w64-example\nsource_x86_64 = patches/upstream.tar.gz\n"
                f"sha512sums_x86_64 = {hashlib.sha512(payload).hexdigest()}\n")
        archive = self.archive([("mingw-w64-example/patches/upstream.tar.gz", payload),
                                ("mingw-w64-example/PKGBUILD", self.recipe),
                                ("mingw-w64-example/.SRCINFO", info.encode())])
        result = sources.inspect_source(archive, "mingw-w64-example", self.recipe_hash)
        self.assertEqual(result["payloads"][0]["status"], "verified")

    def test_empty_inventory_is_not_a_complete_inventory(self):
        path = self.root / "empty.json"
        path.write_text(json.dumps({"schema_version": 1, "packages": []}))
        with self.assertRaises(sources.SourceError):
            sources.load_inventory(path)

    def test_epoch_is_retained_in_metadata_but_removed_from_archive_filename(self):
        self.assertEqual(sources.archive_version("1:1.9.5-2"), "1.9.5-2")
        self.assertEqual(sources.evidence_token("example", "1:1.9.5-2"), "example-1%3A1.9.5-2")

    def test_generated_inputs_are_checked_without_claiming_generated_bytes_match_inputs(self):
        package = "mingw-w64-ucrt-x86_64-example"
        data = b"cache generator program"
        digest = hashlib.sha256(data).hexdigest()
        archive = self.archive([(".PKGINFO", f"pkgname = {package}\npkgbase = mingw-w64-example\npkgver = 1-1\n".encode()),
                                (".BUILDINFO", f"pkgname = {package}\npkgbase = mingw-w64-example\npkgver = 1-1\npkgbuild_sha256sum = {self.recipe_hash}\n".encode()),
                                ("ucrt64/bin/generator.exe", data)], "generated.tar.gz")
        entry = {"package": package, "version": "1-1", "files": [],
                 "_generated_inputs": [{"package": package, "package_file": "ucrt64/bin/generator.exe", "sha256": digest}]}
        checked = sources.inspect_binary(archive, entry, self.root)
        self.assertEqual(checked["generated_inputs"][0]["status"], "input_verified")
        entry["_generated_inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(sources.SourceError, "generated"):
            sources.inspect_binary(archive, entry, self.root)

    def test_msys_package_repositories_and_architecture_are_not_mingw_defaults(self):
        self.assertEqual(sources.package_repositories("mintty"),
                         ("https://repo.msys2.org/msys/x86_64", "https://repo.msys2.org/msys/sources", "x86_64"))
        self.assertEqual(sources.package_repositories("mingw-w64-ucrt-x86_64-gtk4")[2], "any")

    def test_own_ffmpeg_has_its_own_source_hash_and_binary_identity(self):
        build = self.root / "ffmpeg-build"
        bundle = self.root / "bundle"
        (build / "bin").mkdir(parents=True)
        (bundle / "_internal").mkdir(parents=True)
        source = self.root / "ffmpeg.tar.xz"
        source.write_bytes(b"our exact FFmpeg source")
        build_file = build / "BUILD.txt"
        build_file.write_text(f"Sources: https://example.invalid/ffmpeg.tar.xz\nSHA-256 of the sources: {sources.sha256(source)}\n")
        script = self.root / "build_ffmpeg.sh"
        script.write_bytes(b"never execute this")
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            (build / "bin" / name).write_bytes(name.encode())
            (bundle / "_internal" / name).write_bytes(name.encode())
        args = SimpleNamespace(ffmpeg_build=build_file, ffmpeg_source=source, ffmpeg_script=script)
        result = sources.own_ffmpeg(args, self.root / "output", bundle)
        self.assertEqual(result["kind"], "own_ffmpeg")
        self.assertEqual(len(result["programs"]), 2)
        source.write_bytes(b"different archive")
        with self.assertRaisesRegex(sources.SourceError, "checksum"):
            sources.own_ffmpeg(args, self.root / "different-output", bundle)
