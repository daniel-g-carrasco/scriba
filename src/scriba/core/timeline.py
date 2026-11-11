"""Speech cut out of a recording and put end to end, and the times of what
is heard in it brought back to the recording's own clock.

whisper is given only the stretches where somebody speaks (the speech
filter runs first, as a step of its own),
packed one after the other with a short silence between them (GAP, in
app/jobs.py: without it whisper lost the first sentence of the test clip
and wrote "cavelli" seven times for "cavedi"; with 0.2 s it wrote what it
writes on the whole clip). The engine then says "at 12.3 s" of the packed audio;
a Piece says which stretch of the recording that is. A time that falls
between two pieces belongs to the next piece when it is a start and to
the previous one when it is an end, so a line never begins in the silence
before it or ends in the one after it.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Piece:
    start: float        # in the recording, seconds
    end: float
    at: float           # where it starts in the packed audio

    @property
    def length(self) -> float:
        return self.end - self.start


def pack(spans: Iterable[tuple[float, float]], gap: float = 0.0) -> list[Piece]:
    """The stretches (start, end) of the recording as pieces of the packed
    audio, in order, `gap` seconds of silence between one and the next;
    stretches that touch or overlap become one, empty ones are left out."""
    merged: list[list[float]] = []
    for start, end in sorted(spans):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces, at = [], 0.0
    for start, end in merged:
        pieces.append(Piece(start, end, at))
        at += end - start + gap
    return pieces


def packed_length(pieces: Sequence[Piece]) -> float:
    return pieces[-1].at + pieces[-1].length if pieces else 0.0


def original(pieces: Sequence[Piece], t: float, is_end: bool = False) -> float:
    """The recording's time of `t` seconds of the packed audio. A time in
    the silence between two pieces is the start of the next piece for a
    start, the end of the previous one for an end; past the end, the end
    of the last piece; with no pieces, `t` itself."""
    if not pieces:
        return t
    i = bisect.bisect_right([p.at for p in pieces], t) - 1
    if i < 0:
        return pieces[0].start
    piece, offset = pieces[i], t - pieces[i].at
    if is_end and offset <= 0 and i > 0:            # an end on a seam belongs to the piece before
        return pieces[i - 1].end
    if offset > piece.length:                       # in the silence after the piece
        return piece.end if is_end or i + 1 == len(pieces) else pieces[i + 1].start
    return piece.start + offset
