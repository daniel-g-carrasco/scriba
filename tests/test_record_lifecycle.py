"""Recording exit barriers use real threads and temporary files, without audio hardware."""

import contextlib
from dataclasses import replace
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import _setup

_setup.gtk_or_skip()
from scriba.app import recording  # noqa: E402
from scriba.app.config import Config  # noqa: E402
from scriba.app.paths import Paths  # noqa: E402
from scriba.app.task import Failure  # noqa: E402
from scriba.core import devices  # noqa: E402
from scriba.gui import ui  # noqa: E402
from scriba.gui.record import RecordPage  # noqa: E402


class Monitor:
    def __init__(self, backend):
        self.backend = backend
        self.entered = threading.Event()
        self.finished = threading.Event()

    def start(self):
        pass

    def snapshot(self):
        return recording.Meters(devices=(devices.Device("mic", "Test microphone", devices.INPUT),), listed=True)

    def stop(self):
        self.entered.set()
        if not self.backend.monitor_release.wait(2):
            raise TimeoutError("the test monitor was not released")
        if self.backend.monitor_error is not None:
            raise self.backend.monitor_error
        self.finished.set()


class Recorder:
    def __init__(self, backend, folder, stem):
        self.backend, self.folder, self.stem = backend, Path(folder), stem
        self.state = "new"
        self.takes = []
        self.stops = 0
        self.switched = []

    def switch(self, role, on):
        self.switched.append((role, on))

    def start(self):
        self.backend.start_entered.set()
        if not self.backend.start_release.wait(3):
            raise TimeoutError("the test recording was not released")
        if self.backend.start_error is not None:
            raise self.backend.start_error
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / (self.stem + "_mic.flac")
        path.write_bytes(b"the original recording")
        self.takes = [recording.Take("me-1", "me", "Test microphone", path)]
        self.state = "recording"

    def stop(self):
        self.stops += 1
        self.state = "stopped"
        return self.takes

    def snapshot(self):
        return recording.Status(self.state, 1, tuple(self.takes), {}, (), (), 1e9)


class Backend:
    def __init__(self):
        self.start_entered = threading.Event()
        self.finish_entered = threading.Event()
        self.start_release = threading.Event()
        self.finish_release = threading.Event()
        self.monitor_release = threading.Event()
        self.start_release.set()
        self.finish_release.set()
        self.monitor_release.set()
        self.start_error = self.finish_error = self.monitor_error = None
        self.notes = []
        self.monitors, self.recorders = [], []
        self.finishes = 0

    def recorder_missing(self):
        return []

    def monitor(self):
        monitor = Monitor(self)
        self.monitors.append(monitor)
        return monitor

    def recorder(self, plan, folder, stem):
        recorder = Recorder(self, folder, stem)
        self.recorders.append(recorder)
        return recorder

    def finish(self, takes, folder, stem, progress=None):
        self.finishes += 1
        if progress is not None:
            progress(recording.JOINING)
        self.finish_entered.set()
        if not self.finish_release.wait(3):
            raise TimeoutError("the test finalization was not released")
        if self.finish_error is not None:
            raise self.finish_error
        path = folder / (stem + ".mka")
        path.write_bytes(b"the complete recording")
        return recording.Finished(path, None, list(takes), notes=list(self.notes))


class RecordLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.backend = Backend()
        self.blocked, self.opened, self.messages = [], [], []
        self.app = SimpleNamespace(cfg=Config(recordings_folder=str(self.folder / "recordings")),
                                   paths=Paths(self.folder / "config", self.folder / "state", self.folder / "data"),
                                   closing=False, installing=False, save_config=lambda: None,
                                   block_exit=self.block_exit)
        self.window = SimpleNamespace(app=self.app, backend=self.backend, toast=self.messages.append,
                                      recorded=self.opened.append, page_changed=lambda: None,
                                      views=SimpleNamespace(get_visible_child_name=lambda: "record"))
        self.page = RecordPage(self.window)
        self.addCleanup(self.clean_workers)

    def block_exit(self, error):
        self.blocked.append(error)
        self.app.closing = False

    def clean_workers(self):
        self.backend.monitor_release.set()
        self.backend.start_release.set()
        self.backend.finish_release.set()
        self.app.closing = True
        _setup.spin(lambda: not self.page._starting, 3)
        if self.page.recorder is not None and not self.page._stopping:
            self.page.stop(then=lambda: None)
        self.page.stop_all()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count, 3))

    def begin(self, wait=True):
        self.page.shown(True)
        self.page.start()
        self.assertTrue(_setup.spin(self.backend.start_entered.is_set))
        if wait:
            self.assertTrue(_setup.spin(lambda: self.page.recorder is not None))
        return self.backend.recorders[0]

    def complete(self):
        self.assertTrue(_setup.spin(lambda: not self.page.busy and not ui.activities.active_count))

    def test_quit_during_start_stops_and_finalizes_once_before_success_callback(self):
        self.backend.start_release.clear()
        self.backend.finish_release.clear()
        recorder = self.begin(wait=False)
        completed = []
        self.app.closing = True
        self.page.stop(then=lambda: completed.append("saved"))
        self.page.stop(then=lambda: completed.append("duplicate"))
        self.backend.start_release.set()
        self.assertTrue(_setup.spin(self.backend.finish_entered.is_set, 1))
        self.assertEqual(completed, [])
        self.assertTrue(self.page.busy)
        self.assertGreater(ui.activities.active_count, 0)
        self.backend.finish_release.set()
        self.complete()
        self.assertEqual(completed, ["saved"])
        self.assertEqual(recorder.stops, 1)
        self.assertEqual(self.backend.finishes, 1)
        self.assertTrue((recorder.folder / (recorder.stem + ".mka")).is_file())
        self.assertEqual(len(self.backend.monitors), 1)
        self.assertEqual(self.opened, [])

    def test_finalization_failure_blocks_exit_and_keeps_original_tracks_without_success_callback(self):
        recorder = self.begin()
        self.app.closing = True
        failure = self.backend.finish_error = OSError("disk full")
        completed = []
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.page.stop(then=lambda: completed.append("saved"))
            self.complete()
        self.assertEqual(completed, [])
        self.assertEqual(self.blocked, [failure])
        self.assertFalse(self.app.closing)
        self.assertTrue(all(take.path.is_file() for take in recorder.takes))
        self.assertTrue(self.page.banner.get_revealed())
        self.assertIn(str(recorder.folder), self.page.banner.get_title())
        self.assertIn("disk full", stderr.getvalue())
        self.assertEqual(len(self.backend.monitors), 1)

    def test_start_failure_during_quit_blocks_exit_without_reopening_the_monitor(self):
        self.backend.start_release.clear()
        failure = self.backend.start_error = Failure("The device is unavailable.")
        self.begin(wait=False)
        self.app.closing = True
        completed = []
        self.page.stop(then=lambda: completed.append("saved"))
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.backend.start_release.set()
            self.complete()
        self.assertEqual(self.blocked, [failure])
        self.assertEqual(completed, [])
        self.assertEqual(self.backend.finishes, 0)
        self.assertEqual(len(self.backend.monitors), 1)
        self.assertIn("device is unavailable", stderr.getvalue())

    def test_no_monitor_or_recording_is_started_after_exit_has_begun(self):
        self.app.closing = True
        self.page.shown(True)
        self.page.start()
        self.assertEqual(self.backend.monitors, [])
        self.assertEqual(self.backend.recorders, [])

    def test_an_existing_monitor_does_not_allow_a_new_recording_during_exit(self):
        self.page.shown(True)
        self.app.closing = True
        self.page.start()
        self.assertFalse(self.page.busy)
        self.assertEqual(self.backend.recorders, [])

    def test_stop_all_waits_in_a_registered_worker_and_keeps_the_main_loop_responsive(self):
        self.page.shown(True)
        monitor = self.backend.monitors[0]
        self.backend.monitor_release.clear()
        heartbeat = []
        from gi.repository import GLib
        GLib.idle_add(lambda: heartbeat.append(True) and False)
        self.page.stop_all()
        self.assertTrue(_setup.spin(lambda: bool(heartbeat) and monitor.entered.is_set()))
        self.assertFalse(monitor.finished.is_set())
        self.assertGreater(ui.activities.active_count, 0)
        self.backend.monitor_release.set()
        self.assertTrue(_setup.spin(lambda: monitor.finished.is_set() and not ui.activities.active_count))

    def test_a_monitor_already_stopping_cannot_be_reopened_by_a_late_shown_callback(self):
        self.page.shown(True)
        self.backend.monitor_release.clear()
        self.page.shown(False)
        self.app.closing = True
        self.page.shown(True)
        self.assertEqual(len(self.backend.monitors), 1)
        self.assertGreater(ui.activities.active_count, 0)
        self.backend.monitor_release.set()
        self.assertTrue(_setup.spin(lambda: not ui.activities.active_count))

    def test_a_recording_stopped_out_of_sight_leaves_the_meters_closed_until_the_page_is_seen(self):
        # stopped from the tray's menu, the window in the tray or minimized
        self.begin()
        self.page.shown(False)
        self.page.stop()
        self.complete()
        self.assertIsNone(self.page.monitor)
        self.assertEqual(len(self.backend.monitors), 1)
        self.page.shown(True)
        self.assertEqual(len(self.backend.monitors), 2)

    def test_a_recording_that_cannot_start_out_of_sight_leaves_the_meters_closed(self):
        self.backend.start_release.clear()
        self.backend.start_error = Failure("The device is unavailable.")
        self.begin(wait=False)
        self.page.shown(False)
        with contextlib.redirect_stderr(io.StringIO()):
            self.backend.start_release.set()
            self.complete()
        self.assertIsNone(self.page.monitor)
        self.assertEqual(len(self.backend.monitors), 1)

    def test_repeated_stop_while_finalizing_keeps_one_worker_and_the_first_success_callback(self):
        recorder = self.begin()
        self.backend.finish_release.clear()
        self.app.closing = True
        completed = []
        self.page.stop(then=lambda: completed.append("first"))
        self.assertTrue(_setup.spin(self.backend.finish_entered.is_set))
        self.page.stop(then=lambda: completed.append("second"))
        self.backend.finish_release.set()
        self.complete()
        self.assertEqual(completed, ["first"])
        self.assertEqual(recorder.stops, 1)
        self.assertEqual(self.backend.finishes, 1)

    def test_after_the_stop_the_page_names_the_finishing_step_and_offers_no_second_stop(self):
        recorder = self.begin()
        self.backend.finish_release.clear()
        self.page.stop()
        self.assertTrue(_setup.spin(self.backend.finish_entered.is_set))
        self.page._show_status(recorder.snapshot())
        self.assertIn("step 2 of 4: joining the tracks", self.page.state_label.get_label())
        self.assertFalse(self.page.stop_button.get_sensitive())
        self.assertFalse(self.page.pause_button.get_sensitive())
        self.backend.finish_release.set()
        self.complete()
        self.assertEqual(recorder.stops, 1)

    def test_while_recording_the_switch_silences_its_side_without_changing_the_settings(self):
        recorder = self.begin()
        self.page._show_status(recorder.snapshot())
        switch = self.page.input_side.switch_button
        self.assertTrue(switch.get_sensitive())
        self.assertFalse(self.page.output_side.switch_button.get_sensitive())    # no track of its own
        switch.set_active(False)
        self.assertEqual(recorder.switched, [("me", False)])
        self.assertTrue(self.app.cfg.record_input)
        self.page._show_status(replace(recorder.snapshot(), off=frozenset({"me"})))
        self.assertIn("silence", self.page.input_side.row.get_subtitle())
        self.page.stop()
        self.complete()
        self.assertTrue(switch.get_active())        # the next recording as the settings say

    def test_success_during_exit_does_not_open_transcribe_or_restart_meters_even_without_a_callback(self):
        self.begin()
        self.app.closing = True
        self.page.stop()
        self.complete()
        self.assertEqual(self.opened, [])
        self.assertEqual(len(self.backend.monitors), 1)

    def test_monitor_cleanup_failure_blocks_exit_before_its_worker_ticket_is_released(self):
        self.page.shown(True)
        failure = self.backend.monitor_error = OSError("audio cleanup failed")
        self.app.closing = True
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.page.stop_all()
            self.assertTrue(_setup.spin(lambda: not ui.activities.active_count))
        self.assertEqual(self.blocked, [failure])
        self.assertIn("audio cleanup failed", stderr.getvalue())

    def test_late_folder_selection_and_open_actions_do_not_change_files_or_settings_during_exit(self):
        previous = self.app.cfg.recordings_folder
        self.app.closing = True
        self.page._chose_folder(str(self.folder / "other"))
        self.page._open_folder()
        self.assertEqual(self.app.cfg.recordings_folder, previous)
        self.assertFalse(Path(previous).exists())

    def test_degraded_success_keeps_its_result_and_notes_without_blocking_exit(self):
        recorder = self.begin()
        self.backend.notes = ["The listening mix could not be made; the original is kept."]
        self.app.closing = True
        completed = []
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.page.stop(then=lambda: completed.append(True))
            self.complete()
        self.assertEqual(completed, [True])
        self.assertEqual(self.blocked, [])
        self.assertTrue(self.page.last_finished.recording.is_file())
        self.assertEqual(self.page.last_finished.notes, self.backend.notes)
        self.assertIn(self.backend.notes[0], stderr.getvalue())
        self.assertTrue(all(take.path.is_file() for take in recorder.takes))

    def test_a_hidden_recording_error_notifies_once_and_keeps_a_reopen_action(self):
        notifications = []
        background = self.app.background = SimpleNamespace(hidden=True)
        background.present = lambda: setattr(background, "hidden", False)
        self.app.notify = lambda title, body, tag, on_click: notifications.append((title, body, tag, on_click))
        recorder = self.begin()
        failed = replace(recorder.snapshot(), state="failed", error="Microphone disconnected")
        self.page._show_status(failed)
        self.page._show_status(failed)
        self.complete()
        self.assertEqual(self.page.last_error, "Microphone disconnected")
        self.assertEqual(len(notifications), 1)
        self.assertIn("Microphone disconnected", notifications[0][1])
        notifications[0][3]()
        self.assertFalse(background.hidden)
        self.page.start()
        self.assertEqual(self.page.last_error, "")

    def test_finalization_error_remains_available_to_the_tray_after_exit_is_blocked(self):
        self.begin()
        self.app.closing = True
        self.backend.finish_error = OSError("disk full")
        with contextlib.redirect_stderr(io.StringIO()):
            self.page.stop()
            self.complete()
        self.assertIn("disk full", self.page.last_error)

    def test_notification_failure_cannot_strand_recording_finalization(self):
        self.app.background = SimpleNamespace(hidden=True, present=lambda: None)
        def notify(*_args, **_kwargs):
            raise OSError("notifications unavailable")
        self.app.notify = notify
        recorder = self.begin()
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.page._show_status(replace(recorder.snapshot(), state="failed", error="Microphone disconnected"))
            self.complete()
        self.assertEqual(self.backend.finishes, 1)
        self.assertEqual(self.page.last_error, "Microphone disconnected")
        self.assertIn("notifications unavailable", stderr.getvalue())

    def test_exit_failure_uses_the_blocking_error_path_without_a_duplicate_desktop_notification(self):
        notices = []
        self.app.background = SimpleNamespace(hidden=True, present=lambda: None)
        self.app.notify = lambda *_args, **_kwargs: notices.append(True)
        self.begin()
        self.app.closing = True
        failure = self.backend.finish_error = OSError("disk full")
        with contextlib.redirect_stderr(io.StringIO()):
            self.page.stop()
            self.complete()
        self.assertEqual(notices, [])
        self.assertEqual(self.blocked, [failure])


if __name__ == "__main__":
    unittest.main()
