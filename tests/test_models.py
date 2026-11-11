#!/usr/bin/env python3
"""The models: the catalogue, the folder, and the download against a real
HTTP server on this machine (the network is the thing a test must not
touch, so the server is local and the files are made up): cut short and
resumed, a server that does not resume, a wrong file, more than declared,
a redirect to another site, cancelled, already there."""

import hashlib
import http.server
import os
import re
import tempfile
import threading
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

import _setup

from scriba.app import models
from scriba.app.task import Cancelled

BODY = bytes(range(256)) * 1024             # 256 KiB, the stand-in for a model
SHA = hashlib.sha256(BODY).hexdigest()


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.requests = []          # (path, Range header)
        self.cut_once = True


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"   # one answer per connection: a cut is a closed socket

    def log_message(self, *args):
        pass

    def _send(self, status, body, headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        server = self.server
        server.requests.append((self.path, self.headers.get("Range")))
        start = 0
        wanted = self.headers.get("Range")
        if wanted:
            start = int(re.match(r"bytes=(\d+)-", wanted).group(1))
        port = server.server_address[1]
        if self.path in ("/ok", "/cut") and not (self.path == "/cut" and server.cut_once and not start):
            if start:
                self._send(206, BODY[start:], [("Content-Length", str(len(BODY) - start)),
                                               ("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")])
            else:
                self._send(200, BODY, [("Content-Length", str(len(BODY)))])
        elif self.path == "/cut":            # the first time, half the file and the connection closed
            server.cut_once = False
            self._send(200, BODY[:len(BODY) // 2], [("Content-Length", str(len(BODY)))])
        elif self.path == "/norange":        # a server that always sends the whole file
            self._send(200, BODY, [("Content-Length", str(len(BODY)))])
        elif self.path == "/wrong":
            self._send(200, bytes(len(BODY)), [("Content-Length", str(len(BODY)))])
        elif self.path == "/declared-bigger":
            self._send(200, BODY + b"x", [("Content-Length", str(len(BODY) + 1))])
        elif self.path == "/bigger":         # no length given: the file just goes on
            self._send(200, BODY + b"x" * 4096)
        elif self.path == "/away":
            self._send(302, b"", [("Location", f"http://localhost:{port}/ok")])
        elif self.path == "/here":
            self._send(302, b"", [("Location", f"http://127.0.0.1:{port}/ok")])
        else:
            self._send(404, b"")


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def model(self, path):
        port = self.server.server_address[1]
        return models.Model("fake", "fake.bin", f"http://127.0.0.1:{port}{path}", SHA, len(BODY), "MIT", "nobody",
                            "https://example.org", ("127.0.0.1",))

    def files(self):
        return sorted(p.name for p in self.folder.iterdir())

    def test_a_model_arrives_checked_under_its_name(self):
        seen = []
        path = models.download(self.model("/ok"), self.folder, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(self.files(), ["fake.bin"])
        self.assertEqual(seen[-1], (len(BODY), len(BODY)))
        self.assertTrue(models.verify(self.model("/ok"), self.folder))

    def test_a_model_already_there_is_not_downloaded_again(self):
        models.download(self.model("/ok"), self.folder)
        models.download(self.model("/ok"), self.folder)
        self.assertEqual(len(self.server.requests), 1)

    def test_a_download_cut_short_is_kept_and_resumed(self):
        with self.assertRaises(models.ModelError) as caught:
            models.download(self.model("/cut"), self.folder)
        self.assertIn("try again", str(caught.exception))
        self.assertEqual(self.files(), ["fake.bin.part"])
        self.assertEqual((self.folder / "fake.bin.part").stat().st_size, len(BODY) // 2)
        path = models.download(self.model("/cut"), self.folder)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(self.server.requests[-1], ("/cut", f"bytes={len(BODY) // 2}-"))
        self.assertEqual(self.files(), ["fake.bin"])

    def test_a_server_that_does_not_resume_gives_a_clean_start(self):
        (self.folder / "fake.bin.part").write_bytes(b"garbage, not the start of the file")
        path = models.download(self.model("/norange"), self.folder)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(self.files(), ["fake.bin"])

    def test_a_file_that_is_not_the_one_declared_is_thrown_away(self):
        with self.assertRaises(models.ModelError) as caught:
            models.download(self.model("/wrong"), self.folder)
        self.assertIn("checksum", str(caught.exception))
        self.assertEqual(self.files(), [])

    def test_more_than_declared_is_refused_and_not_kept(self):
        for path in ("/declared-bigger", "/bigger"):
            with self.subTest(path), self.assertRaises(models.ModelError) as caught:
                models.download(self.model(path), self.folder)
            self.assertIn("more than", str(caught.exception))
            self.assertEqual(self.files(), [])

    def test_a_redirect_to_another_site_is_refused(self):
        with self.assertRaises(models.ModelError) as caught:
            models.download(self.model("/away"), self.folder)
        self.assertIn("localhost", str(caught.exception))
        self.assertEqual(self.files(), [])
        self.assertEqual([p for p, _r in self.server.requests], ["/away"])
        self.assertEqual(models.download(self.model("/here"), self.folder).read_bytes(), BODY)

    def test_cancelled_keeps_the_part_for_later(self):
        stop = threading.Event()
        with self.assertRaises(Cancelled):
            models.download(self.model("/ok"), self.folder, progress=lambda d, t: stop.set(), cancelled=stop)
        self.assertEqual(self.files(), ["fake.bin.part"])
        self.assertEqual(models.download(self.model("/ok"), self.folder).read_bytes(), BODY)
        self.assertIsNotNone(self.server.requests[-1][1])          # resumed, not started again

    def test_no_download_without_room_on_the_disk(self):
        usage = namedtuple("usage", "total used free")(10**9, 10**9, 1000)
        with mock.patch("shutil.disk_usage", return_value=usage), self.assertRaises(models.ModelError) as caught:
            models.download(self.model("/ok"), self.folder)
        self.assertIn("not enough room", str(caught.exception))
        self.assertEqual(self.server.requests, [])

    def test_a_model_with_the_wrong_size_counts_as_missing(self):
        model = self.model("/ok")
        self.assertEqual(models.missing([model], self.folder), [model])
        (self.folder / "fake.bin").write_bytes(BODY[:-1])
        self.assertEqual(models.missing([model], self.folder), [model])
        (self.folder / "fake.bin").write_bytes(BODY)
        self.assertEqual(models.missing([model], self.folder), [])
        (self.folder / "fake.bin").write_bytes(bytes(len(BODY)))       # the size is right, the file is not
        self.assertFalse(models.verify(model, self.folder))


class CatalogueTest(unittest.TestCase):
    def test_every_model_is_declared_whole_and_downloaded_over_https_from_its_publisher(self):
        self.assertEqual(len({m.key for m in models.CATALOGUE}), len(models.CATALOGUE))
        for model in models.CATALOGUE:
            with self.subTest(model.key):
                self.assertRegex(model.sha256, r"^[0-9a-f]{64}$")
                self.assertGreater(model.size, 0)
                self.assertTrue(model.url.startswith("https://"))
                self.assertTrue(models.allowed(model, model.url))
                self.assertFalse(models.allowed(model, model.url.replace("https://", "http://")))
                self.assertFalse(models.allowed(model, "https://example.com/" + model.filename))
                self.assertNotIn("/main/", model.url)              # a fixed revision, not a branch
                self.assertNotEqual(models.purpose(model), model.key)
                self.assertIn(model.licence, ("MIT", "CC-BY-4.0"))

    def test_hugging_face_may_send_the_file_to_its_own_cdn_and_nowhere_else(self):
        model = models.WHISPER_TURBO
        self.assertTrue(models.allowed(model, "https://us.aws.cdn.hf.co/xet-bridge-us/641ab5d1/9c7b?x=1"))
        self.assertFalse(models.allowed(model, "https://hf.co.example.com/file"))
        self.assertFalse(models.allowed(model, "https://evilhf.co/file"))

    def test_the_folder_is_the_variable_s_or_the_cache(self):
        self.assertEqual(models.folder({models.MODELS_DIR_VARIABLE: "somewhere"}), Path("somewhere"))
        root = r"C:\x" if _setup.WINDOWS else "/x"
        environ = {"XDG_CONFIG_HOME": root, "XDG_CACHE_HOME": os.path.join(root, "cache"), "HOME": root}
        self.assertEqual(models.folder(environ), Path(root) / "cache" / "scriba" / "models")


class CreditsTest(unittest.TestCase):
    def test_every_model_and_engine_is_credited_with_its_licence(self):
        from scriba.app import credits
        licences = {c.licence for c in credits.THIRD_PARTY}
        for model in models.CATALOGUE:
            self.assertIn(model.licence, licences)
        names = " ".join(c.name for c in credits.THIRD_PARTY)
        for engine in ("whisper.cpp", "Silero", "WeSpeaker", "diarize", "ONNX Runtime", "FFmpeg"):
            self.assertIn(engine, names)

    def test_the_code_copied_from_diarize_carries_its_origin_and_its_licence(self):
        head = (_setup.ROOT / "src" / "scriba" / "app" / "engines" / "clustering.py").read_text(encoding="utf-8")[:600]
        self.assertIn("diarize 0.1.2", head)
        self.assertIn("Apache License 2.0", head)
        self.assertIn("Apache License", (_setup.ROOT / "LICENSES" / "diarize-Apache-2.0.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
