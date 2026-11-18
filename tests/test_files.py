#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a produced file goes: the first free name, never over a file that
is there (also one that appears meanwhile), whole or not at all; a set of
files moved to the revisions whole; a file of the app's own replaced."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import _setup

files = _setup.shared("data.files")


class FreeNameTest(unittest.TestCase):
    def test_the_name_itself_then_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(files.free_name(folder, "call", ".md"), folder / "call.md")
            (folder / "call.md").write_text("x")
            self.assertEqual(files.free_name(folder, "call", "md"), folder / "call_R01.md")
            (folder / "call_R01.md").write_text("x")
            self.assertEqual(files.free_name(folder, "call", ".md").name, "call_R02.md")
            self.assertEqual(files.free_name(folder, "call", ".srt").name, "call.srt")

    def test_a_name_with_letters_outside_ascii(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = files.write_new(tmp, "riunione con Jürgen è François", ".md", "ciao")
            self.assertEqual(path.name, "riunione con Jürgen è François.md")
            self.assertEqual(files.write_new(tmp, "riunione con Jürgen è François", ".md", "x").name,
                             "riunione con Jürgen è François_R01.md")


class WriteNewTest(unittest.TestCase):
    def test_a_file_that_appears_between_the_choice_and_the_write_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            real_link = os.link

            def someone_else_first(src, dst, *args, **kwargs):
                if Path(dst).name == "call.md":
                    Path(dst).write_text("theirs", encoding="utf-8")
                return real_link(src, dst, *args, **kwargs)
            with mock.patch.object(os, "link", someone_else_first):
                path = files.write_new(folder, "call", ".md", "mine")
            self.assertEqual(path.name, "call_R01.md")
            self.assertEqual((folder / "call.md").read_text(encoding="utf-8"), "theirs")
            self.assertEqual(path.read_text(encoding="utf-8"), "mine")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["call.md", "call_R01.md"])

    def test_a_disk_without_hard_links_gets_the_file_by_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(os, "link", side_effect=OSError(1, "Operation not permitted")):
                path = files.write_new(tmp, "call", ".md", "testo\n")
            self.assertEqual(path.read_bytes(), b"testo\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["call.md"])

    def test_a_write_that_fails_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(os, "fsync", side_effect=OSError(28, "No space left on device")):
                with self.assertRaises(OSError):
                    files.write_new(tmp, "call", ".md", "testo")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_the_text_is_written_as_it_is_on_every_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = files.write_new(tmp, "call", ".srt", "uno\ndue\n")
            self.assertEqual(path.read_bytes(), b"uno\ndue\n")


class OneNameForTheSetTest(unittest.TestCase):
    def test_the_files_of_one_transcription_share_the_first_name_free_for_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(files.free_stem(tmp, "call", (".md", ".srt")), "call")
            Path(tmp, "call.srt").write_text("of another day", encoding="utf-8")
            self.assertEqual(files.free_stem(tmp, "call", (".md", ".srt")), "call_R01")
            Path(tmp, "call_R01.md").write_text("x", encoding="utf-8")
            self.assertEqual(files.free_stem(tmp, "call", ("md", "srt")), "call_R02")


class ClaimTest(unittest.TestCase):
    def test_a_file_a_program_wrote_takes_a_free_name_and_the_temporary_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "call.mkv").write_bytes(b"the user's")
            made = files.temporary_name(tmp, ".mkv")
            made.write_bytes(b"video")
            path = files.claim(made, tmp, "call", ".mkv")
            self.assertEqual((path.name, path.read_bytes()), ("call_R01.mkv", b"video"))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["call.mkv", "call_R01.mkv"])
            self.assertEqual(Path(tmp, "call.mkv").read_bytes(), b"the user's")


class RevisionsTest(unittest.TestCase):
    def test_a_move_never_goes_over_a_file_that_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.md").write_text("mine", encoding="utf-8")
            Path(tmp, "b.md").write_text("theirs", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                files.move_new(Path(tmp, "a.md"), Path(tmp, "b.md"))
            self.assertEqual(Path(tmp, "b.md").read_text(encoding="utf-8"), "theirs")
            files.move_new(Path(tmp, "a.md"), Path(tmp, "c.md"))
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["b.md", "c.md"])

    def test_a_set_goes_to_the_revisions_under_one_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("call.md", "call.srt", "call_notes.md"):
                Path(tmp, name).write_text(name, encoding="utf-8")
            Path(tmp, "old").mkdir()
            Path(tmp, "old", "call_R01.srt").write_text("before", encoding="utf-8")
            moved = files.to_revisions(tmp, "call", ("", "_notes"), (".md", ".srt"), "old")
            # R01 is taken by one of them: the whole set takes R02
            self.assertEqual(sorted(p.name for p in moved), ["call_R02.md", "call_R02.srt", "call_R02_notes.md"])
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["old"])
            self.assertEqual(Path(tmp, "old", "call_R01.srt").read_text(encoding="utf-8"), "before")
            self.assertEqual(files.to_revisions(tmp, "call", ("",), (".md",), "old"), [])

    def test_a_set_that_cannot_be_moved_whole_is_put_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("call.md", "call.srt"):
                Path(tmp, name).write_text(name, encoding="utf-8")
            real = files.move_new

            def second_locked(source, dest):
                if str(source).endswith(".srt"):
                    raise PermissionError(13, "open in another program")
                return real(source, dest)
            with mock.patch.object(files, "move_new", second_locked), self.assertRaises(OSError):
                files.to_revisions(tmp, "call", ("",), (".md", ".srt"), "old")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["call.md", "call.srt", "old"])
            self.assertEqual(list(Path(tmp, "old").iterdir()), [])


class ReplaceTest(unittest.TestCase):
    def test_a_file_of_the_apps_own_is_replaced_whole_even_when_windows_holds_it_for_an_instant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "session.json")
            path.write_text("old", encoding="utf-8")
            real, refusals = os.replace, [PermissionError(13, "held")]

            def held_at_first(src, dst):
                if refusals:
                    raise refusals.pop()
                return real(src, dst)
            with mock.patch.object(os, "replace", side_effect=held_at_first), mock.patch.object(time, "sleep"):
                files.replace(path, "new\n")
            self.assertEqual(path.read_bytes(), b"new\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["session.json"])


if __name__ == "__main__":
    unittest.main()
