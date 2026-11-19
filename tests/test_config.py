#!/usr/bin/env python3
"""The settings file: a round trip, the settings of a newer version kept,
a damaged file replaced by the previous save and said, format versions."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.app import config
from scriba.app.config import Config, backup_file, load, save


class ConfigTest(unittest.TestCase):
    def test_retired_name_history_is_kept_as_an_unknown_setting_without_being_used(self):
        cfg = Config.from_json({"version": config.CONFIG_VERSION, "speaker_names": "Ada Example"})
        self.assertFalse(hasattr(cfg, "speaker_names"))
        self.assertEqual(cfg.to_json()["speaker_names"], "Ada Example")

    def test_roundtrip_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c" / "config.json"
            self.assertEqual(load(path).theme, "system")
            cfg = Config(theme="dark", last_folder="/h/d")
            save(cfg, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["future_key"] = 1
            path.write_text(json.dumps(data), encoding="utf-8")
            again = load(path)
            self.assertEqual((again.theme, again.last_folder), ("dark", "/h/d"))
            self.assertEqual(sorted(os.listdir(path.parent)), ["config.json", "config.json.bak"])   # no temp leftovers
            # what a newer version wrote survives a round trip through this one
            save(again, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((data["future_key"], data["version"]), (1, config.CONFIG_VERSION))
            self.assertNotIn("extra", data)
            self.assertNotIn("notices", data)

    def test_the_call_s_mute_is_followed_unless_the_user_switched_it_off(self):
        # 0.1.1's settings have no such key: the update takes the default, on
        self.assertTrue(Config().follow_call_mute)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"version": config.CONFIG_VERSION, "theme": "dark"}), encoding="utf-8")
            self.assertTrue(load(path).follow_call_mute)
            path.write_text(json.dumps({"version": config.CONFIG_VERSION, "follow_call_mute": False}),
                            encoding="utf-8")
            self.assertFalse(load(path).follow_call_mute)

    def test_the_previous_save_is_kept_and_used_when_the_file_is_damaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            save(Config(theme="dark"), path)
            save(Config(theme="light"), path)
            self.assertEqual(load(backup_file(path)).theme, "dark")      # the save before the last
            self.assertEqual(load(path).notices, [])
            path.write_text('{"theme": "light", "lang', encoding="utf-8")     # a crash mid-write elsewhere
            again = load(path)
            self.assertEqual(again.theme, "dark")
            self.assertEqual(again.notices[0].kind, "recovered")
            aside = Path(again.notices[0].detail)
            self.assertTrue(aside.name.startswith("config.json.broken-"), aside)
            self.assertIn("JSONDecodeError", again.notices[0].reason)
            self.assertTrue(path.exists())                               # written back at once
            self.assertEqual(load(path).notices, [])
            # the file gone, the previous save stands in
            path.unlink()
            again = load(path)
            self.assertEqual(again.theme, "dark")
            self.assertEqual(again.notices[0].kind, "missing")
            self.assertTrue(path.exists())

    def test_damaged_with_nothing_to_recover_starts_anew_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("[1, 2]", encoding="utf-8")
            cfg = load(path)
            self.assertEqual(cfg.theme, "system")
            self.assertEqual(cfg.notices[0].kind, "fresh")
            self.assertEqual(Path(cfg.notices[0].detail).read_text(encoding="utf-8"), "[1, 2]")
            save(cfg, path)                                             # now there is a .bak to fall back on
            # a value of the wrong kind is damage too, not a crash later on
            for bad in ('{"check_updates": 1}', '{"theme": 3}', '{"version": "2"}'):
                path.write_text(bad, encoding="utf-8")
                self.assertEqual(load(path).notices[0].kind, "recovered", bad)

    def test_format_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            # a newer format: read for what is known, written back whole, said
            path.write_text(json.dumps({"version": 7, "theme": "dark", "brand_new": True}), encoding="utf-8")
            cfg = load(path)
            self.assertEqual(cfg.theme, "dark")
            self.assertEqual([n.kind for n in cfg.notices], ["newer"])
            save(cfg, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((data["brand_new"], data["version"]), (True, 7))
            # an older format goes through the migration steps, one by one
            path.write_text(json.dumps({"version": 0, "old_theme": "dark"}), encoding="utf-8")
            with mock.patch.dict(config.MIGRATIONS, {0: lambda d: {**d, "theme": d.pop("old_theme")}}):
                cfg = load(path)
            self.assertEqual(cfg.theme, "dark")
            self.assertEqual(cfg.notices, [])
            path.write_text(json.dumps({"version": 0}), encoding="utf-8")
            self.assertEqual(load(path).notices[0].kind, "recovered")   # no step for it: as damaged

    def test_two_loaded_components_keep_each_others_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text('{"version": 1, "theme": "dark", "unknown": {"v": 1}}', encoding="utf-8")
            window = load(path)
            updater = load(path)
            window.theme = "light"
            save(window, path)
            updater.last_update_offered = "9.0.0"
            save(updater, path)
            updater.last_update_check = "2026-11-18T10:00:00"
            save(updater, path)
            final = load(path)
            self.assertEqual(final.theme, "light")
            self.assertEqual(final.last_update_offered, "9.0.0")
            self.assertEqual(final.last_update_check, "2026-11-18T10:00:00")
            self.assertEqual(final.extra, {"unknown": {"v": 1}})
            self.assertEqual(final.diarization_backend, "auto")

    def test_no_secret_is_ever_saved(self):
        self.assertNotIn("password", json.dumps(Config().to_json()).lower())


if __name__ == "__main__":
    unittest.main()
