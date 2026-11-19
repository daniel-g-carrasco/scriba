#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""tools/make_icons.py: the PNG sizes of the app icon for the icon theme
and the site, drawn as make_ico.py draws (with the theme's margin), in an
app made for the purpose. The SVG is drawn for real only where GdkPixbuf
is; elsewhere the drawing is replaced."""

import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import _setup

TOOLS = _setup.ROOT / "tools"
INIT = '"""An app made for the test."""\nAPP_ID = "org.example.my-app"\nSLUG = "my-app"\n'
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
       '<circle cx="256" cy="256" r="240" fill="#1a5fb4"/></svg>\n')


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
        (self.root / "tools").mkdir()
        for name in ("make_icons.py", "make_ico.py"):            # make_icons draws with make_ico
            shutil.copy(TOOLS / name, self.root / "tools" / name)

    def png(self, size: int) -> Path:
        return self.root / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "org.example.my-app.png"

    def test_the_three_sizes_and_the_sites_icon(self):
        spec = importlib.util.spec_from_file_location("make_icons_under_test", TOOLS / "make_icons.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        asked = []

        def fake(svg, size, full_view=False):
            asked.append((Path(svg), size, full_view))
            return b"PNG%d" % size
        out = io.StringIO()
        with mock.patch.object(tool, "render_png", fake), redirect_stdout(out):
            tool.main(self.root)
        self.assertEqual(asked, [(self.svg, size, False) for size in (64, 128, 256)], "the theme keeps the margin")
        self.assertEqual([self.png(size).read_bytes() for size in (64, 128, 256)], [b"PNG64", b"PNG128", b"PNG256"])
        self.assertFalse((self.root / "site").exists(), "no site: no site/icon.png")
        (self.root / "site").mkdir()
        with mock.patch.object(tool, "render_png", fake), redirect_stdout(out):
            tool.main(self.root)
        self.assertEqual((self.root / "site" / "icon.png").read_bytes(), b"PNG256")
        self.assertIn("site/icon.png", out.getvalue())

    def test_from_the_command_line(self):
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf  # noqa: F401
        except (ImportError, ValueError) as error:
            self.skipTest(f"GdkPixbuf is not in this Python ({error}): the drawing is not tried")
        run = subprocess.run([sys.executable, str(self.root / "tools" / "make_icons.py")], capture_output=True,
                             text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        for size in (64, 128, 256):
            self.assertTrue(self.png(size).read_bytes().startswith(b"\x89PNG"), size)


if __name__ == "__main__":
    unittest.main()
