#!/usr/bin/env python3
"""The transcript in Markdown and in plain text: trascrivi.py's rows, a
header that says how far to trust them, the doubtful lines marked."""

import datetime
import tempfile
import unittest
from pathlib import Path

import _setup

from scriba.core import segments as sg
from scriba.core import speakers
from scriba.core.segments import Segment
from scriba.data import markdown, txt
from scriba.data.markdown import Header

from test_srt import voiced_fixture
from test_segments import fixture

HEADER = Header(source="C:/Riunioni/2026-11-11 call_prova.mkv", duration=3727.4, language="it",
                language_probability=0.97, model="large-v3-turbo", device="cpu",
                when=datetime.datetime(2026, 11, 11, 14, 5), terms=("QG-01", "CRAH"), speakers_engine="sherpa-onnx")


def body_of(text: str) -> str:
    return text.split("\n---\n\n", 1)[1]


class MarkdownTest(unittest.TestCase):
    def test_the_same_rows_as_trascrivi_with_voices(self):
        self.assertEqual(body_of(markdown.render(HEADER, voiced_fixture())),
                         _setup.fixture_text("invented_call_body.md"))

    def test_the_same_rows_as_trascrivi_without_voices(self):
        segs, _turns = fixture()
        self.assertEqual(body_of(markdown.render(HEADER, sg.spoken(segs))),
                         _setup.fixture_text("invented_call_novoices_body.md"))

    def test_the_header_tells_the_file_the_run_and_the_warning(self):
        lines = markdown.render(HEADER, voiced_fixture()).split("\n")
        self.assertEqual(lines[:8], [
            "# Transcript: 2026-11-11 call_prova.mkv",
            "",
            "- File: `C:/Riunioni/2026-11-11 call_prova.mkv`",
            "- Duration: 1:02:07 | language: it (97%) | model: large-v3-turbo on cpu | 11/11/2026 14:05",
            "- Terms given: QG-01, CRAH",
            "- Speakers: 3 voices told apart automatically (sherpa-onnx), anonymous labels in order of appearance; "
            "the names are to be matched by listening. The attributions are to be checked against the audio.",
            "- Automatic transcription: names, acronyms and numbers are to be checked against the audio.",
            "",
        ])
        self.assertEqual(lines[8:10], ["---", ""])

    def test_without_voices_or_terms_the_header_says_so(self):
        text = markdown.render(Header(source="a.wav", duration=5), [Segment(0, 1, "uno")])
        self.assertIn("- Duration: 00:05\n- Terms given: none\n- Automatic transcription: who speaks is not told "
                      "apart;", text)
        self.assertIn("\n[00:00] uno\n", text)

    def test_one_voice_is_one_voice(self):
        text = markdown.render(HEADER, [Segment(0, 1, "uno", "Speaker 1")])
        self.assertIn("- Speakers: 1 voice told apart", text)

    def test_with_the_names_the_header_says_they_were_given_by_hand(self):
        named = speakers.rename(voiced_fixture(), {"Speaker 1": "Zoë", "Speaker 3": "Jürgen"})
        text = markdown.render(Header(source="a.wav", duration=5, named=True), named)
        self.assertIn("with the names given by hand", text)
        self.assertIn("**Zoë** [00:00]\n", text)
        self.assertIn("**Speaker 2** [00:03]\n", text)
        self.assertIn("**Jürgen** [59:59]\n", text)

    def test_the_voices_can_be_shown_by_another_name_while_the_lines_keep_their_labels(self):
        lines = voiced_fixture()
        shown = {"Speaker 1": "Parlante 1", "Speaker 2": "Parlante 2", "Speaker 3": "Parlante 2"}
        text = markdown.render(HEADER, lines, names=shown)
        self.assertIn("**Parlante 1** [00:00]\n", text)
        self.assertNotIn("**Speaker", text)
        self.assertIn("- Speakers: 2 voices", text)         # two labels shown as one are one voice
        self.assertEqual(lines[0].speaker, "Speaker 1")
        self.assertIn("\nParlante 2 [00:03]\n", txt.render(HEADER, lines, names=shown))

    def test_a_doubtful_line_keeps_its_words_and_says_why(self):
        lines = sg.flag_doubtful([Segment(t, t + 1, "Grazie a tutti.", "Speaker 1", avg_logprob=-1.5)
                                  for t in range(3)] + [Segment(5, 6, "Bene.", "Speaker 1")])
        text = markdown.render(HEADER, lines)
        self.assertIn("\n[00:00] (?) Grazie a tutti. _(words uncertain, repeated many times)_\n", text)
        self.assertIn("\n[00:05] Bene.\n", text)
        self.assertIn("- Lines marked (?) are doubtful", text)
        self.assertNotIn("Lines marked", markdown.render(HEADER, lines[3:]))

    def test_a_largely_doubtful_transcript_says_so_under_the_title_and_the_others_do_not(self):
        lines = [Segment(0, 30, " ".join(["allora"] * 300), "Speaker 1"), Segment(31, 33, "Bene.", "Speaker 1")]
        flagged = sg.flag_doubtful(lines)
        header = Header(source="a.wav", duration=40, doubtful_share=sg.doubt_warning(flagged))
        md = markdown.render(header, flagged).split("\n")
        self.assertEqual(md[:3], ["# Transcript: a.wav", "",
                                  "> **Warning: 100% of the text is doubtful (marked (?)): the engine may have "
                                  "invented or repeated words. Listen to the recording again before relying on "
                                  "this transcript.**"])
        self.assertIn("allora _(too much text for its length)_", "\n".join(md))
        plain = txt.render(header, flagged).split("\n")
        self.assertEqual(plain[0], "Transcript: a.wav")
        self.assertTrue(plain[1].startswith("Warning: 100% of the text is doubtful"))
        self.assertNotIn("Warning:", markdown.render(HEADER, voiced_fixture()))
        self.assertNotIn("Warning:", txt.render(HEADER, voiced_fixture()))

    def test_every_reason_of_the_core_has_its_words(self):
        self.assertEqual(sorted(set(sg.REASONS) - set(markdown.reasons())), [])

    def test_a_line_with_no_voice_among_voices_shows_a_question_mark(self):
        text = markdown.render(HEADER, [Segment(0, 1, "uno", "Speaker 1"), Segment(1, 2, "due")])
        self.assertIn("**Speaker 1** [00:00]\n[00:00] uno\n\n**?** [00:01]\n[00:01] due\n", text)

    def test_a_text_on_several_lines_is_one_row(self):
        text = markdown.render(HEADER, [Segment(0, 1, " prima\nseconda  ")])
        self.assertIn("\n[00:00] prima seconda\n", text)

    def test_no_segments_still_a_header(self):
        text = markdown.render(Header(source="vuoto.wav", duration=0), [])
        self.assertTrue(text.startswith("# Transcript: vuoto.wav\n"))
        self.assertTrue(text.endswith("\n---\n\n"))

    def test_a_long_meeting_of_six_voices(self):
        lines = [Segment(i * 18.5, i * 18.5 + 15, f"battuta {i} con àccenti", f"Speaker {i % 6 + 1}")
                 for i in range(200)]
        text = markdown.render(HEADER, lines)
        self.assertIn("- Speakers: 6 voices", text)
        self.assertEqual(text.count("\n["), 200)
        self.assertIn("\n[1:01:03] battuta 198 con àccenti\n", text)
        self.assertEqual(text.count("**Speaker"), 200)

    def test_the_file_is_new_and_never_over_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "call_transcript.md").write_text("mine", encoding="utf-8")
            path = markdown.write(tmp, "call_transcript", HEADER, voiced_fixture())
            self.assertEqual(path.name, "call_transcript_R01.md")
            self.assertEqual(path.read_text(encoding="utf-8"), markdown.render(HEADER, voiced_fixture()))
            self.assertEqual((Path(tmp) / "call_transcript.md").read_text(encoding="utf-8"), "mine")


class TxtTest(unittest.TestCase):
    def test_the_same_header_and_rows_without_the_marks(self):
        text = txt.render(HEADER, voiced_fixture())
        self.assertTrue(text.startswith("Transcript: 2026-11-11 call_prova.mkv\n"
                                        "File: C:/Riunioni/2026-11-11 call_prova.mkv\n"))
        self.assertIn("\n\nSpeaker 1 [00:00]\n[00:00] Buongiorno a tutti", text)
        self.assertNotIn("**", text)
        self.assertNotIn("---", text)

    def test_the_file_is_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = txt.write(tmp, "call", HEADER, [])
            second = txt.write(tmp, "call", HEADER, [])
            self.assertEqual((first.name, second.name), ("call.txt", "call_R01.txt"))


if __name__ == "__main__":
    unittest.main()
