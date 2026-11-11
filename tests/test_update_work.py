"""Update proposals and installer launch yield to recording and transcription.

All network and process boundaries are replaced; no installer is run.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

_setup.gtk_or_skip()
import fake_engines  # noqa: E402
from test_window import TestApp  # noqa: E402

from scriba.app import services, updates  # noqa: E402
from scriba.app.engines import ffmpeg  # noqa: E402
from scriba.gui import application as app_mod  # noqa: E402
from scriba.gui.update_dialog import UpdateDialog  # noqa: E402
from scriba.gui.window import MainWindow  # noqa: E402


class UpdateWorkTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.app = TestApp(tmp.name)
        self.app.cfg.measured_device = fake_engines.CARD.name
        self.window = self.app.window = MainWindow(self.app)
        self.addCleanup(self.window.destroy)
        self.release = updates.Release("99.0.0", "https://example/release", "Notes", [])
        self.presented, self.notices = [], []
        self.enterContext(mock.patch.object(UpdateDialog, "present", lambda dialog, _parent: self.presented.append(dialog)))
        self.enterContext(mock.patch.object(UpdateDialog, "close", lambda dialog: dialog._cancel.set()))
        self.enterContext(mock.patch.object(self.app, "notify", lambda *args, **kwargs: self.notices.append(args[0])))
        self.enterContext(mock.patch.object(updates, "latest_release", return_value=self.release))

    def immediate_check(self):
        return mock.patch.object(app_mod, "run_in_thread", lambda work, done: done(work(), None))

    def test_checks_and_proposals_wait_through_start_pause_finalisation_and_transcription(self):
        with self.immediate_check():
            for state in ("starting", "paused", "finishing", "transcribing"):
                self.window.record._starting = state == "starting"
                self.window.record.recorder = object() if state == "paused" else None
                self.window.record._stopping = state == "finishing"
                self.window.transcribe.job = (object(),) if state == "transcribing" else None
                self.window.page_changed()
                self.app.check_updates(manual=True)
                self.assertFalse(self.window.update_banner.get_revealed(), state)
                self.assertEqual(self.presented, [], state)
                self.assertEqual(self.notices, [], state)
            self.window.record._starting = self.window.record._stopping = False
            self.window.record.recorder = self.window.transcribe.job = None
            self.window.page_changed()
        self.assertTrue(self.window.update_banner.get_revealed())
        self.assertEqual(len(self.presented), 1)

    def test_a_check_finishing_after_work_started_does_not_raise_a_banner_or_notification(self):
        pending = []
        with mock.patch.object(app_mod, "run_in_thread", lambda work, done: pending.append(done)):
            self.app.check_updates(manual=False)
        self.window.transcribe.job = (object(),)
        self.window.page_changed()
        pending[0](self.release, None)
        self.assertFalse(self.window.update_banner.get_revealed())
        self.assertEqual(self.notices, [])
        self.window.transcribe.job = None
        self.window.page_changed()
        self.assertTrue(self.window.update_banner.get_revealed())
        self.assertEqual(len(self.notices), 1)

    def test_an_existing_banner_and_notification_click_wait_until_the_work_is_over(self):
        with self.immediate_check():
            self.app.check_updates(manual=False)
        self.assertTrue(self.window.update_banner.get_revealed())
        self.window.record._starting = True
        self.window.page_changed()
        self.assertFalse(self.window.update_banner.get_revealed())
        self.app._show_update(self.release)
        self.assertEqual(self.presented, [])
        self.window.record._starting = False
        self.window.page_changed()
        self.assertEqual(len(self.presented), 1)

    def test_a_manual_request_is_kept_when_an_older_banner_was_deferred(self):
        with self.immediate_check():
            self.app.check_updates(manual=False)
            self.window.record._starting = True
            self.window.page_changed()
            self.app.check_updates(manual=True)
            self.assertEqual(self.presented, [])
            self.window.record._starting = False
            self.window.page_changed()
        self.assertEqual(len(self.presented), 1)

    def test_a_closed_dialogs_late_verified_callback_updates_the_dialog_reopened_after_work(self):
        # Set up the installed release, then open it through the real application path.
        self.installed_dialog()
        with mock.patch.object(updates, "installed_build", return_value=True):
            self.app._show_update(self.release)
            first = self.presented[-1]
            self.window.record._starting = True
            self.window.page_changed()
            self.assertTrue(first._cancel.is_set())
            self.window.record._starting = False
            self.window.page_changed()
        again = self.presented[-1]
        self.assertIsNot(first, again)
        installer = self.folder / "verified-setup.exe"
        installer.write_bytes(b"not executable")
        with mock.patch("scriba.gui.update_dialog.subprocess.Popen") as started:
            first._downloaded(installer)
        started.assert_not_called()
        with mock.patch.object(updates, "download", side_effect=AssertionError("must reuse the held file")), \
                mock.patch("scriba.gui.update_dialog.subprocess.Popen") as started:
            again._download()
            self.assertTrue(_setup.spin(lambda: started.called or "failed" in again.status.get_label().lower()))
        started.assert_called_once()

    def installed_dialog(self):
        project = services.project()
        name = project.installer_name(self.release.version)
        self.release.assets = [updates.Asset(name, project.downloads + name, 10, "ab", project.downloads + name + ".minisig")]
        with mock.patch.object(updates, "installed_build", return_value=True):
            return UpdateDialog(self.release, app=self.app)

    def test_a_verified_download_finishing_during_work_waits_and_is_reused_without_downloading_again(self):
        dialog = self.installed_dialog()
        installer = self.folder / "verified-setup.exe"
        installer.write_bytes(b"not executable")
        self.window.record._stopping = True
        with mock.patch("scriba.gui.update_dialog.subprocess.Popen") as started:
            dialog._downloaded(installer)
        started.assert_not_called()
        self.assertIn("wait", dialog.status.get_label().lower())
        self.window.record._stopping = False
        with mock.patch.object(updates, "installed_build", return_value=True):
            self.window.page_changed()
        self.assertEqual(len(self.presented), 1)
        again = self.presented[0]
        with mock.patch.object(updates, "download", side_effect=AssertionError("a verified file must be reused")), \
                mock.patch("scriba.gui.update_dialog.subprocess.Popen") as started:
            again._download()
        started.assert_called_once()

    def test_a_successful_launch_blocks_new_recordings_and_transcriptions(self):
        dialog = self.installed_dialog()
        with mock.patch("scriba.gui.update_dialog.subprocess.Popen"):
            dialog._downloaded(self.folder / "not-run.exe")
        source = self.folder / "source.wav"
        source.write_bytes(b"made up")
        page = self.window.transcribe
        page.source, page.media = source, ffmpeg.Media(2, 1, False, 0)
        page.start()
        self.assertIsNone(page.job)
        self.window.record.monitor = object()  # must be refused before opening or asking any device
        self.window.record.start()
        self.assertIsNone(self.window.record.recorder)
        self.assertFalse(self.window.record._starting)
        self.window.record.monitor = None

    def test_a_failed_installer_launch_releases_the_work_gate(self):
        dialog = self.installed_dialog()
        with mock.patch("scriba.gui.update_dialog.subprocess.Popen", side_effect=OSError("not started")):
            dialog._downloaded(self.folder / "not-run.exe")
        self.assertIn("Cannot start", dialog.status.get_label())
        source = self.folder / "source.wav"
        source.write_bytes(b"made up")
        page = self.window.transcribe
        page.source, page.media = source, ffmpeg.Media(2, 1, False, 0)
        self.app.backend.runner = fake_engines.runner(lambda request: fake_engines.made_result(self.folder, source, []))
        page.start()
        self.assertIsNotNone(page.job)
        self.assertTrue(_setup.spin(lambda: page.job is None))
