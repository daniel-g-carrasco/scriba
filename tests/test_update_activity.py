"""Closing an update download waits for its callback without starting Setup."""

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()

from scriba.app import updates  # noqa: E402
from scriba.gui import ui, update_dialog  # noqa: E402


class UpdateActivityTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "setup.exe"
        self.baseline = ui.activities.active_count
        with mock.patch.object(updates, "installed_build", return_value=False):
            self.dialog = update_dialog.UpdateDialog(updates.Release("99.0.0", "https://example.test/release", "", []))
        self.dialog.installer = SimpleNamespace(name="setup.exe")

    def test_cancelled_download_stays_owned_until_finished_and_never_starts_setup(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def download(_project, _asset, dest, _progress, cancel):
            dest.write_bytes(b"a verified installer, not executable in this test")
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test did not release download")
            self.assertTrue(cancel.is_set())

        with mock.patch.object(updates, "download_folder", return_value=self.path.parent), \
                mock.patch.object(updates, "download", side_effect=download), \
                mock.patch.object(update_dialog.subprocess, "Popen") as launch:
            self.dialog._download()
            self.assertTrue(entered.wait(3))
            self.assertEqual(ui.activities.active_count, self.baseline + 1)
            self.dialog.cancel_download()
            self.assertEqual(ui.activities.active_count, self.baseline + 1)
            release.set()
            self.assertTrue(_setup.spin(lambda: ui.activities.active_count == self.baseline))
            launch.assert_not_called()
        self.assertEqual(self.dialog.ready_installer, self.path)
        self.assertTrue(self.path.read_bytes())

    def test_success_still_starts_setup_from_the_completion_callback(self):
        counts = []

        def download(_project, _asset, dest, _progress, _cancel):
            dest.write_bytes(b"test installer")

        with mock.patch.object(updates, "download_folder", return_value=self.path.parent), \
                mock.patch.object(updates, "download", side_effect=download), \
                mock.patch.object(updates, "per_user_install", return_value=False), \
                mock.patch.object(update_dialog.subprocess, "Popen", side_effect=lambda *_a, **_k: counts.append(
                    ui.activities.active_count)) as launch:
            self.dialog._download()
            self.assertTrue(_setup.spin(lambda: bool(counts)))
            self.assertEqual(counts, [self.baseline + 1])
            self.assertEqual(launch.call_args.args[0][0], str(self.path))
            self.assertEqual(ui.activities.active_count, self.baseline)

    def test_download_error_is_visible_and_releases_the_activity(self):
        with mock.patch.object(updates, "download_folder", return_value=self.path.parent), \
                mock.patch.object(updates, "download", side_effect=OSError("test disk full")):
            self.dialog._download()
            self.assertTrue(_setup.spin(lambda: "test disk full" in self.dialog.status.get_label()))
        self.assertTrue(self.dialog.action.get_sensitive())
        self.assertEqual(ui.activities.active_count, self.baseline)
