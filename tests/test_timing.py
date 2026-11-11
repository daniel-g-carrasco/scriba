#!/usr/bin/env python3
"""Transcript clocks follow trascrivi.py; subtitle milliseconds are rounded
with carry into minutes and hours, then read back."""

import unittest

import _setup  # noqa: F401

from scriba.data.timing import clock, parse_srt_time, srt_time


def fmt(t):
    """The clock branch of trascrivi.py, without its subtitle formatting."""
    h, r = divmod(int(t), 3600); m, s = divmod(r, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class TimingTest(unittest.TestCase):
    def test_under_an_hour_minutes_and_seconds(self):
        self.assertEqual(clock(0), "00:00")
        self.assertEqual(clock(65.9), "01:05")
        self.assertEqual(clock(3599.99), "59:59")

    def test_from_an_hour_on_the_hours_too(self):
        self.assertEqual(clock(3600), "1:00:00")
        self.assertEqual(clock(10 * 3600 + 61), "10:01:01")

    def test_subtitle_times_have_hours_and_milliseconds(self):
        self.assertEqual(srt_time(3.48), "00:00:03,480")
        self.assertEqual(srt_time(3725.875), "01:02:05,875")
        self.assertEqual(srt_time(100 * 3600), "100:00:00,000")

    def test_milliseconds_are_rounded_with_carry_into_minutes_and_hours(self):
        for seconds, expected in ((15.02, "00:00:15,020"), (59.9996, "00:01:00,000"),
                                  (3599.9996, "01:00:00,000"), (0.0006, "00:00:00,001"),
                                  (-0.0006, "00:00:00,000")):
            with self.subTest(seconds=seconds):
                self.assertEqual(srt_time(seconds), expected)

    def test_the_same_characters_as_trascrivi(self):
        for t in (0, 0.001, 2.3, 15.02, 59.999, 60, 3599.5, 3600, 3725.875, 7322.42, 36000.1):
            with self.subTest(t):
                self.assertEqual(clock(t), fmt(t))

    def test_a_time_before_the_start_is_written_as_zero(self):
        self.assertEqual(clock(-2), "00:00")
        self.assertEqual(srt_time(-0.5), "00:00:00,000")

    def test_a_subtitle_time_is_read_back(self):
        self.assertEqual(parse_srt_time("01:02:05,875"), 3725.875)
        self.assertEqual(parse_srt_time(" 00:00:03.48 "), 3.48)
        self.assertEqual(parse_srt_time("00:00:07"), 7)
        self.assertEqual(parse_srt_time("123:00:00,000"), 123 * 3600)
        for t in (0, 3.48, 59.5, 3725.875):
            self.assertAlmostEqual(parse_srt_time(srt_time(t)), t, places=3)

    def test_what_is_not_a_time_is_refused(self):
        for text in ("", "1:2", "00:61:00,000", "00:00:00,0000", "aa:bb:cc,ddd", "-00:00:01,000"):
            with self.subTest(text), self.assertRaises(ValueError):
                parse_srt_time(text)


if __name__ == "__main__":
    unittest.main()
