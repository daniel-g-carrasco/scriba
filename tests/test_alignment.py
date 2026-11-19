"""Require the approved GTK components even when a manifest disappears."""

import json
import unittest

import _setup


class AlignmentTest(unittest.TestCase):
    def test_both_adopted_groups_have_fingerprints(self):
        for group in ("comune", "gtk"):
            with self.subTest(group=group):
                path = _setup.ROOT / "impronte" / f"{group}.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["group"], group)
                self.assertTrue(manifest["files"], f"{group} manifest is empty")

    def test_required_gtk_copies_stay_in_the_manifest(self):
        path = _setup.ROOT / "impronte" / "gtk.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        source = f"src/{_setup.PACKAGE}/gui"
        required = {
            *(f"{source}/{module}.py" for module in
              ("about_widgets", "release_dialog", "secondaries", "sizing", "update_widgets")),
            *(f"tests/test_{test}.py" for test in
              ("collect_sources", "package_inventory", "release_dialog", "secondary_geometry")),
            *(f"tools/{tool}.py" for tool in
              ("check_alignment", "collect_sources", "package_inventory", "release_gate")),
        }
        have = {entry["target"] for entry in manifest["files"] if entry.get("sha256")}
        self.assertEqual(sorted(required - have), [])
