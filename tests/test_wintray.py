#!/usr/bin/env python3
"""The icon's window on Windows: how Scriba answers Windows when it is asked
to quit (Setup before an update, sign-out, shutdown)."""

import sys
import time
import unittest

import _setup  # noqa: F401

if sys.platform != "win32":
    raise unittest.SkipTest("the notification area of Windows")

import locale  # noqa: E402

from gi.repository import GLib  # noqa: E402

_before = locale.setlocale(locale.LC_ALL)
from scriba.gui import wintray  # noqa: E402
# GTK, initialised on import, sets the user's locale: the other tests read
# numbers written as in the C locale ("2000", not "2 000")
locale.setlocale(locale.LC_ALL, _before)


class QuitTest(unittest.TestCase):
    def setUp(self):
        self.asked, self.done = [], []

        def on_quit():
            self.asked.append(1)
            # the quit is over a moment later, as when rclone saves its state
            GLib.timeout_add(300, lambda: self.done.append(1) and False)

        self.tray = wintray.WinTray("io.example.test", "Test", on_activate=lambda: None,
                                    on_quit=on_quit, has_quit=lambda: bool(self.done))

    def test_a_sign_out_is_never_refused(self):
        self.assertEqual(self.tray._window_proc(None, wintray.WM_QUERYENDSESSION, 0, 1), 1)
        self.assertEqual(self.asked, [])

    def test_the_end_of_the_session_is_answered_once_the_quit_is_over(self):
        start = time.monotonic()
        self.assertEqual(self.tray._window_proc(None, wintray.WM_ENDSESSION, 1, 1), 0)
        self.assertEqual(self.asked, [1])
        self.assertEqual(self.done, [1])
        self.assertGreaterEqual(time.monotonic() - start, 0.25)
        # a sign-out cancelled by the user (wParam FALSE): nothing to do
        self.assertEqual(self.tray._window_proc(None, wintray.WM_ENDSESSION, 0, 0), 0)
        self.assertEqual(self.asked, [1])

    def test_close_starts_the_quit_from_the_main_loop(self):
        self.assertEqual(self.tray._window_proc(None, wintray.WM_CLOSE, 0, 0), 0)
        self.assertEqual(self.asked, [])
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while not self.asked and time.monotonic() < deadline:
            context.iteration(False)
        self.assertEqual(self.asked, [1])


class SleepTest(unittest.TestCase):
    def test_a_sleep_stops_the_sync_and_the_wake_resumes_it(self):
        slept, woke = [], []
        tray = wintray.WinTray("io.example.test", "Test", on_activate=lambda: None,
                               on_suspend=lambda: slept.append(1), on_resume=lambda: woke.append(1))
        self.assertEqual(tray._window_proc(None, wintray.WM_POWERBROADCAST, wintray.PBT_APMSUSPEND, 0), 1)
        self.assertEqual(slept, [1])                    # at once: Windows sleeps soon after the answer
        self.assertEqual(tray._window_proc(None, wintray.WM_POWERBROADCAST, wintray.PBT_APMRESUMEAUTOMATIC, 0), 1)
        self.assertEqual(woke, [])                      # from the main loop
        context = GLib.MainContext.default()
        deadline = time.monotonic() + 5
        while not woke and time.monotonic() < deadline:
            context.iteration(False)
        self.assertEqual(woke, [1])
        # the power source or the battery changed: nothing to do
        self.assertEqual(tray._window_proc(None, wintray.WM_POWERBROADCAST, 0x000A, 0), 1)
        self.assertEqual((slept, woke), ([1], [1]))


class AvailabilityTest(unittest.TestCase):
    def test_missing_icon_does_not_allow_hiding(self):
        from unittest.mock import patch
        changes = []
        tray = wintray.WinTray("io.example.test", "Test", lambda: None,
                              on_availability=changes.append)
        tray._hwnd = 1
        with patch.object(tray, "_icon", return_value=0), patch.object(wintray.shell32, "Shell_NotifyIconW") as shell:
            tray._add()
        self.assertFalse(tray.available)
        shell.assert_not_called()
        self.assertEqual(changes, [])

    def test_explorer_restart_failure_is_reported(self):
        from unittest.mock import patch
        changes = []
        tray = wintray.WinTray("io.example.test", "Test", lambda: None,
                              on_availability=changes.append)
        tray._hwnd = 1
        with patch.object(tray, "_icon", return_value=2), patch.object(wintray.shell32, "Shell_NotifyIconW", return_value=1):
            tray._add()
        self.assertTrue(tray.available)
        with patch.object(tray, "_icon", return_value=2), patch.object(wintray.shell32, "Shell_NotifyIconW", return_value=0):
            tray._window_proc(None, tray._taskbar_created, 0, 0)
        self.assertFalse(tray.available)
        self.assertEqual(changes, [True, False])


class TaskbarThemeTest(unittest.TestCase):
    """The icon for a dark taskbar, with the taskbar's colour from a stand-in
    (never the registry)."""

    def tray(self, dark, heard=None):
        from unittest.mock import patch
        tray = wintray.WinTray("io.example.test", "Test", lambda: None, taskbar_dark=lambda: dark[0],
                               on_taskbar_theme=heard.append if heard is not None else None)
        tray.dark = tray._read_dark()
        tray._added = True
        asked = []
        patcher = patch.object(tray, "_icon", side_effect=lambda name: asked.append(name) or 7)
        patcher.start()
        self.addCleanup(patcher.stop)
        return tray, asked

    def drain(self, seconds=0.3):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not context.iteration(False):
                time.sleep(0.01)

    def test_the_variant_on_a_dark_taskbar_the_normal_icon_on_a_light_one(self):
        dark, asked_dark = self.tray([True])
        light, asked_light = self.tray([False])
        for tray in (dark, light):
            tray.icon_name = "io.example.test-recording"
            tray._data(wintray.NIF_ICON)
        self.assertEqual(asked_dark, ["io.example.test-recording-on-dark"])
        self.assertEqual(asked_light, ["io.example.test-recording"])

    def test_a_change_of_the_windows_mode_changes_the_icon(self):
        import ctypes
        from unittest.mock import patch
        dark, heard = [False], []
        tray, asked = self.tray(dark, heard)
        setting = ctypes.create_unicode_buffer(wintray.COLOUR_SET)
        dark[0] = True
        with patch.object(wintray.shell32, "Shell_NotifyIconW", return_value=1) as shell:
            tray._window_proc(None, wintray.WM_SETTINGCHANGE, 0, ctypes.addressof(setting))
            self.assertEqual(heard, [])                 # from the main loop
            self.drain()
            self.assertEqual(heard, [True])
            self.assertEqual(asked, ["io.example.test-on-dark"])
            self.assertEqual(shell.call_args[0][0], wintray.NIM_MODIFY)
            # another setting, no setting, or the same mode again: nothing
            other = ctypes.create_unicode_buffer("Environment")
            for lparam in (ctypes.addressof(other), 0, ctypes.addressof(setting)):
                tray._window_proc(None, wintray.WM_SETTINGCHANGE, 0, lparam)
            self.drain()
            self.assertEqual(heard, [True])
            self.assertEqual(shell.call_count, 1)

    def test_without_the_variant_the_normal_icon(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            tray = wintray.WinTray("io.github.daniel_g_carrasco.scriba", "Scriba", lambda: None,
                                   icon_theme_path=str(_setup.ROOT / "data" / "icons"), cache=Path(tmp))
            self.assertTrue(tray._icon("io.github.daniel_g_carrasco.scriba-recording-on-dark"))
            self.assertTrue((Path(tmp) / "io.github.daniel_g_carrasco.scriba-recording-on-dark.ico").is_file())
            # a state without its variant (from another version of the app):
            # an icon all the same, never none
            self.assertTrue(tray._icon("io.github.daniel_g_carrasco.scriba-nothing-on-dark"))
            tray.stop()


class WindowIconTest(unittest.TestCase):
    def test_the_taskbar_button_follows_the_taskbar_the_title_bar_the_window(self):
        # the big icon is the taskbar button's (measured: see win32.window_icons),
        # the small one the title bar's, whose colour is the content's
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from scriba.app import win32
        from scriba.gui import application
        from gi.repository import Adw
        Adw.init()
        content_dark = Adw.StyleManager.get_default().get_dark()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(win32, "title_bars"), patch.object(win32, "window_icons") as icons:
            for taskbar in (True, False):
                application.native_windows(Path(tmp), taskbar_dark=lambda t=taskbar: t)()
                _exe, small, big = icons.call_args[0]
                self.assertEqual(big.endswith("scriba-on-dark.ico"), taskbar)
                self.assertTrue(big.endswith(".ico"))   # a run from the sources: drawn from the SVG
                self.assertEqual(small.endswith("-on-dark.ico"), content_dark)


class FullDiscTest(unittest.TestCase):
    def test_the_notification_areas_icon_is_full_to_the_edge_at_32_pixels(self):
        # the SVG keeps the theme's margin for Linux; Windows' .ico is drawn without it
        import struct
        import tempfile
        from pathlib import Path
        from gi.repository import GdkPixbuf
        from test_icons import drawn
        svg = _setup.ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.daniel_g_carrasco.scriba-recording-on-dark.svg"
        with tempfile.TemporaryDirectory() as tmp:
            ico = Path(tmp) / "icon.ico"
            wintray.ico_from_svg(str(svg), ico)
            data = ico.read_bytes()
        count = struct.unpack_from("<H", data, 4)[0]
        for n in range(count):
            width, _h, _c, _r, _p, _b, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * n)
            if width == 32:
                loader = GdkPixbuf.PixbufLoader()
                loader.write(data[offset:offset + length])
                loader.close()
                self.assertEqual(drawn(loader.get_pixbuf()), (32, 32))
                return
        self.fail("no 32-pixel image in the .ico")


class IconTest(unittest.TestCase):
    def test_a_large_png_for_the_toasts(self):
        import tempfile
        from pathlib import Path
        from gi.repository import GdkPixbuf
        with tempfile.TemporaryDirectory() as tmp:
            tray = wintray.WinTray("io.github.daniel_g_carrasco.scriba", "Scriba", on_activate=lambda: None,
                                   icon_theme_path=str(_setup.ROOT / "data" / "icons"), cache=Path(tmp))
            png = tray._png("io.github.daniel_g_carrasco.scriba", wintray.TOAST_ICON_SIZE)
            self.assertTrue(png.endswith("-256.png"))
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(png)
            self.assertEqual((pixbuf.get_width(), pixbuf.get_height()), (256, 256))

    def test_the_svg_also_when_the_theme_has_pngs_of_the_icon(self):
        # the app's own icon is installed as SVG and as PNGs (64, 128, 256):
        # the theme's lookup returns a PNG, the .ico and the toast's icon
        # are drawn from the SVG
        import shutil
        import tempfile
        from pathlib import Path
        from gi.repository import Gdk, Gtk
        name = "io.example.spola-icon-test"
        source = _setup.ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / "io.github.daniel_g_carrasco.scriba.svg"
        with tempfile.TemporaryDirectory() as tmp:
            for folder in ("scalable", "64x64"):
                (Path(tmp) / "hicolor" / folder / "apps").mkdir(parents=True)
            svg = Path(tmp) / "hicolor" / "scalable" / "apps" / f"{name}.svg"
            shutil.copy(source, svg)
            from gi.repository import GdkPixbuf
            GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), 64, 64).savev(
                str(Path(tmp) / "hicolor" / "64x64" / "apps" / f"{name}.png"), "png", [], [])
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(tmp)
            tray = wintray.WinTray(name, "Test", on_activate=lambda: None, cache=Path(tmp) / "cache")
            self.assertEqual(Path(tray._svg(name)), svg)


if __name__ == "__main__":
    unittest.main()
