#!/usr/bin/env python3
"""The time a transcription will take: from the speed measured last time,
or from a start on the safe side when there is no measure."""

import math
import unittest

import _setup  # noqa: F401

from scriba.core import estimate


class EstimateTest(unittest.TestCase):
    def test_the_speed_of_a_run_is_audio_over_work(self):
        self.assertEqual(estimate.speed(3600, 900), 4.0)

    def test_a_run_too_short_or_without_times_is_no_measure(self):
        self.assertIsNone(estimate.speed(20, 10))
        for audio, work in ((3600, 0), (0, 10), (-5, 10), (math.inf, 10), (3600, math.nan), (True, 1)):
            with self.subTest((audio, work)):
                self.assertIsNone(estimate.speed(audio, work))

    def test_the_last_speed_gives_the_next_estimate(self):
        self.assertEqual(estimate.work_time(3600, estimate.speed(1800, 600)), 1200)

    def test_without_a_measure_the_start_is_as_long_as_the_audio(self):
        self.assertEqual(estimate.work_time(3600), 3600 / estimate.DEFAULT_SPEED)
        self.assertEqual(estimate.work_time(3600, None, default=2.0), 1800)

    def test_a_measure_that_is_not_a_speed_is_not_used(self):
        for bad in (0, -1, math.nan, math.inf):
            with self.subTest(bad):
                self.assertEqual(estimate.work_time(600, bad, default=2.0), 300)

    def test_no_audio_no_work(self):
        self.assertEqual(estimate.work_time(0, 4.0), 0.0)
        self.assertEqual(estimate.work_time(-3, 4.0), 0.0)

    def test_a_default_that_is_not_a_speed_is_a_mistake_said_at_once(self):
        with self.assertRaises(ValueError):
            estimate.work_time(60, None, default=0)

    def test_the_time_left_is_the_estimate_at_first_then_the_pace_of_the_run(self):
        self.assertEqual(estimate.remaining(300, 30, 0.05), 270)
        self.assertEqual(estimate.remaining(300, 400, 0.05), 0)         # late: never a time below nothing
        self.assertEqual(estimate.remaining(300, 60, 0.5), 60)          # half done in a minute: a minute to go
        self.assertEqual(estimate.remaining(300, 60, 1.0), 0)
        self.assertEqual(estimate.remaining(300, math.nan, 0.5), 300)

    def test_times_to_come_in_round_words_rounded_up(self):
        self.assertEqual(estimate.about(20), "less than a minute")
        self.assertEqual(estimate.about(61), "about 2 minutes")
        self.assertEqual(estimate.about(60), "about 1 minute")
        self.assertEqual(estimate.about(4210), "about 1 h 11 min")
        self.assertEqual(estimate.about(math.nan), "less than a minute")

    def test_times_gone_as_far_as_they_matter(self):
        self.assertEqual(estimate.span(44.6), "45 s")
        self.assertEqual(estimate.span(460), "7 min 40 s")
        self.assertEqual(estimate.span(3733), "1 h 02 min")
        self.assertEqual(estimate.span(-1), "0 s")


if __name__ == "__main__":
    unittest.main()
