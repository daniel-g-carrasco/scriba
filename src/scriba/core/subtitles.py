"""The lines of a transcript cut into subtitles, by the usual conventions.

The speech engine cuts its lines where it likes: on the meeting of
13/11/2026 (5 minutes of the computer's track, turbo) a line lasted from
0.24 to 19.4 s and held up to 256 characters, and one subtitle per line
gave a wall of text or a flash of two words. The subtitles are now cut
again from the words, with the values the BBC's and Netflix's subtitle
guidelines use:
- MAX_ROW, 42 characters, and MAX_ROWS, 2: Netflix's limit for languages
  in the Latin alphabet; the BBC's is about the same (37 to 42);
- MIN_SECONDS, 5/6 s (20 frames at 24 fps), and MAX_SECONDS, 7 s:
  Netflix's shortest and longest subtitle;
- READING_SPEED, 17 characters a second: Netflix's for adult programmes
  in Italian (20 in English); the end of a subtitle is put off to give
  that time, or the shortest duration, when the next subtitle allows it,
  never past its start;
- a subtitle ends at the end of a sentence, else on a comma (or ; :),
  else before a conjunction, never inside a word; the two rows are cut by
  the same rule, with rows of a similar length;
- TINY_WORDS, 2, and TINY_SECONDS, 1 s: a subtitle that short is joined
  to the one before or after of the same voice, when the pause between
  them is at most JOIN_PAUSE;
- JOIN_PAUSE, 1 s: the lines of one voice are read as one stream when the
  pause between them is no longer; past it a subtitle would show words a
  second or more before they are said. (On that meeting the lines of one
  voice follow each other with no gap at all: 63 of 64.)
Where whisper ended a line counts as a comma: it cuts at a pause or at the
end of a phrase, and on that meeting it wrote no punctuation at all.

The times come from the words: Segment.words, the (start, end) of each
word, from the engine's tokens (app/engines/whisper.py). A line without
them (another engine, a meeting reopened from older files) has its time
shared among its words by their length, and its own start and end stay
true. A subtitle never joins two voices, nor a doubtful line with one
that is not, and every word of the lines is in the subtitles once, in its
order (a test holds it).

Plain Python on anything shaped as a line (start, end, text, speaker,
doubts, and optionally words).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

MAX_ROW = 42
MAX_ROWS = 2
MIN_SECONDS = 5 / 6
MAX_SECONDS = 7.0
READING_SPEED = 17.0
TINY_WORDS = 2
TINY_SECONDS = 1.0
JOIN_PAUSE = 1.0

SENTENCE_ENDS = (".", "?", "!", "…")
CLAUSE_ENDS = (",", ";", ":")
# a subtitle or a row may end before these (Italian and English, the
# languages Scriba's users speak); lower case, as written in the middle of
# a sentence
CONJUNCTIONS = frozenset({
    "e", "ed", "o", "od", "oppure", "ma", "però", "quindi", "perché", "poiché", "che", "se", "come",
    "quando", "mentre", "dove", "anche", "cioè", "ovvero", "oltre", "invece", "dunque", "allora",
    "and", "or", "but", "so", "because", "that", "if", "when", "while", "which", "where", "then",
})
# how much a better place to cut is worth against rows of unequal length,
# in characters: a comma (2) is taken over a split 20 characters better
# balanced
ROW_BREAK_WEIGHT = 10


@dataclass(frozen=True)
class Subtitle:
    start: float
    end: float
    text: str                   # its rows, parted by "\n"
    speaker: str | None = None
    doubts: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Word:
    text: str
    start: float
    end: float
    line_end: bool              # the last word of the engine's line


Lead = Callable[[Subtitle, bool], str]


def _no_lead(_subtitle: Subtitle, _new_voice: bool) -> str:
    return ""


def timed_words(line) -> list[tuple[str, float, float]]:
    """The words of a line with their times: the engine's (line.words, one
    (start, end) per word of text.split()) kept in order and inside the
    line; without them, or when they do not match the text, the line's
    time shared among its words by their length."""
    words = line.text.split()
    if not words:
        return []
    start, end = line.start, max(line.end, line.start)
    times = tuple(getattr(line, "words", ()) or ())
    if len(times) == len(words):
        out, last = [], start
        for word, (a, b) in zip(words, times, strict=True):
            a = min(max(a, last), end)
            b = min(max(b, a), end)
            out.append((word, a, b))
            last = a
        return out
    total = sum(len(w) for w in words) + len(words) - 1
    out, at = [], 0
    for word in words:
        out.append((word, start + (end - start) * at / total, start + (end - start) * (at + len(word)) / total))
        at += len(word) + 1
    return out


def _quality(words: Sequence[_Word], k: int) -> int:
    """How good a place to cut it is after words[k]: 3 at the end of a
    sentence or of the stream, 2 after a comma or where the engine ended a
    line, 1 before a conjunction, else 0."""
    if k == len(words) - 1:
        return 3
    text = words[k].text.rstrip("\"'»”)")
    if text.endswith(SENTENCE_ENDS):
        return 3
    if text.endswith(CLAUSE_ENDS) or words[k].line_end:
        return 2
    if words[k + 1].text.lower().strip("\"'«“(") in CONJUNCTIONS:
        return 1
    return 0


def _rows(words: Sequence[_Word], lead: int) -> tuple[list[str], bool]:
    """The words as one or two rows (`lead` characters go before the
    first), and whether they fit."""
    texts = [w.text for w in words]
    whole = " ".join(texts)
    if lead + len(whole) <= MAX_ROW or len(texts) == 1:
        return [whole], lead + len(whole) <= MAX_ROW
    best, best_cost, fits = None, None, False
    for k in range(1, len(texts)):
        first, second = " ".join(texts[:k]), " ".join(texts[k:])
        ok = lead + len(first) <= MAX_ROW and len(second) <= MAX_ROW
        if ok:
            cost = abs(lead + len(first) - len(second)) - ROW_BREAK_WEIGHT * _quality(words[:k + 1], k - 1)
        else:
            cost = 10_000 + max(lead + len(first), len(second))
        if best_cost is None or cost < best_cost:
            best, best_cost, fits = [first, second], cost, ok
    return best, fits


def _tiny(words: Sequence[_Word]) -> bool:
    return 0 < len(words) <= TINY_WORDS and words[-1].end - words[0].start < TINY_SECONDS


def _cut(words: list[_Word], lead_of: Callable[[bool], int]) -> list[tuple[int, int, list[str]]]:
    """The stream of one voice as (first, last, rows) of each subtitle."""
    out = []
    i, first = 0, True
    capacity = MAX_ROW * MAX_ROWS
    while i < len(words):
        lead = lead_of(first)
        best, best_score, best_rows = i, None, None
        for j in range(i, len(words)):
            piece = words[i:j + 1]
            rows, fits = _rows(piece, lead)
            if j > i and (not fits or words[j].end - words[i].start > MAX_SECONDS):
                break
            score = _quality(words, j) + len(" ".join(w.text for w in piece)) / capacity
            if j < len(words) - 1 and (_tiny(piece) or _tiny(words[j + 1:])):
                score -= 10             # a scrap on its own: only when nothing else fits
            if best_score is None or score > best_score:
                best, best_score, best_rows = j, score, rows
        out.append((i, best, best_rows))
        i, first = best + 1, False
    return out


def _streams(lines: Iterable) -> list[tuple[object, list[_Word]]]:
    """The lines with words as streams: one voice, one kind of doubt, no
    pause longer than JOIN_PAUSE; each with its first line and its words."""
    out: list[tuple[object, list[_Word]]] = []
    last = None
    for line in lines:
        timed = timed_words(line)
        if not timed:
            continue
        words = [_Word(text, a, b, n == len(timed) - 1) for n, (text, a, b) in enumerate(timed)]
        if (last is not None and line.speaker == last.speaker and tuple(line.doubts) == tuple(last.doubts)
                and words[0].start - out[-1][1][-1].end <= JOIN_PAUSE):
            out[-1][1].extend(words)
        else:
            out.append((line, words))
        last = line
    return out


def subtitles(lines: Iterable, lead: Lead = _no_lead) -> list[Subtitle]:
    """The subtitles of the lines (see the module's docstring), in order.
    `lead(subtitle, new_voice)` is what the writer puts before a
    subtitle's first row (data/srt.py: the voice's tag where the voice
    changes, the doubt's mark): its length counts in the first row."""
    out: list[Subtitle] = []
    previous: object = object()
    for line, words in _streams(lines):
        speaker, doubts = line.speaker, tuple(line.doubts)
        probe = Subtitle(0.0, 0.0, "", speaker, doubts)
        new_voice = speaker != previous

        def lead_of(first: bool, probe=probe, new_voice=new_voice) -> int:
            return len(lead(probe, new_voice and first))
        for i, j, rows in _cut(words, lead_of):
            out.append(Subtitle(words[i].start, words[j].end, "\n".join(rows), speaker, doubts))
        previous = speaker
    return _timed(out)


def _timed(cues: list[Subtitle]) -> list[Subtitle]:
    """Each subtitle's end put off, when it is too short to read, up to
    the time it needs (the shortest duration, the reading speed), never
    past the next subtitle's start nor MAX_SECONDS."""
    out = []
    for n, cue in enumerate(cues):
        need = max(MIN_SECONDS, len(cue.text.replace("\n", " ")) / READING_SPEED)
        end = cue.end
        if end - cue.start < need:
            limit = cues[n + 1].start if n + 1 < len(cues) else cue.start + need
            end = max(end, min(cue.start + need, limit, cue.start + MAX_SECONDS))
        out.append(cue if end == cue.end else Subtitle(cue.start, end, cue.text, cue.speaker, cue.doubts))
    return out
