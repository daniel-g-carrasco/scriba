"""Named speech intervals, independent of the source of the observations."""
from __future__ import annotations

import json
import math

Span = tuple[str, float, float]
MERGE_GAP = 1.0  # two missed half-second polls must not split a turn
MIN_SPAN = 1.0   # discard a single half-second border flash


class Builder:
    def __init__(self, gap: float = MERGE_GAP, minimum: float = MIN_SPAN):
        self.gap, self.minimum = gap, minimum
        self._open: dict[str, tuple[float, float | None]] = {}
        self._closed: list[Span] = []

    def _close(self, name, start, end):
        if end - start >= self.minimum:
            self._closed.append((name, start, end))

    def read(self, at: float, active) -> None:
        active = set(active)
        for name, (start, off) in list(self._open.items()):
            if off is not None and at - off >= self.gap:
                self._close(name, start, off)
                del self._open[name]
            elif name in active:
                self._open[name] = (start, None)
            elif off is None:
                self._open[name] = (start, at)
        for name in active:
            self._open.setdefault(name, (at, None))

    def unavailable(self, at: float) -> None:
        """A gap in observation is not evidence that someone kept speaking."""
        for name, (start, off) in self._open.items():
            self._close(name, start, at if off is None else off)
        self._open.clear()

    def finish(self, at: float) -> tuple[Span, ...]:
        self.unavailable(at)
        return tuple(sorted(self._closed, key=lambda s: (s[1], s[0])))


def write_spans(spans) -> str:
    # JSON escapes names containing punctuation without a source-specific delimiter.
    return json.dumps(list(spans), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def read_spans(text: str | None) -> tuple[Span, ...]:
    try:
        raw = json.loads(text or "[]")
    except (ValueError, TypeError):
        return ()
    if not isinstance(raw, list):
        return ()
    out = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        name, start, end = item
        if (isinstance(name, str) and name.strip() and type(start) in (int, float)
                and type(end) in (int, float) and math.isfinite(start) and math.isfinite(end)
                and 0 <= start < end):
            out.append((name, float(start), float(end)))
    return tuple(out)
