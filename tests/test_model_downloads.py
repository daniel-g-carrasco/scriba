"""One application queue owns downloads, including pause and orderly shutdown."""

import queue
import tempfile
import threading
import unittest
from pathlib import Path

import _setup  # noqa: F401 - adds the source checkout to sys.path

from scriba.app import models
from scriba.app.config import Config
from scriba.app.model_downloads import ModelDownloads
from scriba.app.task import Cancelled, check


class Backend:
    def __init__(self, folder):
        self.models_dir = Path(folder)
        self.available = set()
        self.calls = []
        self.entered = threading.Event()
        self.finish = threading.Event()
        self.cancelled = threading.Event()
        self.release_cancel = threading.Event()
        self.hold = False
        self.hold_cancel = False
        self.failure = None
        self.network_error_on_cancel = False

    def has(self, model):
        return model.key in self.available

    def download(self, model, progress, cancelled):
        self.calls.append(model.key)
        progress.phase("checking")
        progress(3, model.size)
        self.entered.set()
        if self.hold:
            cancelled.wait(2)
            if cancelled.is_set():
                self.cancelled.set()
                if self.hold_cancel:
                    self.release_cancel.wait(2)
                if self.network_error_on_cancel:
                    raise OSError("connection closed after pause")
                raise Cancelled()
            self.finish.wait(2)
        check(cancelled)
        if self.failure:
            raise self.failure
        progress.phase("verifying")
        self.available.add(model.key)
        return self.models_dir / model.filename


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.backend = Backend(self.tmp)
        self.cfg = Config()
        self.saved = []
        self.callbacks = queue.Queue()
        self.downloads = ModelDownloads(self.backend, self.cfg, lambda: self.saved.append(True),
                                        dispatch=self.callbacks.put)
        self.addCleanup(self.clean)

    def clean(self):
        self.backend.release_cancel.set()
        self.backend.finish.set()
        self.downloads.shutdown()
        self.assertTrue(self.downloads.wait(3))

    def flush(self):
        while not self.callbacks.empty():
            self.callbacks.get_nowait()()

    def test_only_missing_required_models_are_started_automatically(self):
        self.backend.available.add(models.SILERO_VAD.key)
        self.downloads.start_required()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(self.backend.calls, [models.WHISPER_TURBO.key, models.WESPEAKER.key])
        self.assertEqual(self.downloads.state.phase, "ready")

    def test_optional_model_is_never_started_without_an_explicit_request(self):
        self.downloads.start_required()
        self.assertTrue(self.downloads.wait(3))
        self.assertNotIn(models.WHISPER_LARGE.key, self.backend.calls)
        self.downloads.download(models.WHISPER_LARGE)
        self.assertTrue(self.downloads.wait(3))
        self.assertIn(models.WHISPER_LARGE.key, self.backend.calls)

    def test_repeated_requests_do_not_start_a_second_worker(self):
        self.backend.hold = True
        self.downloads.start_required()
        self.assertTrue(self.backend.entered.wait(1))
        self.downloads.start_required()
        self.downloads.download(models.WHISPER_TURBO)
        self.assertEqual(self.backend.calls, [models.WHISPER_TURBO.key])
        self.assertTrue(self.downloads.busy)

    def test_pause_keeps_the_partial_file_and_persists_the_choice(self):
        partial = self.backend.models_dir / (models.WHISPER_TURBO.filename + ".part")
        partial.write_bytes(b"partial data")
        self.backend.hold = True
        self.backend.network_error_on_cancel = True
        self.downloads.start_required()
        self.assertTrue(self.backend.entered.wait(1))
        self.downloads.pause()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(partial.read_bytes(), b"partial data")
        self.assertTrue(self.cfg.models_download_paused)
        self.assertTrue(self.saved)
        self.assertEqual(self.downloads.state.phase, "paused")

    def test_a_persisted_pause_prevents_automatic_network_work(self):
        self.cfg.models_download_paused = True
        self.downloads.start_required()
        self.assertFalse(self.downloads.busy)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.downloads.state.phase, "paused")

    def test_resume_after_pause_reuses_the_queue(self):
        self.cfg.models_download_paused = True
        self.downloads.start_required()
        self.downloads.resume()
        self.assertTrue(self.downloads.wait(3))
        self.assertFalse(self.cfg.models_download_paused)
        self.assertEqual(len(self.backend.calls), 3)

    def test_immediate_resume_waits_for_the_old_writer_to_finish(self):
        self.backend.hold = self.backend.hold_cancel = True
        self.downloads.start_required()
        self.assertTrue(self.backend.entered.wait(1))
        self.downloads.pause()
        self.assertTrue(self.backend.cancelled.wait(1))
        self.downloads.resume()
        self.assertEqual(len(self.backend.calls), 1)
        self.backend.hold = False
        self.backend.release_cancel.set()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(self.backend.calls.count(models.WHISPER_TURBO.key), 2)

    def test_shutdown_remains_busy_until_cancellation_has_finished(self):
        self.backend.hold = self.backend.hold_cancel = True
        self.downloads.start_required()
        self.assertTrue(self.backend.entered.wait(1))
        self.downloads.shutdown()
        self.assertTrue(self.backend.cancelled.wait(1))
        self.assertTrue(self.downloads.busy)
        self.assertEqual(self.downloads.state.phase, "stopping")
        self.assertFalse(self.cfg.models_download_paused)
        self.backend.release_cancel.set()
        self.assertTrue(self.downloads.wait(3))
        self.downloads.resume()
        self.assertEqual(len(self.backend.calls), 1)

    def test_errors_are_recoverable_and_not_automatically_retried(self):
        self.backend.failure = OSError("no room")
        self.downloads.start_required()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(self.downloads.state.phase, "error")
        self.assertIn("no room", self.downloads.state.error)
        self.downloads.start_required()
        self.assertEqual(len(self.backend.calls), 1)
        self.backend.failure = None
        self.downloads.resume()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(self.downloads.state.phase, "ready")

    def test_observers_are_dispatched_and_can_unsubscribe_without_stopping_work(self):
        seen = []
        unsubscribe = self.downloads.subscribe(seen.append)
        self.downloads.start_required()
        self.assertTrue(self.downloads.wait(3))
        self.assertEqual(seen, [])
        self.flush()
        self.assertEqual(seen[-1].phase, "ready")
        unsubscribe()
        self.downloads.download(models.WHISPER_LARGE)
        self.assertTrue(self.downloads.wait(3))
        count = len(seen)
        self.flush()
        self.assertEqual(len(seen), count)

    def test_phase_and_partial_byte_progress_belong_to_the_current_model(self):
        self.backend.hold = True
        self.downloads.start_required()
        self.assertTrue(self.backend.entered.wait(1))
        self.assertEqual(self.downloads.state.model_key, models.WHISPER_TURBO.key)
        self.assertEqual(self.downloads.state.done, 3)
        self.assertEqual(self.downloads.state.total, sum(m.size for m in self.downloads.required))

    def test_new_settings_roundtrip_and_old_settings_receive_defaults(self):
        cfg = Config.from_json({"version": 1, "theme": "dark"})
        self.assertFalse(cfg.models_download_paused)
        self.assertTrue(cfg.keep_running)
        cfg.models_download_paused = True
        cfg.keep_running = False
        copy = Config.from_json(cfg.to_json())
        self.assertTrue(copy.models_download_paused)
        self.assertFalse(copy.keep_running)
