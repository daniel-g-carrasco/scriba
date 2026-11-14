#!/usr/bin/env python3
"""The terms whisper gets: the files of terms read (data/termfiles.py) and
joined with the meeting's (core/terms.py)."""

import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401

from scriba.core import terms
from scriba.data import termfiles


class JoinedTest(unittest.TestCase):
    def test_the_meetings_terms_come_first_and_a_term_is_given_once(self):
        chosen = terms.joined(("Officina Esempio", "CRAH"),
                              [["crah", "quadro  generale", "Officina esempio"], ["UPS", "Quadro generale"]])
        self.assertEqual(chosen.terms, ("Officina Esempio", "CRAH", "quadro generale", "UPS"))
        self.assertEqual((chosen.offered, chosen.left_out), (4, 0))

    def test_what_does_not_fit_is_cut_from_the_files_whole_terms_only(self):
        meeting = ("Cantiere Esempio",)
        files = [[f"termine {n:03d}" for n in range(100)]]          # 11 characters each, 13 with the comma
        chosen = terms.joined(meeting, files, limit=100)
        self.assertEqual(chosen.terms[0], "Cantiere Esempio")
        self.assertLessEqual(len(terms.SEPARATOR.join(chosen.terms)), 100)
        self.assertEqual(len(chosen.terms), 7)                      # 16 + 6 x 13 = 94; one more is 107
        self.assertEqual((chosen.offered, chosen.left_out), (101, 94))

    def test_with_nothing_given_nothing_is_offered(self):
        self.assertEqual(terms.joined((), []), terms.Joined((), 0))
        self.assertEqual(terms.joined(("  ",), [["", " "]]), terms.Joined((), 0))


class TermsFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_one_term_a_line_without_empty_lines_and_comments(self):
        path = self.folder / "mestiere.txt"
        path.write_bytes("﻿# del mestiere\r\nquadro generale\r\n\r\n  cavedi  \r\n#UPS\r\nàccento\r\n".encode())
        self.assertEqual(termfiles.read(path), ["quadro generale", "cavedi", "àccento"])

    def test_a_file_that_cannot_be_used_says_why(self):
        with self.assertRaisesRegex(termfiles.TermsFileError, "sparito.txt is not there"):
            termfiles.read(self.folder / "sparito.txt")
        latin = self.folder / "vecchio.txt"
        latin.write_bytes("perché\n".encode("cp1252"))
        with self.assertRaisesRegex(termfiles.TermsFileError, "vecchio.txt is not text in UTF-8"):
            termfiles.read(latin)
        big = self.folder / "registrazione.txt"
        big.write_bytes(b"x" * (termfiles.MAX_BYTES + 1))
        with self.assertRaisesRegex(termfiles.TermsFileError, "too large"):
            termfiles.read(big)
        with self.assertRaisesRegex(termfiles.TermsFileError, "is not there"):
            termfiles.read(self.folder)                             # a folder is no file of terms


if __name__ == "__main__":
    unittest.main()
