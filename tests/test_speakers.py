#!/usr/bin/env python3
"""The names of the voices: a map a level above the labels, applied to new
segments, never to the engine's."""

import unittest

import _setup  # noqa: F401

from scriba.core import speakers
from scriba.core.segments import Segment


class RenameTest(unittest.TestCase):
    def segs(self):
        return [Segment(0, 1, "a", "Speaker 1"), Segment(1, 2, "b", "Speaker 2"), Segment(2, 3, "c", None)]

    def test_each_voice_goes_under_its_name_and_the_others_keep_their_label(self):
        out = speakers.rename(self.segs(), {"Speaker 2": "Anna"})
        self.assertEqual([s.speaker for s in out], ["Speaker 1", "Anna", None])

    def test_the_segments_given_are_not_changed(self):
        segs = self.segs()
        speakers.rename(segs, {"Speaker 1": "Anna"})
        self.assertEqual(segs[0].speaker, "Speaker 1")

    def test_an_empty_name_is_no_name_and_spaces_are_tidied(self):
        out = speakers.rename(self.segs(), {"Speaker 1": "   ", "Speaker 2": "  Jürgen   Müller "})
        self.assertEqual([s.speaker for s in out], ["Speaker 1", "Jürgen Müller", None])

    def test_two_labels_with_one_name_become_one_voice(self):
        out = speakers.rename(self.segs(), {"Speaker 1": "François", "Speaker 2": "François"})
        self.assertEqual({s.speaker for s in out if s.speaker}, {"François"})

    def test_no_segments_or_no_names(self):
        self.assertEqual(speakers.rename([], {"Speaker 1": "Anna"}), [])
        self.assertEqual(speakers.rename(self.segs(), {}), self.segs())


class VoicesTest(unittest.TestCase):
    def test_each_voice_in_order_with_its_first_time_its_lines_and_a_stretch_to_listen_to(self):
        segs = [Segment(1, 1.5, "Sì.", "Speaker 2"), Segment(2, 30, "Buongiorno a tutti, allora...", "Speaker 1"),
                Segment(31, 34, "Bene.", "Speaker 2"), Segment(35, 36, "  ", "Speaker 3"), Segment(37, 38, "x", None)]
        found = speakers.voices(segs)
        self.assertEqual([(v.label, v.first, v.lines) for v in found], [("Speaker 2", 1, 2), ("Speaker 1", 2, 1)])
        self.assertEqual(found[0].listen, (31, 34))         # "Sì." is too short to know a voice by
        self.assertEqual(found[1].listen, (2, 2 + speakers.LISTEN_MAX))

    def test_a_voice_with_only_short_lines_is_played_from_its_first(self):
        self.assertEqual(speakers.voices([Segment(5, 6, "Sì.", "Speaker 1")])[0].listen, (5, 6))


class DisplayNamesTest(unittest.TestCase):
    def test_a_name_given_is_shown_and_a_label_is_shown_in_the_users_language(self):
        shown = speakers.display_names(["Speaker 1", "Speaker 2", "mic"], {"Speaker 2": "Anna"})
        self.assertEqual(shown, {"Speaker 1": "Speaker 1", "Speaker 2": "Anna", "mic": "mic"})

    def test_the_label_is_the_one_the_segments_make(self):
        from scriba.core import segments
        self.assertEqual(speakers.shown_label(segments.SPEAKER_LABEL.format(number=12)), "Speaker 12")


class SuggestionsTest(unittest.TestCase):
    def test_previous_names_are_trimmed_deduplicated_and_filtered_while_typing(self):
        known = "Anna Rossi\nBob\nAnna Rossi\n  Anna   Maria  "
        self.assertEqual(speakers.suggestions("aN", known), ["Anna Rossi", "Anna Maria"])
        self.assertEqual(speakers.suggestions("Anna Rossi", known), [])
        self.assertEqual(speakers.remembered(known, {"Speaker 1": " Carla  ", "Speaker 2": "Bob"}),
                         "Anna Rossi\nBob\nAnna Maria\nCarla")


if __name__ == "__main__":
    unittest.main()
