"""The names the user gives to the voices, kept a level above the labels.

The engine's "Speaker 2" stays in the saved original segments; the names
("Speaker 2" -> "Anna") live in a map of their own, saved with the meeting.
The known microphone name is used in the first files; later changes give
new files. A wrong attribution costs a rename, never a transcription,
and the automatic files are never touched (the plan, risks).
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence
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


LISTEN_MAX = 15.0       # the stretch to know a voice by is cut there: a few sentences
RUN_GAP = 1.0           # lines of one voice this close are one stretch of speech


@dataclass(frozen=True)
class Voice:
    label: str
    first: float                    # when it is first heard, seconds
    lines: int                      # how many lines it has
    listen: tuple[float, float]     # a stretch to play to know it (see voices)


def _runs(lines: Sequence[Segment], label: str, sure: bool) -> list[tuple[float, float]]:
    """The stretches where `label` speaks and no other voice does: its
    lines in a row (in the order of all the lines) with less than RUN_GAP
    between them, cut where another voice's line reaches into them. With
    `sure` its own doubtful lines cut them as another voice's would."""
    out: list[tuple[float, float]] = []
    start = end = None
    others_until = 0.0                  # the end of the latest line of another voice so far
    for line in lines:
        if line.speaker != label or (sure and line.doubts):
            if start is not None:
                out.append((start, min(end, line.start)))
                start = None
            others_until = max(others_until, line.end)
            continue
        if start is not None and line.start - end > RUN_GAP:
            out.append((start, end))
            start = None
        if start is None:
            start, end = max(line.start, others_until), line.end
        else:
            end = max(end, line.end)
    if start is not None:
        out.append((start, end))
    return [(a, b) for a, b in out if b > a]


def voices(segments: Iterable[Segment]) -> list[Voice]:
    """The voices in order of appearance, each with a stretch of the
    recording to play when the user wants to know who it is: the longest
    stretch where that voice alone speaks (_runs), its first LISTEN_MAX
    seconds. Its first line was taken before, and on the meeting of
    13/11/2026 that gave 2 to 5 seconds for four voices out of five, too
    little to tell who it is. A doubtful line (no speech, a faint voice)
    is left out of the stretches unless the voice has nothing else; the
    stretch never takes in another voice."""
    lines = sorted((s for s in segments if s.speaker and s.text.strip()), key=lambda s: s.start)
    own: dict[str, list[Segment]] = {}
    for seg in lines:
        own.setdefault(seg.speaker, []).append(seg)
    out = []
    for label, mine in own.items():
        runs = _runs(lines, label, sure=True) or _runs(lines, label, sure=False) or [(mine[0].start, mine[0].end)]
        start, end = max(runs, key=lambda run: run[1] - run[0])        # the first of the longest
        out.append(Voice(label, mine[0].start, len(mine), (start, min(end, start + LISTEN_MAX))))
    return out


PREVIEW_SECONDS = 20.0  # the balance's preview: two of the echo check's 10-second windows


def both_heard(segments: Iterable[Segment], me: str, length: float = PREVIEW_SECONDS,
               duration: float = 0.0) -> tuple[float, float]:
    """The stretch of `length` seconds where the user (the voice `me`) and
    the others are both heard the most: what the balance between them is
    judged on. Each line's start is a candidate; the score is the lesser of
    the two sides' seconds of speech in the stretch, then all the speech in
    it. Without `me` (not known) the two loudest voices of the stretch
    stand for the two sides. Held within `duration` when it is given (>0);
    from the start when there are no lines."""
    lines = sorted((s for s in segments if s.text.strip() and s.end > s.start), key=lambda s: s.start)
    if not lines:
        return 0.0, min(length, duration) if duration > 0 else length
    longest = max(s.end - s.start for s in lines)
    starts = [s.start for s in lines]
    best: tuple[tuple[float, float], float] | None = None
    for line in lines:
        start = max(0.0, min(line.start, duration - length)) if duration > 0 else line.start
        end = start + length
        heard: dict[str, float] = {}
        # only the lines that can overlap the stretch (bisect: a long meeting has thousands)
        for other in lines[bisect.bisect_left(starts, start - longest):bisect.bisect_left(starts, end)]:
            overlap = min(other.end, end) - max(other.start, start)
            if overlap > 0:
                heard[other.speaker or ""] = heard.get(other.speaker or "", 0.0) + overlap
        if me:
            mine = heard.get(me, 0.0)
            others = sum(seconds for label, seconds in heard.items() if label != me)
            score = (min(mine, others), mine + others)
        else:
            ranked = sorted(heard.values(), reverse=True)
            score = (ranked[1] if len(ranked) > 1 else 0.0, sum(ranked))
        if best is None or score > best[0]:
            best = (score, start)
    start = best[1]
    return start, min(start + length, duration) if duration > 0 else start + length


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
