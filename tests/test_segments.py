#!/usr/bin/env python3
"""The segments: each voice found as trascrivi.py found it, the voices
numbered in the order they are heard, the doubtful lines marked and kept,
two tracks put together."""

import json
import random
import unittest

import _setup

from scriba.core import segments as sg
from scriba.core.segments import Segment, Turn


def fixture():
    data = json.loads(_setup.fixture_text("invented_call.json"))
    return ([Segment(a, b, text) for a, b, text in data["segments"]],
            [Turn(a, b, label) for a, b, label in data["turns"]])


def chi_parla(turni, a, b):
    """trascrivi.py's function, copied as it is: the reference the new one
    must agree with."""
    best, bl = None, 0.0
    for s, e, p in turni:
        o = min(e, b) - max(s, a)
        if o > bl:
            best, bl = p, o
    if best is None and turni:
        m = (a + b) / 2
        best = min(turni, key=lambda t: min(abs(t[0] - m), abs(t[1] - m)))[2]
    return best or "?"


class AssignSpeakersTest(unittest.TestCase):
    def test_the_turn_that_covers_the_most_of_a_segment_gives_its_voice(self):
        turns = [Turn(0, 4, "A"), Turn(4, 10, "B")]
        out = sg.assign_speakers([Segment(3, 7, "x"), Segment(0, 4.5, "y")], turns)
        self.assertEqual([s.speaker for s in out], ["B", "A"])

    def test_a_segment_no_turn_covers_takes_the_nearest(self):
        turns = [Turn(0, 2, "A"), Turn(10, 12, "B")]
        out = sg.assign_speakers([Segment(3, 4, "near A"), Segment(8, 9, "near B")], turns)
        self.assertEqual([s.speaker for s in out], ["A", "B"])

    def test_without_turns_the_segments_come_back_as_they_are(self):
        seg = [Segment(0, 1, "x")]
        self.assertEqual(sg.assign_speakers(seg, []), seg)
        self.assertEqual(sg.assign_speakers([], [Turn(0, 1, "A")]), [])

    def test_the_segments_given_are_not_changed(self):
        seg = [Segment(0, 1, "x")]
        sg.assign_speakers(seg, [Turn(0, 1, "A")])
        self.assertIsNone(seg[0].speaker)

    def test_it_agrees_with_trascrivi_on_a_long_meeting_of_six_voices(self):
        rnd = random.Random(11)
        turns, t = [], 0.0
        while t < 3600:
            length = rnd.uniform(0.5, 40)
            turns.append(Turn(round(t, 2), round(t + length, 2), f"SPEAKER_{rnd.randrange(6):02d}"))
            t += length + rnd.choice((0, 0, 0.3, 2.5))         # touching, or a pause in between
        segs = []
        for _ in range(200):
            a = rnd.uniform(0, 3650)
            segs.append(Segment(round(a, 2), round(a + rnd.uniform(0, 12), 2), "words"))
        reference = [chi_parla([(x.start, x.end, x.label) for x in turns], s.start, s.end) for s in segs]
        self.assertEqual([s.speaker for s in sg.assign_speakers(segs, turns)], reference)
        self.assertEqual(len(set(reference)), 6)

    def test_turns_out_of_order_are_put_in_order_first(self):
        turns = [Turn(10, 12, "B"), Turn(0, 2, "A")]
        self.assertEqual(sg.assign_speakers([Segment(0, 1, "x")], turns)[0].speaker, "A")


class NumberSpeakersTest(unittest.TestCase):
    def test_the_voices_are_numbered_in_the_order_they_are_heard(self):
        segs = [Segment(0, 1, "a", "SPEAKER_02"), Segment(1, 2, "b", "SPEAKER_00"),
                Segment(2, 3, "c", "SPEAKER_02"), Segment(3, 4, "d", None), Segment(4, 5, "e", "SPEAKER_01")]
        out = sg.number_speakers(segs)
        self.assertEqual([s.speaker for s in out], ["Speaker 1", "Speaker 2", "Speaker 1", None, "Speaker 3"])
        self.assertEqual(sg.speakers_of(out), ["Speaker 1", "Speaker 2", "Speaker 3"])

    def test_an_empty_segment_does_not_take_a_number(self):
        segs, turns = fixture()
        voiced = sg.number_speakers(sg.assign_speakers(sg.spoken(segs), turns))
        self.assertEqual(len(voiced), 10)
        self.assertEqual(sg.speakers_of(voiced), ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertEqual(voiced[0].text, "Buongiorno a tutti, iniziamo con il quadro generale.")

    def test_no_segments_no_voices(self):
        self.assertEqual(sg.number_speakers([]), [])
        self.assertEqual(sg.speakers_of([]), [])


class FlagDoubtfulTest(unittest.TestCase):
    def test_a_line_whisper_heard_as_no_speech_is_marked_and_kept(self):
        out = sg.flag_doubtful([Segment(0, 1, "x", no_speech_prob=0.8), Segment(1, 2, "y", no_speech_prob=0.2)])
        self.assertEqual([s.doubts for s in out], [(sg.NO_SPEECH,), ()])
        self.assertEqual([s.text for s in out], ["x", "y"])

    def test_a_line_of_low_confidence_is_marked(self):
        out = sg.flag_doubtful([Segment(0, 1, "x", avg_logprob=-1.4), Segment(1, 2, "y", avg_logprob=-0.3)])
        self.assertEqual([s.doubts for s in out], [(sg.LOW_CONFIDENCE,), ()])

    def test_a_measure_not_given_is_no_doubt(self):
        self.assertEqual(sg.flag_doubtful([Segment(0, 1, "x")])[0].doubts, ())

    def test_the_same_words_three_times_in_a_minute_are_all_marked(self):
        segs = [Segment(0, 2, "Grazie a tutti."), Segment(10, 12, "grazie, a tutti"),
                Segment(20, 22, "Altro testo qui"), Segment(50, 52, "GRAZIE A TUTTI!")]
        out = sg.flag_doubtful(segs)
        self.assertEqual([s.doubts for s in out], [(sg.REPEATED,), (sg.REPEATED,), (), (sg.REPEATED,)])

    def test_three_times_spread_over_more_than_the_window_is_not_a_loop(self):
        segs = [Segment(t, t + 1, "ne parliamo dopo") for t in (0, 50, 120)]
        self.assertEqual([s.doubts for s in sg.flag_doubtful(segs)], [(), (), ()])
        self.assertEqual([s.doubts for s in sg.flag_doubtful(segs, repeat_window=200)], [(sg.REPEATED,)] * 3)

    def test_short_answers_said_again_and_again_are_a_conversation(self):
        segs = [Segment(t, t + 1, "Sì, sì.") for t in range(5)]
        self.assertEqual([s.doubts for s in sg.flag_doubtful(segs)], [()] * 5)
        self.assertEqual(sg.flag_doubtful(segs, repeat_min_words=1)[0].doubts, (sg.REPEATED,))

    def test_every_reason_is_listed_in_order_and_old_doubts_are_kept(self):
        segs = [Segment(t, t + 1, "una frase inventata qui", no_speech_prob=0.9, avg_logprob=-2.0,
                        doubts=("unspecified",)) for t in range(3)]
        self.assertEqual(sg.flag_doubtful(segs)[0].doubts,
                         (sg.NO_SPEECH, sg.LOW_CONFIDENCE, sg.REPEATED, "unspecified"))

    def test_nothing_in_nothing_out(self):
        self.assertEqual(sg.flag_doubtful([]), [])

    def test_a_faint_line_whisper_was_not_sure_of_is_marked_and_a_faint_line_it_was_sure_of_is_not(self):
        # the numbers of 11/11/2026: the call's first minute, the clip's remote speaker
        segs = [Segment(0, 1, "non è il programma", avg_logprob=-0.5, level=-45.0),
                Segment(1, 2, "voi avete già avuto modo", avg_logprob=-0.19, level=-51.8),
                Segment(2, 3, "una riga ben sentita", avg_logprob=-0.5, level=-30.0),
                Segment(3, 4, "un livello non misurato", avg_logprob=-0.5)]
        self.assertEqual([s.doubts for s in sg.flag_doubtful(segs)], [(sg.QUIET,), (), (), ()])
        self.assertEqual(sg.flag_doubtful(segs, quiet_logprob_below=-0.1)[1].doubts, (sg.QUIET,))


class ShiftAndMergeTest(unittest.TestCase):
    def test_a_track_is_moved_and_nothing_falls_before_zero(self):
        out = sg.shift([Segment(1, 2, "a"), Segment(5, 6, "b")], -1.5)
        self.assertEqual([(s.start, s.end) for s in out], [(0.0, 0.5), (3.5, 4.5)])

    def test_two_tracks_are_put_together_in_the_order_of_time(self):
        mic = [Segment(0, 2, "me first"), Segment(10, 11, "me again")]
        out = [Segment(1, 3, "them"), Segment(8, 9, "them later")]
        merged = sg.merge(mic, out, offset=0.5)
        self.assertEqual([(s.start, s.text) for s in merged],
                         [(0, "me first"), (1.5, "them"), (8.5, "them later"), (10, "me again")])

    def test_at_the_same_moment_the_first_track_comes_first(self):
        merged = sg.merge([Segment(1, 2, "a")], [Segment(1, 2, "b")])
        self.assertEqual([s.text for s in merged], ["a", "b"])

    def test_two_tracks_with_the_same_engine_label_can_be_told_apart(self):
        mic = sg.prefix_speakers([Segment(0, 1, "a", "SPEAKER_00")], "mic ")
        out = sg.prefix_speakers([Segment(2, 3, "b", "SPEAKER_00"), Segment(3, 4, "c")], "out ")
        merged = sg.number_speakers(sg.merge(mic, out))
        self.assertEqual([s.speaker for s in merged], ["Speaker 1", "Speaker 2", None])

    def test_empty_tracks(self):
        self.assertEqual(sg.merge([], []), [])
        self.assertEqual(sg.merge([], [Segment(0, 1, "x")], 2.0), [Segment(2.0, 3.0, "x")])


if __name__ == "__main__":
    unittest.main()
