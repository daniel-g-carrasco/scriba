#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The PNG sizes of the app icon, from the scalable one in
data/icons/hicolor/scalable/apps (drawn as make_ico.py draws: GdkPixbuf
in a GTK app, Qt in a Qt app; no Pillow):

  data/icons/hicolor/<N>x<N>/apps/<APP_ID>.png   for N in 64, 128, 256
  site/icon.png                                   the 256 one, for the site and the Flatpak page

    python3 tools/make_icons.py

The scalable SVG is the master: edit it, run this, commit both. It needs
tools/make_ico.py next to it.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_ico import ROOT, icon_paths, identity, render_png  # noqa: E402

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


def main(root: Path = ROOT) -> int:
    root = Path(root)
    app_id = identity(root)["APP_ID"]
    svg, _small = icon_paths(root)
    for master in sorted(svg.parent.glob(f"{app_id}*.svg")):
        if bad := prefixed_elements(master.read_text(encoding="utf-8")):
            print(f"{master.relative_to(root)}: {len(bad)} prefixed SVG elements ({bad[0]}...) are invisible in GTK",
                  file=sys.stderr)
            return 1
    for size in SIZES:
        png = render_png(svg, size)
        out = root / "data" / "icons" / "hicolor" / f"{size}x{size}" / "apps" / f"{app_id}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        print(out.relative_to(root))
        if size == 256 and (root / "site").is_dir():
            (root / "site" / "icon.png").write_bytes(png)
            print("site/icon.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
