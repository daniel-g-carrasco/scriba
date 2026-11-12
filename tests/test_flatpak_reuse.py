"""The Flatpak repository imports only an already published, signed release."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _setup


class ReuseReleaseTest(unittest.TestCase):
    def run_step(self, state="published", failed_signature=""):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash required for workflow checks")
        workflow = (_setup.ROOT / ".github/workflows/flatpak-repo.yml").read_text(encoding="utf-8")
        match = re.search(r"- name: Fetch and verify the published release\n.*?        run: \|\n(.*?)(?=\n      - |\Z)", workflow, re.S)
        self.assertIsNotNone(match)
        script = "\n".join(line[10:] for line in match.group(1).splitlines())
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "gh").write_text('#!/bin/sh\nif [ "$2" = view ]; then\n'
                                 ' case "$STATE" in published) echo false;; draft) echo true;; *) exit 1;; esac\n'
                                 'else echo "$@" >> calls; fi\n', encoding="utf-8", newline="\n")
        (tmp / "sleep").write_text('#!/bin/sh\nexit 0\n', encoding="utf-8", newline="\n")
        (tmp / "minisign").write_text('#!/bin/sh\necho "$@" >> signatures\n'
                                       'case "$*" in *"$BAD_SIGNATURE"*) [ -z "$BAD_SIGNATURE" ];; *) exit 0;; esac\n',
                                       encoding="utf-8", newline="\n")
        setup = (f'export STATE={state} BAD_SIGNATURE="{failed_signature}"\n'
                 'export GITHUB_REF_NAME=v1.2.3 GITHUB_REPOSITORY=me/app PUBLIC_KEY=trusted\n'
                 'export PATH="$PWD:$PATH"\nchmod +x gh sleep minisign\n')
        (tmp / "step.sh").write_text("set -eu\n" + setup + script, encoding="utf-8", newline="\n")
        result = subprocess.run([bash, "step.sh"], cwd=tmp, text=True, capture_output=True)
        return result, {name: (tmp / name).read_text() if (tmp / name).exists() else ""
                        for name in ("calls", "signatures")}

    def test_published_bundle_and_matching_sources_are_both_verified(self):
        result, files = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release download v1.2.3", files["calls"])
        self.assertIn("scriba-v1.2.3-linux-x86_64-sources.tar.xz", files["calls"])
        self.assertEqual(len(files["signatures"].splitlines()), 2)

    def test_draft_or_unavailable_release_never_downloads_or_publishes(self):
        for state in ("draft", "absent"):
            with self.subTest(state=state):
                result, files = self.run_step(state)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(files["calls"])
                self.assertFalse(files["signatures"])

    def test_invalid_bundle_or_sources_signature_fails(self):
        for asset in ("scriba.flatpak", "sources.tar.xz"):
            with self.subTest(asset=asset):
                result, _ = self.run_step(failed_signature=asset)
                self.assertNotEqual(result.returncode, 0)

    def test_repository_workflow_never_rebuilds_the_bundle(self):
        text = (_setup.ROOT / ".github/workflows/flatpak-repo.yml").read_text(encoding="utf-8")
        self.assertNotIn("flatpak-builder", text)
        self.assertIn("timeout-minutes: 90", text)
        self.assertIn('--bundle scriba.flatpak --app-commit "$GITHUB_SHA"', text)
        self.assertIn('--verify-pair "scriba-${GITHUB_REF_NAME}-linux-x86_64-sources.tar.xz"', text)
