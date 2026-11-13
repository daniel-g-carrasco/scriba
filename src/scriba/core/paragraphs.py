"""The lines of a transcript gathered into paragraphs to read.

The speech engine cuts its lines where it likes: on the meeting of
13/11/2026 (5 minutes of the computer's track, five voices) the lines that
reach the files lasted 2.2 s and held 5 words at the median, with Veloce
and with Precisa alike, and the transcript read as a list of scraps, one
row and one time each. The files and the Result page now show paragraphs:
the lines of one voice that follow each other, until the voice changes,
the voice stops for a while, or the paragraph has grown long. The
subtitles are cut on their own rules (core/subtitles.py).

The thresholds, and why:
- PAUSE_SECONDS, 3 s: within one voice the lines follow each other
  without a gap (median 0 s on that meeting); a silence of seconds is a
  new thought, or a voice the engines could not tell apart taking over.
- SOFT_SECONDS, 30 s: from there a paragraph ends at the end of a
  sentence (., ?, !, ...): about 75 spoken words, four or five lines of a
  page, a piece one reads at a glance.
- HARD_SECONDS, 60 s, and HARD_WORDS, 150: a paragraph never longer, even
  without a full stop (whisper does not always write one): the time at
  its start must still find the passage in the audio within a minute.
A doubtful line (segments.flag_doubtful) is a paragraph of its own, so that
its mark and its reasons stay on it and on nothing else.

Plain Python on anything shaped as a line (start, end, text, speaker,
doubts): core's Segment, data's Cue.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

PAUSE_SECONDS = 3.0
SOFT_SECONDS = 30.0
HARD_SECONDS = 60.0
HARD_WORDS = 150
SENTENCE_ENDS = (".", "?", "!", "…")

L = TypeVar("L")


def _ends_sentence(text: str) -> bool:
    return text.rstrip().rstrip("\"'»”)").endswith(SENTENCE_ENDS)


def paragraphs(lines: Iterable[L]) -> list[list[L]]:
    """The lines with words, in their order, gathered into paragraphs (see
    the module's docstring); each paragraph is the list of its lines."""
    out: list[list[L]] = []
    current: list[L] = []
    words = 0
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        count = len(text.split())
        if current:
            last = current[-1]
            if (line.speaker != last.speaker or line.doubts or last.doubts
                    or line.start - last.end > PAUSE_SECONDS
                    or line.end - current[0].start > HARD_SECONDS or words + count > HARD_WORDS
                    or (last.end - current[0].start >= SOFT_SECONDS and _ends_sentence(last.text))):
                out.append(current)
                current, words = [], 0
        current.append(line)
        words += count
    if current:
        out.append(current)
    return out
