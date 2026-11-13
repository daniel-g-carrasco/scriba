"""The levels of a recording as it goes: what the meters show, and the
warnings that something is wrong while there is still time to put it right.

The signal is recorded raw, never touched (no gain, no automatic level:
core/loudness.py puts the volume right afterwards, on the file). What can
go wrong at the source is told instead, from the levels the recorder
measures ten times a second on every track (RMS and peak, dBFS):

- CLIPPING: the microphone reaches 0 dBFS, the sound is cut at the source
  and nothing afterwards brings it back (turn the microphone's level down
  in the system's settings);
- QUIET: someone speaks (the level goes up and down by more than
  SPEAKING_SWING over the noise of the room) and the loud part stays between
  SPEECH_FROM and QUIET_BELOW for QUIET_FOR seconds: a distant microphone
  may hear speech at -44 to -53 dB, where a speech engine can invent words.
  Under SPEECH_FROM it is silence or the room, never a voice: on 13/11/2026
  the right microphone of a user who was simply not speaking during a call
  read -73 dB and was warned about;
- SILENT: no sound at all from the computer's output for SILENT_AFTER
  seconds (the call plays on another device, or the others are muted). A
  call has pauses: the warning is a question, not an error.

Plain numbers in and out: the window turns a warning into a banner.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

FLOOR = -90.0               # dBFS shown as the bottom of a meter: digital silence is -inf, or -700 from GStreamer
CLIP_AT = -0.1              # dBFS of peak: the source is cut there
QUIET_BELOW = -40.0         # dBFS of the voice's loud part
SPEECH_FROM = -60.0         # dBFS: under this no voice (silence measured at -73, faint speech at -44 to -53)
QUIET_FOR = 15.0            # seconds of faint speech before it is said: longer than WINDOW: one cough cannot hold it
SPEAKING_SWING = 12.0       # dB between the room's noise and the loud part: someone is speaking
WINDOW = 10.0               # seconds of levels looked at for QUIET
SILENT_BELOW = -70.0        # dBFS of peak: nothing comes out (the loopback of a silent output is exact zeros)
SILENT_AFTER = 15.0         # seconds without sound from the outputs before it is said
KEEP_WARNING = 5.0          # seconds a warning stays after its cause has gone

CLIPPING, QUIET, SILENT = "clipping", "quiet", "silent"


def clamp(db: float) -> float:
    """A level as a meter shows it: never below FLOOR, never above 0."""
    if db is None or math.isnan(db):
        return FLOOR
    return max(FLOOR, min(0.0, db))


def fraction(db: float) -> float:
    """0..1 for a meter (a Gtk.LevelBar): FLOOR to 0 dBFS, linear in dB,
    as a recording meter is read."""
    return (clamp(db) - FLOOR) / -FLOOR


def percentile(values, share: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return FLOOR
    k = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[k]


@dataclass(frozen=True)
class Alert:
    kind: str           # CLIPPING, QUIET or SILENT; the recorder adds core/echo.py's ECHO
    track: str          # the track's key; for SILENT, "" when it is every output
    level: float        # dBFS: the peak, the voice's level, or the outputs' loudest peak
    seconds: float = 0.0    # SILENT: for how long


class Watch:
    """The levels of every track as they come (feed), and the warnings that
    hold now (warnings). The input's track is the user's microphone; the
    outputs' tracks are the others. Not thread-safe: one thread feeds and
    asks (the recorder's)."""

    def __init__(self) -> None:
        self._levels: dict[str, deque] = {}     # track -> (time, rms, peak) of the last WINDOW seconds
        self._inputs: set[str] = set()
        self._outputs: set[str] = set()
        self._clipped: dict[str, tuple[float, float]] = {}  # track -> (when, peak)
        self._quiet: dict[str, tuple[float, float]] = {}    # track -> (when, level)
        self._faint_since: dict[str, float] = {}     # track -> when its faint speech began
        self._sound_at: float | None = None     # the last time an output had sound
        self._began: float | None = None
        self.loudest: dict[str, float] = {}     # track -> the loudest peak heard, for keeping it or not

    def track(self, key: str, is_input: bool) -> None:
        (self._inputs if is_input else self._outputs).add(key)
        self._levels.setdefault(key, deque())
        self.loudest.setdefault(key, -math.inf)

    def feed(self, key: str, when: float, rms: float, peak: float) -> None:
        if self._began is None:
            self._began = when
        levels = self._levels.setdefault(key, deque())
        levels.append((when, rms, peak))
        while levels and levels[0][0] < when - WINDOW:
            levels.popleft()
        self.loudest[key] = max(self.loudest.get(key, -math.inf), peak)
        if key in self._inputs:
            if peak >= CLIP_AT:
                self._clipped[key] = (when, peak)
            loud = percentile([r for _t, r, _p in levels], 0.9)
            noise = percentile([r for _t, r, _p in levels], 0.1)
            span = levels[-1][0] - levels[0][0]
            if span >= WINDOW / 2 and loud - noise >= SPEAKING_SWING and SPEECH_FROM <= loud < QUIET_BELOW:
                if when - self._faint_since.setdefault(key, when) >= QUIET_FOR:
                    self._quiet[key] = (when, loud)
            else:
                self._faint_since.pop(key, None)
                if loud >= QUIET_BELOW:
                    self._quiet.pop(key, None)
        elif key in self._outputs and peak > SILENT_BELOW:
            self._sound_at = when

    def voice(self, key: str) -> float | None:
        """The loud part of what the track heard lately (the 90th
        percentile of its RMS), dBFS; None before anything was heard."""
        levels = self._levels.get(key)
        return percentile([r for _t, r, _p in levels], 0.9) if levels else None

    def warnings(self, now: float) -> list[Alert]:
        out = []
        for key, (when, peak) in sorted(self._clipped.items()):
            if now - when <= KEEP_WARNING:
                out.append(Alert(CLIPPING, key, peak))
        for key, (when, level) in sorted(self._quiet.items()):
            if now - when <= KEEP_WARNING:
                out.append(Alert(QUIET, key, level))
        if self._outputs and self._began is not None:
            since = self._sound_at if self._sound_at is not None else self._began
            if now - since >= SILENT_AFTER:
                loudest = max((self.loudest.get(k, -math.inf) for k in self._outputs), default=-math.inf)
                out.append(Alert(SILENT, next(iter(self._outputs)) if len(self._outputs) == 1 else "",
                                   loudest, now - since))
        return out

    def resume(self, when: float) -> None:
        """The outputs switched back on by the user: the silence while they
        were off was wanted, not a sign the call plays elsewhere."""
        self._sound_at = when

    def forget(self, key: str) -> None:
        """A track that ended (its device is gone): no more warnings about it."""
        self._levels.pop(key, None)
        self._clipped.pop(key, None)
        self._quiet.pop(key, None)
        self._faint_since.pop(key, None)
        self._inputs.discard(key)
        self._outputs.discard(key)


def kept(loudest_peak: float) -> bool:
    """An output's track is kept when it ever had sound: one that stayed at
    digital silence (an output nobody played on) holds nothing."""
    return loudest_peak > SILENT_BELOW
