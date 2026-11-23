import unittest

import _setup  # noqa: F401

from scriba.core import echo


class EchoRangesTest(unittest.TestCase):
    def test_keeps_only_audible_windows_with_echo(self):
        windows = [
            echo.Window(0, 10, None, None, -30, -100),
            echo.Window(10, 20, .7, .030, -30, -28),
            echo.Window(20, 30, .09, .030, -30, -28),
            echo.Window(30, 40, None, None, -25, -100),
            echo.Window(40, 50, None, None, -100, -30),
        ]
        self.assertEqual(echo.clean_ranges(windows), [(10, 30)])
