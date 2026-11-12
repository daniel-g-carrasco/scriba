"""Windows folder selection keeps Unicode paths, ownership and cancellation."""

import contextlib
import ctypes
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _setup

from scriba.app import win32


@unittest.skipUnless(_setup.WINDOWS, "Windows COM folder picker")
class NativeFolderTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.cancelled = threading.Event()
        self.answer = 0
        self.name = ctypes.create_unicode_buffer("C:\\Call con spazi e caffè")
        self.ole = mock.Mock()
        self.shell = mock.Mock()
        self.user = mock.Mock()
        self.ole.CoInitializeEx.return_value = 0
        self.ole.CoCreateInstance.side_effect = lambda *args: self.pointer(args[-1], 100)
        self.shell.SHCreateItemFromParsingName.side_effect = lambda *args: self.pointer(args[-1], 200)
        self.user.SetTimer.side_effect = self.timer
        self.user.PostMessageW.return_value = True
        self.timer_callback = None
        self.cancel_during_show = False
        self.bad_result = False
        self.fail_slot = None
        self.patch_dll = mock.patch.object(ctypes, "WinDLL", side_effect=lambda name, **_kw: {
            "ole32": self.ole, "shell32": self.shell, "user32": self.user}[name])
        self.patch_method = mock.patch.object(win32, "_com_method", side_effect=self.method)
        self.patch_dll.start()
        self.patch_method.start()
        self.addCleanup(self.patch_dll.stop)
        self.addCleanup(self.patch_method.stop)

    @staticmethod
    def pointer(output, value):
        ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = value
        return 0

    def timer(self, _owner, _id, _interval, callback):
        self.timer_callback = callback
        return 77

    def method(self, pointer, slot, *_types, **_kwargs):
        pointer = pointer.value
        def invoke(_self, *args):
            self.calls.append((pointer, slot, args))
            if self.fail_slot == slot:
                return -2147024891  # E_ACCESSDENIED
            if slot == 0:
                return self.pointer(args[-1], 400)  # IOleWindow of this dialog
            if slot == 10:
                ctypes.cast(args[0], ctypes.POINTER(ctypes.c_uint32))[0] = 0
            if slot == 3:
                if pointer == 400:
                    return self.pointer(args[0], 987654)
                if self.cancel_during_show:
                    self.cancelled.set()
                    self.timer_callback(None, 0, 77, 0)
                    self.timer_callback(None, 0, 77, 0)
                    return -2147023673  # HRESULT_FROM_WIN32(ERROR_CANCELLED)
                return self.answer
            if slot == 20:
                return self.pointer(args[0], 0 if self.bad_result else 300)
            if slot == 5:
                return self.pointer(args[-1], ctypes.addressof(self.name))
            return 0
        return invoke

    def pick(self, initial=None):
        return win32.choose_folder_native("Choose", initial, 123456, self.cancelled)

    def test_unicode_native_initial_path_and_selected_path_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "spazi e caffè"
            folder.mkdir()
            answer = self.pick(str(folder))
            self.assertEqual(self.shell.SHCreateItemFromParsingName.call_args.args[0], str(folder))
        self.assertEqual(answer, self.name.value)
        shown = [args for pointer, slot, args in self.calls if pointer == 100 and slot == 3]
        self.assertEqual(shown, [(123456,)])
        released = [pointer for pointer, slot, _args in self.calls if slot == 2]
        self.assertCountEqual(released, [100, 200, 300, 400])
        self.ole.CoTaskMemFree.assert_called_once()
        self.ole.CoUninitialize.assert_called_once()
        self.user.KillTimer.assert_called_once_with(None, 77)

    def test_cancelled_show_returns_none_without_reading_a_result(self):
        self.answer = -2147023673
        self.assertIsNone(self.pick())
        self.assertNotIn(20, [slot for _pointer, slot, _args in self.calls])
        self.ole.CoUninitialize.assert_called_once()

    def test_timer_closes_only_the_dialogs_own_ole_window_on_its_sta(self):
        self.cancel_during_show = True
        self.assertIsNone(self.pick())
        self.user.PostMessageW.assert_called_once()
        handle, message, wparam, lparam = self.user.PostMessageW.call_args.args
        self.assertEqual((handle.value, message, wparam, lparam), (987654, 0x10, 0, 0))
        self.assertNotIn(23, [slot for _pointer, slot, _args in self.calls])

    def test_cancellation_before_show_never_opens_a_dialog(self):
        self.cancelled.set()
        self.assertIsNone(self.pick())
        self.assertNotIn(3, [slot for pointer, slot, _args in self.calls if pointer == 100])
        self.user.SetTimer.assert_not_called()

    def test_shell_failure_and_null_result_are_errors_with_resources_released(self):
        self.fail_slot = 12
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                self.pick(temp)
        self.ole.CoUninitialize.assert_called_once()
        self.fail_slot = None
        self.bad_result = True
        with self.assertRaisesRegex(OSError, "folder"):
            self.pick()

    def test_a_missing_initial_folder_uses_the_shells_default_without_fabricating_an_item(self):
        with tempfile.TemporaryDirectory() as temp:
            self.pick(str(Path(temp) / "missing caffè"))
        self.shell.SHCreateItemFromParsingName.assert_not_called()

    def test_failure_to_register_cancellation_does_not_show_an_unclosable_dialog(self):
        self.user.SetTimer.side_effect = None
        self.user.SetTimer.return_value = 0
        with self.assertRaises(OSError):
            self.pick()
        self.assertNotIn(3, [slot for pointer, slot, _args in self.calls if pointer == 100])
        self.ole.CoUninitialize.assert_called_once()

    def test_com_initialization_failure_is_not_uninitialized(self):
        self.ole.CoInitializeEx.return_value = -2147024891
        with self.assertRaises(OSError):
            self.pick()
        self.ole.CoUninitialize.assert_not_called()


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
