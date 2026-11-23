import unittest
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import _setup  # noqa: F401

from scriba.app.finishing import CallChoice, Estimate
from scriba.app.task import Cancelled


class EstimateTest(unittest.TestCase):
    def test_estimate_uses_the_excerpt_duration_for_refining(self):
        estimate = Estimate(None, 600, 2, 1)
        estimate.step("waiting")
        before = estimate.snapshot().remaining
        estimate.use_excerpt(120)
        self.assertAlmostEqual(estimate.snapshot().remaining, before / 5)

    def test_work_and_observed_rate_update_a_single_bar(self):
        with TemporaryDirectory() as folder:
            speed_file = Path(folder) / "speeds.json"
            now = [0.0]
            estimate = Estimate(speed_file, 60, 3, 1, timer=lambda: now[0])
            estimate.step("joining")
            before = estimate.snapshot()
            now[0] += before.remaining * 3
            self.assertTrue(estimate.snapshot().slow)
            estimate.report(.5)
            partial = estimate.snapshot().fraction
            self.assertGreater(partial, before.fraction)
            estimate.report(.4)
            self.assertEqual(estimate.snapshot().fraction, partial)
            estimate.step("mixing")
            self.assertGreater(estimate.snapshot().fraction, partial)
            estimate.complete()
            self.assertEqual(estimate.snapshot().fraction, 1)
            self.assertIn("joining", speed_file.read_text(encoding="utf-8"))


class CallChoiceTest(unittest.TestCase):
    def test_whole_recording_answer_wins_before_the_deadline(self):
        now = [8.0]
        choice = CallChoice((10, 30), timer=lambda: now[0])
        self.assertEqual(choice.left(), 120)
        now[0] += 119
        self.assertTrue(choice.answer(False))
        self.assertFalse(choice.answer(True))
        now[0] += 1
        self.assertFalse(choice.wait(threading.Event()))

    def test_after_two_minutes_the_call_is_kept(self):
        now = [8.0]
        choice = CallChoice((10, 30), timer=lambda: now[0])
        now[0] += 120
        self.assertFalse(choice.answer(False))
        self.assertTrue(choice.wait(threading.Event()))

    def test_cancelling_leaves_the_answer_open_for_recovery(self):
        choice = CallChoice((10, 30))
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            choice.wait(cancelled)
