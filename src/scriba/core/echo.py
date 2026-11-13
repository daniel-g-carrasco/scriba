"""Whether the microphone hears the computer's sound, stretch by stretch.

With loudspeakers the microphone picks up the call as well as the user:
the others' voices come back on the user's track a little later (232 ms,
steady, in a real meeting of 1 h 48 min on 13/11/2026), are heard twice in
the copy to listen to and are transcribed as the user's. With earphones
the microphone hears the user alone, and its track is best left as it is.

The test is the one of the measures of 13/11/2026: in windows of WINDOW
seconds, the correlation between the microphone and the computer's sound,
normalised (1: the same signal; 0: nothing in common), at the delay where
it is highest within REACH either way. It only says something where the
computer plays (its level above PLAYING_ABOVE) and the microphone is not
switched off (above MIC_ABOVE); elsewhere the window is UNKNOWN.

The threshold, from three stretches of five minutes of that meeting
(10 s windows): where the microphone heard the loudspeakers, a median of
0.26, 0.42 and 0.38 (most windows 0.18 to 0.52, the delay 232 to 239 ms);
the microphone of one stretch against the computer of another, where
there can be no echo, never above 0.11 (median 0.03, the delay anywhere).
ECHO_ABOVE = 0.15 sits between the two with room on both sides.

A window can fall under it while the echo is there: the user speaking
loudly over a quiet call (0.01 to 0.08 seen, the delay lost). The echo is
then small beside the voice, and the way back is slow on purpose: a
recording goes on hearing the computer until CLEAN_AFTER windows in a row
say it does not (earphones put on halfway), while one window is enough to
say it does (earphones taken off). Taking the echo out where there is
none costs little (the canceller then leaves the voice nearly as it is);
leaving it in costs every remote voice twice.

Plain numbers in and out: the measure is numpy's (app/engines/echo.py),
the cancelling GStreamer's.
"""

from __future__ import annotations

from dataclasses import dataclass

WINDOW = 10.0               # seconds per verdict
REACH = 0.5                 # seconds of delay looked at, either way
PLAYING_ABOVE = -55.0       # dBFS (RMS over the window) of the computer's sound: below, nothing to hear back
MIC_ABOVE = -70.0           # dBFS of the microphone: below, it is switched off (digital silence)
ECHO_ABOVE = 0.15           # normalised correlation (see the docstring)
CLEAN_AFTER = 3             # windows in a row without echo before a recording is taken as clean again
# the same for the warning while recording: a minute of the computer playing
# without echo heard. On the real meeting the echo went unseen for three
# windows in a row (30 s of the user speaking over a quiet call) and the
# warning, at CLEAN_AFTER, went away for 10 s and came back; twice that
# leaves room for such a stretch, and earphones put on still clear it in a
# minute of the call (windows where the computer is silent do not count)
LIVE_CLEAN_AFTER = 6
CROSSFADE = 0.05            # seconds over which the cleaned track takes over from the raw one, and back

ECHO, CLEAN, UNKNOWN = "echo", "clean", "unknown"


@dataclass(frozen=True)
class Window:
    start: float                # seconds into the recording
    end: float
    correlation: float | None   # None where it says nothing (see verdict)
    delay: float | None         # seconds the microphone is behind the computer's sound
    mic: float                  # dBFS, RMS over the window
    output: float

    @property
    def verdict(self) -> str:
        return verdict(self.correlation, self.mic, self.output)


def verdict(correlation: float | None, mic: float, output: float) -> str:
    """ECHO, CLEAN or UNKNOWN for one window."""
    if correlation is None or output < PLAYING_ABOVE or mic < MIC_ABOVE:
        return UNKNOWN
    return ECHO if correlation >= ECHO_ABOVE else CLEAN


class Follow:
    """The state as the windows come: echo from the first window with echo,
    clean again after CLEAN_AFTER windows in a row without; UNKNOWN changes
    nothing. The recorder feeds it live (with LIVE_CLEAN_AFTER: its warning
    should not come and go), stretches() a whole track."""

    def __init__(self, clean_after: int = CLEAN_AFTER) -> None:
        self.echo = False
        self.clean_after = clean_after
        self._clean = 0

    def feed(self, verdict: str) -> bool:
        if verdict == ECHO:
            self.echo, self._clean = True, 0
        elif verdict == CLEAN:
            self._clean += 1
            if self._clean >= self.clean_after:
                self.echo = False
        return self.echo


@dataclass(frozen=True)
class Stretch:
    start: float
    end: float
    echo: bool


def stretches(windows: list[Window], duration: float) -> list[Stretch]:
    """The track cut where the state changes (Follow), from 0 to
    `duration`. The windows before the first one that says anything take
    the state of that one; the tail after the last window, the last state."""
    follow = Follow()
    states = [follow.feed(w.verdict) for w in windows]
    first = next((n for n, w in enumerate(windows) if w.verdict != UNKNOWN), None)
    if first is not None:
        states[:first] = [states[first]] * first
    out: list[Stretch] = []
    for n, (window, echo) in enumerate(zip(windows, states, strict=True)):
        start = 0.0 if n == 0 else window.start
        end = duration if n == len(windows) - 1 else windows[n + 1].start
        if out and out[-1].echo == echo:
            out[-1] = Stretch(out[-1].start, end, echo)
        elif end > start:
            out.append(Stretch(start, end, echo))
    if not out and duration > 0:
        out.append(Stretch(0.0, duration, False))
    return out


def echo_seconds(parts: list[Stretch]) -> float:
    return sum(s.end - s.start for s in parts if s.echo)


def share(parts: list[Stretch]) -> float:
    """The part of the track (0 to 1) where the microphone hears the computer."""
    total = sum(s.end - s.start for s in parts)
    return echo_seconds(parts) / total if total > 0 else 0.0


def blend_points(parts: list[Stretch]) -> tuple[list[float], list[float]]:
    """Where the cleaned track counts and where the raw one does, as points
    (seconds, weight of the cleaned: 0 or 1) to interpolate between: each
    change is a ramp of CROSSFADE seconds centred on it, so that nothing
    clicks where one track takes over from the other."""
    if not parts:
        return [0.0], [0.0]
    half = CROSSFADE / 2
    xs, ys = [parts[0].start], [1.0 if parts[0].echo else 0.0]
    for before, after in zip(parts, parts[1:], strict=False):     # each stretch with the next
        if before.echo == after.echo:
            continue
        xs += [max(after.start - half, xs[-1]), after.start + half]
        ys += [1.0 if before.echo else 0.0, 1.0 if after.echo else 0.0]
    xs.append(max(parts[-1].end, xs[-1]))
    ys.append(1.0 if parts[-1].echo else 0.0)
    return xs, ys
