# SPDX-License-Identifier: GPL-3.0-or-later
"""Update dialog policies with fake releases and a fake installer."""

import unittest
from pathlib import Path
from unittest import mock

import _setup

Gtk, Adw = _setup.gtk_or_skip()
updates = _setup.shared("app.updates")
ui = _setup.shared("gui.release_dialog")


class ReleaseDialogTest(unittest.TestCase):
    def setUp(self):
        self.allowed = mock.Mock(return_value=True)
        self.run = mock.Mock()
        self.launch = mock.Mock()
        self.project = updates.Project("made/up", "fiction", "Fiction", "1.0", "")
        self.release = updates.Release("2.0", "https://example.invalid/release", "### Fixed\n- A detail")
        self.asset = updates.Asset("setup.exe", "https://example.invalid/file", 1)

    def make_dialog(self, channel):
        with mock.patch.object(updates, "installer_of", return_value=self.asset) as choose:
            dialog = ui.UpdateDialog(self.release, project=self.project, app_name="Fiction", app_icon="",
                                     layout=ui.UpdateLayout(480, 580, 440, 12, 16), channel=channel,
                                     run_in_thread=self.run, can_install=self.allowed, launch=self.launch)
        self.assertEqual(choose.call_count, int(channel == "installed"))
        return dialog

    def test_work_blocks_download_and_install_and_a_verified_file_can_be_retried(self):
        dialog = self.make_dialog("installed")
        self.allowed.return_value = False
        dialog._download()
        self.run.assert_not_called()
        self.assertTrue(dialog.banner.get_revealed())
        self.allowed.return_value = True
        dialog._download()
        complete = self.run.call_args.args[1]
        self.allowed.return_value = False
        installer = Path("test-only.exe")
        complete(installer, None)
        self.launch.assert_not_called()
        self.assertTrue(dialog.banner.get_revealed())
        self.assertTrue(dialog.action.get_sensitive())
        self.allowed.return_value = True
        dialog._download()
        self.run.assert_called_once()
        self.launch.assert_called_once_with(installer)

    def test_signature_failure_shows_the_error_without_launching(self):
        dialog = self.make_dialog("installed")
        dialog._download()
        complete = self.run.call_args.args[1]
        complete(None, updates.UpdateError("bad signature"))
        self.launch.assert_not_called()
        self.assertIn("bad signature", dialog.banner.get_title())
        self.assertTrue(dialog.action.get_sensitive())

    def test_other_channels_never_offer_an_installer(self):
        for channel in ("portable", "flatpak", "source"):
            with self.subTest(channel=channel):
                dialog = self.make_dialog(channel)
                self.assertIsNone(dialog.installer)
                dialog._download()
        self.run.assert_not_called()
        self.launch.assert_not_called()

    def test_the_dialog_reads_a_complete_sentence_from_the_localized_asset(self):
        sentence = "È possibile riaprire la finestra dopo uno scaricamento interrotto."
        asset = updates.Asset(updates.ITALIAN_NOTES, self.project.downloads + "v2.0/" + updates.ITALIAN_NOTES, 0)
        self.release.assets.append(asset)
        with mock.patch.object(updates, "_get") as get:
            get.return_value.__enter__.return_value.read.return_value = (
                f"<!-- release-notes: it v2.0 -->\n\n### Correzioni\n- {sentence}").encode()
            self.release = updates.with_localized_notes(self.project, self.release, "it-IT")
        dialog = self.make_dialog("portable")
        self.assertEqual([row.get_title() for row in dialog.note_rows], [sentence])
