"""Teams tile labels, border detection and observation counts."""
import re
from dataclasses import dataclass

BAND = 5
OUTSET = 1
BLUE_MIN = 170
BLUE_EXCESS = 50
SIDE_SHARE = 0.60
EDGE_CONTRAST = 0.20
# A one-pixel line on three sides was measured on a 4K Teams window.
# The interior comparison rejects blue video filling several edges.

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


class ReadingSummary:
    """Counts by minute on the recording timeline, with no names or images."""

    def __init__(self):
        self.minutes: list[dict] = []
        self._no_border_since = None
        self.without_border = 0

    def observe(self, at, state, tiles, active, source, covered, border_sides=None):
        minute = int(at // 60)
        if not self.minutes or self.minutes[-1]["minute"] != minute:
            self.minutes.append({"minute": minute, "states": {}, "max_tiles": 0, "with_border": 0,
                                 "sources": {}, "covered": 0, "uncovered": 0})
        row = self.minutes[-1]
        states = row["states"]
        states[state] = states.get(state, 0) + 1
        row["max_tiles"] = max(row["max_tiles"], tiles)
        row["with_border"] += int(active)
        sources = row["sources"]
        if source in ("print-window", "screen"):
            sources[source] = sources.get(source, 0) + 1
        if covered is not None:
            row["covered" if covered else "uncovered"] += 1
        if border_sides is not None:
            previous = row.get("border_side_max", (0.0, 0.0, 0.0, 0.0))
            row["border_side_max"] = [round(max(old, new), 4) for old, new in zip(previous, border_sides, strict=True)]

        if state != "ready" or not tiles or active:
            self.interrupt()
        else:
            if self._no_border_since is None:
                self._no_border_since = at
            self.without_border = int((at - self._no_border_since) // 60)

    def interrupt(self):
        self._no_border_since = None
        self.without_border = 0


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
    Each side reports its strongest line. A distinct edge also has substantially
    less blue on the line just inside the band.
    """
    if width <= 0 or height <= 0 or stride < width * 4 or len(pixels) < stride * height:
        raise ValueError("Invalid BGRA frame dimensions")
    left, top, right, bottom = rectangle
    left, top, right, bottom = left - OUTSET, top - OUTSET, right + OUTSET, bottom + OUTSET
    if (left < 0 or top < 0 or right > width or bottom > height
            or right - left <= 2 * BAND or bottom - top <= 2 * BAND):
        return Border((0.0, 0.0, 0.0, 0.0), False)

    def blue_share(start, length, step):
        lit = 0
        for at in range(start, start + length * step, step):
            blue = pixels[at]
            lit += blue > BLUE_MIN and blue - max(pixels[at + 1], pixels[at + 2]) > BLUE_EXCESS
        return lit / length

    def horizontal(y):
        return blue_share(y * stride + left * 4, right - left, 4)

    def vertical(x):
        return blue_share((top + BAND) * stride + x * 4, bottom - top - 2 * BAND, stride)

    shares = (
        max(map(horizontal, range(top, top + BAND))),
        max(map(horizontal, range(bottom - BAND, bottom))),
        max(map(vertical, range(left, left + BAND))),
        max(map(vertical, range(right - BAND, right))),
    )
    inside = (horizontal(top + BAND), horizontal(bottom - BAND - 1),
              vertical(left + BAND), vertical(right - BAND - 1))
    edges = sum(share >= SIDE_SHARE and share - inner >= EDGE_CONTRAST
                for share, inner in zip(shares, inside, strict=True))
    return Border(shares, edges >= 3)
