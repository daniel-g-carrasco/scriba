"""Synthetic BGRA frames and invented accessibility labels."""
import unittest

import _setup  # noqa: F401
from scriba.core import teams


class BorderTest(unittest.TestCase):
    def frame(self, sides):
        width, height, stride = 100, 80, 416
        pixels = bytearray(stride * height)
        for y in range(9, 71):
            for x in range(9, 91):
                side = 0 if y < 14 else 1 if y >= 66 else 2 if x < 14 else 3 if x >= 86 else -1
                if side in sides:
                    i = y * stride + x * 4
                    pixels[i:i + 4] = bytes((220, 80, 90, 255))
        return pixels, width, height, stride

    def test_three_sides_are_required_with_padded_stride(self):
        for sides, lit in [({0, 1, 2, 3}, True), ({0}, False), (set(), False)]:
            result = teams.border(*self.frame(sides), (10, 10, 90, 70))
            self.assertEqual(result.active, lit)
            self.assertEqual(len(result.sides), 4)

    def test_an_incomplete_tile_is_unavailable_instead_of_guessed(self):
        self.assertFalse(teams.border(*self.frame({0, 1, 2, 3}), (-10, 10, 90, 70)).active)
        with self.assertRaises(ValueError):
            teams.border(b"short", 100, 80, 400, (0, 0, 10, 10))


class TileNameTest(unittest.TestCase):
    def test_italian_and_english_labels_and_own_video(self):
        labels = [
            ("Example, Ada (Org) Esterno sconosciuto, Ha il controllo degli strumenti di produzione, "
             "notifiche disattivate, Menu di scelta rapida disponibile", "Example, Ada (Org)", False),
            ("Bob Example External, video on, Has producer controls, Context menu available", "Bob Example", False),
            ("Video personale, Cora Example - Org, Microfono disattivato, Ha il menu contestuale", "Cora Example - Org", True),
            ("Your video, Drew Example, Microphone muted, Has context menu", "Drew Example", True),
        ]
        for text, name, own in labels:
            with self.subTest(text=text):
                self.assertEqual(teams.tile_name(text), teams.Person(name, own))

    def test_sharing_and_unrelated_menu_items_are_rejected(self):
        for name in ("Contenuto condiviso da Ada Example", "Content shared by Bob Example", "Settings", ""):
            self.assertIsNone(teams.tile_name(name))
