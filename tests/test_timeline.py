#!/usr/bin/env python3
"""The speech packed end to end for whisper, and its times brought back to
the recording's clock."""

import unittest

import _setup  # noqa: F401

from scriba.core.timeline import Piece, original, pack, packed_length


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


if __name__ == "__main__":
    unittest.main()
