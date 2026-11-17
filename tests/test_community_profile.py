"""Development profiling must leave inputs and outputs alone."""
import _setup  # noqa: F401
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))


class ProfileTests(unittest.TestCase):
    def test_timer_returns_the_original_object_and_counts_calls(self):
        from community_profile import Timings
        times = Timings()
        original = object()
        wrapped = times.wrap('fbank', lambda: original)
        self.assertIs(wrapped(), original)
        self.assertIs(wrapped(), original)
        self.assertEqual(times.calls, {'fbank': 2})
        self.assertGreaterEqual(times.seconds['fbank'], 0)
