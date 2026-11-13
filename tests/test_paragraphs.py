#!/usr/bin/env python3
"""The lines gathered into paragraphs to read (core/paragraphs.py), and the
files written with them: the subtitles keep the engine's lines."""

import unittest

import _setup  # noqa: F401

from scriba.core import paragraphs as pg
from scriba.core.paragraphs import paragraphs
from scriba.core.segments import Segment
from scriba.data import markdown, srt, txt
from scriba.data.markdown import Header


def texts(groups):
    return [[s.text for s in group] for group in groups]


def steady(speaker, start, count, seconds=2.0, words="una battuta breve"):
    """`count` lines of one voice one after the other, as the engine cuts them."""
    return [Segment(start + n * seconds, start + (n + 1) * seconds, f"{words} {n}", speaker) for n in range(count)]


class ParagraphsTest(unittest.TestCase):
    def test_the_lines_of_one_voice_in_a_row_are_one_paragraph_until_the_voice_changes(self):
        segs = [Segment(0, 2, "Allora,", "Speaker 1"), Segment(2, 4, "la quota", "Speaker 1"),
                Segment(4.1, 6, "è giusta.", "Speaker 1"), Segment(6, 7, "Sì.", "Speaker 2"),
                Segment(7, 9, "Bene.", "Speaker 1")]
        self.assertEqual(texts(paragraphs(segs)), [["Allora,", "la quota", "è giusta."], ["Sì."], ["Bene."]])

    def test_a_long_pause_parts_them(self):
        segs = [Segment(0, 2, "uno", "Speaker 1"), Segment(2 + pg.PAUSE_SECONDS + 0.1, 7, "due", "Speaker 1"),
                Segment(7 + pg.PAUSE_SECONDS, 12, "tre", "Speaker 1")]
        self.assertEqual(texts(paragraphs(segs)), [["uno"], ["due", "tre"]])

    def test_past_half_a_minute_at_the_end_of_a_sentence(self):
        segs = steady("Speaker 1", 0, 15)                  # 30 s with no full stop
        segs += [Segment(30, 32, "e qui finisce.", "Speaker 1"), Segment(32, 34, "Poi riprende", "Speaker 1")]
        groups = paragraphs(segs)
        self.assertEqual([len(g) for g in groups], [16, 1])
        self.assertEqual(groups[0][-1].text, "e qui finisce.")
        # before half a minute a full stop does not part them
        short = [Segment(0, 2, "Va bene.", "Speaker 1"), Segment(2, 4, "Andiamo avanti.", "Speaker 1")]
        self.assertEqual(len(paragraphs(short)), 1)

    def test_never_past_a_minute_or_the_words_without_a_full_stop(self):
        groups = paragraphs(steady("Speaker 1", 0, 45))    # 90 s, never a full stop
        self.assertEqual([len(g) for g in groups], [30, 15])
        self.assertLessEqual(groups[0][-1].end - groups[0][0].start, pg.HARD_SECONDS)
        many = steady("Speaker 1", 0, 20, seconds=1.0, words=" ".join(["parola"] * 10))     # 11 words a line
        sizes = [sum(len(s.text.split()) for s in g) for g in paragraphs(many)]
        self.assertTrue(all(size <= pg.HARD_WORDS for size in sizes), sizes)
        self.assertEqual(sum(sizes), 220)

    def test_a_doubtful_line_is_a_paragraph_of_its_own(self):
        segs = [Segment(0, 2, "uno", "Speaker 1"), Segment(2, 3, "boh", "Speaker 1", doubts=("low_confidence",)),
                Segment(3, 5, "due", "Speaker 1"), Segment(5, 7, "tre", "Speaker 1")]
        self.assertEqual(texts(paragraphs(segs)), [["uno"], ["boh"], ["due", "tre"]])

    def test_lines_without_words_are_left_out_and_lines_without_voices_are_gathered_too(self):
        segs = [Segment(0, 2, "uno"), Segment(2, 3, "  "), Segment(3, 5, "due"), Segment(9, 10, "tre")]
        self.assertEqual(texts(paragraphs(segs)), [["uno", "due"], ["tre"]])
        self.assertEqual(paragraphs([]), [])


class WrittenTest(unittest.TestCase):
    SEGS = [Segment(0, 2, "Allora,", "Speaker 1"), Segment(2, 4, "la quota", "Speaker 1"),
            Segment(4, 6, "è giusta.", "Speaker 1"), Segment(6, 7, "Sì.", "Speaker 2"),
            Segment(7, 8, "boh", "Speaker 2", doubts=("low_confidence",)), Segment(8, 9, "Bene.", "Speaker 2"),
            Segment(15, 17, "Altro punto.", "Speaker 2")]

    def test_the_files_have_one_row_per_paragraph_with_its_time_and_the_doubtful_line_marked(self):
        header = Header(source="a.wav", duration=20)
        body = markdown.render(header, self.SEGS, group=paragraphs).split("\n---\n\n", 1)[1]
        self.assertEqual(body, "**Speaker 1** [00:00]\n"
                               "[00:00] Allora, la quota è giusta.\n"
                               "\n"
                               "**Speaker 2** [00:06]\n"
                               "[00:06] Sì.\n"
                               "\n"
                               "[00:07] (?) boh _(words uncertain)_\n"
                               "\n"
                               "[00:08] Bene.\n"
                               "\n"
                               "[00:15] Altro punto.\n")
        self.assertIn("\nSpeaker 1 [00:00]\n[00:00] Allora, la quota è giusta.\n\nSpeaker 2 [00:06]\n",
                      txt.render(header, self.SEGS, group=paragraphs))

    def test_the_subtitles_stay_line_by_line(self):
        self.assertEqual(srt.render(self.SEGS).count(" --> "), len(self.SEGS))


if __name__ == "__main__":
    unittest.main()
