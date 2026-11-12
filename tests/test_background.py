"""Hiding is conditional on a working way to present the window again."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import _setup

_setup.gtk_or_skip()

from scriba.gui.background import Background  # noqa: E402


class BackgroundAccessTest(unittest.TestCase):
    def setUp(self):
        self.window = Mock()
        self.background = Background.__new__(Background)
        self.background.app = SimpleNamespace(window=self.window, _window=lambda: self.window, closing=False, exit_ready=False)
        self.background.tray = SimpleNamespace(available=False)
        self.background._stopping = False
        self.background.hidden = False

    def test_missing_tray_keeps_window_accessible(self):
        self.assertFalse(self.background.hide())
        self.window.set_visible.assert_not_called()

    def test_losing_the_tray_presents_a_hidden_window(self):
        self.background.tray.available = True
        self.assertTrue(self.background.hide())
        self.window.set_visible.assert_called_once_with(False)
        self.background._availability(False)
        self.window.present.assert_called_once()
        self.assertFalse(self.background.hidden)

    def test_stopping_does_not_present_window_again(self):
        self.background.hidden = True
        self.background._stopping = True
        self.background._availability(False)
        self.window.present.assert_not_called()
