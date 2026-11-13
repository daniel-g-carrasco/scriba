"""Subtitles in SubRip (.srt): written as trascrivi.py wrote them, read back
from what Scriba or another program wrote.

A cue is its number, `start --> end` and the text; cues are parted by an
empty line. With voices, the text of a cue where the voice changes starts
with a short tag: `P3: ` for the
anonymous "Speaker 3" (the tag trascrivi.py used, short enough not to push
the words off a video's line), the name itself for a voice the user named
(`Anna: `). A doubtful line (core/segments.py, flag_doubtful) starts with
`(?) ` after the tag, so that it is seen while the video plays.

The reader is lenient where files differ in the wild (a BOM, Windows line
ends, extra empty lines, a text on several lines, a full stop before the
milliseconds) and strict where a guess could shift the words: a file with
text and no cue in it is refused.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from pathlib import Path

from scriba.i18n import _

from . import files
from .lines import UNSPECIFIED, Cue, Line, speaker_label, speaker_number
from .timing import parse_srt_time, srt_time

DOUBT_MARK = "(?) "
_TIMING = re.compile(r"\s*(\S+)\s*-->\s*(\S+)(?:\s.*)?")
_TAG = re.compile(r"P(\d+): ")


class SrtError(ValueError):
    """The file is not subtitles; str() is a sentence the window can show."""


def _timing(line: str) -> tuple[float, float] | None:
    found = _TIMING.fullmatch(line)
    if found is None:
        return None
    try:
        return parse_srt_time(found.group(1)), parse_srt_time(found.group(2))
    except ValueError:
        return None


def _text_lines(text: str) -> list[str]:
    """The text's own lines, without the empty ones: an empty line ends a
    cue for every player, so one inside the text would cut it."""
    return [line.rstrip() for line in text.strip().splitlines() if line.strip()]


def tag(speaker: str | None) -> str:
    """What goes before the text for this voice: "P3: ", "Anna: " or ""."""
    if not speaker:
        return ""
    number = speaker_number(speaker)
    return f"P{number}: " if number is not None else f"{speaker}: "


# What cuts the lines into subtitles (core/subtitles.py: data does not know
# core, app gives it), told what goes before a subtitle's first row
Lead = Callable[[Line, bool], str]
Cut = Callable[[Iterable[Line], Lead], Iterable[Line]]


def render(lines: Iterable[Line], speakers: bool = True, mark_doubtful: bool = True, cut: Cut | None = None) -> str:
    """The subtitles as text, one per line or, with `cut`, as it cuts them.
    Lines with no words are left out (and not numbered), as trascrivi.py
    left them out. `speakers` puts the voice's tag before the text where
    the voice changes, as subtitles name a speaker (13/11/2026: once the
    lines are cut to subtitle size, a tag on each would repeat "P2: " three
    or four times in a row and take a tenth of the row); `mark_doubtful`
    puts the "(?) " on every subtitle of a doubtful line."""
    def lead(line: Line, new_voice: bool) -> str:
        return ((tag(line.speaker) if speakers and new_voice else "")
                + (DOUBT_MARK if mark_doubtful and line.doubts else ""))
    cues = []
    previous: object = object()
    for line in (cut(lines, lead) if cut is not None else lines):
        words = _text_lines(line.text)
        if not words:
            continue
        body = "\n".join([lead(line, line.speaker != previous) + words[0], *words[1:]])
        previous = line.speaker
        cues.append(f"{len(cues) + 1}\n{srt_time(line.start)} --> {srt_time(line.end)}\n{body}\n")
    return "\n".join(cues)


def write(folder: str | os.PathLike, stem: str, lines: Iterable[Line], speakers: bool = True,
          mark_doubtful: bool = True, cut: Cut | None = None) -> Path:
    """The subtitles in a new file `stem.srt` in `folder` (or `stem_R01.srt`
    if that is taken: never over a file that is there); its path."""
    return files.write_new(folder, stem, ".srt", render(lines, speakers, mark_doubtful, cut))


def _untag(text: str, names: Iterable[str]) -> tuple[str | None, str]:
    found = _TAG.match(text)
    if found:
        return speaker_label(int(found.group(1))), text[found.end():]
    for name in sorted(names, key=len, reverse=True):      # "Anna Maria" before "Anna"
        if name and text.startswith(name + ": "):
            return name, text[len(name) + 2:]
    return None, text


def parse(text: str, speakers: bool = True, names: Iterable[str] = ()) -> list[Cue]:
    """The cues in the subtitles' text. With `speakers`, a leading `P3: `
    becomes the voice "Speaker 3"; a leading `Name: ` is taken as a voice
    only for the `names` given, since "Note: ..." may well be what was said;
    a cue without a tag is the voice of the cue before (the tag is written
    where the voice changes).
    A leading "(?) " marks the cue doubtful. The cue numbers are not
    trusted (editors renumber, or do not): the order is the file's.
    SrtError when there is text and no cue."""
    text = text.lstrip("﻿")
    names = tuple(names)
    rows = text.splitlines()
    cues: list[list] = []           # [start, end, text lines]
    blank_before = True
    for i, row in enumerate(rows):
        timing = _timing(row)
        is_number = row.strip().isdigit()
        next_timing = _timing(rows[i + 1]) if i + 1 < len(rows) else None
        if is_number and next_timing is not None:
            blank_before = False
            continue                # the number of the cue below (also when the empty line before it is missing)
        if timing is not None and (blank_before or (i > 0 and rows[i - 1].strip().isdigit())):
            cues.append([timing[0], timing[1], []])
        elif row.strip():
            if not cues:
                raise SrtError(_("This is not a subtitle file: there is text before the first time line."))
            cues[-1][2].append(row.rstrip())
        blank_before = not row.strip()
    if not cues and text.strip():
        raise SrtError(_("This is not a subtitle file: no time line was found."))
    out = []
    speaker = None
    for start, end, body in cues:
        words = "\n".join(body)
        if speakers:
            tagged, words = _untag(words, names)
            speaker = tagged or speaker
        doubts: tuple[str, ...] = ()
        if words.startswith(DOUBT_MARK):
            doubts, words = (UNSPECIFIED,), words[len(DOUBT_MARK):]
        out.append(Cue(start, end, words, speaker, doubts))
    return out


def read(path: str | os.PathLike, speakers: bool = True, names: Iterable[str] = ()) -> list[Cue]:
    """The cues of a subtitle file (UTF-8, with or without a BOM). SrtError,
    with a sentence to show, when the file is not UTF-8 or not subtitles;
    OSError when it cannot be read."""
    data = Path(path).read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise SrtError(_("The subtitle file is not in UTF-8, the only encoding read.")) from e
    return parse(text, speakers, names)
