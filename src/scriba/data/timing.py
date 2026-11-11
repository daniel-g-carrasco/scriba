"""Times as the files write them: `mm:ss` (or `h:mm:ss`) in the transcript,
`hh:mm:ss,mmm` in the subtitles, and back.

This module is in data/ and not in core/, where the plan had it: the two
writers that need it (srt.py, markdown.py) are in data/, and data may not
import core. The formats are the files' own, so they live with the files;
the window shows times through the same functions.

The transcript's seconds are cut, as trascrivi.py wrote them (12.9 s is
00:12, as a player's clock shows it). Subtitle times round total
milliseconds before splitting hours, minutes and seconds, so floating
point noise cannot turn 15.02 into 15,019. Negative times become zero.
"""

from __future__ import annotations

import re

_SRT_TIME = re.compile(r"\s*(\d+):([0-5]?\d):([0-5]?\d)(?:[,.](\d{1,3}))?\s*")


def clock(t: float) -> str:
    """`mm:ss` under an hour, `h:mm:ss` from an hour on: the transcript's
    time stamps, short where they can be."""
    t = max(t, 0.0)
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def srt_time(t: float) -> str:
    """`hh:mm:ss,mmm`, as SubRip wants it (a comma before the milliseconds)."""
    total = round(max(t, 0.0) * 1000)
    seconds, millis = divmod(total, 1000)
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def parse_srt_time(text: str) -> float:
    """The seconds of `hh:mm:ss,mmm`; also with a full stop before the
    milliseconds (as WebVTT and some editors write it), without them, with
    hours past 99 and with spaces around. ValueError for anything else."""
    found = _SRT_TIME.fullmatch(text)
    if found is None:
        raise ValueError(f"not a subtitle time: {text!r}")
    h, m, s, ms = found.groups()
    millis = int((ms or "0").ljust(3, "0"))
    return int(h) * 3600 + int(m) * 60 + int(s) + millis / 1000
