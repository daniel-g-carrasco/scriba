#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/make_ico.py: the Windows .ico, one PNG per size the system draws,
from the app icon's SVG (the version drawn for 16-24 pixels when there is
one), with what is drawn to the edge; in an app made for the purpose. The SVG
is drawn for real only where GdkPixbuf is (a GTK app's Python); elsewhere
the drawing is replaced and the file around it is checked."""

import importlib.util
import io
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _setup

TOOL = _setup.ROOT / "tools" / "make_ico.py"
INIT = '"""An app made for the test."""\nAPP_ID = "org.example.my-app"\nSLUG = "my-app"\n'
# the family's disc: radius 240 of 256, a margin on Linux
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
       '<circle cx="256" cy="256" r="240" fill="#1a5fb4"/></svg>\n')
# the family's square canvas: a face of 448 with its side 17.92 lower, a margin on Linux too
SQUARE = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
          '<rect x="32" y="40.96" width="448" height="448" rx="81" fill="#bfb9aa"/>'
          '<rect x="32" y="23.04" width="448" height="448" rx="81" fill="#f3f1ea"/></svg>\n')


def load(path: Path = TOOL, name: str = "make_ico_under_test"):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gdk_pixbuf():
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except (ImportError, ValueError) as error:
        raise unittest.SkipTest(f"GdkPixbuf is not in this Python ({error}): the drawing is not tried") from error
    return GdkPixbuf


def drawn(GdkPixbuf, png: bytes) -> tuple[int, int]:
    """The width and height of what is drawn (the pixels not transparent)."""
    loader = GdkPixbuf.PixbufLoader()
    loader.write(png)
    loader.close()
    pixbuf = loader.get_pixbuf()
    data, stride = pixbuf.get_pixels(), pixbuf.get_rowstride()
    xs, ys = set(), set()
    for y in range(pixbuf.get_height()):
        for x in range(pixbuf.get_width()):
            if data[y * stride + 4 * x + 3] > 0:
                xs.add(x)
                ys.add(y)
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if xs else (0, 0)


def entries(data: bytes) -> list[tuple[int, bytes]]:
    """The (size, image) of each entry of an .ico."""
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    out = []
    for n in range(count):
        width, height, _colours, _r, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * n)
        assert (width, planes, bits) == (height, 1, 32)
        out.append((width or 256, data[offset:offset + size]))
    return out


class App(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "src" / "my_app").mkdir(parents=True)
        (self.root / "src" / "my_app" / "__init__.py").write_text(INIT, encoding="utf-8")
        self.svg = self.root / "data" / "icons" / "hicolor" / "scalable" / "apps" / "org.example.my-app.svg"
        self.svg.parent.mkdir(parents=True)
        self.svg.write_text(SVG, encoding="utf-8")
        (self.root / "build-aux" / "windows").mkdir(parents=True)
        (self.root / "tools").mkdir()
        shutil.copy(TOOL, self.root / "tools" / TOOL.name)


class TheFile(App):
    def test_one_png_per_size_the_small_ones_from_their_own_drawing(self):
        tool = load()
        small = self.root / "build-aux" / "windows" / "my-app-small.svg"
        small.write_text(SVG, encoding="utf-8")
        asked = []

        def fake(svg, size, full_view=False):
            asked.append((Path(svg).name, size, full_view))
            return b"PNG%d" % size
        with mock.patch.object(tool, "render_png", fake), redirect_stdout(io.StringIO()):
            self.assertEqual(tool.main([], root=self.root), 0)
        data = (self.root / "build-aux" / "windows" / "my-app.ico").read_bytes()
        self.assertEqual(entries(data), [(size, b"PNG%d" % size) for size in tool.SIZES])
        self.assertEqual(tool.SIZES, (16, 20, 24, 32, 40, 48, 64, 128, 256))
        self.assertEqual([a for a in asked if a[0] == small.name], [(small.name, s, True) for s in (16, 20, 24)])
        self.assertTrue(all(full for _n, _s, full in asked), "every size with the drawing to the edge")

    def test_without_the_small_drawing_the_icon_everywhere_and_another_file_asked(self):
        tool = load()
        out = self.root / "elsewhere.ico"
        with mock.patch.object(tool, "render_png", lambda svg, size, full_view=False: Path(svg).name.encode()), \
                redirect_stdout(io.StringIO()):
            tool.main([str(out)], root=self.root)
        self.assertEqual({png for _size, png in entries(out.read_bytes())}, {self.svg.name.encode()})

    def test_the_names_are_read_without_importing_the_package(self):
        (self.root / "src" / "my_app" / "__init__.py").write_text(
            INIT + 'raise SystemExit("imported")\nWEBSITE = f"https://example.org/{SLUG}"\n', encoding="utf-8")
        tool = load()
        self.assertEqual(tool.identity(self.root)["WEBSITE"], "https://example.org/my-app")
        self.assertEqual(tool.icon_paths(self.root), (self.svg, self.root / "build-aux" / "windows" / "my-app-small.svg"))


class TheDrawing(App):
    def test_the_svg_keeps_the_margin_and_the_windows_icon_reaches_the_edge(self):
        GdkPixbuf = gdk_pixbuf()
        tool = load()
        self.assertEqual(drawn(GdkPixbuf, tool.render_png(self.svg, 32)), (30, 30), "240 of 256: a pixel a side")
        self.assertEqual(drawn(GdkPixbuf, tool.render_png(self.svg, 32, full_view=True)), (32, 32))

    def test_the_square_canvas_keeps_its_margin_and_reaches_the_edge_on_windows(self):
        GdkPixbuf = gdk_pixbuf()
        self.svg.write_text(SQUARE, encoding="utf-8")
        tool = load()
        self.assertIn('viewBox="23 23 466 466"', tool.windows_view(self.svg), "a square round the face and its side")
        self.assertEqual(drawn(GdkPixbuf, tool.render_png(self.svg, 32)), (28, 30), "448 of 512, the side below")
        self.assertEqual(drawn(GdkPixbuf, tool.render_png(self.svg, 32, full_view=True)), (32, 32))
        self.assertEqual(drawn(GdkPixbuf, tool.render_png(self.svg, 24, full_view=True)), (24, 24))

    def test_a_drawing_for_the_small_sizes_is_taken_as_it_is(self):
        gdk_pixbuf()
        small = self.root / "build-aux" / "windows" / "my-app-small.svg"
        text = SVG.replace('width="512" height="512" viewBox="0 0 512 512"', 'width="16" height="16" viewBox="0 0 16 16"')
        small.write_text(text, encoding="utf-8")
        self.assertEqual(load().windows_view(small), text, "drawn on the pixel grid: no view closed in")

    def test_from_the_command_line(self):
        gdk_pixbuf()
        run = subprocess.run([sys.executable, str(self.root / "tools" / TOOL.name)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        sizes = [size for size, png in entries((self.root / "build-aux" / "windows" / "my-app.ico").read_bytes())
                 if png.startswith(b"\x89PNG")]
        self.assertEqual(sizes, [16, 20, 24, 32, 40, 48, 64, 128, 256])


if __name__ == "__main__":
    unittest.main()
