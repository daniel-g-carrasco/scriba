#!/usr/bin/env python3
"""Where a produced file goes: the first free name, never over a file that
is there (also one that appears meanwhile), whole or not at all."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.data import files


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
            with mock.patch("os.link", someone_else_first):
                path = files.write_new(folder, "call", ".md", "mine")
            self.assertEqual(path.name, "call_R01.md")
            self.assertEqual((folder / "call.md").read_text(encoding="utf-8"), "theirs")
            self.assertEqual(path.read_text(encoding="utf-8"), "mine")
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["call.md", "call_R01.md"])

    def test_a_disk_without_hard_links_gets_the_file_by_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("os.link", side_effect=OSError(1, "Operation not permitted")):
                path = files.write_new(tmp, "call", ".md", "testo\n")
            self.assertEqual(path.read_bytes(), b"testo\n")
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["call.md"])

    def test_a_write_that_fails_leaves_nothing_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("os.fsync", side_effect=OSError(28, "No space left on device")):
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


if __name__ == "__main__":
    unittest.main()
