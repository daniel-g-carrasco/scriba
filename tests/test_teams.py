"""The reader's policy and lifecycle without Teams or a desktop."""
import threading
import unittest
from unittest.mock import Mock

import _setup  # noqa: F401
from scriba.app import teams
from scriba.core.teams import tile_name


class CaptureTest(unittest.TestCase):
    def test_own_tile_counts_without_being_used_for_names(self):
        reader = teams.Reader.__new__(teams.Reader)
        reader._user32 = Mock()
        reader._user32.IsIconic.return_value = False
        reader._user32.GetDpiForWindow.return_value = 192
        reader._teams_windows = lambda: [123]
        bounds = (0, 0, 80, 60)
        own = tile_name("Video personale, Ada Example, Microfono disattivato")
        other = tile_name("Cora Example, Microfono disattivato")
        reader._elements = lambda _hwnd: (True, [(own, bounds), (other, bounds)])
        reader._window, reader._policy = None, teams.CapturePolicy()
        reader._meeting_title = lambda _hwnd: "Example"
        reader._rect = lambda _hwnd: bounds
        reader._uncovered = lambda _hwnd, _bounds: True
        reader._capture = Mock(return_value=bytes((90, 80, 70, 0)) * 80 * 60)
        sides = (.05, .85, .94, .96)
        bands = (None, teams.teams.Band(.85, 3, 4), teams.teams.Band(.94, 3, 4), teams.teams.Band(.96, 3, 4))
        with unittest.mock.patch.object(teams.teams, "border", return_value=teams.teams.Border(sides, True, bands)) as border:
            reading = reader.read()
        self.assertEqual(reading.people, 2)
        self.assertEqual(reading.active, ("Cora Example",))
        self.assertEqual((reading.tiles, reading.capture_source, reading.covered), (2, "print-window", False))
        self.assertEqual(reading.border_sides, sides)
        self.assertEqual(reading.border_bands, bands)
        border.assert_called_once_with(unittest.mock.ANY, 80, 60, 320, bounds, dpi=192)
        self.assertEqual(reading.remote_names, ("Cora Example",))
        reader._capture.return_value = None
        unavailable = reader.read()
        self.assertEqual(unavailable.state, "unavailable")
        self.assertEqual(unavailable.remote_names, ("Cora Example",))

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
        self.assertEqual([r.error for r in readings], [True, False])
        self.assertTrue(reader.closed)

    def test_end_requires_a_seen_meeting_and_six_consecutive_absences(self):
        states = (["no-meeting"] * 6 + ["ready"] + ["no-meeting"] * 5 + ["unavailable", "minimized"]
                  + ["no-meeting"] * 8)
        replies = iter(teams.Reading(state) for state in states)
        reader = Mock()
        reader.read.side_effect = replies
        stop, seen = threading.Event(), []

        def told(reading):
            seen.append(reading.state)
            if len(seen) == len(states):
                stop.set()

        teams.follow(told, stop, every=0.001, opener=lambda: reader)
        self.assertEqual([i for i, state in enumerate(seen) if state == "ended"], [19])
