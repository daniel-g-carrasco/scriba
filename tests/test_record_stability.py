"""Audio sources retain their widgets while monitoring data changes."""

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()
from scriba.app import recording  # noqa: E402
from scriba.core import devices as dev  # noqa: E402
from scriba.app.config import Config  # noqa: E402
from scriba.app.paths import Paths  # noqa: E402
from scriba.gui.record import RecordPage, Side  # noqa: E402
from gi.repository import GLib  # noqa: E402


class SourceRowsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(record_input=True, record_output=True, input_device="", output_device="")
        self.saved = 0
        self.page = SimpleNamespace(cfg=self.cfg, app=SimpleNamespace(save_config=self.save), refresh=lambda: None,
                                    busy=False)
        self.side = Side(self.page, dev.INPUT)
        self.mic = dev.Device("mic", "Headset", dev.INPUT, default=True, programs=("ms-teams.exe",))
        self.other = dev.Device("other", "Desk microphone", dev.INPUT)

    def save(self):
        self.saved += 1

    def show(self, devices):
        meters = recording.Meters(devices=tuple(devices), listed=True)
        plan = dev.plan(list(devices), True, False)
        self.side.show(meters, plan, None)

    def test_program_default_and_name_changes_keep_the_check_button_and_meter(self):
        self.show([self.mic, self.other])
        before = self.side.rows["mic"]
        before[1].set_active(True)
        changed = replace(self.mic, name="Headset with a changed name", default=False, programs=("zoom.exe",))
        self.show([changed, self.other])
        self.assertIs(self.side.rows["mic"][0], before[0])
        self.assertIs(self.side.rows["mic"][1], before[1])
        self.assertIs(self.side.rows["mic"][2], before[2])
        self.assertTrue(before[1].get_active())
        self.assertEqual(before[0].get_title(), "Headset with a changed name")
        self.assertIn("Zoom", before[0].get_subtitle())
        self.assertEqual(self.saved, 1)

    def test_removing_one_device_does_not_replace_the_others_or_reset_expansion(self):
        self.show([self.mic, self.other])
        before = self.side.rows["other"]
        self.side.row.set_expanded(True)
        self.show([self.other])
        self.assertEqual(set(self.side.rows), {"other"})
        self.assertIs(self.side.rows["other"][0], before[0])
        self.assertTrue(self.side.row.get_expanded())

    def test_long_device_and_reason_text_is_available_in_details_with_a_single_summary_line(self):
        long = replace(self.mic, name="A very long headset name " * 8, programs=("ms-teams.exe", "chrome.exe"))
        self.show([long])
        self.assertEqual(self.side.row.get_subtitle_lines(), 1)
        self.assertIn(long.name, self.side.auto.get_subtitle())
        self.assertIn("Teams", self.side.auto.get_subtitle())
        self.assertIn("Chrome", self.side.auto.get_subtitle())


class RecordingLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.cfg = Config(recordings_folder=str(self.folder / "recordings"))
        self.app = SimpleNamespace(cfg=self.cfg, paths=Paths(self.folder / "config", self.folder / "state",
                                                            self.folder / "data"), save_config=lambda: None,
                                   installing=False)
        self.backend = SimpleNamespace(recorder_missing=lambda: [])
        self.window = SimpleNamespace(app=self.app, backend=self.backend, toast=lambda text: None,
                                      page_changed=lambda: None)
        self.page = RecordPage(self.window)
        self.addCleanup(self.page.stop_all)
        self.mic = dev.Device("mic", "Headset microphone", dev.INPUT)
        self.output = dev.Device("out", "Headset speakers", dev.OUTPUT)
        self.page._show_meters(recording.Meters(devices=(self.mic, self.output), listed=True))
        self.status = recording.Status("recording", 0, (
            recording.Take("me-1", "me", self.mic.name, self.folder / "mic.flac"),
            recording.Take("others-1", "others", self.output.name, self.folder / "out.flac")),
            {}, (), (), 1e9)

    def started(self):
        self.page._started(SimpleNamespace(snapshot=lambda: self.status), None, self.folder)
        self.page._show_status(self.status)

    def test_recording_keeps_both_source_rows_and_only_locks_their_selection_controls(self):
        first, second = self.page.input_side.row, self.page.output_side.row
        self.started()
        self.assertTrue(self.page.sources.get_visible())

        self.assertIs(self.page.input_side.row, first)
        self.assertIs(self.page.output_side.row, second)
        self.assertTrue(first.get_sensitive())
        # the switch stays usable (Scriba's own mute); the choice of device does not
        self.assertTrue(self.page.input_side.switch_button.get_sensitive())
        self.assertFalse(self.page.input_side.auto_check.get_sensitive())
        self.assertFalse(self.page.choose_button.get_sensitive())
        first.set_expanded(True)
        self.assertTrue(first.get_expanded())
        self.assertEqual(len(self.page._track_rows), 2)

    def test_recording_pause_and_hotplug_preserve_the_existing_track_widgets(self):
        self.started()
        before = self.page._track_rows["me-1"]
        self.status = replace(self.status, state="paused", elapsed=20,
                              tracks=(*self.status.tracks, recording.Take(
                                  "others-2", "others", "USB speakers", self.folder / "usb.flac", began=19)))
        self.page._show_status(self.status)
        self.assertIs(self.page._track_rows["me-1"][0], before[0])
        self.assertIs(self.page._track_rows["me-1"][1], before[1])
        self.assertEqual(before[1].get_value(), 0)
        self.assertEqual(len(self.page._track_rows), 3)
        self.assertIn("USB speakers", self.page._track_rows["others-2"][0].get_title())

    def test_returning_to_idle_restores_device_selection_inside_the_same_source_rows(self):
        first = self.page.input_side.row
        self.started()
        self.page.recorder = None
        self.page._show_idle()
        self.assertIs(self.page.input_side.row, first)
        self.assertTrue(self.page.input_side.switch_button.get_sensitive())
        self.assertTrue(self.page.input_side.rows["mic"][0].get_visible())
        self.assertFalse(self.page._track_rows["me-1"][0].get_visible())

    def test_device_and_folder_choices_are_locked_before_the_worker_starts(self):
        meters = recording.Meters(devices=(self.mic, self.output), listed=True)
        self.page.monitor = SimpleNamespace(snapshot=lambda: meters, stop=lambda: None)
        with mock.patch("scriba.gui.record.run_in_thread"):
            self.page.start()
        self.assertTrue(self.page._starting)
        self.assertFalse(self.page.input_side.switch_button.get_sensitive())
        self.assertFalse(self.page.choose_button.get_sensitive())
        self.assertTrue(self.page.sources.get_visible())

    def test_finishing_stays_on_the_record_page_and_disables_duplicate_stop_requests(self):
        self.started()
        with mock.patch("scriba.gui.record.run_in_thread"):
            self.page.stop()
        self.assertTrue(self.page._stopping)
        self.assertEqual(self.page.stack.get_visible_child_name(), "page")
        self.assertFalse(self.page.pause_button.get_sensitive())
        self.assertFalse(self.page.stop_button.get_sensitive())
        self.assertTrue(self.page.sources.get_visible())


class GeometryTest(unittest.TestCase):
    started = RecordingLayoutTest.started
    def setUp(self):
        RecordingLayoutTest.setUp(self)
        self.gtk_window = Gtk.Window(title="Scriba isolated audio geometry", default_width=700,
                                     default_height=800, opacity=.05)
        self.gtk_window.set_child(self.page)
        self.addCleanup(self.gtk_window.destroy)
        self.gtk_window.present()
        self.assertTrue(_setup.spin(lambda: self.page.folder_row.get_height() > 0))
        self.settle()

    def settle(self):
        ready = []
        GLib.timeout_add(100, lambda: ready.append(True) and False)
        self.assertTrue(_setup.spin(lambda: bool(ready)))

    def geometry(self):
        found = {}
        for name, widget in (("input", self.page.input_side.row), ("output", self.page.output_side.row),
                             ("controls", self.page.controls), ("folder", self.page.folder_row)):
            ok, bounds = widget.compute_bounds(self.page)
            self.assertTrue(ok, name)
            found[name] = tuple(round(value) for value in (bounds.origin.x, bounds.origin.y,
                                                          bounds.size.width, bounds.size.height))
        return found

    def test_collapsed_sources_and_folder_stay_put_across_search_record_pause_and_finish(self):
        for font in ("Sans 11", "Sans 22"):
            with self.subTest(font=font):
                settings = Gtk.Settings.get_default()
                previous = settings.get_property("gtk-font-name")
                try:
                    settings.set_property("gtk-font-name", font)
                    self.page.recorder = None
                    self.page._stopping = False
                    self.page._show_idle()
                    self.page._show_meters(recording.Meters())
                    self.settle()
                    baseline = self.geometry()
                    longer = replace(self.mic, name="Microphone with a very long friendly name " * 10)
                    self.page._show_meters(recording.Meters(devices=(longer, self.output), listed=True))
                    self.settle()
                    self.assertEqual(self.geometry(), baseline, "device discovery changed the main controls")
                    self.status = replace(self.status, state="recording")
                    self.started()
                    self.settle()
                    self.assertEqual(self.geometry(), baseline, "recording replaced or resized the source group")
                    self.status = replace(self.status, state="paused")
                    self.page._show_status(self.status)
                    self.settle()
                    self.assertEqual(self.geometry(), baseline, "pause shifted the main controls")
                    with mock.patch("scriba.gui.record.run_in_thread"):
                        self.page.stop()
                    self.settle()
                    self.assertEqual(self.geometry(), baseline, "finishing replaced the record page")
                finally:
                    settings.set_property("gtk-font-name", previous)

    def test_device_metadata_updates_keep_keyboard_focus_on_the_existing_choice(self):
        self.page.input_side.row.set_expanded(True)
        self.settle()
        check = self.page.input_side.rows["mic"][1]
        self.assertTrue(check.grab_focus())
        previous = self.gtk_window.get_focus()
        self.assertIsNotNone(previous)
        changed = replace(self.mic, programs=("ms-teams.exe", "chrome.exe"))
        self.page._show_meters(recording.Meters(devices=(changed, self.output), listed=True))
        self.settle()
        self.assertIs(self.gtk_window.get_focus(), previous)

    def test_large_text_can_scroll_to_the_folder_actions_without_fixed_height_clipping(self):
        settings = Gtk.Settings.get_default()
        previous = settings.get_property("gtk-font-name")
        try:
            settings.set_property("gtk-font-name", "Sans 22")
            self.settle()
            scroller = self.page.page.get_first_child()
            while scroller is not None and not isinstance(scroller, Gtk.ScrolledWindow):
                scroller = scroller.get_next_sibling()
            self.assertIsNotNone(scroller)
            adjustment = scroller.get_vadjustment()
            self.assertGreater(adjustment.get_upper(), adjustment.get_page_size())
            adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
            self.settle()
            ok, bounds = self.page.choose_button.compute_bounds(scroller)
            self.assertTrue(ok)
            self.assertGreaterEqual(bounds.origin.y, 0)
            self.assertLessEqual(bounds.origin.y + bounds.size.height, scroller.get_height())
        finally:
            settings.set_property("gtk-font-name", previous)


if __name__ == "__main__":
    unittest.main()
