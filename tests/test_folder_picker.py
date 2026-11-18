"""Windows folder selection keeps ownership and cancellation in Scriba's
windows. The native picker itself (Unicode paths, COM calls) is the shared
module's test: NativeFolderTest in test_win32.py, a copy of the model's."""

import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest import mock

import _setup

from scriba.app import win32


Gtk, Adw = _setup.gtk_or_skip()
from scriba.gui import ui  # noqa: E402


@unittest.skipUnless(_setup.WINDOWS, "Windows folder picker integration")
class FolderOwnerTest(unittest.TestCase):
    def setUp(self):
        self.native = mock.Mock()
        self.native.get_mapped.return_value = True
        self.owner = mock.Mock()
        self.owner.get_native.return_value = self.native
        self.owner.get_mapped.return_value = True
        self.window = SimpleNamespace(app=SimpleNamespace(closing=False), toast=mock.Mock())
        self.chosen = mock.Mock()
        self.workers = []
        self.callbacks = []
        self.patches = [
            mock.patch("scriba.gui.winquirks.hwnd_of", return_value=1234),
            mock.patch.object(ui, "run_in_thread", side_effect=lambda work, done:
                              (self.workers.append(work), self.callbacks.append(done))),
            mock.patch("scriba.app.win32.choose_folder_native", return_value="C:/caffè"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def pick(self):
        ui.choose_folder(self.window, "Choose", "C:/caffè", self.chosen, owner_widget=self.owner)

    def complete(self, result=None, error=None):
        self.callbacks[-1](result, error)

    def test_native_owner_is_the_preferences_window_and_one_picker_is_pending(self):
        self.pick()
        self.pick()
        self.assertEqual(len(self.workers), 1)
        self.workers[0]()
        win32.choose_folder_native.assert_called_once()
        self.assertEqual(win32.choose_folder_native.call_args.args[:3], ("Choose", "C:/caffè", 1234))
        self.complete("C:/caffè")
        self.chosen.assert_called_once_with("C:/caffè")
        self.pick()
        self.assertEqual(len(self.workers), 2)
        self.complete()

    def test_closing_cancels_pending_picker_and_ignores_late_selection(self):
        self.pick()
        self.workers[0]()
        event = win32.choose_folder_native.call_args.args[3]
        self.window.app.closing = True
        self.assertTrue(_setup.spin(event.is_set, timeout=1))
        self.complete("C:/late")
        self.chosen.assert_not_called()

    def test_closing_preferences_cancels_its_picker(self):
        self.pick()
        self.workers[0]()
        event = win32.choose_folder_native.call_args.args[3]
        self.owner.get_mapped.return_value = False
        self.assertTrue(_setup.spin(event.is_set, timeout=1))
        self.complete()
        self.chosen.assert_not_called()

    def test_a_missing_native_owner_reports_an_error_without_starting_work(self):
        from scriba.gui import winquirks
        with mock.patch.object(winquirks, "hwnd_of", return_value=0), \
                contextlib.redirect_stderr(io.StringIO()):
            self.pick()
        self.assertEqual(self.workers, [])
        self.window.toast.assert_called_once()

    def test_real_errors_are_reported_and_cancellation_is_silent(self):
        self.pick()
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.complete(error=OSError("Access denied"))
        self.assertIn("Access denied", stderr.getvalue())
        self.window.toast.assert_called_once()
        self.pick()
        self.complete()
        self.window.toast.assert_called_once()


@unittest.skipUnless(_setup.WINDOWS, "Windows GDK native surface")
class SurfaceHandleTest(unittest.TestCase):
    def test_frozen_fallback_gets_the_exact_surface_without_a_win32_typelib(self):
        import gi
        from scriba.gui import winquirks
        window = Gtk.Window(title="Repeated Preferences title")
        other = Gtk.Window(title=window.get_title())
        try:
            window.realize()
            other.realize()
            with mock.patch.object(gi, "require_version", side_effect=ValueError("typelib omitted")):
                handle = winquirks.hwnd_of(window)
                other_handle = winquirks.hwnd_of(other)
            self.assertGreater(handle, 0)
            self.assertGreater(other_handle, 0)
            self.assertNotEqual(handle, other_handle)
        finally:
            window.destroy()
            other.destroy()
