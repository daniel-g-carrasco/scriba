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
before it or ends in the one after it. A line that runs across a long
silence of the recording keeps to the side where most of it is (spans).
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


# seconds of silence in the recording between two pieces past which they are
# taken as apart: a line of whisper's that runs across such a silence keeps
# to the side where most of it is (spans). On a real meeting (13/11/2026)
# whisper made one line of nine scraps of the computer's sound left in the
# microphone, 11 to 192 s, and the first 6.5 s of the user speaking at 217
# s: the line took 00:11. The pauses inside a speech are shorter (there the
# speech filter keeps one piece across them)
FAR = 5.0


def spans(pieces: Sequence[Piece], times: Iterable[tuple[float, float]],
          far: float = FAR) -> list[tuple[float, float]]:
    """The recording's (start, end) of each line heard from start to end of
    the packed audio (original). A line that runs across pieces more than
    `far` seconds apart in the recording is cut to the run of near pieces
    where most of its time is, so that a scrap heard minutes before (whisper
    joins it to the sentence after it) does not give the line its start."""
    runs: list[list[int]] = []          # first and last piece of each run of near pieces
    for i, piece in enumerate(pieces):
        if runs and piece.start - pieces[runs[-1][1]].end <= far:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    out = []
    for start, end in times:
        first, last = original(pieces, start), original(pieces, end, is_end=True)
        if len(runs) > 1:
            def inside(run, start=start, end=end) -> float:
                low, high = pieces[run[0]].at, pieces[run[1]].at + pieces[run[1]].length
                return min(end, high) - max(start, low)
            best = max(runs, key=inside)
            if inside(best) > 0:
                first, last = max(first, pieces[best[0]].start), min(last, pieces[best[1]].end)
        out.append((first, last))
    return out


def words(pieces: Sequence[Piece], times: Iterable[tuple[float, float]], start: float,
          end: float) -> tuple[tuple[float, float], ...]:
    """The recording's (start, end) of the words of a line heard at `times`
    of the packed audio, as original gives them, kept in order and inside
    the line's own (start, end) in the recording (which spans may have
    cut), to the millisecond."""
    out, last = [], start
    for a, b in times:
        a = min(max(original(pieces, a), last), end)
        b = min(max(original(pieces, b, is_end=True), a), end)
        out.append((round(a, 3), round(b, 3)))
        last = a
    return tuple(out)


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
