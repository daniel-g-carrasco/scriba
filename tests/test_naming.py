"""Source-independent name proposals, using invented meetings."""
import unittest

import _setup  # noqa: F401
from scriba.core import naming, spans
from scriba.core.segments import Segment


class NamingTest(unittest.TestCase):
    def test_names_follow_overlap_and_exclude_the_microphone(self):
        lines = [Segment(0, 10, "hello", "Speaker 1"), Segment(12, 22, "yes", "Speaker 2"),
                 Segment(24, 34, "me", "Speaker 3")]
        found = naming.propose(lines, [("Ada Example", 0, 10), ("Bob Example", 12, 22),
                                       ("Me Example", 24, 34)], microphone="Speaker 3")
        self.assertEqual({k: v.name for k, v in found.items()},
                         {"Speaker 1": "Ada Example", "Speaker 2": "Bob Example"})
        self.assertEqual(found["Speaker 1"].share, 1)

    def test_ambiguous_and_short_matches_stay_unnamed(self):
        lines = [Segment(0, 10, "hello", "Speaker 1")]
        self.assertFalse(naming.propose(lines, [("Ada", 0, 5), ("Bob", 5, 10)]))
        self.assertFalse(naming.propose([Segment(0, 2, "yes", "Speaker 1")], [("Ada", 0, 2)]))

    def test_two_voices_cannot_take_one_name(self):
        found = naming.propose([Segment(0, 10, "hello", "A"), Segment(12, 18, "yes", "B")],
                               [("Ada", 0, 20)])
        self.assertEqual(list(found), ["A"])

    def test_one_second_delay_does_not_change_the_name(self):
        lines = [Segment(0, 10, "hello", "A"), Segment(12, 22, "yes", "B")]
        for delay in (0, 1):
            found = naming.propose(lines, [("Ada", delay, 10 + delay), ("Bob", 12 + delay, 22 + delay)])
            self.assertEqual([p.name for p in found.values()], ["Ada", "Bob"])
        self.assertEqual(naming.propose(lines, []), {})

    def test_duplicate_intervals_do_not_inflate_coverage(self):
        found = naming.propose([Segment(0, 10, "hello", "A"), Segment(0, 10, "hello", "A")],
                               [("Ada", 0, 8), ("Ada", 0, 8)])
        self.assertAlmostEqual(found["A"].share, .8)


class SpansTest(unittest.TestCase):
    def test_short_holes_merge_and_short_flashes_are_discarded(self):
        track = spans.Builder()
        for at, names in [(0, ["Ada"]), (2, []), (2.5, ["Ada"]), (5, []),
                          (7, ["Bob"]), (7.5, []), (8, [])]:
            track.read(at, names)
        self.assertEqual(track.finish(9), (("Ada", 0, 5),))

    def test_unavailable_closes_without_bridging_and_text_roundtrips(self):
        track = spans.Builder()
        track.read(0, ["Ada, Example; x"])
        track.unavailable(2)
        track.read(2.5, ["Ada, Example; x"])
        result = track.finish(5)
        self.assertEqual(len(result), 2)
        self.assertEqual(spans.read_spans(spans.write_spans(result)), result)
        self.assertEqual(spans.read_spans(None), ())
        self.assertEqual(spans.read_spans('[["Ada",0,NaN],["Bob",4,2]]'), ())
