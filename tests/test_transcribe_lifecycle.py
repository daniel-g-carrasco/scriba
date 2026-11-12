"""Cancellation requests keep workers owned until their actual completion."""

import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()
from gi.repository import GLib  # noqa: E402

import fake_engines  # noqa: E402
from scriba.app.config import Config  # noqa: E402
from scriba.app.model_downloads import ModelDownloads  # noqa: E402
from scriba.app.task import Failure  # noqa: E402
from scriba.gui import ui  # noqa: E402
from scriba.gui.cards import CardTest  # noqa: E402
from scriba.gui.transcribe import TranscribePage  # noqa: E402


class Coordinator:
    def __init__(self, backend, folder):
        self.cfg = Config(measured_device="cpu", output_folder=str(folder))
        self.closing = self.installing = False
        self.saved = 0
        self.errors = []
        self.window = None
        self.downloads = ModelDownloads(backend, self.cfg, self.save_config, dispatch=GLib.idle_add)

    @property
    def work_busy(self):
        page = getattr(self.window, "page", None)
        return bool(page and page.busy)

    def save_config(self):
        self.saved += 1

    def block_exit(self, error):
        self.errors.append(error)
        self.closing = False

    def get_is_registered(self):
        return False


class Window:
    def __init__(self, app, backend):
        self.app, self.backend = app, backend
        app.window = self
        self.messages, self.results = [], []
        self.changes = 0
        self.cards = CardTest(app, backend)
        self.page = TranscribePage(self)

    def page_changed(self):
        self.changes += 1

    def toast(self, message):
        self.messages.append(message)

    def show_result(self, result, request):
        self.results.append((result, request))

    def is_active(self):
        return True


class TranscribeLifecycleTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.backend = fake_engines.FakeBackend(self.folder)
        self.app = Coordinator(self.backend, self.folder)
        self.window = Window(self.app, self.backend)
        self.page = self.window.page
        self.source = self.folder / "sample.wav"
        self.source.write_bytes(b"fixture inspected by the controlled backend")
        self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
        self.addCleanup(self.clean_up)

    def clean_up(self):
        self.app.closing = True
        self.page.stop_all()
        self.app.downloads.shutdown()
        self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))

    def barrier(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        return entered, release

    def test_card_cancel_keeps_ownership_and_discards_a_late_success(self):
        entered, release = self.barrier()
        self.app.cfg.measured_device = ""

        def work(cancelled):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("card test not released")
            return self.backend.choice

        with mock.patch.object(self.backend, "try_devices", side_effect=work):
            self.assertTrue(self.window.cards.start())
            self.assertTrue(entered.wait(3))
            owner = self.window.cards.running
            self.window.cards.stop()
            self.assertIs(self.window.cards.running, owner)
            self.assertTrue(owner.is_set())
            self.assertFalse(self.window.cards.start())
            self.app.closing = True
            release.set()
            self.assertTrue(_setup.spin(lambda: self.window.cards.running is None))
        self.assertEqual(self.app.cfg.measured_device, "")
        self.assertEqual(self.app.saved, 0)

    def test_closing_prevents_explicit_and_automatic_new_work(self):
        self.app.closing = True
        self.app.cfg.measured_device = ""
        with mock.patch.object(self.backend, "try_devices", return_value=self.backend.choice) as gpu, \
                mock.patch.object(self.backend, "inspect", return_value=self.backend.media) as inspect, \
                mock.patch.object(self.backend, "run", return_value=object()) as run:
            self.window.cards.start_if_needed()
            self.assertFalse(self.window.cards.start())
            self.page.open(str(self.source))
            self.page.media, self.page.source = self.backend.media, self.source
            self.page.start()
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
            gpu.assert_not_called()
            inspect.assert_not_called()
            run.assert_not_called()

    def test_shutdown_cancels_inspection_and_discards_its_late_result(self):
        entered, release = self.barrier()
        cancellations = []

        def inspect(_source, cancelled=None):
            cancellations.append(cancelled)
            entered.set()
            if not release.wait(3):
                raise TimeoutError("inspection not released")
            return self.backend.media

        with mock.patch.object(self.backend, "inspect", side_effect=inspect):
            self.page.open(str(self.source))
            self.assertTrue(entered.wait(3))
            self.app.closing = True
            self.page.stop_all()
            self.assertIsNotNone(cancellations[0])
            self.assertTrue(cancellations[0].is_set())
            self.assertGreater(ui.activities.active_count, 0)
            release.set()
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
        self.assertIsNone(self.page.media)

    def test_finished_job_during_shutdown_does_not_navigate_notify_or_restart_gpu(self):
        entered, release = self.barrier()
        self.page.open(str(self.source))
        self.assertTrue(_setup.spin(lambda: self.page.media is not None))

        def work(_request, _progress, _cancelled):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("transcription not released")
            return object()

        self.backend.runner = work
        with mock.patch.object(self.page, "_tell") as notify, \
                mock.patch.object(self.window.cards, "start_if_needed") as restart:
            self.page.start()
            self.assertTrue(entered.wait(3))
            self.app.closing = True
            self.page.stop_all()
            release.set()
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
            notify.assert_not_called()
            restart.assert_not_called()
        self.assertFalse(self.page.busy)
        self.assertEqual(self.page._tick_id, 0)
        self.assertEqual(self.window.results, [])

    def test_failure_during_shutdown_is_reported_and_blocks_silent_exit(self):
        self.page.open(str(self.source))
        self.assertTrue(_setup.spin(lambda: self.page.media is not None))
        progress, cancel = object(), threading.Event()
        self.page.job = (progress, cancel, 0, 0, self.page.request())
        self.app.closing = True
        error = Failure("the transcript could not be saved", "disk full")
        with contextlib.redirect_stderr(io.StringIO()):
            self.page._finished(progress, None, error)
        self.assertFalse(self.page.busy)
        self.assertEqual(self.app.errors, [error])
        self.assertEqual(self.window.results, [])

    def test_aborted_shutdown_keeps_model_updates_connected(self):
        self.page._was_missing = True
        self.app.closing = True
        self.page.stop_all()
        self.app.closing = False
        self.app.downloads.start_required()
        self.assertTrue(_setup.spin(lambda: any("models are ready" in text for text in self.window.messages)))

    def test_worker_launch_failure_clears_the_job_and_its_timer(self):
        self.page.open(str(self.source))
        self.assertTrue(_setup.spin(lambda: self.page.media is not None))
        with mock.patch("scriba.gui.transcribe.run_in_thread", side_effect=RuntimeError("cannot start worker")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.page.start()
        self.assertFalse(self.page.busy)
        self.assertEqual(self.page._tick_id, 0)
        self.assertEqual(self.page.stack.get_visible_child_name(), "failed")

    def test_transcription_waits_for_cancelled_gpu_worker_and_starts_once(self):
        entered, release = self.barrier()
        order = []
        self.page.open(str(self.source))
        self.assertTrue(_setup.spin(lambda: self.page.media is not None))

        def gpu(_cancelled):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("GPU cleanup not released")
            order.append("gpu finished")
            return self.backend.choice

        def transcribe(*_args):
            order.append("transcription started")
            return object()

        self.backend.runner = transcribe
        with mock.patch.object(self.backend, "try_devices", side_effect=gpu):
            self.assertTrue(self.window.cards.start())
            self.assertTrue(entered.wait(3))
            self.page.start()
            self.page.start()  # a repeated click cannot queue another job
            self.assertTrue(self.page.busy)
            self.assertIsNone(self.page.job)
            release.set()
            self.assertTrue(_setup.spin(lambda: len(self.window.results) == 1))
        self.assertEqual(order, ["gpu finished", "transcription started"])

    def test_closing_discards_transcription_waiting_for_gpu_cleanup(self):
        self._pending_start_is_discarded("close")

    def test_new_source_discards_transcription_waiting_for_gpu_cleanup(self):
        self._pending_start_is_discarded("source")

    def test_cancel_discards_transcription_waiting_for_gpu_cleanup(self):
        self._pending_start_is_discarded("cancel")

    def _pending_start_is_discarded(self, action):
        entered, release = self.barrier()
        started = []
        self.page.open(str(self.source))
        self.assertTrue(_setup.spin(lambda: self.page.media is not None))

        def gpu(_cancelled):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("GPU cleanup not released")
            return self.backend.choice

        self.backend.runner = lambda *_: started.append(True) or object()
        with mock.patch.object(self.backend, "try_devices", side_effect=gpu):
            self.window.cards.start()
            self.assertTrue(entered.wait(3))
            self.page.start()
            if action == "close":
                self.app.closing = True
                self.page.stop_all()
            elif action == "source":
                replacement = self.folder / "replacement.wav"
                replacement.write_bytes(b"another fixture")
                self.page.open(str(replacement))
                self.assertEqual(self.page.source, replacement)
            else:
                self.page.cancel()
            release.set()
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
        self.assertEqual(started, [])
        self.assertFalse(self.page.busy)
