"""Explicit exit waits for actual workers and preserves failures for review."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import _setup

_setup.gtk_or_skip()

import fake_engines  # noqa: E402
from test_window import TestApp  # noqa: E402
from scriba.gui import ui  # noqa: E402


class ExitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.backend = fake_engines.FakeBackend(Path(self.tmp))
        self.app = TestApp(self.tmp, self.backend)
        self.app.cfg.measured_device = "cpu"
        self.window = self.app._window()
        self.window.present = Mock()
        self.app.quit = Mock()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count and not self.app.downloads.busy))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.downloads.shutdown()
        self.assertTrue(_setup.spin(lambda: not self.app.downloads.busy and not ui.activities.active_count))
        self.window.destroy()

    def test_explicit_exit_waits_for_a_write_and_its_callback(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        finished = []

        def write():
            entered.set()
            release.wait(3)
            return "saved"
        ui.run_in_thread(write, lambda result, error: finished.append(result))
        self.assertTrue(entered.wait(1))
        with patch.object(self.app.downloads, "shutdown", wraps=self.app.downloads.shutdown) as stop_models:
            self.app.request_quit()
            self.assertTrue(self.app.closing)
            self.app.quit.assert_not_called()
            stop_models.assert_not_called()
            release.set()
            self.assertTrue(_setup.spin(lambda: self.app.exit_ready))
        self.assertEqual(finished, ["saved"])
        self.app.quit.assert_called_once()

    def test_failed_write_blocks_exit_before_models_are_shut_down(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def write():
            entered.set()
            release.wait(3)
            raise OSError("disk full")

        def done(_result, error):
            self.app.block_exit(error)
        ui.run_in_thread(write, done)
        self.assertTrue(entered.wait(1))
        with patch.object(self.app, "_show_close_error") as message, patch.object(self.app.downloads, "shutdown") as stop_models:
            self.app.request_quit()
            release.set()
            self.assertTrue(_setup.spin(lambda: not ui.activities.active_count))
            self.assertFalse(self.app.closing)
            self.app.quit.assert_not_called()
            stop_models.assert_not_called()
            message.assert_called_once()
        self.window.present.assert_called_once()

    def test_export_failure_after_switching_results_keeps_the_app_open(self):
        from scriba.app import jobs
        source = Path(self.tmp) / "call.wav"
        source.write_bytes(b"fixture")
        first = fake_engines.made_result(Path(self.tmp) / "first", source, fake_engines.lines(2, 4, 0))
        second = fake_engines.made_result(Path(self.tmp) / "second", source, fake_engines.lines(2, 4, 0))
        page = self.window.result
        self.window.show_result(first, jobs.Request(source))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def write(*_args):
            entered.set()
            release.wait(3)
            raise OSError("disk full")
        with patch.object(self.backend, "save_names", write), patch.object(self.app, "_show_close_error") as message:
            page.save_names()
            self.assertTrue(entered.wait(1))
            page.show(second, jobs.Request(source))
            self.assertTrue(page.busy)
            self.app.request_quit()
            release.set()
            self.assertTrue(_setup.spin(lambda: not ui.activities.active_count))
            self.assertFalse(self.app.closing)
            self.assertFalse(page.busy)
            self.app.quit.assert_not_called()
            message.assert_called_once()
        self.assertTrue(first.session.is_file())

    def test_close_hides_only_when_the_background_is_reachable(self):
        background = self.app.background = Mock()
        background.hide.return_value = True
        self.app.cfg.keep_running = True
        with patch.object(self.app, "request_quit") as quit:
            self.assertTrue(self.window._on_close())
            background.hide.assert_called_once()
            quit.assert_not_called()
            background.hide.return_value = False
            self.assertTrue(self.window._on_close())
            quit.assert_called_once()
