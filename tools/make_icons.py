#!/usr/bin/env python3
"""The PNG sizes of the app icon (GdkPixbuf with librsvg, which GTK brings:
no Pillow), from the scalable one in data/icons/hicolor/scalable/apps, and
the variants of the scalable icons for a dark Windows taskbar:

  data/icons/hicolor/<N>x<N>/apps/<APP_ID>.png   for N in 64, 128, 256
  site/icon.png                                   the 256 one, for the site and the Flatpak page
  data/icons/hicolor/scalable/apps/<name>-on-dark.svg   for every <APP_ID>*.svg there

    python3 tools/make_icons.py

The scalable SVGs are the masters: edit them, run this, commit all.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from scriba import APP_ID  # noqa: E402

SCALABLE = ROOT / "data" / "icons" / "hicolor" / "scalable" / "apps"
SVG = SCALABLE / f"{APP_ID}.svg"
SIZES = (64, 128, 256)
ON_DARK = "-on-dark"
# The disc dark and the nib ivory sink into a dark taskbar: on it the two
# swap, through the gradients every icon already defines. The state's mark
# (red, amber, blue) stays as it is.
SWAPS = (("url(#sfumatura-cerchio-scuro)", "url(#sfumatura-cerchio-avorio)"),
         ("url(#sfumatura-corpo-avorio)", "url(#sfumatura-corpo-scuro)"),
         ("url(#sfumatura-ombra-avorio)", "url(#sfumatura-ombra-scura)"))


def on_dark(svg: str) -> str:
    """The variant of an icon for a dark taskbar: ivory disc, dark nib,
    shapes and measures untouched. Fails rather than write an icon that
    one of the swaps missed."""
    for normal, dark in SWAPS:
        if svg.count(normal) != 1:
            raise ValueError(f"{normal} is used {svg.count(normal)} times, not once")
        svg = svg.replace(normal, dark)
    return svg


def masters() -> list[Path]:
    """The normal scalable icons: the app's and its states'."""
    return sorted(path for path in SCALABLE.glob(f"{APP_ID}*.svg") if not path.stem.endswith(ON_DARK))


def variant(path: Path) -> Path:
    return path.with_name(path.stem + ON_DARK + path.suffix)


def main() -> int:
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
    for master in masters():
        out = variant(master)
        out.write_bytes(on_dark(master.read_text(encoding="utf-8")).encode("utf-8"))
        print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
