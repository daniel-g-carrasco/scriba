#!/usr/bin/env python3
"""The subtitles: with rounded timestamps, never over a file
that is there, and read back from what this and other programs write."""

import tempfile
import unittest
from pathlib import Path

import _setup

from scriba.core import segments as sg
from scriba.core.segments import Segment, Turn
from scriba.data import srt
from scriba.data.lines import UNSPECIFIED, Cue

from test_segments import fixture


def voiced_fixture():
    segs, turns = fixture()
    return sg.number_speakers(sg.assign_speakers(sg.spoken(segs), turns))


class WriteTest(unittest.TestCase):
    def test_subtitles_with_voices_match_the_checked_fixture(self):
        self.assertEqual(srt.render(voiced_fixture()), _setup.fixture_text("invented_call.srt"))

    def test_subtitles_without_voices_match_the_checked_fixture(self):
        segs, _turns = fixture()
        self.assertEqual(srt.render(sg.spoken(segs)), _setup.fixture_text("invented_call_novoices.srt"))

    def test_a_named_voice_is_written_with_its_name(self):
        text = srt.render([Segment(0, 1, "Ciao.", "Zoë Ågren")])
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:01,000\nZoë Ågren: Ciao.\n")

    def test_a_doubtful_line_is_marked_after_the_voice(self):
        line = Segment(0, 1, "Grazie a tutti.", "Speaker 2", doubts=(sg.REPEATED,))
        self.assertIn("\nP2: (?) Grazie a tutti.\n", srt.render([line]))
        self.assertIn("\nP2: Grazie a tutti.\n", srt.render([line], mark_doubtful=False))
        self.assertIn("\nGrazie a tutti.\n", srt.render([line], speakers=False, mark_doubtful=False))

    def test_lines_without_words_are_left_out_and_not_numbered(self):
        text = srt.render([Segment(0, 1, "  "), Segment(1, 2, "uno"), Segment(2, 3, "")])
        self.assertEqual(text, "1\n00:00:01,000 --> 00:00:02,000\nuno\n")
        self.assertEqual(srt.render([]), "")

    def test_an_empty_line_inside_a_text_does_not_cut_the_cue(self):
        text = srt.render([Segment(0, 1, "prima riga\n\n  seconda riga  ")])
        self.assertEqual(text, "1\n00:00:00,000 --> 00:00:01,000\nprima riga\n  seconda riga\n")

    def test_a_file_that_is_there_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "call.srt").write_text("mine", encoding="utf-8")
            first = srt.write(tmp, "call", voiced_fixture())
            second = srt.write(tmp, "call", voiced_fixture())
            self.assertEqual((first.name, second.name), ("call_R01.srt", "call_R02.srt"))
            self.assertEqual((Path(tmp) / "call.srt").read_text(encoding="utf-8"), "mine")
            self.assertEqual(first.read_bytes().decode("utf-8"), _setup.fixture_text("invented_call.srt"))
            self.assertNotIn(b"\r\n", first.read_bytes())


class ReadTest(unittest.TestCase):
    def test_what_is_written_is_read_back(self):
        lines = voiced_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            cues = srt.read(srt.write(tmp, "call", lines))
        self.assertEqual([(c.text, c.speaker) for c in cues], [(s.text, s.speaker) for s in lines])
        for cue, seg in zip(cues, lines, strict=True):
            self.assertAlmostEqual(cue.start, seg.start, delta=0.0011)
        self.assertIn("-->", cues[2].text)

    def test_windows_line_ends_a_bom_and_extra_empty_lines(self):
        text = ("﻿\r\n\r\n1\r\n00:00:01,000 --> 00:00:02,500\r\nP1: prima\r\n\r\n\r\n\r\n"
                "2\r\n00:00:03,000 --> 00:00:04,000\r\nseconda\r\n\r\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.srt"
            path.write_bytes(text.encode("utf-8"))
            cues = srt.read(path)
        self.assertEqual(cues, [Cue(1.0, 2.5, "prima", "Speaker 1"), Cue(3.0, 4.0, "seconda")])

    def test_a_text_on_several_lines_and_a_time_inside_the_text(self):
        text = ("1\n00:00:01,000 --> 00:00:02,000 X1:10 X2:20\nuno --> due\n00:00:05,000 --> 00:00:06,000\n42\n\n"
                "2\n00:00:03.000 --> 00:00:04.000\n\nseguito dopo una riga vuota\n")
        cues = srt.parse(text)
        self.assertEqual(cues[0].text, "uno --> due\n00:00:05,000 --> 00:00:06,000\n42")
        self.assertEqual(cues[1], Cue(3.0, 4.0, "seguito dopo una riga vuota"))

    def test_cues_without_numbers_or_without_the_empty_line_between(self):
        text = "00:00:01,000 --> 00:00:02,000\nuno\n2\n00:00:03,000 --> 00:00:04,000\ndue\n"
        self.assertEqual([c.text for c in srt.parse(text)], ["uno", "due"])

    def test_a_name_is_taken_as_a_voice_only_when_it_is_one_of_the_names_given(self):
        text = ("1\n00:00:01,000 --> 00:00:02,000\nNota: il quadro.\n\n"
                "2\n00:00:02,000 --> 00:00:03,000\nAnna Maria: sì.\n")
        self.assertEqual([c.speaker for c in srt.parse(text)], [None, None])
        cues = srt.parse(text, names=["Anna", "Anna Maria"])
        self.assertEqual([(c.speaker, c.text) for c in cues], [(None, "Nota: il quadro."), ("Anna Maria", "sì.")])
        self.assertEqual(srt.parse(text.replace("Nota", "P4"), speakers=False)[0].text, "P4: il quadro.")

    def test_a_doubtful_mark_is_read_back(self):
        cues = srt.parse("1\n00:00:01,000 --> 00:00:02,000\nP3: (?) Grazie a tutti.\n")
        self.assertEqual(cues, [Cue(1.0, 2.0, "Grazie a tutti.", "Speaker 3", (UNSPECIFIED,))])

    def test_an_empty_file_has_no_cues(self):
        self.assertEqual(srt.parse(""), [])
        self.assertEqual(srt.parse("﻿\n\n"), [])

    def test_what_is_not_subtitles_is_refused_with_a_sentence(self):
        for text in ("Just a note.\n", "# Transcript\n\n[00:00] hello\n"):
            with self.subTest(text), self.assertRaises(srt.SrtError) as caught:
                srt.parse(text)
            self.assertIn("not a subtitle file", str(caught.exception))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "latin1.srt"
            path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nperché\n".encode("latin-1"))
            with self.assertRaises(srt.SrtError):
                srt.read(path)


class ShapeTest(unittest.TestCase):
    def test_core_segments_and_read_cues_have_the_shape_the_writers_take(self):
        from scriba.data.lines import Line
        self.assertIsInstance(Segment(0, 1, "x"), Line)
        self.assertIsInstance(Cue(0, 1, "x"), Line)
        self.assertNotIsInstance(Turn(0, 1, "A"), Line)

    def test_the_voice_label_is_the_same_in_core_and_data(self):
        from scriba.data import lines
        self.assertEqual(lines.SPEAKER_LABEL, sg.SPEAKER_LABEL)


if __name__ == "__main__":
    unittest.main()
