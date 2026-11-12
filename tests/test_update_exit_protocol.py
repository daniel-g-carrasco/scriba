"""An installer may close an idle app, but must leave ongoing work untouched."""

import contextlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import _setup

_setup.gtk_or_skip()
from test_window import TestApp  # noqa: E402
from scriba.gui import ui  # noqa: E402


class PrepareUpdateTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.app = TestApp(str(self.folder))
        self.app.cfg.measured_device = "cpu"
        self.window = self.app._window()
        self.app.quit = Mock()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count and not self.app.downloads.busy))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.app.downloads.shutdown()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count and not self.app.downloads.busy))
        self.window.destroy()

    def test_each_busy_owner_refuses_without_cancelling_any_work(self):
        owners = (self.window.record, self.window.transcribe, self.window.result, self.app.downloads)
        with patch.object(self.app, "request_quit") as quit:
            for owner in owners:
                with self.subTest(owner=type(owner).__name__), \
                        patch.object(type(owner), "busy", new_callable=PropertyMock, return_value=True):
                    self.assertEqual(self.app.prepare_update_exit(), 2)
                    self.assertFalse(self.app.closing)
            with patch.object(self.window.cards, "running", threading.Event()):
                self.assertEqual(self.app.prepare_update_exit(), 2)
                self.assertFalse(self.window.cards.running.is_set())
            quit.assert_not_called()

    def test_real_write_worker_is_left_running_and_can_finish_normally(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        saved = []
        target = self.folder / "export.txt"

        def write():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("write barrier was not released")
            target.write_text("saved", encoding="utf-8")
            return target
        ui.run_in_thread(write, lambda value, error: saved.append((value, error)))
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.app.work_busy)
        with patch.object(self.app, "request_quit") as quit:
            self.assertEqual(self.app.prepare_update_exit(), 2)
            quit.assert_not_called()
        self.assertFalse(self.app.closing)
        self.assertFalse(target.exists())
        release.set()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count))
        self.assertEqual(saved, [(target, None)])
        self.assertEqual(target.read_text(encoding="utf-8"), "saved")

    def test_installer_reservation_does_not_prevent_idle_acceptance_or_repeated_requests(self):
        self.app.installing = True
        self.assertEqual(self.app.prepare_update_exit(), 1)
        self.assertTrue(self.app.closing, "acceptance must close the start gate synchronously")
        self.assertFalse(self.app.exit_ready, "completion remains asynchronous")
        self.assertEqual(self.app.prepare_update_exit(), 1)
        self.assertTrue(_setup.spin(lambda: self.app.exit_ready))
        self.assertEqual(self.app.prepare_update_exit(), 1)
        self.app.quit.assert_called_once()

    def test_failure_to_begin_shutdown_is_not_reported_as_accepted(self):
        with patch.object(self.app, "request_quit"):
            self.assertEqual(self.app.prepare_update_exit(), 0)


@unittest.skipUnless(_setup.WINDOWS, "the Windows tray message protocol")
class PrepareUpdateMessageTest(unittest.TestCase):
    def test_registered_message_is_routed_synchronously_with_defined_integer_results(self):
        from scriba.gui import wintray
        callback = Mock(return_value=2)
        with patch.object(wintray.user32, "RegisterWindowMessageW", side_effect=[0xC010, 0xC011]) as register:
            tray = wintray.WinTray("io.example.update", "Test", lambda: None, on_prepare_update=callback)
        register.assert_any_call("io.example.update.prepare-update.v1")
        self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 2)
        callback.assert_called_once_with()
        callback.return_value = 1
        self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 1)
        callback.return_value = None
        self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 0)
        callback.side_effect = RuntimeError("controlled failure")
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 0)
        self.assertIn("controlled failure", errors.getvalue())
        callback.side_effect = KeyboardInterrupt("controlled interruption")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 0)

    def test_logging_failure_cannot_escape_the_native_callback(self):
        from scriba.gui import wintray
        with patch.object(wintray.user32, "RegisterWindowMessageW", side_effect=[0xC010, 0xC011]):
            tray = wintray.WinTray("io.example.update", "Test", lambda: None,
                                  on_prepare_update=Mock(side_effect=RuntimeError("handler failed")))
        with patch.object(wintray, "complain", side_effect=OSError("stderr closed")), \
                patch.object(wintray.kernel32, "OutputDebugStringW") as debug:
            self.assertEqual(tray._window_proc(None, 0xC011, 0, 0), 0)
            self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 0)
        self.assertEqual(debug.call_count, 2)

    def test_message_without_handler_is_unsupported(self):
        from scriba.gui import wintray
        with patch.object(wintray.user32, "RegisterWindowMessageW", side_effect=[0xC010, 0xC011]):
            tray = wintray.WinTray("io.example.update", "Test", lambda: None)
        self.assertEqual(tray._wndproc(None, 0xC011, 0, 0), 0)

    def test_background_connects_the_application_handshake(self):
        from scriba.gui.background import Background
        app = Mock()
        app.paths.cache_folder = Path("unused")
        app.prepare_update_exit.return_value = 2
        with patch("scriba.gui.wintray.WinTray") as tray:
            Background(app)
        callback = tray.call_args.kwargs["on_prepare_update"]
        self.assertEqual(callback(), 2)
        app.prepare_update_exit.assert_called_once_with()
