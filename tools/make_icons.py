#!/usr/bin/env python3
"""The PNG sizes of the app icon (GdkPixbuf with librsvg, which GTK brings:
no Pillow), from the scalable one in data/icons/hicolor/scalable/apps:

  data/icons/hicolor/<N>x<N>/apps/<APP_ID>.png   for N in 64, 128, 256
  site/icon.png                                   the 256 one, for the site and the Flatpak page

    python3 tools/make_icons.py

The scalable SVG is the master: edit it, run this, commit both. The SVGs
GTK loads are written plain (<defs>, never <ns0:defs>): this refuses one
with an SVG element under a prefix before drawing anything.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from scriba import APP_ID  # noqa: E402

SVG = ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
SIZES = (64, 128, 256)
SVG_NS = "http://www.w3.org/2000/svg"


def prefixed_elements(text: str) -> list[str]:
    """The SVG elements written with a prefix (<ns0:defs>, <svg:path>).

    GTK 4.24 draws an SVG icon with its own renderer, not librsvg, and that
    renderer skips such an element with all it holds: the gradients and the
    mask in a <ns0:defs> were lost and the app's icon came out empty in the
    update window and wherever GTK took the scalable size, while librsvg (the
    PNGs, the .ico) drew it whole (16/11/2026). ElementTree writes ns0: when
    the namespace is not registered: ET.register_namespace("", SVG_NS)
    before ET.tostring or write. A prefix for another namespace (Inkscape's
    metadata) is not drawn by anyone and is left alone."""
    prefixes = re.findall(r"xmlns:([\w.-]+)\s*=\s*[\"']" + re.escape(SVG_NS) + r"[\"']", text)
    if not prefixes:
        return []
    found = re.compile(r"<(?:%s):[\w.-]+" % "|".join(map(re.escape, prefixes)))
    return found.findall(text)


def main() -> int:
    for svg in sorted(SVG.parent.glob(f"{APP_ID}*.svg")):
        if bad := prefixed_elements(svg.read_text(encoding="utf-8")):
            print(f"{svg.relative_to(ROOT)}: {len(bad)} elements under a prefix ({bad[0]}...): "
                  "GTK does not draw them, write the SVG without it", file=sys.stderr)
            return 1
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

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
