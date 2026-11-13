"""The text drawn larger on Windows where GTK's whole scale falls short of
the screen's (core/textscale.py)."""

import unittest

import _setup  # noqa: F401

from scriba.core import textscale

# Windows' scale, the screen's dpi, GTK's whole scale, the text's factor
CASES = ((100, 96, 1, 1.0), (125, 120, 1, 1.25), (150, 144, 1, 1.5), (175, 168, 1, 1.75),
         (200, 192, 2, 1.0), (250, 240, 2, 1.25), (300, 288, 3, 1.0))


class TextScaleTest(unittest.TestCase):
    def test_gtk_scale_is_the_screens_rounded_down(self):
        for percent, dpi, scale, _factor in CASES:
            self.assertEqual(textscale.gtk_scale(dpi), scale, percent)
        self.assertEqual(textscale.gtk_scale(72), 1)        # never below 1

    def test_the_text_gets_back_what_the_whole_scale_leaves_out(self):
        for percent, dpi, scale, factor in CASES:
            self.assertAlmostEqual(textscale.text_factor(dpi, scale), factor, msg=percent)

    def test_the_text_and_the_whole_scale_together_make_the_screens_scale(self):
        for percent, dpi, scale, _factor in CASES:
            self.assertAlmostEqual(textscale.text_factor(dpi, scale) * scale, percent / 100, msg=percent)

    def test_an_unknown_screen_leaves_the_text_as_it_is(self):
        self.assertEqual(textscale.text_factor(0, 2), 1.0)
        self.assertEqual(textscale.text_factor(240, 0), 1.0)

    def test_gtks_default_is_96_dots_per_inch(self):
        self.assertEqual(textscale.text_dpi(-1, 240, 2), 120 * 1024)
        self.assertEqual(textscale.text_dpi(0, 144, 1), 144 * 1024)
        self.assertEqual(textscale.text_dpi(-1, 192, 2), 96 * 1024)

    def test_a_dpi_the_user_already_had_is_kept_and_multiplied(self):
        self.assertEqual(textscale.text_dpi(110 * 1024, 240, 2), round(110 * 1024 * 1.25))
        self.assertEqual(textscale.text_dpi(110 * 1024, 96, 1), 110 * 1024)

    def test_it_is_what_gdk_gives_a_system_aware_process(self):
        # gtk-xft-dpi = 1024 * dpi / scale, measured on 2026-11-13: 120 at 240 dpi and scale 2
        for _percent, dpi, scale, _factor in CASES:
            self.assertEqual(textscale.text_dpi(-1, dpi, scale), 1024 * dpi // scale)


if __name__ == "__main__":
    unittest.main()
