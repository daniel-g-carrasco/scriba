"""Pinned Linux sources are acquired without executing recipes or trusting cache bytes."""

import hashlib
import http.server
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import _setup

SPEC = importlib.util.spec_from_file_location("collect_linux_sources", _setup.ROOT / "tools/collect_linux_sources.py")
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)
BODY = b"published source bytes\n" * 1024


def artifact(**changes):
    result = {"id": "source-1", "type": "source_archive", "url": "https://sources.example/ok",
              "destination": "sources/published.tar.gz", "size": len(BODY),
              "sha256": hashlib.sha256(BODY).hexdigest(), "sha512": hashlib.sha512(BODY).hexdigest()}
    result.update(changes)
    return result


class Handler(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.requests.append((self.path, self.headers.get("Accept")))
        if self.path == "/missing":
            self.send_error(404)
            return
        body = BODY + b"extra" if self.path == "/big" else BODY
        if self.path == "/raw" and self.headers.get("Accept") != "application/vnd.github.raw+json":
            body = b'{"encoding":"base64"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[:len(body) // 2] if self.path == "/cut" else body)


class LocalHTTPS:
    """Only the transport is made up; real HTTP framing exercises the reader."""
    def __init__(self, port, read=None):
        self.port, self.read = port, read

    def open(self, request, timeout):
        path = "/raw" if request.host == "api.github.com" else urllib.parse.urlsplit(request.full_url).path
        reply = urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=dict(request.header_items())), timeout=timeout)
        original_read = reply.read
        callback = self.read

        def reading(count):
            data = original_read(count)
            if callback:
                callback()
            return data
        reply.read = reading
        reply.geturl = lambda: request.full_url
        return reply


class AcquisitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cache = self.root / "cache"
        self.opener = LocalHTTPS(self.server.server_address[1])
        Handler.requests = []

    def test_valid_source_is_checked_then_reused_only_after_rechecking_bytes(self):
        path, result = collector.acquire(artifact(), self.cache, opener=self.opener)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(result["verified_hashes"]["sha512"], hashlib.sha512(BODY).hexdigest())
        again, reused = collector.acquire(artifact(), self.cache, opener=self.opener)
        self.assertEqual(again, path)
        self.assertEqual(reused["acquisition"], "cache")
        self.assertEqual(len(Handler.requests), 1)
        path.write_bytes(b"tampered source".ljust(len(BODY), b"x"))
        with self.assertRaisesRegex(collector.SourceError, "sha256"):
            collector.acquire(artifact(), self.cache, opener=self.opener)
        self.assertEqual(len(Handler.requests), 1)

    def test_a_secondary_hash_mismatch_never_publishes_a_cache_file(self):
        with self.assertRaisesRegex(collector.SourceError, "sha512"):
            collector.acquire(artifact(sha512="0" * 128), self.cache, opener=self.opener)
        self.assertFalse(list(self.cache.iterdir()))

    def test_too_large_truncated_and_missing_responses_fail_and_clean_owned_partials(self):
        for route in ("big", "cut", "missing"):
            with self.subTest(route=route), self.assertRaises(collector.SourceError):
                collector.acquire(artifact(url="https://sources.example/" + route), self.cache, opener=self.opener)
            self.assertFalse(list(self.cache.iterdir()))

    def test_raw_github_headers_are_sent_and_json_is_not_accepted_as_the_file(self):
        url = "https://api.github.com/repos/example/project/git/blobs/" + "a" * 40
        item = artifact(url=url, request_headers={"Accept": "application/vnd.github.raw+json"})
        collector.acquire(item, self.cache, opener=self.opener)
        self.assertEqual(Handler.requests[-1], ("/raw", "application/vnd.github.raw+json"))
        other = self.root / "other-cache"
        with self.assertRaises(collector.SourceError):
            collector.acquire(artifact(url=url), other, opener=self.opener)

    def test_cancellation_during_io_does_not_leave_a_completed_or_partial_file(self):
        cancelled = threading.Event()
        opener = LocalHTTPS(self.server.server_address[1], cancelled.set)
        with self.assertRaises(collector.SourceError):
            collector.acquire(artifact(), self.cache, opener=opener, cancelled=cancelled)
        self.assertFalse(list(self.cache.iterdir()))

    def test_seed_sources_are_verified_and_private_paths_are_not_reported(self):
        seed = self.root / "seed"
        seed.mkdir()
        (seed / "published.tar.gz").write_bytes(BODY)
        path, report = collector.acquire(artifact(), self.cache, seeds=(seed,), opener=self.opener)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(report["acquisition"], "seed")
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertEqual(Handler.requests, [])


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def catalog(self, *entries):
        return {"schema_version": 1, "status": "unsigned recipe-derived inventory", "artifacts": list(entries)}

    def test_paths_collisions_and_secret_headers_are_rejected_before_any_write(self):
        for name in ("../escape", "/escape", "C:/escape", "a\\escape", "a:stream", "metadata/fake", "Metadata/fake"):
            with self.subTest(name=name), self.assertRaises(collector.SourceError):
                collector.validate_catalog(self.catalog(artifact(destination=name)))
        with self.assertRaises(collector.SourceError):
            collector.validate_catalog(self.catalog(artifact(), artifact(id="other")))
        with self.assertRaises(collector.SourceError):
            collector.validate_catalog(self.catalog(artifact(request_headers={"Authorization": "secret"})))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_only_https_and_declared_hashes_are_accepted(self):
        for url in ("http://example.org/source", "file:///etc/passwd", "https://secret@example.org/source"):
            with self.subTest(url=url), self.assertRaises(collector.SourceError):
                collector.validate_catalog(self.catalog(artifact(url=url)))
        with self.assertRaises(collector.SourceError):
            collector.validate_catalog(self.catalog(artifact(sha256="SKIP")))

    def test_redirects_cannot_downgrade_https_or_forward_credentials_to_another_origin(self):
        redirect = collector.HTTPSRedirect()
        request = urllib.request.Request("https://api.github.com/source", headers={"Authorization": "Bearer private"})
        with self.assertRaises(collector.SourceError):
            redirect.redirect_request(request, None, 302, "Found", {}, "http://example.org/source")
        changed = redirect.redirect_request(request, None, 302, "Found", {}, "https://codeload.github.com/source")
        self.assertFalse(changed.has_header("Authorization"))

    def test_source_references_cannot_point_to_missing_artifacts(self):
        catalog = self.catalog(artifact())
        catalog["binary_mappings"] = [{"binary_path": "lib/example.so", "binary_sha256": "0" * 64,
                                       "source_artifacts": ["missing"]}]
        with self.assertRaises(collector.SourceError):
            collector.validate_catalog(catalog)

    def test_a_changed_current_binary_invalidates_its_source_mapping(self):
        folder = self.root / "build"
        (folder / "lib").mkdir(parents=True)
        (folder / "lib/example.so").write_bytes(BODY)
        mapping = {"binary_path": "lib/example.so", "binary_sha256": hashlib.sha256(BODY).hexdigest()}
        self.assertTrue(collector.verify_binary(mapping, folder)["matched"])
        (folder / "lib/example.so").write_bytes(b"changed")
        with self.assertRaises(collector.SourceError):
            collector.verify_binary(mapping, folder)

    def test_application_archive_is_tied_to_the_source_files_in_the_current_build(self):
        folder = self.root / "build/share/scriba/scriba"
        folder.mkdir(parents=True)
        (folder / "main.py").write_bytes(BODY)
        archive = self.root / "app-source.tar"
        with tarfile.open(archive, "w") as out:
            info = tarfile.TarInfo("scriba/src/scriba/main.py")
            info.size = len(BODY)
            out.addfile(info, io.BytesIO(BODY))
        result = collector.verify_application(archive, "a" * 40, self.root / "build")
        self.assertEqual(result["source_files_verified"], 1)
        (folder / "main.py").write_bytes(b"changed")
        with self.assertRaises(collector.SourceError):
            collector.verify_application(archive, "a" * 40, self.root / "build")

    def test_incomplete_application_archive_is_rejected(self):
        folder = self.root / "build/share/scriba/scriba"
        folder.mkdir(parents=True)
        (folder / "main.py").write_bytes(BODY)
        (folder / "omitted.py").write_bytes(b"also installed")
        archive = self.root / "app.tar"
        with tarfile.open(archive, "w") as out:
            info = tarfile.TarInfo("scriba/src/scriba/main.py")
            info.size = len(BODY)
            out.addfile(info, io.BytesIO(BODY))
        with self.assertRaisesRegex(collector.SourceError, "omits"):
            collector.verify_application(archive, "a" * 40, self.root / "build")

    def test_strict_collection_checks_the_current_inputs_before_it_can_be_published(self):
        build = self.root / "build/files"
        (build / "lib").mkdir(parents=True)
        (build / "lib/example.so").write_bytes(BODY)
        installed = build / "share/scriba/scriba"
        installed.mkdir(parents=True)
        (installed / "main.py").write_bytes(BODY)
        (build.parent / "metadata").write_text("[Application]\nname=example.app\nruntime=example/runtime/1\n")
        archive = self.root / "public-app.tar"
        with tarfile.open(archive, "w") as out:
            info = tarfile.TarInfo("scriba/src/scriba/main.py")
            info.size = len(BODY)
            out.addfile(info, io.BytesIO(BODY))
        item = artifact()
        catalog = self.catalog(item)
        catalog.update(python_sources=[], binary_mappings=[{
            "binary_path": "lib/example.so", "binary_sha256": item["sha256"], "source_artifacts": [item["id"]]}],
            onnxruntime_binary={"path": "lib/example.so", "sha256": item["sha256"], "source_artifact": item["id"]})
        catalog_file = self.root / "catalog.json"
        catalog_file.write_text(json.dumps(catalog))
        manifest = self.root / "expanded.json"
        manifest.write_text(json.dumps({"modules": [{"name": "onnxruntime", "sources": [{
            "url": "https://sources.example/input.tgz", "sha256": item["sha256"]}]}]}))
        state = self.root / "state"
        binary_input = state / "downloads" / item["sha256"] / "input.tgz"
        binary_input.parent.mkdir(parents=True)
        binary_input.write_bytes(BODY)
        seed = self.root / "seeds"
        seed.mkdir()
        (seed / "published.tar.gz").write_bytes(BODY)
        bundle = self.root / "scriba.flatpak"
        bundle.write_bytes(b"current bundle")
        args = ["--catalog", str(catalog_file), "--cache", str(self.root / "cache"),
                "--build-root", str(build), "--builder-state", str(state), "--bundle", str(bundle),
                "--resolved-manifest", str(manifest), "--app-archive", str(archive), "--app-commit", "a" * 40,
                "--runtime-commit", "b" * 64, "--sdk-commit", "c" * 64, "--seed-dir", str(seed)]
        output = self.root / "output"
        with mock.patch.object(urllib.request, "build_opener", side_effect=AssertionError("network not expected")):
            self.assertEqual(collector.main(args + ["--output", str(output)]), 0)
            report = json.loads((output / "manifest.json").read_text())
            self.assertTrue(report["publishable"])
            self.assertTrue(report["binary_association_verified"])
            self.assertEqual(report["bundle"]["sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
            (build / "lib/example.so").write_bytes(b"changed bundle input")
            rejected = self.root / "rejected"
            self.assertNotEqual(collector.main(args + ["--output", str(rejected)]), 0)
            report = json.loads((rejected / "manifest.json").read_text())
            self.assertFalse(report["publishable"])
            self.assertEqual(report["artifacts"], [])
            self.assertIn("binary differs", report["errors"][0]["error"])

    def test_strict_mode_cannot_acquire_or_publish_without_application_source(self):
        catalog = self.root / "catalog.json"
        catalog.write_text(json.dumps(self.catalog(artifact())))
        with mock.patch.object(collector, "acquire", side_effect=AssertionError("network not expected")):
            status = collector.main(["--catalog", str(catalog), "--output", str(self.root / "output"),
                                     "--cache", str(self.root / "cache")])
        self.assertNotEqual(status, 0)

    def test_public_catalogue_has_complete_source_references_and_preserves_audit_limits(self):
        path = _setup.ROOT / "build-aux/flatpak/sources/linux-corresponding-sources.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        collector.validate_catalog(catalog)
        self.assertEqual(len(catalog["artifacts"]), 49)
        self.assertEqual(len(catalog["binary_mappings"]), 7)
        self.assertEqual(len(catalog["resolved_ports"]), 29)
        self.assertEqual(len(catalog["python_sources"]), 7)
        self.assertIn("not a vendor-signed SBOM", catalog["status"])
        self.assertTrue(catalog["recipe_assumptions"])


class PairVerificationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.bundle = self.root / "scriba.flatpak"
        self.bundle.write_bytes(BODY)
        self.manifest = {"publishable": True, "sources_complete": True, "binary_association_verified": True,
                         "errors": [], "bundle": {"sha256": hashlib.sha256(BODY).hexdigest(), "size": len(BODY)},
                         "application_source": {"status": "source_files_match", "commit": "a" * 40}}

    def archive(self, name, manifest=None, duplicate=False):
        archive = self.root / name
        payload = json.dumps(self.manifest if manifest is None else manifest).encode()
        with tarfile.open(archive, "w:xz") as out:
            info = tarfile.TarInfo("./manifest.json")
            info.size = len(payload)
            out.addfile(info, io.BytesIO(payload))
            if duplicate:
                out.addfile(info, io.BytesIO(payload))
        return archive

    def test_the_signed_source_manifest_must_match_this_bundle_and_commit(self):
        archive = self.archive("source.tar.xz")
        self.assertEqual(collector.main(["--verify-pair", str(archive), "--bundle", str(self.bundle),
                                         "--app-commit", "a" * 40]), 0)
        self.bundle.write_bytes(b"another bundle")
        with self.assertRaises(collector.SourceError):
            collector.verify_pair(archive, self.bundle, "a" * 40)

    def test_an_acquisition_only_or_wrong_commit_manifest_cannot_be_published(self):
        for key in ("publishable", "sources_complete", "binary_association_verified"):
            with self.subTest(key=key):
                manifest = {**self.manifest, key: False}
                with self.assertRaises(collector.SourceError):
                    collector.verify_pair(self.archive(key + ".tar.xz", manifest), self.bundle, "a" * 40)
        archive = self.archive("wrong-commit.tar.xz")
        with self.assertRaises(collector.SourceError):
            collector.verify_pair(archive, self.bundle, "b" * 40)

    def test_ambiguous_or_non_regular_manifests_are_rejected_without_extraction(self):
        with self.assertRaises(collector.SourceError):
            collector.verify_pair(self.archive("duplicate.tar.xz", duplicate=True), self.bundle, "a" * 40)
        archive = self.root / "linked.tar"
        with tarfile.open(archive, "w") as out:
            info = tarfile.TarInfo("manifest.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "/outside"
            out.addfile(info)
        with self.assertRaises(collector.SourceError):
            collector.verify_pair(archive, self.bundle, "a" * 40)


class SourceWorkflowTest(unittest.TestCase):
    def test_each_packaged_linux_bundle_has_strict_sources_from_the_public_checkout(self):
        text = (_setup.ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        package = text.split('  flatpak:\n', 1)[1].split('\n  release:\n', 1)[0]
        self.assertIn('git -c safe.directory="$GITHUB_WORKSPACE" archive --format=tar --prefix=scriba/', package)
        self.assertIn('tools/collect_linux_sources.py', package)
        for option in ('--app-archive', '--app-commit', '--resolved-manifest', '--build-root',
                       '--builder-state', '--bundle', '--runtime-commit', '--sdk-commit'):
            self.assertIn(option, package)
        self.assertNotIn('--acquire-only', package)
        self.assertIn('scriba-${version}-linux-x86_64-sources.tar.xz', package)
        self.assertIn('scriba-*-linux-x86_64-sources.tar.xz', package)
        self.assertIn('linux-sources-v1-', package)
        self.assertNotIn('restore-keys:', package)

    def test_tag_signs_verifies_and_uploads_the_sources_with_the_same_bundle(self):
        text = (_setup.ROOT / '.github/workflows/build.yml').read_text(encoding='utf-8')
        release = text.split('\n  release:\n', 1)[1]
        self.assertIn('for f in scriba.flatpak "scriba-${GITHUB_REF_NAME}-linux-x86_64-sources.tar.xz"', release)
        self.assertIn('minisign -S', release)
        self.assertIn('minisign -V', release)
        upload = release.split('gh release upload', 1)[1].split('- name: Published', 1)[0]
        self.assertIn('"scriba-${GITHUB_REF_NAME}-linux-x86_64-sources.tar.xz"', upload)
        self.assertIn('"scriba-${GITHUB_REF_NAME}-linux-x86_64-sources.tar.xz.minisig"', upload)
