#!/usr/bin/env python3
"""The lines cut into subtitles by the conventions: two rows of 42
characters at most, 5/6 to 7 seconds, cut at the end of a sentence, on a
comma or before a conjunction, the times from the words, no word lost or
repeated, never two voices in one subtitle."""

import random
import unittest
from itertools import pairwise

import _setup  # noqa: F401

from scriba.core import subtitles as st
from scriba.core.segments import Segment
from scriba.data import srt


def timed(start, text, speaker=None, per_word=0.4, doubts=()):
    """A line whose words last `per_word` seconds each, timed as the engine
    gives them."""
    words = text.split()
    times = tuple((start + n * per_word, start + (n + 1) * per_word) for n in range(len(words)))
    return Segment(start, start + len(words) * per_word, text, speaker, doubts=doubts, words=times)


def words_of(items):
    return " ".join(item.text for item in items).split()


class LimitsTest(unittest.TestCase):
    def assert_within_limits(self, cues, lead=0):
        for cue in cues:
            rows = cue.text.split("\n")
            self.assertLessEqual(len(rows), st.MAX_ROWS, cue)
            for n, row in enumerate(rows):
                if " " in row:              # a single word longer than a row is the one thing let through
                    self.assertLessEqual(len(row) + (lead if n == 0 else 0), st.MAX_ROW, cue)
            self.assertLessEqual(cue.end - cue.start, st.MAX_SECONDS + 1e-9, cue)

    def test_a_long_sentence_without_punctuation_becomes_short_subtitles_with_every_word(self):
        text = " ".join(f"parola{n}" for n in range(60))
        line = timed(10.0, text, per_word=0.35)           # 21 s, 480 characters
        cues = st.subtitles([line])
        self.assertGreater(len(cues), 5)
        self.assert_within_limits(cues)
        self.assertEqual(words_of(cues), text.split())
        self.assertEqual(cues[0].start, 10.0)
        self.assertGreaterEqual(cues[-1].end, line.end)                 # the last may stay up to be read

    def test_a_subtitle_ends_at_the_end_of_a_sentence_or_on_a_comma(self):
        text = ("Abbiamo ricevuto le tavole del quadro generale ieri. Il cavo arriva dal locale tecnico, "
                "poi sale lungo il cavedio fino al terzo piano dove c'è il quadro di zona.")
        cues = st.subtitles([timed(0.0, text, per_word=0.3)])
        self.assertTrue(cues[0].text.endswith("ieri."), cues[0])
        self.assertTrue(cues[1].text.endswith("tecnico,"), cues[1])
        self.assertEqual(words_of(cues), text.split())

    def test_the_rows_are_cut_before_a_conjunction_and_of_a_similar_length(self):
        text = "il cavo arriva dal locale tecnico quindi sale fino al terzo piano"
        cues = st.subtitles([timed(0.0, text, per_word=0.3)])
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "il cavo arriva dal locale tecnico\nquindi sale fino al terzo piano")

    def test_a_word_longer_than_a_row_is_kept_whole(self):
        long_word = "x" * 60
        text = f"prima {long_word} dopo di questo segue altro testo"
        cues = st.subtitles([timed(0.0, text)])
        self.assertEqual(words_of(cues), text.split())
        self.assertIn(long_word, [row for cue in cues for row in cue.text.split("\n")])

    def test_the_writer_s_lead_counts_in_the_first_row(self):
        text = "il cavo arriva dal locale tecnico quindi sale fino al terzo piano"
        lead = "Anna Maria Bianchi Rossi: "
        cues = st.subtitles([timed(0.0, text, "Speaker 1")], lambda cue, new: lead if new else "")
        self.assert_within_limits(cues[:1], len(lead))
        self.assert_within_limits(cues[1:])
        self.assertEqual(words_of(cues), text.split())


class TimesTest(unittest.TestCase):
    def test_the_times_come_from_the_words(self):
        # the words in the second half are slower: the cut follows them, not the characters
        words = "uno due tre quattro cinque sei sette otto nove dieci. undici dodici tredici quattordici".split()
        times = tuple((n * 0.2, n * 0.2 + 0.2) for n in range(10)) + tuple((2.0 + n * 1.5, 3.4 + n * 1.5)
                                                                          for n in range(4))
        line = Segment(0.0, 7.9, " ".join(words), words=times)
        cues = st.subtitles([line])
        self.assertEqual([c.text.replace("\n", " ") for c in cues][0], "uno due tre quattro cinque sei sette otto nove dieci.")
        self.assertEqual(cues[0].start, 0.0)
        self.assertAlmostEqual(cues[0].end, 2.0)
        self.assertAlmostEqual(cues[1].start, 2.0)
        self.assertAlmostEqual(cues[1].end, 7.9)

    def test_without_the_words_times_the_line_s_time_is_shared_by_length(self):
        text = "Prima parte della frase lunga che continua, seconda parte della frase lunga che non finisce mai."
        line = Segment(4.0, 12.0, text)
        cues = st.subtitles([line])
        self.assertGreater(len(cues), 1)
        self.assertEqual(cues[0].start, 4.0)
        self.assertAlmostEqual(cues[-1].end, 12.0)
        self.assertTrue(all(a.end <= b.start + 1e-9 for a, b in pairwise(cues)))
        self.assertEqual(words_of(cues), text.split())

    def test_times_that_do_not_fit_the_text_are_not_used(self):
        line = Segment(1.0, 3.0, "tre parole qui", words=((1.0, 1.5),))
        words = st.timed_words(line)
        self.assertEqual([w for w, _a, _b in words], ["tre", "parole", "qui"])
        self.assertEqual((words[0][1], words[-1][2]), (1.0, 3.0))
        self.assertAlmostEqual(words[1][2] - words[1][1], 2.0 * 6 / 14)     # 6 of the 14 characters

    def test_a_short_subtitle_stays_up_long_enough_to_read_but_never_over_the_next(self):
        a = timed(0.0, "Va bene.", "Speaker 1", per_word=0.2)
        b = timed(5.0, "Allora passiamo al punto due.", "Speaker 2", per_word=0.3)
        c = timed(6.6, "Sì.", "Speaker 1", per_word=0.2)
        cues = st.subtitles([a, b, c])
        self.assertAlmostEqual(cues[0].end, st.MIN_SECONDS)             # 0.4 s said, 5/6 s shown
        self.assertAlmostEqual(cues[1].end, 6.6)                        # 1.5 s said, would need 1.7: the next starts
        self.assertAlmostEqual(cues[2].end, 6.6 + st.MIN_SECONDS)


class VoicesTest(unittest.TestCase):
    def test_a_subtitle_never_joins_two_voices(self):
        lines = [timed(0.0, "Allora io direi di", "Speaker 1"), timed(1.6, "Sì.", "Speaker 2", 0.2),
                 timed(1.8, "procedere con il quadro.", "Speaker 1")]
        cues = st.subtitles(lines)
        self.assertEqual([(c.speaker, c.text) for c in cues], [("Speaker 1", "Allora io direi di"),
                                                                ("Speaker 2", "Sì."),
                                                                ("Speaker 1", "procedere con il quadro.")])

    def test_a_scrap_joins_the_same_voice_after_a_short_pause_only(self):
        lines = [timed(0.0, "Sì.", "Speaker 1", 0.3), timed(0.5, "Allora andiamo avanti.", "Speaker 1")]
        self.assertEqual([c.text for c in st.subtitles(lines)], ["Sì. Allora andiamo avanti."])
        lines[1] = timed(3.0, "Allora andiamo avanti.", "Speaker 1")
        self.assertEqual([c.text for c in st.subtitles(lines)], ["Sì.", "Allora andiamo avanti."])

    def test_a_doubtful_line_is_never_joined_to_a_good_one(self):
        lines = [timed(0.0, "Grazie a tutti.", "Speaker 1", doubts=("repeated",)),
                 timed(1.3, "Ci vediamo lunedì.", "Speaker 1")]
        cues = st.subtitles(lines)
        self.assertEqual([(c.text, c.doubts) for c in cues], [("Grazie a tutti.", ("repeated",)),
                                                               ("Ci vediamo lunedì.", ())])


class EveryWordTest(unittest.TestCase):
    def test_on_made_up_meetings_every_word_is_there_once_and_in_order(self):
        rng = random.Random(13112026)
        vocabulary = ["e", "ma", "quadro", "cavo", "il", "che", "sottostazione", "va", "bene,", "fine.",
                      "precipitevolissimevolmente", "x" * 50, "sì", "quindi", "allora?"]
        for _round in range(40):
            lines, at = [], 0.0
            for _n in range(rng.randint(1, 30)):
                count = rng.randint(1, 40)
                text = " ".join(rng.choice(vocabulary) for _ in range(count))
                length = rng.uniform(0.2, 25.0)
                speaker = rng.choice(["Speaker 1", "Speaker 2", None])
                if rng.random() < 0.5:
                    line = Segment(at, at + length, text, speaker)
                else:
                    step = length / count
                    line = Segment(at, at + length, text, speaker,
                                   words=tuple((at + k * step, at + (k + 1) * step) for k in range(count)))
                lines.append(line)
                at += length + rng.choice([0.0, 0.0, 0.3, 2.0])
            cues = st.subtitles(lines, lambda cue, new: srt.tag(cue.speaker) if new else "")
            self.assertEqual(words_of(cues), words_of(lines))
            for cue in cues:
                self.assertLessEqual(len(cue.text.split("\n")), st.MAX_ROWS)
                self.assertLessEqual(cue.start, cue.end)
            self.assertTrue(all(a.start <= b.start for a, b in pairwise(cues)))


class WrittenTest(unittest.TestCase):
    def test_the_subtitles_file_names_the_voice_where_it_changes_and_reads_back_whole(self):
        lines = [timed(0.0, "Buongiorno a tutti, iniziamo con il quadro generale che arriva dal locale "
                            "tecnico al piano terra.", "Speaker 1", 0.3),
                 timed(6.0, "Perfetto.", "Speaker 2", 0.4, doubts=("low_confidence",))]
        text = srt.render(lines, cut=st.subtitles)
        self.assertEqual(text.count("P1: "), 1)
        self.assertIn("\nP2: (?) Perfetto.\n", text)
        cues = srt.parse(text)
        self.assertEqual(words_of(cues), words_of(lines))
        self.assertEqual({c.speaker for c in cues[:-1]}, {"Speaker 1"})
        self.assertEqual(cues[-1].speaker, "Speaker 2")


if __name__ == "__main__":
    unittest.main()
