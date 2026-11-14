#!/usr/bin/env python3
"""The app's icons: one drawing for every surface (dark disc, ivory nib), with
the icon theme's margin in the SVGs and the disc to the edge on Windows."""

import importlib.util
import re
import unittest

import _setup

APP_ID = "io.github.daniel_g_carrasco.scriba"
SCALABLE = _setup.ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps"
SVG = SCALABLE / f"{APP_ID}.svg"


def masters():
    """The scalable icons: the app's and its states'."""
    return sorted(SCALABLE.glob(f"{APP_ID}*.svg"))


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


class OneIconTest(unittest.TestCase):
    def test_there_is_one_drawing_for_every_surface(self):
        # the app's icon and five states, each once: no variant for a dark
        # taskbar since 14/11/2026, the dark disc and ivory nib everywhere
        self.assertEqual([m.name.removeprefix(APP_ID) for m in masters()],
                         ["-downloading.svg", "-error.svg", "-paused.svg", "-recording.svg",
                          "-transcribing.svg", ".svg"])
        for master in masters():
            uses = re.findall(r"url\(#[^)]*\)", master.read_text(encoding="utf-8"))
            self.assertIn("url(#sfumatura-cerchio-scuro)", uses, master.name)
            self.assertIn("url(#sfumatura-corpo-avorio)", uses, master.name)


class MarginTest(unittest.TestCase):
    """Linux keeps the icon theme's margin, Windows draws the disc to the
    edge (the taskbar shrinks the icon: there a margin is a smaller disc)."""

    def test_the_svg_has_the_margin_and_the_exes_icon_is_full_to_the_edge(self):
        GdkPixbuf = pixbufs()
        spec = importlib.util.spec_from_file_location("make_ico", _setup.ROOT / "tools" / "make_ico.py")
        make_ico = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(make_ico)
        for svg in masters():
            self.assertIn('<circle cx="256" cy="256" r="240"', svg.read_text(encoding="utf-8"), svg.name)
            self.assertEqual(drawn(make_ico.windows_pixbuf(svg, 32)), (32, 32), svg.name)
        linux = GdkPixbuf.Pixbuf.new_from_file_at_size(str(SVG), 32, 32)
        self.assertEqual(drawn(linux), (30, 30))                # 240 of 256: a pixel of margin a side
        png = GdkPixbuf.Pixbuf.new_from_file(str(_setup.ROOT / "data" / "icons" / "hicolor" / "256x256" / "apps"
                                                 / f"{APP_ID}.png"))
        self.assertEqual(drawn(png), (240, 240))                # the PNGs drawn from it, with the margin

    def test_the_states_mark_stays_inside_windows_view(self):
        # Windows shows 16 16 480 480 of the 512 (the disc to the edge): the
        # mark, at the corner, was cut 16 units right and below until 13/11/2026
        mark = re.compile(r'translate\((\d+) (\d+)\) scale\((\d+)\)"><circle r="([\d.]+)"')
        states = [m for m in masters() if m != SVG]
        self.assertEqual(len(states), 5)
        for svg in states:
            x, y, scale, radius = (float(v) for v in mark.search(svg.read_text(encoding="utf-8")).groups())
            self.assertEqual((scale * radius), 128.0, svg.name)          # measure and drawing untouched
            self.assertLessEqual(x + scale * radius, 16 + 480, svg.name)
            self.assertLessEqual(y + scale * radius, 16 + 480, svg.name)


if __name__ == "__main__":
    unittest.main()
