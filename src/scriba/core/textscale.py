"""How much larger the text is drawn on Windows so that the app has the size
the screen's scale asks for, although GTK scales only by whole numbers.

GTK on Windows draws a window at a whole scale: the screen's dpi over 96,
rounded down, at least 1 (250% -> 2, 150% -> 1; measured on 2026-11-13 on a
3840x2160 screen at 240 dpi: GTK scale 2, GetDpiForWindow 240). The part
it leaves out is given back to the text through GTK's text dpi
(gtk-xft-dpi): (dpi / 96) / scale, so 250% draws the text 1.25 times, 150%
1.5 times, 100%, 200% and 300% as they are. Icons, margins and switches
keep the whole scale and stay that much smaller. GDK does the same when the
process is system DPI aware (gtk-xft-dpi = 1024 * dpi / scale, measured:
120 dpi at 240 and scale 2); per monitor it leaves the text at 96, which is
why the app does it here, for the screen each window is on.
"""

from __future__ import annotations

BASE_DPI = 96           # Windows' 100%
XFT_UNIT = 1024         # gtk-xft-dpi counts 1/1024 of a dot per inch


def gtk_scale(dpi: int) -> int:
    """GTK's whole scale on Windows for a screen at `dpi`: rounded down,
    at least 1."""
    return max(1, int(dpi) // BASE_DPI)


def text_factor(dpi: int, scale: int) -> float:
    """How much larger the text is drawn on a screen at `dpi` where GTK
    draws at the whole `scale`: 1.0 when either is unknown."""
    if dpi <= 0 or scale < 1:
        return 1.0
    return (dpi / BASE_DPI) / scale


def text_dpi(base: int, dpi: int, scale: int) -> int:
    """GTK's text dpi (gtk-xft-dpi, in 1/1024 dots per inch) for a screen at
    `dpi` and the whole `scale`, from the `base` the app or the user had
    (0 or less: GTK's own default, 96 dots per inch)."""
    if base <= 0:
        base = BASE_DPI * XFT_UNIT
    return round(base * text_factor(dpi, scale))
