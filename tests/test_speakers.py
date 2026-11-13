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
        self.assertEqual(found[0].listen, (31, 34))         # "Sì." is the shorter stretch
        self.assertEqual(found[1].listen, (2, 2 + speakers.LISTEN_MAX))

    def test_the_longest_stretch_of_the_voice_alone_not_its_first_line(self):
        # short lines, as the engine gives them: four in a row are one stretch of 9.5 s
        segs = [Segment(0, 2, "allora", "Speaker 1"), Segment(2, 3, "sì", "Speaker 2"),
                Segment(10, 12, "dunque", "Speaker 1"), Segment(12.2, 14.5, "la quota", "Speaker 1"),
                Segment(15, 17.5, "del quadro", "Speaker 1"), Segment(17.5, 19.5, "è giusta", "Speaker 1"),
                Segment(25, 40, "un lungo intervento", "Speaker 2")]
        found = {v.label: v.listen for v in speakers.voices(segs)}
        self.assertEqual(found["Speaker 1"], (10, 19.5))
        self.assertEqual(found["Speaker 2"], (25, 25 + speakers.LISTEN_MAX))      # cut at the most

    def test_never_another_voice_in_the_stretch(self):
        # another voice speaks over the end of a line, and starts before the next ends
        segs = [Segment(0, 6, "uno", "Speaker 1"), Segment(4, 9, "sovrapposta", "Speaker 2"),
                Segment(6.5, 14, "due", "Speaker 1"), Segment(12, 13, "interruzione", "Speaker 3")]
        found = {v.label: v.listen for v in speakers.voices(segs)}
        self.assertEqual(found["Speaker 1"], (0, 4))            # up to Speaker 2 (then 9-12: shorter)
        self.assertEqual(found["Speaker 2"], (6, 6.5))          # between Speaker 1's two lines
        # all of it under Speaker 1: its line all the same, rather than nothing to listen to
        self.assertEqual(found["Speaker 3"], (12, 13))

    def test_doubtful_lines_only_when_there_is_nothing_else(self):
        segs = [Segment(0, 10, "rumore", "Speaker 1", doubts=("no_speech",)), Segment(20, 23, "sì certo", "Speaker 1"),
                Segment(30, 40, "rumore", "Speaker 2", doubts=("quiet",))]
        found = {v.label: v.listen for v in speakers.voices(segs)}
        self.assertEqual(found, {"Speaker 1": (20, 23), "Speaker 2": (30, 40)})

    def test_a_voice_with_only_short_lines_is_played_from_its_first(self):
        self.assertEqual(speakers.voices([Segment(5, 6, "Sì.", "Speaker 1")])[0].listen, (5, 6))


class BothHeardTest(unittest.TestCase):
    """The stretch the balance's preview plays: where the user and the
    others both speak the most."""

    def test_the_stretch_where_both_sides_speak_not_the_one_with_most_speech(self):
        segs = [Segment(0, 40, "un lungo intervento degli altri", "Speaker 2"),        # most speech, one side
                Segment(100, 104, "domanda", "Speaker 1"), Segment(104, 112, "risposta", "Speaker 2"),
                Segment(112, 118, "replica", "Speaker 1"),
                Segment(300, 301, "sì", "Speaker 1"), Segment(301, 330, "altro", "Speaker 3")]
        self.assertEqual(speakers.both_heard(segs, "Speaker 1", 20), (100, 120))

    def test_without_my_voice_the_two_loudest_voices_of_a_stretch_stand_for_the_sides(self):
        segs = [Segment(0, 30, "a", "Speaker 1"), Segment(50, 58, "b", "Speaker 1"), Segment(58, 68, "c", "Speaker 2")]
        self.assertEqual(speakers.both_heard(segs, "", 20), (50, 70))

    def test_held_within_the_recording_and_from_the_start_without_lines(self):
        segs = [Segment(85, 88, "a", "Speaker 1"), Segment(88, 90, "b", "Speaker 2")]
        self.assertEqual(speakers.both_heard(segs, "Speaker 1", 20, duration=90), (70, 90))
        self.assertEqual(speakers.both_heard(segs, "Speaker 1", 20, duration=12), (0, 12))
        self.assertEqual(speakers.both_heard([Segment(1, 2, " ", "Speaker 1")], "Speaker 1", 20, 300), (0, 20))

    def test_a_long_meeting_is_quick(self):
        import time
        segs = [Segment(i * 3.0, i * 3.0 + 2.5, "x", f"Speaker {i % 3 + 1}") for i in range(3000)]     # 2.5 h
        began = time.perf_counter()
        speakers.both_heard(segs, "Speaker 1", duration=9000)
        self.assertLess(time.perf_counter() - began, 1.0)


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
