"""Teams tile labels and border detection, with no window-system dependency."""
import re
from dataclasses import dataclass

BAND = 5
OUTSET = 1
BLUE_MIN = 170
BLUE_EXCESS = 50
SIDE_SHARE = 0.25
# Dark Teams at 100 percent scale, 2026-11-16: speaking 0.38-0.39
# on the third best side; a neighbour affected only one side (0.19).
# Other themes and display scales still need a live measurement.

# Localized accessibility fragments live here alone. English is covered by
# synthetic tests, not yet verified in a live English Teams meeting.
WORDS = {
    "own": ("Video personale", "Your video", "Self video"),
    "sharing": ("Contenuto condiviso", "Content shared", "Shared content"),
    "metadata": ("video acceso", "Ha il controllo", "notifiche", "Menu di scelta",
                 "Microfono", "Audio riattivato", "Ha il menu", "video on", "Has producer",
                 "notifications", "Context menu", "Microphone", "Audio unmuted", "Has context menu"),
    "external": ("Esterno sconosciuto", "Non verificato", "Esterno", "Unknown external", "Unverified", "External"),
}


@dataclass(frozen=True)
class Person:
    name: str
    own: bool = False


def tile_name(text: str) -> Person | None:
    text = text.strip()
    if not text or text.casefold().startswith(tuple(s.casefold() for s in WORDS["sharing"])):
        return None
    own = False
    for prefix in WORDS["own"]:
        if text.casefold().startswith(prefix.casefold() + ","):
            own, text = True, text[len(prefix) + 1:].strip()
            break
    pattern = r",\s*(?:" + "|".join(re.escape(s) for s in WORDS["metadata"]) + ")"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        return None
    name = text[:match.start()].strip()
    for suffix in WORDS["external"]:
        if name.casefold().endswith(" " + suffix.casefold()):
            name = name[:-len(suffix)].strip()
            break
    return Person(name, own) if name else None


@dataclass(frozen=True)
class Border:
    sides: tuple[float, float, float, float]
    active: bool


def border(pixels: bytes, width: int, height: int, stride: int,
           rectangle: tuple[int, int, int, int]) -> Border:
    """Top-down BGRA pixels, rectangle in physical pixels (left, top, right, bottom).

    A clipped tile cannot supply three reliable sides, so is never active.
    Only the thin bands are visited, not the whole video area.
    """
    if width <= 0 or height <= 0 or stride < width * 4 or len(pixels) < stride * height:
        raise ValueError("Invalid BGRA frame dimensions")
    left, top, right, bottom = rectangle
    left, top, right, bottom = left - OUTSET, top - OUTSET, right + OUTSET, bottom + OUTSET
    if left < 0 or top < 0 or right > width or bottom > height or right - left < 2 * BAND or bottom - top < 2 * BAND:
        return Border((0.0, 0.0, 0.0, 0.0), False)
    areas = ((left, top, right, top + BAND), (left, bottom - BAND, right, bottom),
             (left, top + BAND, left + BAND, bottom - BAND),
             (right - BAND, top + BAND, right, bottom - BAND))
    shares = []
    for x0, y0, x1, y1 in areas:
        lit = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                i = y * stride + x * 4
                blue, green, red = pixels[i:i + 3]
                lit += blue > BLUE_MIN and blue - max(red, green) > BLUE_EXCESS
        shares.append(lit / ((x1 - x0) * (y1 - y0)))
    return Border(tuple(shares), sorted(shares, reverse=True)[2] >= SIDE_SHARE)
