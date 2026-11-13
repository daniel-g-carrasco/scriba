"""The segments of a transcript and what is worked out on them: which voice
says each one, the voices numbered, the lines that may not be what was
said, two tracks put together.

A segment is what the speech engine gives (start, end, text, and the two
measures of its own doubt that whisper reports); a turn is what the voice
engine gives (start, end, a raw label such as "SPEAKER_03"). Both are
frozen: every function returns new segments, so the engine's output is
never changed under the caller (the plan: the automatic file is not
touched, the version with the names is a new one).

The logic of assign_speakers and number_speakers is trascrivi.py's
(chi_parla and the numbering in order of appearance), which gave the
transcripts read so far; the same input gives the same voices.
"""

from __future__ import annotations

import bisect
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

# The anonymous label of a voice, numbered from 1 in order of appearance.
# Not translated here: it is a key (the names map uses it, the subtitles
# shorten it to "P3"); the window shows it in the user's language
# (core/speakers.py, display_names). data/lines.py has the same text.
SPEAKER_LABEL = "Speaker {number}"

# Why a line may not be what was said (Segment.doubts). The files say it in
# words (data/markdown.py, reasons): a test holds that every code has them.
NO_SPEECH = "no_speech"
LOW_CONFIDENCE = "low_confidence"
REPEATED = "repeated"
QUIET = "quiet"
REASONS = (NO_SPEECH, LOW_CONFIDENCE, REPEATED, QUIET)

# The thresholds of flag_doubtful (see there for the reasons)
NO_SPEECH_ABOVE = 0.6
LOGPROB_BELOW = -1.0
REPEATS = 3
REPEAT_WINDOW = 60.0
REPEAT_MIN_WORDS = 3
QUIET_BELOW = -40.0
QUIET_LOGPROB_BELOW = -0.2


@dataclass(frozen=True)
class Segment:
    start: float                        # seconds from the start of the recording
    end: float
    text: str
    speaker: str | None = None          # raw label, then "Speaker k", then a name; None: voices not told apart
    no_speech_prob: float | None = None  # whisper's: how likely there was no speech at all, 0..1
    avg_logprob: float | None = None    # whisper's: mean log probability of the words, 0 = certain
    level: float | None = None          # dBFS of the line's loud part (the app measures it); None: not measured
    doubts: tuple[str, ...] = ()        # REASONS that hold for this line, in their order
    # (start, end) of each word of text.split(), as the engine timed it;
    # (): not given (core/subtitles.py then shares the line's time)
    words: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class Turn:
    start: float
    end: float
    label: str                          # the voice engine's own label


def spoken(segments: Iterable[Segment]) -> list[Segment]:
    """The segments with words, the text trimmed: what trascrivi.py kept of
    the engine's output before anything else. The voices are numbered in
    the order they are heard in these, so an empty segment must not come
    first and take number 1."""
    out = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append(seg if text == seg.text else replace(seg, text=text))
    return out


def assign_speakers(segments: Iterable[Segment], turns: Iterable[Turn]) -> list[Segment]:
    """Each segment with the label of the turn that overlaps it the most;
    a segment no turn overlaps (a pause the voice engine cut out) takes the
    nearest turn, measured from the segment's middle to the turn's nearer
    end. Ties go to the earlier turn. With no turns at all the segments
    come back as they are: voices were not told apart."""
    turns = sorted(turns, key=lambda t: t.start)       # stable: equal starts keep the engine's order
    segments = list(segments)
    if not turns:
        return segments
    starts = [t.start for t in turns]
    # the longest turn bounds how far back a turn that still overlaps may start
    longest = max(max(t.end - t.start for t in turns), 0.0)
    out = []
    for seg in segments:
        best, overlap = None, 0.0
        first = bisect.bisect_left(starts, seg.start - longest)
        last = bisect.bisect_right(starts, seg.end)
        for turn in turns[first:last]:
            o = min(turn.end, seg.end) - max(turn.start, seg.start)
            if o > overlap:
                best, overlap = turn, o
        if best is None:
            middle = (seg.start + seg.end) / 2
            best = min(turns, key=lambda t: min(abs(t.start - middle), abs(t.end - middle)))
        out.append(replace(seg, speaker=best.label))
    return out


def speakers_of(segments: Iterable[Segment]) -> list[str]:
    """The voices, each once, in the order they first speak."""
    return list(dict.fromkeys(seg.speaker for seg in segments if seg.speaker is not None))


def number_speakers(segments: Iterable[Segment]) -> list[Segment]:
    """The raw labels ("SPEAKER_03") replaced by "Speaker 1", "Speaker 2"...
    in the order the voices first speak: the engine's numbers mean nothing
    to a reader, the first voice heard being number 1 does. A segment with
    no voice keeps none."""
    segments = list(segments)
    numbers = {label: SPEAKER_LABEL.format(number=i) for i, label in enumerate(speakers_of(segments), 1)}
    return [seg if seg.speaker is None else replace(seg, speaker=numbers[seg.speaker]) for seg in segments]


def _words(text: str) -> tuple[str, ...]:
    """The text as words for comparing two lines: case, punctuation and
    spacing do not count ("Grazie a tutti." is "grazie a tutti")."""
    kept = "".join(" " if unicodedata.category(c).startswith(("P", "S")) else c for c in text.casefold())
    return tuple(kept.split())


def flag_doubtful(segments: Iterable[Segment], *, no_speech_above: float = NO_SPEECH_ABOVE,
                  logprob_below: float = LOGPROB_BELOW, repeats: int = REPEATS,
                  repeat_window: float = REPEAT_WINDOW, repeat_min_words: int = REPEAT_MIN_WORDS,
                  quiet_below: float = QUIET_BELOW, quiet_logprob_below: float = QUIET_LOGPROB_BELOW) -> list[Segment]:
    """The segments with their doubts (Segment.doubts), the words kept as
    they are: a doubtful line is shown as such, never dropped (it may be
    the one thing that was said there).

    - NO_SPEECH: whisper's probability of no speech above `no_speech_above`
      (0.6, whisper's own no_speech_threshold). Whisper drops a segment
      only when that and a low confidence hold together; on the test call
      of 11/11/2026 the invented lines were in the stretch at -44 to -53 dB
      where either one was high [I: to be measured on that file].
    - LOW_CONFIDENCE: the mean log probability of the words below
      `logprob_below` (-1.0, whisper's logprob_threshold, under which it
      tries again at a higher temperature: the words it kept were the
      best of bad guesses).
    - REPEATED: the same words (case and punctuation aside) `repeats` times
      or more (3) within `repeat_window` seconds (60), the loop whisper
      falls into on noise ("Grazie a tutti." over and over). Lines shorter
      than `repeat_min_words` words (3) do not count: "Sì." or "Va bene."
      said again and again is a conversation, not an invention.
    - QUIET: the line is faint (its level below `quiet_below`, -40 dBFS:
      12 dB under the median line of both test recordings) and whisper was
      not sure of it (mean log probability below `quiet_logprob_below`,
      -0.2). Measured on 11/11/2026: in the first minute and a half of the
      call of that day (voices far from the microphone, -40 to -48 dBFS)
      whisper wrote 16 lines of which none was right; 13 are QUIET,
      where LOW_CONFIDENCE alone marks 5. Neither measure alone parts them
      from good lines: on the 10-minute test clip a remote speaker at -51
      dBFS is transcribed right, mostly with -0.09 to -0.19; of its 116
      lines one was QUIET in one run of two (a good line of that speaker:
      a false alarm, harmless since the line is kept). Two recordings are
      what these numbers rest on [I: to be checked on more].

    A measure the engine did not give (None) counts as no doubt. Doubts
    already there are kept."""
    segments = list(segments)
    found: list[set[str]] = [set(seg.doubts) for seg in segments]
    for i, seg in enumerate(segments):
        if seg.no_speech_prob is not None and seg.no_speech_prob > no_speech_above:
            found[i].add(NO_SPEECH)
        if seg.avg_logprob is not None and seg.avg_logprob < logprob_below:
            found[i].add(LOW_CONFIDENCE)
        if (seg.level is not None and seg.level < quiet_below and seg.avg_logprob is not None
                and seg.avg_logprob < quiet_logprob_below):
            found[i].add(QUIET)
    same: dict[tuple[str, ...], list[int]] = {}
    for i, seg in enumerate(segments):
        words = _words(seg.text)
        if len(words) >= max(repeat_min_words, 1):
            same.setdefault(words, []).append(i)
    for indices in same.values():
        if len(indices) < repeats:
            continue
        indices.sort(key=lambda i: segments[i].start)
        times = [segments[i].start for i in indices]
        last = 0
        for first in range(len(indices)):
            last = max(last, first)
            while last + 1 < len(times) and times[last + 1] - times[first] <= repeat_window:
                last += 1
            if last - first + 1 >= repeats:
                for k in indices[first:last + 1]:
                    found[k].add(REPEATED)
    order = {code: n for n, code in enumerate(REASONS)}
    return [seg if set(seg.doubts) == doubts else
            replace(seg, doubts=tuple(sorted(doubts, key=lambda code: (order.get(code, len(order)), code))))
            for seg, doubts in zip(segments, found, strict=True)]


def shift(segments: Iterable[Segment], seconds: float) -> list[Segment]:
    """The segments moved by `seconds` (later when positive). A time that
    would fall before the start is put at 0: the line is kept, since its
    words matter more than a fraction of a second."""
    return [replace(seg, start=max(seg.start + seconds, 0.0), end=max(seg.end + seconds, 0.0),
                    words=tuple((max(a + seconds, 0.0), max(b + seconds, 0.0)) for a, b in seg.words))
            for seg in segments]


def prefix_speakers(segments: Iterable[Segment], prefix: str) -> list[Segment]:
    """Every voice's label with `prefix` before it: two tracks told apart
    on their own both have a "SPEAKER_00", which are two people."""
    return [seg if seg.speaker is None else replace(seg, speaker=prefix + seg.speaker) for seg in segments]


def merge(a: Sequence[Segment], b: Sequence[Segment], offset: float = 0.0) -> list[Segment]:
    """Two tracks of the same meeting (the microphone, the system's output)
    as one transcript, in the order of time; `b` moved by `offset` seconds
    first (where its recording started later than `a`'s). Lines starting
    at the same moment keep `a` first. The labels are not touched: when
    both tracks had their voices told apart, give them apart first
    (prefix_speakers)."""
    tagged = [(seg.start, 0, n, seg) for n, seg in enumerate(a)]
    tagged += [(seg.start, 1, n, seg) for n, seg in enumerate(shift(b, offset) if offset else b)]
    return [seg for _start, _track, _n, seg in sorted(tagged, key=lambda t: t[:3])]
