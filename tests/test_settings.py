# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings' mechanism (app/settings.py): the file every app has always
written is read and written as it was, a damaged file is replaced by the
previous save and said, an older format is migrated, a newer one is kept
whole with its version, a section has its own version and migrations, and
who saves does not take back what another holder wrote meanwhile.

The fields are made up: the mechanism knows no setting."""

import copy
import json
import os
import shutil
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import _setup

settings = _setup.shared("app.settings")
Notice = settings.Notice

MIGRATIONS: dict = {}


@dataclass
class Plain(settings.Fields):
    theme: str = "system"
    check_updates: bool = True
    count: int = 0
    scale: float = 1.0
    recent: list = field(default_factory=list, metadata={"check": settings.list_of(str)})
    sizes: dict = field(default_factory=dict, metadata={"check": settings.table_of(int)})


PLAIN = settings.Store(Plain, version=3, migrations=MIGRATIONS)

KIT_MIGRATIONS = {1: lambda data: {**{k: v for k, v in data.items() if k != "colour"}, "accent": data.get("colour", "")}}


@dataclass
class Kit:
    accent: str = ""
    dense: bool = False


@dataclass
class WithKit(settings.Fields):
    theme: str = "system"
    last_folder: str = ""
    kit: Kit = field(default_factory=Kit)


def _to_kit(data: dict) -> dict:
    return settings.move_to_section(data, "kit", ["accent"], version=2)


KIT = settings.Section(Kit, version=2, migrations=KIT_MIGRATIONS)
WITH_KIT = settings.Store(WithKit, version=2, migrations={1: _to_kit}, sections={"kit": KIT})


@dataclass
class BeforeTheKit(settings.Fields):
    """The same app one version before: it knows no section."""
    theme: str = "system"
    last_folder: str = ""
    accent: str = ""


BEFORE = settings.Store(BeforeTheKit, version=1)


class InAFolder(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "c" / "config.json"

    def write(self, data) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def on_disk(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def left(self) -> list:
        return sorted(os.listdir(self.path.parent))


class TheFileOfAlways(InAFolder):
    def test_a_round_trip_keeps_the_unknown_keys_and_leaves_no_temporary_file(self):
        self.assertEqual(PLAIN.load(self.path).theme, "system")            # no file: the defaults, written at once
        self.assertTrue(self.path.exists())
        cfg = Plain(theme="dark", recent=["a", "b"], sizes={"w": 3})
        PLAIN.save(cfg, self.path)
        data = self.on_disk()
        data["future_key"] = 1
        self.write(data)
        again = PLAIN.load(self.path)
        self.assertEqual((again.theme, again.recent, again.sizes, again.notices), ("dark", ["a", "b"], {"w": 3}, []))
        self.assertEqual(again.extra, {"future_key": 1})
        PLAIN.save(again, self.path)
        self.assertEqual(self.left(), ["config.json", "config.json.bak"])
        data = self.on_disk()
        self.assertEqual((data["future_key"], data["version"]), (1, 3))
        self.assertEqual(again, cfg)                                         # the unknown keys are no setting

    def test_the_file_is_the_flat_object_with_the_version_last_and_unix_line_ends(self):
        PLAIN.save(Plain(theme="dark", scale=2), self.path)
        self.assertEqual(self.path.read_bytes(),
                         b'{\n  "theme": "dark",\n  "check_updates": true,\n  "count": 0,\n  "scale": 2,\n'
                         b'  "recent": [],\n  "sizes": {},\n  "version": 3\n}\n')

    def test_the_fields_base_gives_the_dict_and_reads_one(self):
        data = Plain(theme="dark").to_json()
        self.assertEqual((data["theme"], data["version"]), ("dark", 3))
        self.assertEqual(Plain.from_json({"theme": "light", "new": 1, "version": 3}).extra, {"new": 1})
        with self.assertRaises(ValueError):
            Plain.from_json([1, 2])

        @dataclass
        class NoStore(settings.Fields):
            a: int = 0
        with self.assertRaises(TypeError):
            NoStore().to_json()

    def test_a_copy_carries_the_unknown_keys(self):
        self.write({"theme": "dark", "future_key": 1, "version": 3})
        cfg = copy.deepcopy(PLAIN.load(self.path))
        cfg.count = 2
        PLAIN.save(cfg, self.path)
        self.assertEqual((self.on_disk()["future_key"], self.on_disk()["count"]), (1, 2))


class ADamagedFile(InAFolder):
    def test_the_previous_save_is_kept_and_used_and_it_is_said(self):
        PLAIN.save(Plain(theme="dark"), self.path)
        PLAIN.save(Plain(theme="light"), self.path)
        self.assertEqual(PLAIN.load(settings.backup_file(self.path)).theme, "dark")       # the save before the last
        self.assertEqual(PLAIN.load(self.path).notices, [])
        self.write('{"theme": "light", "lang')                              # a crash mid-write elsewhere
        again = PLAIN.load(self.path)
        self.assertEqual((again.theme, again.notices[0].kind), ("dark", "recovered"))
        aside = Path(again.notices[0].detail)
        self.assertTrue(aside.name.startswith("config.json.broken-"), aside)
        self.assertIn("JSONDecodeError", again.notices[0].reason)
        self.assertEqual(PLAIN.load(self.path).notices, [])                 # written back at once
        self.path.unlink()                                                  # the file gone: the previous save stands in
        again = PLAIN.load(self.path)
        self.assertEqual((again.theme, again.notices), ("dark", [Notice("missing")]))
        self.assertTrue(self.path.exists())

    def test_with_nothing_to_recover_the_app_starts_anew_and_says_so(self):
        self.write("[1, 2]")
        cfg = PLAIN.load(self.path)
        self.assertEqual((cfg.theme, cfg.notices[0].kind), ("system", "fresh"))
        self.assertEqual(Path(cfg.notices[0].detail).read_text(encoding="utf-8"), "[1, 2]")

    def test_a_value_of_another_type_is_damage_not_a_crash_later(self):
        PLAIN.save(Plain(), self.path)
        PLAIN.save(Plain(), self.path)                                      # now there is a .bak to fall back on
        for bad in ({"check_updates": 1}, {"theme": 3}, {"count": True}, {"count": 1.5}, {"scale": "big"},
                    {"scale": float("inf")}, {"recent": "a"}, {"recent": [1]}, {"sizes": {"w": "3"}}, {"sizes": []},
                    {"version": "2"}, {"version": True}):
            self.write(json.dumps({"version": 3, **bad}))
            self.assertEqual(PLAIN.load(self.path).notices[0].kind, "recovered", bad)
        self.write({"scale": 2, "version": 3})                              # a whole number is a number
        self.assertEqual((PLAIN.load(self.path).scale, PLAIN.load(self.path).notices), (2, []))

    def test_a_backup_that_cannot_be_made_does_not_stop_the_save(self):
        PLAIN.save(Plain(theme="dark"), self.path)
        with mock.patch.object(shutil, "copy2", side_effect=PermissionError(13, "denied")):
            PLAIN.save(Plain(theme="light"), self.path)
        self.assertEqual(self.on_disk()["theme"], "light")


class AnOlderAndANewerFormat(InAFolder):
    def test_an_older_format_goes_through_the_steps_one_by_one(self):
        self.write({"version": 1, "old_theme": "dark"})
        steps = {1: lambda d: {**d, "mid_theme": d.pop("old_theme")}, 2: lambda d: {**d, "theme": d.pop("mid_theme")}}
        with mock.patch.dict(MIGRATIONS, steps):
            cfg = PLAIN.load(self.path)
        self.assertEqual((cfg.theme, cfg.notices), ("dark", []))
        PLAIN.save(cfg, self.path)
        self.assertEqual(self.on_disk()["version"], 3)
        self.write({"old_theme": "dark"})                                   # no version: the first format
        with mock.patch.dict(MIGRATIONS, steps):
            self.assertEqual(PLAIN.load(self.path).theme, "dark")
        PLAIN.save(Plain(), self.path)
        PLAIN.save(Plain(), self.path)                                      # a good previous save to fall back on
        self.write({"version": 1})
        notice = PLAIN.load(self.path).notices[0]                           # no step for it: as damaged
        self.assertEqual(notice.kind, "recovered")
        self.assertIn("no migration from format version 1", notice.reason)

    def test_a_newer_format_is_read_for_what_is_known_and_keeps_its_version(self):
        self.write({"version": 7, "theme": "dark", "brand_new": True})
        cfg = PLAIN.load(self.path)
        self.assertEqual((cfg.theme, cfg.notices), ("dark", [Notice("newer", "7")]))
        cfg.count = 4
        PLAIN.save(cfg, self.path)
        data = self.on_disk()
        # written back whole, with its own number: the newer app must not migrate again what is already its format
        self.assertEqual((data["brand_new"], data["count"], data["version"]), (True, 4, 7))
        self.assertEqual(PLAIN.load(self.path).notices, [Notice("newer", "7")])

    def test_a_known_field_of_another_shape_in_a_newer_format_is_kept_not_damage(self):
        self.write({"version": 7, "theme": {"name": "dark", "contrast": 2}, "count": 5})
        cfg = PLAIN.load(self.path)
        self.assertEqual((cfg.theme, cfg.count), ("system", 5))              # the default stands for this run
        self.assertEqual([n.kind for n in cfg.notices], ["newer"])
        PLAIN.save(cfg, self.path)
        self.assertEqual(self.on_disk()["theme"], {"name": "dark", "contrast": 2})
        cfg.theme = "light"                                                 # the user changes that very setting
        PLAIN.save(cfg, self.path)
        self.assertEqual(self.on_disk()["theme"], "light")


class Sections(InAFolder):
    def test_a_section_is_an_object_with_its_own_version(self):
        cfg = WithKit(theme="dark")
        cfg.kit.accent = "#1c6e8c"
        WITH_KIT.save(cfg, self.path)
        self.assertEqual(self.on_disk(), {"theme": "dark", "last_folder": "",
                                          "kit": {"accent": "#1c6e8c", "dense": False, "version": 2}, "version": 2})
        again = WITH_KIT.load(self.path)
        self.assertEqual((again, again.notices), (cfg, []))
        self.assertIsInstance(again.kit, Kit)

    def test_a_file_without_the_section_gives_its_defaults(self):
        self.write({"theme": "dark", "version": 2})
        cfg = WITH_KIT.load(self.path)
        self.assertEqual((cfg.theme, cfg.kit, cfg.notices), ("dark", Kit(), []))

    def test_a_section_migrates_alone_and_keeps_its_unknown_keys(self):
        self.write({"theme": "dark", "kit": {"colour": "red", "later": [1], "version": 1}, "version": 2})
        cfg = WITH_KIT.load(self.path)
        self.assertEqual((cfg.kit.accent, cfg.notices), ("red", []))
        WITH_KIT.save(cfg, self.path)
        self.assertEqual(self.on_disk()["kit"], {"later": [1], "accent": "red", "dense": False, "version": 2})

    def test_a_newer_section_is_said_with_its_name_and_keeps_its_version(self):
        self.write({"theme": "dark", "kit": {"accent": "red", "dense": "very", "version": 5}, "version": 2})
        cfg = WITH_KIT.load(self.path)
        self.assertEqual(cfg.notices, [Notice("newer", "5", section="kit")])
        self.assertEqual((cfg.kit.accent, cfg.kit.dense), ("red", False))
        WITH_KIT.save(cfg, self.path)
        self.assertEqual(self.on_disk()["kit"], {"accent": "red", "dense": "very", "version": 5})
        self.assertEqual(self.on_disk()["version"], 2)

    def test_a_section_that_is_not_an_object_is_damage(self):
        WITH_KIT.save(WithKit(), self.path)
        WITH_KIT.save(WithKit(), self.path)
        for bad in ({"kit": 3}, {"kit": {"accent": 1, "version": 2}}, {"kit": {"version": "2"}}):
            self.write({"version": 2, **bad})
            self.assertEqual(WITH_KIT.load(self.path).notices[0].kind, "recovered", bad)

    def test_the_fields_of_before_move_into_the_section_and_nothing_is_lost(self):
        BEFORE.save(BeforeTheKit(theme="dark", last_folder="/h/d", accent="red"), self.path)
        data = self.on_disk()
        data["future_key"] = 1
        self.write(data)
        cfg = WITH_KIT.load(self.path)                                      # the app's migration 1 -> 2
        self.assertEqual((cfg.theme, cfg.last_folder, cfg.kit.accent, cfg.notices), ("dark", "/h/d", "red", []))
        WITH_KIT.save(cfg, self.path)
        self.assertEqual(self.on_disk(), {"future_key": 1, "theme": "dark", "last_folder": "/h/d",
                                          "kit": {"accent": "red", "dense": False, "version": 2}, "version": 2})

    def test_the_app_of_before_keeps_a_section_it_does_not_know(self):
        cfg = WithKit(theme="dark")
        cfg.kit.accent = "red"
        WITH_KIT.save(cfg, self.path)
        old = BEFORE.load(self.path)                                        # a downgrade: version 2 read by version 1
        self.assertEqual((old.theme, [n.kind for n in old.notices]), ("dark", ["newer"]))
        old.last_folder = "/h/e"
        BEFORE.save(old, self.path)
        back = WITH_KIT.load(self.path)                                     # and the newer app again
        self.assertEqual((back.kit.accent, back.last_folder, back.notices), ("red", "/h/e", []))

    def test_the_definitions_are_checked_when_the_app_starts(self):
        @dataclass
        class Named:
            version: int = 1

        @dataclass
        class NoDefault:
            theme: str

        @dataclass
        class Holder:
            kit: Kit = field(default_factory=Kit)
            other: str = ""

        for build in (lambda: settings.Section(Named), lambda: settings.Section(NoDefault),
                      lambda: settings.Section(Holder, sections={"missing": KIT}),
                      lambda: settings.Section(Holder, sections={"other": KIT}),
                      lambda: settings.Section(Holder, sections={"kit": settings.Section(Holder, sections={"kit": KIT})})):
            with self.assertRaises(ValueError):
                build()
        with self.assertRaises(TypeError):
            settings.Section(dict)


class TwoHoldersOfTheSameFile(InAFolder):
    def test_who_saves_does_not_take_back_what_the_other_wrote(self):
        PLAIN.save(Plain(theme="dark"), self.path)
        first, second = PLAIN.load(self.path), PLAIN.load(self.path)
        first.count = 7
        PLAIN.save(first, self.path)
        second.theme = "light"
        PLAIN.save(second, self.path)
        self.assertEqual((self.on_disk()["count"], self.on_disk()["theme"]), (7, "light"))
        self.assertEqual(second.count, 7)                                   # the holder is brought up to date
        first.scale = 2.0
        PLAIN.save(first, self.path)                                        # and the first one takes the theme
        self.assertEqual((first.theme, self.on_disk()["theme"], self.on_disk()["scale"]), ("light", "light", 2.0))

    def test_when_both_changed_the_same_setting_the_last_save_wins(self):
        PLAIN.save(Plain(), self.path)
        first, second = PLAIN.load(self.path), PLAIN.load(self.path)
        first.theme, second.theme = "dark", "light"
        PLAIN.save(first, self.path)
        PLAIN.save(second, self.path)
        self.assertEqual(self.on_disk()["theme"], "light")

    def test_inside_a_section_and_among_the_unknown_keys_too(self):
        WITH_KIT.save(WithKit(), self.path)
        first, second = WITH_KIT.load(self.path), WITH_KIT.load(self.path)
        first.kit.accent = "red"
        WITH_KIT.save(first, self.path)
        data = self.on_disk()
        data["future_key"] = 1                                              # a newer app wrote a key meanwhile
        self.write(data)
        second.kit.dense = True
        WITH_KIT.save(second, self.path)
        self.assertEqual(self.on_disk()["kit"], {"accent": "red", "dense": True, "version": 2})
        self.assertEqual((self.on_disk()["future_key"], second.kit.accent), (1, "red"))

    def test_a_file_of_another_format_or_unreadable_is_not_merged(self):
        PLAIN.save(Plain(theme="dark"), self.path)
        cfg = PLAIN.load(self.path)
        cfg.count = 1
        self.write({"theme": "light", "version": 9})
        PLAIN.save(cfg, self.path)
        self.assertEqual((self.on_disk()["theme"], self.on_disk()["version"]), ("dark", 3))
        self.write("{half")
        PLAIN.save(cfg, self.path)
        self.assertEqual(self.on_disk()["theme"], "dark")

    def test_an_object_never_loaded_writes_the_whole_of_itself(self):
        PLAIN.save(Plain(theme="dark", count=3), self.path)
        PLAIN.save(Plain(theme="light"), self.path)
        self.assertEqual((self.on_disk()["theme"], self.on_disk()["count"]), ("light", 0))

    def test_adopt_brings_the_original_to_its_saved_copy(self):
        PLAIN.save(Plain(theme="dark"), self.path)
        cfg, other = PLAIN.load(self.path), PLAIN.load(self.path)
        other.count = 5
        PLAIN.save(other, self.path)
        recent = cfg.recent
        draft = copy.deepcopy(cfg)
        draft.theme = "light"
        PLAIN.save(draft, self.path)                                        # the copy is saved, and took the count
        PLAIN.adopt(cfg, draft)
        self.assertEqual((cfg.theme, cfg.count), ("light", 5))
        self.assertIs(cfg.recent, recent)                                   # what did not change keeps its object
        cfg.scale = 3.0
        PLAIN.save(cfg, self.path)
        self.assertEqual((self.on_disk()["theme"], self.on_disk()["count"], self.on_disk()["scale"]), ("light", 5, 3.0))


class TheMoveToASection(unittest.TestCase):
    def test_the_keys_leave_the_root_and_what_the_section_holds_stays(self):
        data = {"theme": "dark", "accent": "red", "dense": True, "kit": {"dense": False, "version": 2}, "version": 1}
        self.assertEqual(settings.move_to_section(data, "kit", ["accent", "dense", "absent"]),
                         {"theme": "dark", "kit": {"dense": False, "version": 2, "accent": "red"}, "version": 1})
        self.assertEqual(settings.move_to_section({"accent": "red"}, "kit", ["accent"], version=4),
                         {"kit": {"version": 4, "accent": "red"}})


if __name__ == "__main__":
    unittest.main()
