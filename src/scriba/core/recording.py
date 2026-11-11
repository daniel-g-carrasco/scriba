"""A recording's names and its room on the disk.

The files of one recording share a name with the date and the time it
began ("2026-11-11 10-54-08 registrazione"), so that they sort in the
order they were made and say when; each track adds what it is (the
microphone, an output and the device's name), and the finished recording
is "<name>.mka" with every track inside, next to "<name>_insieme.m4a",
the mix to listen to. The words come in the user's language, from the
caller.

The disk: a track is FLAC of 24 bits at 48 kHz, one channel, which never
weighs more than the samples themselves (BYTES_PER_SECOND); measured on
11/11/2026 a room's noise took 89 kB/s and a quiet microphone 16 kB/s, a
silent output almost nothing. The room left is worked out at that worst
rate: an hour of three tracks is 1.5 GB at most.
"""

from __future__ import annotations

import datetime
import re

RATE = 48000                # Hz: every track, whatever the device's own (see app/recorder.py)
BITS = 24
BYTES_PER_SECOND = RATE * BITS // 8         # one channel: FLAC's ceiling, 144 kB/s
MARGIN = 200 * 1024 * 1024                  # bytes the disk keeps whatever happens: the system needs room too
START_MINUTES = 10          # a recording does not start with less room than this
WARN_MINUTES = 30           # said while recording when the room left falls under this
STOP_MINUTES = 1            # the recording is stopped, its files closed properly, under this
NAME_LIMIT = 40             # characters of a device's name in a file's name


def stem(when: datetime.datetime, word: str) -> str:
    """"2026-11-11 10-54-08 <word>": the name the files of a recording
    share (colons are not allowed in a file's name on Windows)."""
    return f"{when:%Y-%m-%d %H-%M-%S} {word}"


def safe(name: str, limit: int = NAME_LIMIT) -> str:
    """A device's name as a piece of a file's name: what Windows forbids
    taken out, spaces kept, at most `limit` characters."""
    kept = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", name)
    kept = re.sub(r"\s+", " ", kept).strip(" .")
    return kept[:limit].rstrip(" .") or "audio"


def seconds_left(free: int, tracks: int) -> float:
    """How long the disk can go on taking `tracks` tracks, with MARGIN kept."""
    if tracks <= 0:
        return float("inf")
    return max(free - MARGIN, 0) / (BYTES_PER_SECOND * tracks)


def may_start(free: int, tracks: int) -> bool:
    return seconds_left(free, tracks) >= START_MINUTES * 60


def room(free: int, tracks: int) -> str:
    """"ok", "low" (said), or "stop" (the recording ends cleanly)."""
    left = seconds_left(free, tracks)
    if left < STOP_MINUTES * 60:
        return "stop"
    if left < WARN_MINUTES * 60:
        return "low"
    return "ok"

