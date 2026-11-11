"""The names the user gives to the voices, kept a level above the labels.

The engine's "Speaker 2" stays in the saved original segments; the names
("Speaker 2" -> "Anna") live in a map of their own, saved with the meeting.
The known microphone name is used in the first files; later changes give
new files. A wrong attribution costs a rename, never a transcription,
and the automatic files are never touched (the plan, risks).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from scriba.i18n import _

from .segments import SPEAKER_LABEL, Segment

_LABEL = re.compile(re.escape(SPEAKER_LABEL).replace(re.escape("{number}"), r"(\d+)"))


def clean(names: Mapping[str, str]) -> dict[str, str]:
    """The map with the names trimmed, and without the empty ones (a field
    the user cleared means "no name", not a voice called "")."""
    return {label: " ".join(name.split()) for label, name in names.items() if name and name.strip()}


def shown_label(label: str) -> str:
    """An anonymous label in the user's language ("Parlante 2" in Italian);
    any other label as it is."""
    found = _LABEL.fullmatch(label)
    return _("Speaker {number}").format(number=found.group(1)) if found else label


def display_names(labels: Iterable[str], names: Mapping[str, str]) -> dict[str, str]:
    """For each label, what is shown: the name the user gave, or the label
    in the user's language."""
    names = clean(names)
    return {label: names.get(label) or shown_label(label) for label in labels}


LISTEN_MIN = 2.0        # a stretch shorter than this ("Sì.") says little about who speaks
LISTEN_MAX = 12.0       # and a longer one is cut: enough to know the voice


@dataclass(frozen=True)
class Voice:
    label: str
    first: float                    # when it is first heard, seconds
    lines: int                      # how many lines it has
    listen: tuple[float, float]     # a stretch to play to know it (see voices)


def voices(segments: Iterable[Segment]) -> list[Voice]:
    """The voices in order of appearance, each with a stretch of the
    recording to play when the user wants to know who it is: its first line
    of at least LISTEN_MIN seconds (its very first if none is that long),
    at most LISTEN_MAX seconds of it."""
    lines: dict[str, list[Segment]] = {}
    for seg in segments:
        if seg.speaker and seg.text.strip():
            lines.setdefault(seg.speaker, []).append(seg)
    out = []
    for label, own in lines.items():
        chosen = next((s for s in own if s.end - s.start >= LISTEN_MIN), own[0])
        out.append(Voice(label, own[0].start, len(own), (chosen.start, min(chosen.end, chosen.start + LISTEN_MAX))))
    return out


def rename(segments: Iterable[Segment], names: Mapping[str, str]) -> list[Segment]:
    """New segments with each voice under its name; a voice with no name
    keeps its label. The segments given are not changed (they are frozen,
    and new ones are made). Two labels may get the same name (the user
    found that one voice was cut in two): they become one voice."""
    names = clean(names)
    return [seg if seg.speaker not in names else replace(seg, speaker=names[seg.speaker]) for seg in segments]


def remembered(known: str, names: Mapping[str, str]) -> str:
    """A compact history of distinct names in first-use order."""
    values = [" ".join(n.split()) for n in (*known.splitlines(), *names.values())]
    return "\n".join(dict.fromkeys(n for n in values if n))


def suggestions(text: str, known: str) -> list[str]:
    """Previous names matching the beginning typed, except a complete match."""
    prefix = " ".join(text.split()).casefold()
    return [n for n in remembered(known, {}).splitlines()
            if n.casefold().startswith(prefix) and n.casefold() != prefix]
