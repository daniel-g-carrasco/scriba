"""The reader's policy and lifecycle without Teams or a desktop."""
import threading
import unittest
from unittest.mock import Mock

import _setup  # noqa: F401
from scriba.app import teams


class CaptureTest(unittest.TestCase):
    def test_minimized_and_absent_meetings_never_capture(self):
        reader = teams.Reader.__new__(teams.Reader)
        reader._user32 = Mock()
        reader._teams_windows = lambda: [123]
        reader._elements = lambda hwnd: (True, [("Ada Example", (0, 0, 80, 60))])
        reader._window, reader._policy = None, teams.CapturePolicy()
        reader._capture = Mock()
        reader._user32.IsIconic.return_value = True
        self.assertEqual(reader.read().state, "minimized")
        reader._capture.assert_not_called()
        reader._elements = lambda hwnd: (False, [])
        self.assertEqual(reader.read().state, "no-meeting")
        reader._capture.assert_not_called()

    def test_black_and_stale_prints_need_an_uncovered_screen(self):
        policy = teams.CapturePolicy()
        good, changed, black = bytes((90, 80, 70, 0)) * 20, bytes((80, 70, 60, 0)) * 20, bytes(80)
        self.assertIsNone(policy.choose(black, None))
        self.assertEqual(policy.choose(good, good), good)
        self.assertIsNone(policy.choose(good, None))
        self.assertEqual(policy.choose(changed, None), changed)
        self.assertIsNone(policy.choose(changed, None))
        self.assertEqual(policy.choose(black, good), good)

    def test_first_covered_frame_is_not_claimed_to_be_fresh(self):
        self.assertIsNone(teams.CapturePolicy().choose(bytes((80, 70, 60, 0)) * 20, None))

    def test_follow_reports_failures_and_closes_on_the_same_thread(self):
        stop, readings = threading.Event(), []

        class Reader:
            closed = False
            def read(self):
                if not readings:
                    raise OSError("invented failure")
                return teams.Reading("ready", ("Ada Example",), 2)
            def close(self):
                self.closed = True

        reader = Reader()
        def told(reading):
            readings.append(reading)
            if len(readings) == 2:
                stop.set()
        teams.follow(told, stop, every=0.001, opener=lambda: reader)
        self.assertEqual([r.state for r in readings], ["unavailable", "ready"])
        self.assertTrue(reader.closed)
