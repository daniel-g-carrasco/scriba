"""The one models page does not own transfers or prevent recording."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()

import fake_engines  # noqa: E402
from test_window import TestApp  # noqa: E402

from scriba.app import models  # noqa: E402
from scriba.gui.preferences import PreferencesDialog, preference_size  # noqa: E402


class ModelsInterfaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.engines = fake_engines.FakeBackend(Path(self.tmp))
        self.app = TestApp(self.tmp, self.engines)
        self.app.cfg.measured_device = "cpu"
        self.window = self.app._window()
        self.window.result.muted = True
        self.addCleanup(self.close)

    def close(self):
        self.app.downloads.shutdown()
        self.assertTrue(_setup.spin(lambda: not self.app.downloads.busy))
        self.window.destroy()

    def test_automatic_missing_models_leave_record_available_and_share_one_page(self):
        self.engines._missing = list(self.app.downloads.required)
        self.assertEqual(self.window.views.get_visible_child_name(), "record")
        self.assertTrue(_setup.spin(lambda: not self.engines.missing()))
        self.window.show_models()
        page = self.window.nav.get_visible_page()
        self.assertEqual(page.get_tag(), "models")
        self.window.nav.pop()
        self.window.show_models()
        self.assertIs(self.window.nav.get_visible_page(), page)
        self.assertNotIn(models.WHISPER_LARGE, self.app.downloads.required)

    def test_closing_preferences_does_not_cancel_the_application_download(self):
        entered, released = threading.Event(), threading.Event()
        original = self.engines.download

        def download(model, progress, cancelled):
            entered.set()
            released.wait(3)
            self.assertFalse(cancelled.is_set())
            return original(model, progress, cancelled)
        self.addCleanup(released.set)
        with mock.patch.object(self.engines, "download", side_effect=download):
            self.engines._missing = [models.WHISPER_TURBO]
            self.app.downloads.start_required()
            self.assertTrue(entered.wait(1))
            dialog = PreferencesDialog(self.window, self.app)
            dialog.present(self.window)
            dialog.close()
            self.assertTrue(self.app.downloads.busy)
            released.set()
            self.assertTrue(_setup.spin(lambda: not self.app.downloads.busy))
        self.assertFalse(hasattr(dialog, "_download"))
        self.assertFalse(hasattr(dialog, "precise_button"))

    def test_file_choices_stay_accessible_while_required_models_are_missing(self):
        self.engines._missing = list(self.app.downloads.required)
        self.app.cfg.models_download_paused = True
        source = Path(self.tmp) / "recording.wav"
        source.write_bytes(b"fixture")
        self.window.transcribe.open(str(source))
        self.assertTrue(_setup.spin(lambda: self.window.transcribe.media is not None))
        self.assertEqual(self.window.transcribe.stack.get_visible_child_name(), "setup")
        self.assertFalse(self.window.transcribe.start_button.get_sensitive())
        self.window.transcribe.start()
        self.assertFalse(self.window.transcribe.busy)
        self.assertEqual(self.window.nav.get_visible_page().get_tag(), "models")

    def test_precise_quality_stays_visible_and_never_silently_falls_back_to_fast(self):
        source = Path(self.tmp) / "recording.wav"
        source.write_bytes(b"fixture")
        page = self.window.transcribe
        page.open(str(source))
        self.assertTrue(_setup.spin(lambda: page.media is not None))
        self.assertTrue(page.quality_row.get_visible())
        page.quality_row.set_selected(1)
        self.assertEqual(self.app.cfg.quality, "large")
        self.assertFalse(page.start_button.get_sensitive())
        page.start()
        self.assertFalse(page.busy)
        self.assertFalse(self.engines.precise)
        self.window.models.download_precise()
        self.assertTrue(_setup.spin(lambda: self.engines.precise))
        self.assertTrue(_setup.spin(lambda: page.start_button.get_sensitive()))
        self.assertEqual(page.request().model, "large")

    def test_graphics_card_test_cannot_start_during_recording(self):
        self.app.cfg.measured_device = ""
        with mock.patch.object(type(self.window.record), "busy", new_callable=mock.PropertyMock, return_value=True):
            self.assertFalse(self.window.cards.start())
            dialog = PreferencesDialog(self.window, self.app)
            dialog._test_again()
            self.assertIsNone(self.window.cards.running)
        self.assertEqual(self.engines.tests, 0)

    def test_preferences_size_has_no_minimum_larger_than_the_available_workarea(self):
        self.assertEqual(preference_size(600, 640, 400, 300), (400, 300))
        self.assertEqual(preference_size(600, 640, 1200, 900), (600, 640))
        with mock.patch("scriba.gui.preferences._available_size", return_value=(400, 300)):
            dialog = PreferencesDialog(self.window, self.app)
            dialog.present(self.window)
            self.assertTrue(_setup.spin(lambda: dialog.get_root() is not None))
            from scriba import WINDOWS
            if WINDOWS:
                self.assertLessEqual(dialog.get_content_width(), 400)
                self.assertLessEqual(dialog.get_content_height(), 300)
            root = dialog.get_root()
            if isinstance(root, Gtk.Window) and root is not self.window:
                self.assertTrue(root.get_resizable())
                width, height = root.get_default_size()
                self.assertLessEqual(width, 400)
                self.assertLessEqual(height, 300)
                self.assertTrue(_setup.spin(lambda: root.get_mapped() and root.get_width() > 0))
                root.set_default_size(380, 250)
                from gi.repository import Gdk
                layout = Gdk.ToplevelLayout.new()
                layout.set_resizable(root.get_resizable())
                Gdk.Toplevel.present(root.get_surface(), layout)
                self.assertTrue(_setup.spin(lambda: 0 < root.get_width() <= 380 and 0 < root.get_height() <= 250))
