#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/release_notes.py: the notes of one version from a CHANGELOG, as
the release page and the update window show them."""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _setup

TOOL = _setup.ROOT / "tools" / "release_notes.py"
CHANGELOG = """# Changelog

## [Unreleased]

## [2.0.0] - 2026-11-15

### Fixed
- b

## [1.0.0] - 2026-11-01

- a

[2.0.0]: https://example.org/2.0.0
[1.0.0]: https://example.org/1.0.0
"""


def load():
    spec = importlib.util.spec_from_file_location("release_notes", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Notes(unittest.TestCase):
    def test_the_section_of_a_version_without_its_heading(self):
        tool = load()
        self.assertEqual(tool.notes("2.0.0", CHANGELOG), "### Fixed\n- b")
        self.assertEqual(tool.notes("1.0.0", CHANGELOG), "- a", "the links at the end are not the last section's")
        self.assertEqual(tool.notes("3.0.0", CHANGELOG), "")
        self.assertEqual(tool.notes("2.0", CHANGELOG), "", "a version is matched whole")

    def run_tool(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, encoding="utf-8")

    def test_paired_notes_keep_the_english_body_and_bind_the_italian_asset_to_the_tag(self):
        italian = CHANGELOG.replace("### Fixed\n- b", "### Correzioni\n- È stato corretto il problema.")
        expected = "<!-- release-notes: it v2.0.0 -->\n\n### Correzioni\n- È stato corretto il problema.\n"
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            en, it, output = root / "CHANGELOG.md", root / "CHANGELOG.it.md", root / "release-notes.it.md"
            en.write_text(CHANGELOG, encoding="utf-8")
            it.write_text(italian, encoding="utf-8")
            found = self.run_tool("v2.0.0", str(en), "--italian-input", str(it), "--italian-output", str(output))
            self.assertEqual((found.returncode, found.stdout), (0, "### Fixed\n- b\n"), found.stderr)
            self.assertEqual(output.read_text(encoding="utf-8"), expected)
        bad = italian.replace("Correzioni", "Modifiche")
        with self.assertRaisesRegex(ValueError, "categories differ"):
            load().paired_notes("2.0.0", CHANGELOG, bad)

    def test_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "CHANGELOG.md"
            path.write_text(CHANGELOG, encoding="utf-8")
            found = self.run_tool("v2.0.0", str(path))           # the tag's name, as the workflow gives it
            self.assertEqual((found.returncode, found.stdout), (0, "### Fixed\n- b\n"))
            missing = self.run_tool("3.0.0", str(path))
            self.assertEqual(missing.returncode, 1)
            self.assertIn("no section for 3.0.0", missing.stderr)
        self.assertEqual(self.run_tool().returncode, 2, "no version: the usage")


if __name__ == "__main__":
    unittest.main()
