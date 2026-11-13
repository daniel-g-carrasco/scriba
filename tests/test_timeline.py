#!/usr/bin/env python3
"""The speech packed end to end for whisper, and its times brought back to
the recording's clock."""

import unittest

import _setup  # noqa: F401

from scriba.core.timeline import Piece, original, pack, packed_length, spans, words


class TimelineTest(unittest.TestCase):
    def test_the_stretches_are_packed_in_order_without_the_silences(self):
        pieces = pack([(10.0, 12.0), (2.0, 5.0), (20.0, 20.0)])
        self.assertEqual(pieces, [Piece(2.0, 5.0, 0.0), Piece(10.0, 12.0, 3.0)])
        self.assertEqual(packed_length(pieces), 5.0)

    def test_stretches_that_touch_or_overlap_become_one(self):
        self.assertEqual(pack([(0.0, 2.0), (2.0, 3.0), (2.5, 4.0)]), [Piece(0.0, 4.0, 0.0)])

    def test_a_time_inside_a_piece_is_moved_by_the_silence_before_it(self):
        pieces = pack([(2.0, 5.0), (10.0, 12.0)])
        self.assertEqual(original(pieces, 1.0), 3.0)
        self.assertEqual(original(pieces, 4.0), 11.0)

    def test_on_a_seam_a_start_goes_to_the_next_piece_and_an_end_to_the_previous_one(self):
        pieces = pack([(2.0, 5.0), (10.0, 12.0)])
        self.assertEqual(original(pieces, 3.0), 10.0)
        self.assertEqual(original(pieces, 3.0, is_end=True), 5.0)

    def test_past_the_end_is_the_end_of_the_last_piece_and_no_pieces_change_nothing(self):
        pieces = pack([(2.0, 5.0), (10.0, 12.0)])
        self.assertEqual(original(pieces, 99.0, is_end=True), 12.0)
        self.assertEqual(original(pieces, 0.0), 2.0)
        self.assertEqual(original([], 7.5), 7.5)

    def test_a_time_in_the_silence_between_two_pieces(self):
        pieces = pack([(2.0, 5.0), (10.0, 12.0)], gap=0.5)
        self.assertEqual(pieces[1].at, 3.5)
        self.assertEqual(original(pieces, 3.2), 10.0)                   # a start: the next piece
        self.assertEqual(original(pieces, 3.2, is_end=True), 5.0)       # an end: the piece before
        self.assertEqual(original(pieces, 4.0), 10.5)
        self.assertEqual(packed_length(pieces), 5.5)

    def test_a_line_joining_a_scrap_heard_minutes_before_keeps_the_time_of_the_speech(self):
        # scraps of 1 s at 11 s and at 39 s, then the user speaks from 217 to 257 s; whisper makes one line from
        # the start of the packed audio to 6.5 s into the speech, and one across the last pause of 26 s
        pieces = pack([(11.5, 12.5), (39.0, 40.0), (217.0, 257.0), (283.0, 284.0)], gap=0.2)
        first = (0.0, pieces[2].at + 6.5)
        last = (pieces[2].at + 38.0, pieces[3].at + 0.5)
        self.assertEqual(original(pieces, first[0]), 11.5)          # what it did: the line at 00:11
        (start, end), (start2, end2) = spans(pieces, [first, last])
        self.assertEqual(start, 217.0)
        self.assertAlmostEqual(end, 223.5)
        self.assertAlmostEqual(start2, 255.0)
        self.assertEqual(end2, 257.0)

    def test_a_line_across_a_short_pause_keeps_both_sides(self):
        pieces = pack([(100.0, 103.0), (104.5, 110.0)], gap=0.2)
        self.assertEqual(spans(pieces, [(0.5, pieces[1].at + 5.0)]), [(100.5, 109.5)])
        self.assertEqual(spans([], [(1.0, 2.0)]), [(1.0, 2.0)])

    def test_the_words_come_back_with_their_line_in_order_and_inside_it(self):
        pieces = pack([(2.0, 5.0), (10.0, 12.0)], gap=0.2)
        # a word in each piece, one across the seam, one out of order (kept where the one before starts)
        heard = [(0.5, 1.0), (2.9, 3.4), (3.5, 4.0), (0.1, 0.2)]
        self.assertEqual(words(pieces, heard, 2.5, 10.8), ((2.5, 3.0), (4.9, 10.2), (10.3, 10.8), (10.3, 10.3)))
        self.assertEqual(words(pieces, [(0.0, 9.0)], 2.0, 4.0), ((2.0, 4.0),))


if __name__ == "__main__":
    unittest.main()
