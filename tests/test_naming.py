"""Source-independent name proposals, using invented meetings."""
import unittest

import _setup  # noqa: F401
from scriba.core import naming, spans
from scriba.core.segments import Segment


class NamingTest(unittest.TestCase):
    def test_one_named_quarter_proposes_the_name_for_the_whole_voice(self):
        lines = [Segment(0, 40, "hello", "A")]
        found = naming.propose(lines, [("Ada", 0, 10)])
        self.assertEqual(found["A"], naming.Proposal("Ada", 10, .25))

    def test_mixed_named_time_needs_seventy_percent_and_eight_seconds_total(self):
        lines = [Segment(0, 40, "hello", "A")]
        for named in ([("Ada", 0, 6.9), ("Bob", 6.9, 10)], [("Ada", 0, 7.9)],
                      [("Ada", 0, 10), ("Bob", .5, 10)]):
            self.assertEqual(naming.propose(lines, named), {})
        self.assertEqual(naming.propose(lines, [("Ada", 0, 7), ("Bob", 7, 10)])["A"].share, .175)

    def test_a_segment_fingerprint_recognises_an_unseen_voice_but_not_the_microphone(self):
        lines = [Segment(0, 40, "seen", "A"), Segment(40, 70, "unseen", "B"),
                 Segment(70, 90, "me", "Mic")]
        centroids = {"A": [0, 1], "B": [1, .1], "Mic": [1, 0]}
        named = [("Ada", 0, 10), ("Own tile", 70, 85)]
        found, _ = naming.propose_with_voices(lines, named, centroids, "Mic",
                                             fingerprints={"Ada": [[1, 0]]})
        self.assertEqual(found["A"].share, .25)
        self.assertEqual((found["B"].name, found["B"].source, found["B"].seconds), ("Ada", "voice", 0))
        self.assertNotIn("Mic", found)
        self.assertNotIn("B", naming.propose_with_voices(lines, named, centroids, "Mic")[0])
        centroids["A"] = [1, 0]
        self.assertEqual(naming.propose_with_voices(lines, named, centroids, "Mic")[0]["B"].name, "Ada")

    def test_fingerprints_do_not_break_conflicts(self):
        lines = [Segment(0, 20, "mixed", "A"), Segment(20, 40, "unseen", "B")]
        named = [("Ada", 0, 10), ("Bob", 10, 20)]
        found, _ = naming.propose_with_voices(lines, named, {"A": [1, 0], "B": [1, 0]},
                                             fingerprints={"Ada": [[1, 0]], "Bob": [[1, .01]]})
        self.assertEqual(found, {})

    def test_automatic_names_require_strong_overlap_and_no_competing_voice(self):
        lines = [Segment(0, 20, "hello", "A"), Segment(20, 26, "yes", "B")]
        self.assertEqual(naming.automatic(lines, [("Ada", 0, 19)]), {"A": "Ada"})
        self.assertEqual(naming.automatic(lines, [("Ada", 0, 16)]), {})
        self.assertEqual(naming.automatic(lines, [("Ada", 0, 26)]), {})

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
