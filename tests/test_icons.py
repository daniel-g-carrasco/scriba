#!/usr/bin/env python3
"""The icons for a dark Windows taskbar (tools/make_icons.py): one for every
normal icon, the same drawing with the disc and the nib in each other's
colours, and up to date with the normal ones."""

import importlib.util
import re
import unittest

import _setup

SPEC = importlib.util.spec_from_file_location("make_icons", _setup.ROOT / "tools" / "make_icons.py")
make_icons = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(make_icons)


class OnDarkTest(unittest.TestCase):
    def test_every_icon_has_its_variant_written_from_it(self):
        masters = make_icons.masters()
        self.assertEqual(len(masters), 6)           # the app's and five states
        for master in masters:
            variant = make_icons.variant(master)
            self.assertTrue(variant.is_file(), f"{variant.name}: run tools/make_icons.py")
            self.assertEqual(variant.read_text(encoding="utf-8"),
                             make_icons.on_dark(master.read_text(encoding="utf-8")),
                             f"{variant.name} is older than {master.name}: run tools/make_icons.py")

    def test_only_the_three_gradients_change(self):
        master = make_icons.SVG.read_text(encoding="utf-8")
        variant = make_icons.on_dark(master)
        uses = re.compile(r"url\(#[^)]*\)")
        self.assertEqual(uses.findall(variant), ["url(#sfumatura-cerchio-avorio)", "url(#nib-holes)",
                                                 "url(#sfumatura-corpo-scuro)", "url(#sfumatura-ombra-scura)"])
        self.assertEqual(uses.sub("", variant), uses.sub("", master))       # shapes and measures as they are

    def test_a_drawing_the_swap_does_not_fit_is_refused(self):
        with self.assertRaises(ValueError):
            make_icons.on_dark('<svg><circle fill="#000"/></svg>')


def drawn(pixbuf) -> tuple[int, int]:
    """The width and height of what is drawn (pixels not transparent)."""
    data, stride, size = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_width()
    xs, ys = set(), set()
    for y in range(pixbuf.get_height()):
        for x in range(size):
            if data[y * stride + 4 * x + 3] > 0:
                xs.add(x)
                ys.add(y)
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if xs else (0, 0)


def pixbufs():
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except (ImportError, ValueError) as error:
        raise unittest.SkipTest(f"GdkPixbuf: {error}") from error
    return GdkPixbuf


class MarginTest(unittest.TestCase):
    """Linux keeps the icon theme's margin, Windows draws the disc to the
    edge (the taskbar shrinks the icon: there a margin is a smaller disc)."""

    def test_the_svg_has_the_margin_and_the_exes_icon_is_full_to_the_edge(self):
        GdkPixbuf = pixbufs()
        spec = importlib.util.spec_from_file_location("make_ico", _setup.ROOT / "tools" / "make_ico.py")
        make_ico = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_ico)
        for master in make_icons.masters():
            for svg in (master, make_icons.variant(master)):
                self.assertIn('<circle cx="256" cy="256" r="240"', svg.read_text(encoding="utf-8"), svg.name)
                self.assertEqual(drawn(make_ico.windows_pixbuf(svg, 32)), (32, 32), svg.name)
        linux = GdkPixbuf.Pixbuf.new_from_file_at_size(str(make_icons.SVG), 32, 32)
        self.assertEqual(drawn(linux), (30, 30))                # 240 of 256: a pixel of margin a side
        png = GdkPixbuf.Pixbuf.new_from_file(str(_setup.ROOT / "data" / "icons" / "hicolor" / "256x256" / "apps"
                                                 / f"{make_icons.APP_ID}.png"))
        self.assertEqual(drawn(png), (240, 240))                # the PNGs drawn from it, with the margin

    def test_the_states_mark_stays_inside_windows_view(self):
        # Windows shows 16 16 480 480 of the 512 (the disc to the edge): the
        # mark, at the corner, was cut 16 units right and below until 13/11/2026
        mark = re.compile(r'translate\((\d+) (\d+)\) scale\((\d+)\)"><circle r="([\d.]+)"')
        states = [m for m in make_icons.masters() if m != make_icons.SVG]
        self.assertEqual(len(states), 5)
        for master in states:
            for svg in (master, make_icons.variant(master)):
                x, y, scale, radius = (float(v) for v in mark.search(svg.read_text(encoding="utf-8")).groups())
                self.assertEqual((scale * radius), 128.0, svg.name)          # measure and drawing untouched
                self.assertLessEqual(x + scale * radius, 16 + 480, svg.name)
                self.assertLessEqual(y + scale * radius, 16 + 480, svg.name)


if __name__ == "__main__":
    unittest.main()
