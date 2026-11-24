"""Synthetic BGRA frames and invented accessibility labels."""
import unittest

import _setup  # noqa: F401
from scriba.core import teams

TEAMS_BLUE = bytes((243, 132, 128, 255))


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

    def draw_border(self, pixels, stride, tile, thickness=1, *, top_visible=True, corners=0):
        left, top, right, bottom = tile
        edge = TEAMS_BLUE * (right - left - 2 * corners)
        for n in range(thickness):
            for y in ((top + n, bottom - 1 - n) if top_visible else (bottom - 1 - n,)):
                at = y * stride + (left + corners) * 4
                pixels[at:at + len(edge)] = edge
            for y in range(top + corners, bottom - corners):
                for x in (left + n, right - 1 - n):
                    at = y * stride + x * 4
                    pixels[at:at + 4] = TEAMS_BLUE
        if top_visible and corners:
            for dx in range(corners):
                for dy in range(corners):
                    squared = (dx - corners) ** 2 + (dy - corners) ** 2
                    if abs(squared - corners ** 2) > corners:
                        continue
                    for x in (left + dx, right - 1 - dx):
                        for y in (top + dy, bottom - 1 - dy):
                            at = y * stride + x * 4
                            pixels[at:at + 4] = TEAMS_BLUE

    def test_one_pixel_on_three_sides_passes_at_the_measured_4k_geometry(self):
        width, height = 3866, 2090
        stride = width * 4
        tile = (400, 250, 400 + 2604, 250 + 1463)
        pixels = bytearray(stride * height)
        self.assertFalse(teams.border(pixels, width, height, stride, tile).active)
        self.draw_border(pixels, stride, tile, top_visible=False, corners=12)
        measured = teams.border(pixels, width, height, stride, tile)
        self.assertTrue(measured.active)
        self.assertEqual(measured.sides[0], 0)
        self.assertGreater(min(measured.sides[1:]), .95)

    def test_three_pixel_and_rounded_borders_pass(self):
        width, height, stride = 120, 100, 496
        tile = (10, 10, 110, 90)
        for thickness, corners in ((3, 0), (1, 12)):
            with self.subTest(thickness=thickness, corners=corners):
                pixels = bytearray(stride * height)
                self.draw_border(pixels, stride, tile, thickness, corners=corners)
                self.assertTrue(teams.border(pixels, width, height, stride, tile).active)

    def test_extensive_blue_video_at_two_edges_or_all_edges_is_not_a_border(self):
        width, height, stride = 120, 100, 496
        tile = (10, 10, 110, 90)
        for end_x, end_y in ((80, 70), (110, 90)):
            pixels = bytearray(stride * height)
            row = TEAMS_BLUE * (end_x - tile[0])
            for y in range(tile[1], end_y):
                at = y * stride + tile[0] * 4
                pixels[at:at + len(row)] = row
            self.assertFalse(teams.border(pixels, width, height, stride, tile).active)


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
