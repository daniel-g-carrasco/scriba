#!/usr/bin/env python3
"""Scriba's own side of the updates: its project finds its own installer
in a release, the CI checks the signatures with the key the app trusts,
that key is not the tests' one, its changelog reads as the update window
shows it. The rest is the shared module's test (test_updates.py, a copy of
the model's, with a made-up project)."""

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.response import addinfourl

import _setup
from test_minisign import PAYLOAD, SIGNED_V9, digest_of

from scriba import PUBLIC_KEY as PROJECT_KEY, SLUG     # the real key: the tests sign with a key of their own
from scriba.app import minisign, services, update_release, updates

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
PROJECT = services.project()                    # this app's, with its real key


class ProjectTest(unittest.TestCase):
    def test_scribas_project_finds_its_own_installer_in_its_release(self):
        release = updates.parse_release(PROJECT, RELEASE)
        self.assertEqual((release.version, release.notes), ("0.4.0", "What is new"))
        installer = updates.installer_of(PROJECT, release)
        self.assertEqual(installer.name, SETUP_NAME)
        self.assertEqual(installer.sha256, "BB")
        self.assertEqual(installer.signature_url, RELEASE["assets"][3]["browser_download_url"])
        self.assertEqual(PROJECT.installer_name("v0.4.0"), SETUP_NAME)
        self.assertEqual(PROJECT.dev_release_variable, "SCRIBA_DEV_RELEASE_API")


class ReleaseWebFallbackTest(unittest.TestCase):
    def check(self, status, *, checksum=None, signature=True, redirect=None, italian=None):
        latest = PROJECT.releases_url + "/latest"
        assets = PROJECT.releases_url + "/expanded_assets/v0.4.0"
        installer = DOWNLOADS + SETUP_NAME
        path = installer.removeprefix("https://github.com")
        checksum = hashlib.sha256(PAYLOAD).hexdigest() if checksum is None else checksum
        listing = f'<li><a href="{path}">{SETUP_NAME}</a><span>sha256:{checksum}</span></li>'
        if signature:
            listing += f'<li><a href="{path}.minisig">{SETUP_NAME}.minisig</a></li>'
        if italian is not None:
            notes_url = DOWNLOADS + updates.ITALIAN_NOTES
            notes_path = notes_url.removeprefix("https://github.com")
            digest = hashlib.sha256(italian).hexdigest()
            listing += f'<li><a href="{notes_path}">Italian</a><span>sha256:{digest}</span></li>'
        responses = {
            latest: (redirect or RELEASE["html_url"], b""),
            assets: (assets, listing.encode()),
            installer: (installer, PAYLOAD),
            installer + ".minisig": (installer + ".minisig", SIGNED_V9.encode()),
        }

        if italian is not None:
            responses[notes_url] = (notes_url, italian)

        def get(request, **_kwargs):
            if request.full_url == PROJECT.latest_release_api:
                raise HTTPError(request.full_url, status, "GitHub answer", {}, None)
            url, body = responses[request.full_url]
            return addinfourl(io.BytesIO(body), {}, url)

        return mock.patch.object(updates.urllib.request, "urlopen", side_effect=get)

    def test_api_limits_fall_back_to_the_latest_page_and_its_signed_installer(self):
        for status in (403, 429):
            with self.subTest(status=status), self.check(status):
                release = update_release.latest_release(PROJECT)
                self.assertEqual((release.version, release.url), ("0.4.0", RELEASE["html_url"]))
                installer = updates.installer_of(PROJECT, release)
                self.assertIsNotNone(installer)
                self.assertEqual(installer.url, DOWNLOADS + SETUP_NAME)
                self.assertEqual(installer.signature_url, DOWNLOADS + SETUP_NAME + ".minisig")
                self.assertEqual(installer.sha256, hashlib.sha256(PAYLOAD).hexdigest())

    def test_the_web_fallback_discovers_notes_for_the_same_tag(self):
        italian = b"<!-- release-notes: it v0.4.0 -->\n\n### Correzioni\n- Il lavoro rimane salvato."
        with self.check(403, italian=italian):
            release = update_release.latest_release(PROJECT)
            local = updates.with_localized_notes(PROJECT, release, "it")
        self.assertEqual(local.tag, "v0.4.0")
        self.assertEqual(updates.notes_sections(local.notes), [("Correzioni", ["Il lavoro rimane salvato."])])

    def test_another_status_or_a_foreign_redirect_does_not_produce_a_release(self):
        with self.check(500), self.assertRaisesRegex(updates.UpdateError, "500"):
            update_release.latest_release(PROJECT)
        with self.check(403, redirect="https://example.com/releases/tag/v0.4.0"), \
                self.assertRaises(updates.UpdateError):
            update_release.latest_release(PROJECT)

    def test_without_both_metadata_items_only_the_release_page_is_offered(self):
        for metadata in ({"checksum": ""}, {"signature": False}):
            with self.subTest(**metadata), self.check(403, **metadata):
                release = update_release.latest_release(PROJECT)
                self.assertEqual(release.url, RELEASE["html_url"])
                self.assertIsNone(updates.installer_of(PROJECT, release))

    def test_fallback_downloads_are_rejected_on_checksum_or_signature_mismatch(self):
        for checksum, error in (("0" * 64, "does not match"), (None, "not signed")):
            with self.subTest(error=error), self.check(403, checksum=checksum), tempfile.TemporaryDirectory() as tmp:
                installer = updates.installer_of(PROJECT, update_release.latest_release(PROJECT))
                dest = Path(tmp) / SETUP_NAME
                with self.assertRaisesRegex(updates.UpdateError, error):
                    updates.download(PROJECT, installer, dest)
                self.assertEqual(list(dest.parent.iterdir()), [])


class KeyTest(unittest.TestCase):
    def test_the_ci_checks_its_signatures_with_the_key_the_app_trusts(self):
        workflow = (_setup.ROOT / ".github" / "workflows" / "windows.yml").read_text(encoding="utf-8")
        self.assertIn(f"PUBLIC_KEY: {PROJECT_KEY}\n", workflow)

    def test_the_apps_key_is_not_the_one_the_tests_sign_with(self):
        with self.assertRaisesRegex(minisign.SignatureError, "another key"):
            minisign.verify(digest_of(PAYLOAD), SIGNED_V9, PROJECT_KEY)


class NotesTest(unittest.TestCase):
    def test_scribas_own_changelog_reads_as_rows(self):
        # the entries of 0.1.3 as Keep a Changelog writes them: items wrapped
        # over several lines, under Added, Changed and Fixed
        section = _setup.ROOT.joinpath("CHANGELOG.md").read_text(encoding="utf-8").split("## [0.1.3]", 1)[1]
        section = section.split("\n## [", 1)[0].split("\n", 1)[1]
        found = updates.notes_sections(section, limit=99)
        self.assertEqual([heading for heading, _items in found], ["Added", "Changed", "Fixed"])
        self.assertTrue(found[0][1][0].startswith("Files of terms: in Preferences, under Terms,"))
        self.assertTrue(all("\n" not in item and not item.startswith("-") for _h, items in found for item in items))


if __name__ == "__main__":
    unittest.main()
