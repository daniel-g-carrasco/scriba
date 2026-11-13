#!/usr/bin/env python3
"""build-aux/windows/<slug>.ico from the app icon's SVG: one PNG per size,
for the title bars, Explorer, the taskbar and the installer (GdkPixbuf
with librsvg). The title bar draws 16 pixels at 100% and 20 at 125%, and
shrinks what it is given without a filter: every size the system draws
must be in the file. An icon with thin lines needs a version redrawn for
the small sizes (SMALL_SVG, as Spola has); a tile with thick shapes reads
at 16 pixels as it is."""

import struct
import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from scriba import APP_ID, SLUG  # noqa: E402

SVG = ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
SMALL_SVG = ROOT / "build-aux" / "windows" / f"{SLUG}-small.svg"     # optional: drawn for 16-24 pixels
SMALL = (16, 20, 24)
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
# The SVG keeps the icon theme's margin (the disc's radius 240 of 256), for
# Linux; on Windows the disc goes to the edge, the view closed on it
# (512/480 = 1.0667 times larger around the centre): the same as
# gui/wintray.py's FULL_VIEW for the notification area.
FULL_VIEW = ('viewBox="0 0 512 512"', 'viewBox="16 16 480 480"')


def windows_pixbuf(svg: Path, size: int) -> GdkPixbuf.Pixbuf:
    """The icon at `size` pixels as Windows shows it: the disc to the edge."""
    loader = GdkPixbuf.PixbufLoader()
    loader.set_size(size, size)
    loader.write(svg.read_text(encoding="utf-8").replace(*FULL_VIEW).encode("utf-8"))
    loader.close()
    return loader.get_pixbuf()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build-aux" / "windows" / f"{SLUG}.ico"
    images = []
    for size in SIZES:
        source = SMALL_SVG if size in SMALL and SMALL_SVG.is_file() else SVG
        pixbuf = windows_pixbuf(source, size)
        _ok, data = pixbuf.save_to_bufferv("png", [], [])
        images.append((size, bytes(data)))
    offset = 6 + 16 * len(images)
    entries, payload = b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(payload))
        payload += png
    out.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + payload)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
