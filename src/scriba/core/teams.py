"""Teams tile labels, border detection and observation counts."""
import re
from dataclasses import dataclass

INSET_AT_96_DPI = 5
MAX_OUTSET = 2
BLUE_MIN = 170
BLUE_EXCESS = 50
SIDE_SHARE = 0.60
EDGE_CONTRAST = 0.20

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


@dataclass(frozen=True)
class Band:
    share: float
    offset: int
    thickness: int


Bands = tuple[Band | None, Band | None, Band | None, Band | None]
NO_BANDS: Bands = (None, None, None, None)


def strongest_bands(previous: Bands, current: Bands) -> Bands:
    return tuple(new if old is None or (new is not None and new.share > old.share) else old
                 for old, new in zip(previous, current, strict=True))


class ReadingSummary:
    """Counts by minute on the recording timeline, with no names or images."""

    def __init__(self):
        self.minutes: list[dict] = []
        self._bands = NO_BANDS
        self._no_border_since = None
        self.without_border = 0

    def observe(self, at, state, tiles, active, source, covered, border_sides=None, border_bands=None):
        minute = int(at // 60)
        if not self.minutes or self.minutes[-1]["minute"] != minute:
            self.minutes.append({"minute": minute, "states": {}, "max_tiles": 0, "with_border": 0,
                                 "sources": {}, "covered": 0, "uncovered": 0})
            self._bands = NO_BANDS
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
        if border_bands is not None:
            self._bands = strongest_bands(self._bands, border_bands)
            row["border_side_offset"] = [band.offset if band else None for band in self._bands]
            row["border_side_thickness"] = [band.thickness if band else None for band in self._bands]

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
    bands: Bands = NO_BANDS


def _strongest_band(shares: list[float], first_offset: int) -> Band | None:
    best = None
    start, peak = 0, 0.0
    for offset, share in enumerate(shares, start=first_offset):
        if share >= SIDE_SHARE:
            if not peak:
                start = offset
            peak = max(peak, share)
            continue
        if peak - share >= EDGE_CONTRAST and (best is None or peak > best.share):
            best = Band(peak, start, offset - start)
        peak = 0.0
    return best


def border(pixels: bytes, width: int, height: int, stride: int,
           rectangle: tuple[int, int, int, int], *, dpi: int = 96) -> Border:
    """Top-down BGRA pixels, rectangle in physical pixels (left, top, right, bottom).

    A clipped tile cannot supply three reliable sides, so is never active.
    Side order is top, bottom, left, right. Offsets are signed physical pixels
    from the tile's outermost row or column, positive toward its interior.
    A band must end with a sharp drop in blue before it can count as an edge.
    """
    if width <= 0 or height <= 0 or stride < width * 4 or len(pixels) < stride * height:
        raise ValueError("Invalid BGRA frame dimensions")
    left, top, right, bottom = rectangle
    dpi = max(96, dpi)
    inset = round(INSET_AT_96_DPI * dpi / 96)
    outset = min(MAX_OUTSET, round(dpi / 96))
    if (left < 0 or top < 0 or right > width or bottom > height
            or min(right - left, bottom - top) <= 2 * inset):
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
        return blue_share(top * stride + x * 4, bottom - top, stride)

    def side(sample, origin, direction, available_outside):
        first_offset = -min(outset, available_outside)
        # One extra sample lets a band end at the last searched position.
        shares = [sample(origin + direction * offset) for offset in range(first_offset, inset + 2)]
        return max(shares[:-1]), _strongest_band(shares, first_offset)

    measures = (
        side(horizontal, top, 1, top),
        side(horizontal, bottom - 1, -1, height - bottom),
        side(vertical, left, 1, left),
        side(vertical, right - 1, -1, width - right),
    )
    shares, bands = zip(*measures, strict=True)
    return Border(shares, sum(band is not None for band in bands) >= 3, bands)
