#!/usr/bin/env python3
"""Windows only: the files that run when opened, the toasts of the
Windows Runtime (their text, the COM object that hears a click, the
notifier)."""

import ctypes
import unittest
from unittest import mock

import _setup

if not _setup.WINDOWS:
    raise unittest.SkipTest("Windows only")

from scriba.app import win32, wintoast  # noqa: E402


class RunsWhenOpenedTest(unittest.TestCase):
    def test_programs_scripts_and_shortcuts_are_told_apart(self):
        for name in ("setup.exe", "Fattura.pdf.exe", "avvio.LNK", "x.bat", "x.cmd", "x.ps1", "x.vbs",
                     "x.js", "x.hta", "x.scr", "x.msi", "x.reg", "x.url"):
            self.assertTrue(win32.runs_when_opened(rf"C:\tmp\{name}"), name)
        for name in ("relazione.pdf", "tavola.dwg", "foglio.xlsx", "note.txt", "foto.jpg", "senza-estensione"):
            self.assertFalse(win32.runs_when_opened(rf"C:\tmp\{name}"), name)


class TitleBarTest(unittest.TestCase):
    def test_a_colour_as_windows_writes_it(self):
        self.assertEqual(win32.colorref((250, 251, 252)), 0x00FCFBFA)       # 0x00BBGGRR
        self.assertEqual(win32.colorref((34, 34, 38)), 0x00262222)
        self.assertEqual(win32.colorref((300, -5, 12.6)), 0x000C00FF)       # held within a byte

    def test_the_bars_of_a_process_without_windows(self):
        win32.title_bars(True, (34, 34, 38), (255, 255, 255))               # nothing to paint, nothing raised
        win32.title_bars(False)


class ScreenDpiTest(unittest.TestCase):
    def test_no_window_has_no_dpi(self):
        self.assertEqual(win32.window_dpi(0), 0)

    def test_the_desktops_dpi_is_a_screens(self):
        user32 = ctypes.WinDLL("user32")
        user32.GetDesktopWindow.restype = ctypes.c_void_p
        self.assertGreaterEqual(win32.window_dpi(user32.GetDesktopWindow()), 96)

    def test_the_awareness_is_read_without_being_changed(self):
        self.assertIsInstance(win32.per_monitor_aware(), bool)


class TaskbarThemeTest(unittest.TestCase):
    """The taskbar's colour from the registry, with a stand-in for winreg:
    the user's own settings are never read in a test (and never written)."""

    def read(self, value=None, missing=False):
        import winreg
        opened = []

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        def open_key(root, path):
            opened.append((root, path))
            if missing:
                raise FileNotFoundError(path)
            return Key()

        def query(_key, name):
            self.assertEqual(name, "SystemUsesLightTheme")
            if value is None:
                raise FileNotFoundError(name)
            return value, winreg.REG_DWORD

        with mock.patch.object(winreg, "OpenKey", open_key), mock.patch.object(winreg, "QueryValueEx", query):
            dark = win32.taskbar_dark()
        self.assertEqual(opened, [(winreg.HKEY_CURRENT_USER, win32.PERSONALIZE)])
        return dark

    def test_zero_is_a_dark_taskbar_one_a_light_one(self):
        self.assertTrue(self.read(0))
        self.assertFalse(self.read(1))

    def test_without_the_value_the_taskbar_is_dark_as_before_windows_10_1903(self):
        self.assertTrue(self.read(None))
        self.assertTrue(self.read(missing=True))


def vtable_function(obj, slot, prototype):
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return prototype(vtable[slot])


class ToastTest(unittest.TestCase):
    def test_text_is_escaped(self):
        xml = wintoast.toast_xml("“A & B” <ok>", "x > y")
        self.assertIn("<text>“A &amp; B” &lt;ok&gt;</text>", xml)
        self.assertIn("<text>x &gt; y</text>", xml)
        self.assertTrue(xml.startswith("<toast>") and xml.endswith("</toast>"))

    def test_an_emoji_counts_two_utf16_units(self):
        self.assertEqual(wintoast.utf16_length("ab"), 2)
        self.assertEqual(wintoast.utf16_length("a😀"), 3)

    def test_the_click_handler_is_a_com_object(self):
        clicked = []
        handler = wintoast._ActivatedHandler(lambda: clicked.append(1))
        query = vtable_function(handler.pointer, 0, wintoast._QI)
        out = ctypes.c_void_p()
        for iid in (wintoast.IID_ActivatedHandler, wintoast.IID_IUnknown, wintoast.IID_IAgileObject):
            self.assertEqual(query(handler.pointer, ctypes.byref(iid), ctypes.byref(out)), wintoast.S_OK)
            self.assertEqual(out.value, handler.pointer.value)
        other = wintoast._guid("11111111-2222-3333-4444-555555555555")
        self.assertEqual(query(handler.pointer, ctypes.byref(other), ctypes.byref(out)), wintoast.E_NOINTERFACE)
        invoke = vtable_function(handler.pointer, 3, wintoast._INVOKE)
        self.assertEqual(invoke(handler.pointer, None, None), wintoast.S_OK)
        self.assertEqual(clicked, [1])

    def test_a_failing_callback_stays_on_this_side(self):
        handler = wintoast._ActivatedHandler(lambda: 1 / 0)
        invoke = vtable_function(handler.pointer, 3, wintoast._INVOKE)
        self.assertEqual(invoke(handler.pointer, None, None), wintoast.S_OK)

    def test_the_notifier_of_the_windows_runtime(self):
        toasts = wintoast.Toasts("io.example.scriba-test", "scriba-test")
        self.assertTrue(toasts._notifier)
        self.assertTrue(toasts._toast_factory)

    def test_only_the_latest_toasts_are_kept(self):
        # real toasts and click handlers, never shown: the notifier's Show
        # alone is left out
        toasts = wintoast.Toasts("io.example.scriba-test", "scriba-test")
        real = wintoast._call

        def call(obj, slot, *args, **kwargs):
            if obj is toasts._notifier and slot == wintoast.NOTIFIER_SHOW:
                return None
            return real(obj, slot, *args, **kwargs)

        with mock.patch.object(wintoast, "_call", call):
            for i in range(wintoast.KEPT_TOASTS + 5):
                toasts.show(f"Toast {i}", "body", tag=f"t{i}", on_click=lambda: None)
        self.assertEqual(len(toasts._kept), wintoast.KEPT_TOASTS)
        self.assertTrue(all(handler is not None for _toast, handler, _token in toasts._kept))


if __name__ == "__main__":
    unittest.main()
