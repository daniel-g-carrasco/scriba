#!/usr/bin/env python3
"""The PNG sizes of the app icon (GdkPixbuf with librsvg, which GTK brings:
no Pillow), from the scalable one in data/icons/hicolor/scalable/apps:

  data/icons/hicolor/<N>x<N>/apps/<APP_ID>.png   for N in 64, 128, 256
  site/icon.png                                   the 256 one, for the site and the Flatpak page

    python3 tools/make_icons.py

The scalable SVG is the master: edit it, run this, commit both.
"""

import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from scriba import APP_ID  # noqa: E402

SVG = ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
SIZES = (64, 128, 256)


def main() -> int:
    for size in SIZES:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(SVG), size, size)
        out = ROOT / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{APP_ID}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        pixbuf.savev(str(out), "png", [], [])
        print(out.relative_to(ROOT))
        if size == 256 and (ROOT / "site").is_dir():
            pixbuf.savev(str(ROOT / "site" / "icon.png"), "png", [], [])
            print("site/icon.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
