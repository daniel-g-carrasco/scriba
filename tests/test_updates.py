#!/usr/bin/env python3
"""A newer version from the GitHub releases: versions, the release's files,
the installer downloaded and checked against GitHub's SHA-256 and the
project's signature, no more read than the release says."""

import dataclasses
import datetime
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup
from test_minisign import PAYLOAD, PUBLIC_KEY, SIGNED_V8, SIGNED_V9

from scriba import PUBLIC_KEY as PROJECT_KEY, SLUG     # the real key: the tests sign with a key of their own
from scriba.app import services, updates

DOWNLOADS = f"https://github.com/daniel-g-carrasco/{SLUG}/releases/download/v0.4.0/"
SETUP_NAME = f"{SLUG}-setup-v0.4.0-windows-x64.exe"
RELEASE = {
    "tag_name": "v0.4.0",
    "html_url": f"https://github.com/daniel-g-carrasco/{SLUG}/releases/tag/v0.4.0",
    "body": "What is new",
    "assets": [
        {"name": f"{SLUG}.flatpak", "browser_download_url": "https://example/app.flatpak", "size": 10,
         "digest": "sha256:aa"},
        {"name": f"{SLUG}-v0.4.0-windows-x64-portable.zip", "browser_download_url": "https://example/zip",
         "size": 20},
        {"name": SETUP_NAME, "browser_download_url": DOWNLOADS + SETUP_NAME, "size": 30, "digest": "sha256:BB"},
        {"name": SETUP_NAME + ".minisig", "browser_download_url": DOWNLOADS + SETUP_NAME + ".minisig", "size": 300},
    ],
}
SETUP, SIGNATURE = RELEASE["assets"][2], RELEASE["assets"][3]
NAME = "spola-setup-v9-windows-x64.exe"         # the name the test signatures give
PROJECT = services.project()                    # this app's, with its real key
SIGNED = dataclasses.replace(PROJECT, public_key=PUBLIC_KEY)    # the same, trusting the tests' key


class VersionTest(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(updates.is_newer("0.4.0", "0.3.10"))
        self.assertTrue(updates.is_newer("v0.3.11", "0.3.10"))
        self.assertFalse(updates.is_newer("0.3.10", "0.3.10"))
        self.assertFalse(updates.is_newer("0.3.9", "0.3.10"))           # not as text: 9 < 10
        self.assertTrue(updates.is_newer("1.0", "0.9.9"))
        self.assertEqual(updates.parse_version("v1.2.0-rc1"), (1, 2, 0))


class CheckDueTest(unittest.TestCase):
    """When a check made by itself is due: every few hours, never in a burst."""

    NOW = datetime.datetime(2026, 11, 14, 15, 30)

    def test_the_morning_check_of_0_1_2_does_not_hide_an_afternoon_release(self):
        # 0.1.2 wrote the date alone: a release out after the morning's check
        # waited until the next day; now the date is its midnight
        self.assertTrue(updates.check_due("2026-11-14", self.NOW))
        self.assertFalse(updates.check_due("2026-11-14", datetime.datetime(2026, 11, 14, 3, 0)))

    def test_a_check_is_due_after_the_hours_and_not_before(self):
        hours = updates.CHECK_EVERY_HOURS
        just_before = self.NOW - datetime.timedelta(hours=hours) + datetime.timedelta(minutes=1)
        self.assertFalse(updates.check_due(updates.check_stamp(just_before), self.NOW))
        self.assertTrue(updates.check_due(updates.check_stamp(self.NOW - datetime.timedelta(hours=hours)), self.NOW))
        self.assertFalse(updates.check_due(updates.check_stamp(self.NOW), self.NOW))

    def test_nothing_readable_or_a_time_to_come_makes_it_due(self):
        for last in ("", "  ", "yesterday", "2026-13-40", "2026-11-14T17:00:00"):
            with self.subTest(last=last):
                self.assertTrue(updates.check_due(last, self.NOW))

    def test_the_stamp_reads_back_as_the_time_of_the_check(self):
        stamp = updates.check_stamp(datetime.datetime(2026, 11, 14, 10, 23, 5, 123456))
        self.assertEqual(stamp, "2026-11-14T10:23:05")
        self.assertEqual(datetime.datetime.fromisoformat(stamp), datetime.datetime(2026, 11, 14, 10, 23, 5))


class ReleaseTest(unittest.TestCase):
    def test_the_release_and_its_installer(self):
        release = updates.parse_release(PROJECT, RELEASE)
        self.assertEqual((release.version, release.notes), ("0.4.0", "What is new"))
        installer = updates.installer_of(PROJECT, release)
        self.assertEqual(installer.name, SETUP_NAME)
        self.assertEqual(installer.sha256, "BB")
        self.assertEqual(installer.signature_url, SIGNATURE["browser_download_url"])
        self.assertIsNone(updates.installer_of(PROJECT, updates.parse_release(PROJECT, {**RELEASE, "assets": RELEASE["assets"][:2]})))
        with self.assertRaises(updates.UpdateError):
            updates.parse_release(PROJECT, {"assets": []})

    def test_only_a_released_installer_of_the_project_with_its_checksum_and_signature(self):
        def installer(signature=SIGNATURE, **changes):
            assets = [{**SETUP, **changes}] + ([signature] if signature else [])
            return updates.installer_of(PROJECT, updates.parse_release(PROJECT, {**RELEASE, "assets": assets}))
        self.assertIsNotNone(installer())
        self.assertIsNone(installer(digest=None))                                     # nothing to check it with
        self.assertIsNone(installer(signature=None))                                  # not signed
        self.assertIsNone(installer(signature={**SIGNATURE, "browser_download_url": "https://example.com/s.minisig"}))
        self.assertIsNone(installer(browser_download_url="https://example.com/" + SETUP_NAME))
        self.assertIsNone(installer(name=f"{SLUG}-setup-vdev-windows-x64.exe"))        # a CI build
        # another version's installer, duly signed, offered under a newer tag: not this release's
        self.assertIsNone(installer(name=f"{SLUG}-setup-v0.3.9-windows-x64.exe"))
        self.assertEqual(PROJECT.installer_name("v0.4.0"), SETUP_NAME)
        self.assertIsNone(installer(name=f"{SLUG}-setup-v0.4.0-evil-windows-x64.exe"))


class DownloadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.source = self.tmp / "setup.exe"
        self.addCleanup(updates.release_held)          # the verified installer is kept open
        self.source.write_bytes(PAYLOAD)
        self.signature = self.tmp / "setup.exe.minisig"
        self.signature.write_text(SIGNED_V9)

    def asset(self, **changes) -> updates.Asset:
        fields = {"name": NAME, "url": self.source.as_uri(), "size": len(PAYLOAD),
                  "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "signature_url": self.signature.as_uri()}
        return updates.Asset(**{**fields, **changes})

    def refused(self, asset: updates.Asset) -> None:
        dest = self.tmp / "got.exe"
        with self.assertRaises(updates.UpdateError):
            updates.download(SIGNED, asset, dest)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("got.exe.part").exists())

    def test_download_checked_against_its_sha256_and_signature(self):
        seen = []
        dest = updates.download(SIGNED, self.asset(), self.tmp / "got.exe", lambda done, total: seen.append((done, total)))
        self.assertEqual(dest.read_bytes(), PAYLOAD)
        self.assertEqual(seen[-1], (9000, 9000))

    def test_a_download_cut_short_or_without_checksum_is_refused(self):
        self.refused(self.asset(size=len(PAYLOAD) + 10))
        self.refused(self.asset(sha256=""))
        self.refused(self.asset(sha256="0" * 64))

    def test_no_more_is_read_than_the_release_says(self):
        seen = []
        with mock.patch.object(updates, "CHUNK", 1000), self.assertRaises(updates.UpdateError):
            updates.download(SIGNED, self.asset(size=3000), self.tmp / "got.exe", lambda done, total: seen.append(done))
        self.assertEqual(seen, [1000, 2000, 3000])           # the fourth chunk, past the size, not taken
        self.assertFalse((self.tmp / "got.exe.part").exists())

    def test_an_answer_too_big_for_a_release_is_refused(self):
        answer = self.tmp / "release.json"
        answer.write_text(json.dumps({**RELEASE, "body": "x" * updates.RELEASE_MAX_BYTES}))
        with mock.patch.dict(os.environ, {PROJECT.dev_release_variable: answer.as_uri()}):
            with self.assertRaises(updates.UpdateError):
                updates.latest_release(PROJECT)
            answer.write_text(json.dumps(RELEASE))
            self.assertEqual(updates.latest_release(PROJECT).version, "0.4.0")

    def test_an_installer_not_signed_for_this_release_is_refused(self):
        self.refused(self.asset(signature_url=""))
        # signed, with the name of another release's installer
        self.refused(self.asset(name="spola-setup-v8-windows-x64.exe"))
        self.signature.write_text(SIGNED_V8)
        self.refused(self.asset())
        # signed by a key that is not the project's
        self.signature.write_text(SIGNED_V9)
        dest = self.tmp / "got.exe"
        with self.assertRaises(updates.UpdateError):
            updates.download(PROJECT, self.asset(), dest)
        self.assertFalse(dest.exists())
        self.signature.write_text("Not Found")
        self.refused(self.asset())


class KeyTest(unittest.TestCase):
    def test_the_ci_checks_its_signatures_with_the_key_the_app_trusts(self):
        workflow = (_setup.ROOT / ".github" / "workflows" / "windows.yml").read_text(encoding="utf-8")
        self.assertIn(f"PUBLIC_KEY: {PROJECT_KEY}\n", workflow)


class InstalledTest(unittest.TestCase):

    @unittest.skipUnless(_setup.WINDOWS, "Windows only")
    def test_a_verified_installer_cannot_be_replaced_or_renamed_but_still_runs(self):
        import shutil
        import subprocess
        folder = Path(tempfile.mkdtemp(prefix=f"{SLUG}-hold-"))
        try:
            exe = folder / "held.exe"
            shutil.copy2(os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe"), exe)
            updates.hold(exe)
            with self.assertRaises(OSError):
                os.replace(exe, folder / "renamed.exe")                 # no delete sharing: no rename
            with self.assertRaises(OSError):
                open(exe, "r+b").close()                                # no write sharing
            with self.assertRaises(OSError):
                exe.unlink()
            self.assertTrue(exe.exists())
            done = subprocess.run([str(exe)], capture_output=True, timeout=30)   # Setup only reads it
            self.assertEqual(done.returncode, 0)
        finally:
            updates.release_held()
            shutil.rmtree(folder, ignore_errors=True)

    def test_installed_build_only_when_frozen(self):
        self.assertFalse(updates.installed_build())     # the tests run from the sources


if __name__ == "__main__":
    unittest.main()
