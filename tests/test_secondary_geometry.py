# SPDX-License-Identifier: GPL-3.0-or-later
"""Sizing and centering apply to all the secondary surfaces."""

import unittest

import _setup

sizing = _setup.shared("gui.sizing")


class GeometryTest(unittest.TestCase):
    def test_small_displays_clamp_the_content_without_a_minimum(self):
        self.assertEqual(sizing.preference_size(600, 640, 300, 180), (300, 180))

    def test_a_resized_parent_is_the_current_reference(self):
        work_area = (-800, 0, 800, 600)
        self.assertEqual(sizing.centred((200, 100), (0, 40, 600, 420), work_area), (200, 180))
        self.assertEqual(sizing.centred((200, 100), (0, 40, 400, 300), work_area), (100, 120))

    def test_an_oversize_dialog_keeps_the_native_title_bar_in_the_work_area(self):
        self.assertEqual(sizing.centred((900, 1000), (0, 20, 600, 480), (10, 30, 800, 640)), (10, 30))
