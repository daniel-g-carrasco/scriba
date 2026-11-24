"""The two ends of a recording selection are shared by the stop and edit views."""

import unittest

import _setup  # noqa: F401
from scriba.app.finishing import CallChoice
from scriba.core.trim_range import Selection


class TrimRangeTest(unittest.TestCase):
    def test_handles_clamp_and_steps_work_without_crossing(self):
        bars = Selection(120, 25, 102)
        self.assertEqual(bars.position("start", 240), 50)
        bars.step("start", 10)
        bars.step("end", -1)
        self.assertEqual(bars.interval, (35, 101))
        bars.move("start", 110)
        self.assertLess(bars.start, bars.end)
        bars.move("end", 500)
        self.assertEqual(bars.end, 120)
        self.assertEqual(bars.position("end", 240), 240)

    def test_automatic_trim_applies_the_last_valid_selection(self):
        now = [1000]
        choice = CallChoice((20, 90), duration=120, timer=lambda: now[0])
        self.assertTrue(choice.select((30, 80)))
        self.assertFalse(choice.select((110, 100)))
        now[0] += 120
        self.assertTrue(choice.wait(None))
        self.assertEqual(choice.interval, (30, 80))
        self.assertFalse(choice.select((40, 60)))

    def test_keep_all_wins_over_the_countdown(self):
        now = [1000]
        choice = CallChoice((20, 90), duration=120, timer=lambda: now[0])
        self.assertTrue(choice.answer(False))
        now[0] += 120
        self.assertFalse(choice.wait(None))
