"""What the writers take and the readers give: a line of a transcript.

The segments are worked out in core (core/segments.py), and data may not
import core. So the writers do not ask for core's Segment by name: they ask
for anything with its shape (Line, a Protocol), which core's Segment has
without knowing it, and the readers give their own plain value (Cue) with
the same fields. app/ is where the two meet; a test holds that the shapes
stay the same.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# The anonymous label of a voice, the same text as core/segments.py's
# SPEAKER_LABEL (a test holds it): the subtitles shorten it to "P3: ".
SPEAKER_LABEL = "Speaker {number}"
_LABEL = re.compile(re.escape(SPEAKER_LABEL).replace(re.escape("{number}"), r"(\d+)"))

# A line that was marked doubtful in a file that does not say why (a
# subtitle "(?) "): the reason core gives is lost there.
UNSPECIFIED = "unspecified"


@runtime_checkable
class Line(Protocol):
    start: float            # seconds from the start of the recording
    end: float
    text: str
    speaker: str | None     # the label or the name of the voice; None when voices were not told apart
    doubts: tuple[str, ...]  # why the line may not be what was said; () when it is good


@dataclass(frozen=True)
class Cue:
    """A line as read back from a file."""
    start: float
    end: float
    text: str
    speaker: str | None = None
    doubts: tuple[str, ...] = ()


def speaker_number(label: str | None) -> int | None:
    """3 for "Speaker 3"; None for a name the user gave, or for no label."""
    if label is None:
        return None
    found = _LABEL.fullmatch(label)
    return int(found.group(1)) if found else None


def speaker_label(number: int) -> str:
    return SPEAKER_LABEL.format(number=number)
