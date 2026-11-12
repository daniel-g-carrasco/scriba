"""Closing an owner cancels device discovery and waits for its child to exit."""

import contextlib
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup

_setup.gtk_or_skip()
import fake_engines  # noqa: E402
from test_window import TestApp  # noqa: E402
from scriba.app.backend import Backend  # noqa: E402
from scriba.app.engines import process, whisper  # noqa: E402
from scriba.gui import ui  # noqa: E402
from scriba.gui.preferences import PreferencesDialog  # noqa: E402
from scriba.gui.window import MainWindow  # noqa: E402


class DeviceDiscoveryLifecycleTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.backend = fake_engines.FakeBackend(Path(folder.name))
        self.app = TestApp(folder.name, self.backend)
        self.app.cfg.measured_device = "cpu"
        self.window = None
        self.addCleanup(self.clean_up)

    def clean_up(self):
        self.app.closing = True
        if self.window is not None:
            self.window.transcribe.stop_all()
            self.window.record.stop_all()
            self.window.destroy()
        if self.app._downloads is not None:
            self.app._downloads.shutdown()
        self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0, 3))

    def make_window(self):
        self.window = MainWindow(self.app)
        return self.window

    @contextlib.contextmanager
    def blocked_discovery(self):
        entered = threading.Event()
        children = []
        popen = subprocess.Popen
        self.backend._devices = None
        self.backend.devices = Backend.devices.__get__(self.backend)

        def start(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        def discover(cancelled=None):
            process.run([sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)"],
                        cancelled=cancelled, on_stdout=lambda _line: entered.set())
            return [fake_engines.CARD]

        with mock.patch.object(subprocess, "Popen", side_effect=start), \
                mock.patch.object(whisper, "list_devices", side_effect=discover):
            try:
                yield entered, children
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=3)
                self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0, 3))

    def test_page_shutdown_terminates_discovery_child_without_refreshing_closed_ui(self):
        with self.blocked_discovery() as (entered, children):
            page = self.make_window().transcribe
            self.assertTrue(entered.wait(3))
            self.assertIsNone(children[0].poll())
            with mock.patch.object(page, "refresh_estimate") as refresh:
                self.app.closing = True
                page.stop_all()
                self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0, 1.5),
                                "device discovery still owns a live worker after shutdown")
                self.assertIsNotNone(children[0].poll())
                refresh.assert_not_called()
            self.assertIsNone(self.backend._devices, "cancellation must not cache an empty device list")

    def test_closed_preferences_terminate_discovery_without_updating_the_dialog(self):
        window = self.make_window()
        self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
        with self.blocked_discovery() as (entered, children):
            dialog = PreferencesDialog(window, self.app)
            self.assertTrue(entered.wait(3))
            with mock.patch.object(dialog, "_fill_devices") as fill:
                dialog.emit("closed")
                self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0, 1.5),
                                "closed preferences still own a live discovery worker")
                self.assertIsNotNone(children[0].poll())
                fill.assert_not_called()
            self.assertIsNone(self.backend._devices)

    def test_cancel_does_not_remove_a_successful_device_cache(self):
        self.backend._devices = None
        self.backend.devices = Backend.devices.__get__(self.backend)
        with mock.patch.object(whisper, "list_devices", return_value=[fake_engines.CARD]) as discover:
            page = self.make_window().transcribe
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
            page.stop_all()
            self.assertEqual(self.backend.devices(), [fake_engines.CARD])
            self.assertEqual(discover.call_count, 1)

    def test_application_quit_cancels_discovery_in_presented_preferences(self):
        window = self.make_window()
        self.app.window = window
        self.assertTrue(_setup.spin(lambda: ui.activities.active_count == 0))
        with self.blocked_discovery() as (entered, children):
            dialog = PreferencesDialog(window, self.app)
            dialog.present(window)
            self.assertIn(dialog, self.app._preferences_dialogs)
            self.addCleanup(dialog.force_close)
            self.assertTrue(entered.wait(3))
            self.assertIsNotNone(dialog.get_root(), "the preferences must be attached to a real window")
            with mock.patch.object(self.app, "quit"):
                self.app.request_quit(system=True)
                self.assertTrue(_setup.spin(lambda: self.app.exit_ready, 1.5),
                                "application shutdown is waiting for the open preferences discovery")
                self.assertIsNotNone(children[0].poll())
                self.assertEqual(ui.activities.active_count, 0)
                self.assertTrue(_setup.spin(lambda: dialog not in self.app._preferences_dialogs, 1))
