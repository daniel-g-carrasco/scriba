"""Two tracks of the computer's sound that carry the same sound.

Every output is recorded on a track of its own (core/devices.py), and an
output can pass into another: on Linux an effects program such as
EasyEffects is the default output and sends what it processed on to the
loudspeakers, a loopback or a virtual cable does the same on any system.
The two tracks then hold the same voices, and taken as two sources they are
heard twice in the copy to listen to and written twice in the transcript,
each split into its own speakers (2 asked for, 5 found, on 22/11/2026).

The test is the echo's measure (core/echo.py, app/engines/echo.py): in
windows of echo.WINDOW seconds, the normalised correlation of the two at
the delay where it is highest, where both play. On the recording of
22/11/2026 (EasyEffects' sink and the loudspeakers, 34 minutes) it was 0.63
to 0.65, the delay 20.4 ms in every window: the processing (compressor,
equaliser, limiter) keeps it under 1. Sounds with nothing in common never
went above 0.11 in the echo's measures. SAME_ABOVE sits between, nearer
the low end, and a steady delay is asked for as well: two programs playing
the same video by chance would not keep one.

Of the two, the track that comes first is kept: the sound before the
processing, the closest to what the programs played. The other stays in the
recording; it is left out of the mix and of the transcript, and that is said.

Plain numbers in and out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from scriba.core import echo

PLAYING_ABOVE = echo.PLAYING_ABOVE  # dBFS on both tracks: a window where one is silent says nothing
SAME_ABOVE = 0.35                   # normalised correlation (see the docstring)
SHARE = 0.7                         # of the windows where both play, alike and at the same delay
DELAY_WITHIN = 0.003                # seconds from the median delay
LEAST = 3                           # windows where both play, at least, to say "the same"


@dataclass(frozen=True)
class Verdict:
    same: bool
    delay: float = 0.0      # seconds, positive: the first track later than the second
    windows: int = 0        # where both played


def verdict(windows: Iterable[echo.Window]) -> Verdict:
    """Whether the two tracks of the measure (the "microphone" and "computer"
    of echo.Window, here the first and second track) carry the same sound."""
    heard = [w for w in windows if w.correlation is not None and w.delay is not None
             and w.mic >= PLAYING_ABOVE and w.output >= PLAYING_ABOVE]
    if len(heard) < LEAST:
        return Verdict(False, 0.0, len(heard))
    alike = sorted(w.delay for w in heard if w.correlation >= SAME_ABOVE)
    if not alike:
        return Verdict(False, 0.0, len(heard))
    middle = alike[len(alike) // 2]
    steady = [d for d in alike if abs(d - middle) <= DELAY_WITHIN]
    return Verdict(len(steady) >= SHARE * len(heard), middle, len(heard))


def later(verdict_of: Verdict) -> int:
    """Of a pair found the same, the one to leave out: 0 the first, 1 the second."""
    return 0 if verdict_of.delay > 0 else 1


def left_out(count: int, compare) -> dict[int, int]:
    """The tracks (0..count-1) to leave out, each with the one kept in its
    place: every pair still in is compared (`compare(a, b)` gives a
    Verdict), and of a pair found the same the later goes."""
    out: dict[int, int] = {}
    for a in range(count):
        for b in range(a + 1, count):
            if a in out or b in out:
                continue
            found = compare(a, b)
            if found.same:
                gone, kept = (a, b) if later(found) == 0 else (b, a)
                out[gone] = kept
    return out


def kept_in_place(out: dict[int, int], index: int) -> int:
    """The track that stands for `index` (itself when it is kept)."""
    seen: set[int] = set()
    while index in out and index not in seen:
        seen.add(index)
        index = out[index]
    return index


def names(out: dict[int, int], titles: Sequence[str]) -> list[tuple[str, str]]:
    """(left out, kept) by their titles, for the notes."""
    return [(titles[gone], titles[kept_in_place(out, gone)]) for gone in sorted(out)]
