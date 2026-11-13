"""Whether the user is muted in the call, from the words of Windows 11's
microphone indicator, and what that does to the transcription.

Windows' own audio sessions do not change when Teams mutes the microphone
(measured on 2026-11-13: the call keeps the device open and reading). The
microphone indicator in the notification area does: on Windows 11 in
Italian its name read "Microfono Audio disattivato: Microsoft Teams" while
muted and "Microfono Audio riattivato: Microsoft Teams" after, then lines
with the shortcut and "App che usano il microfono: ...". A 25-minute log
caught four changes within a second; three readings out of about 1500
failed and came back the second after (app/callmute.py reads it).

The English words are NOT verified: "Microphone ... muted / unmuted" is a
guess at Windows 11's English text, and any other text, any other language,
gives UNKNOWN, which changes nothing.

While muted in the call, what the microphone hears is not the user speaking
in the call (someone in the room, the user aside): the sound stays in the
recording, whole, but the speech found on the microphone's track in those
stretches is left out of the transcript (`outside`).
"""

from __future__ import annotations

MUTED, UNMUTED, UNKNOWN = "muted", "unmuted", "unknown"

# the first line of the indicator's name, lower case: (starts with, word, state);
# the first word that matches wins, so "disattivato" is looked for before "attivato"
WORDS = (
    ("microfono", "disattivato", MUTED),        # Windows 11, Italian: measured
    ("microfono", "riattivato", UNMUTED),
    ("microfono", "attivato", UNMUTED),
    ("microphone", "unmuted", UNMUTED),         # English: a guess, not verified
    ("microphone", "muted", MUTED),
)


def state_of(name: str | None) -> str:
    """MUTED, UNMUTED or UNKNOWN from the indicator's name (None: there is
    no indicator, no app uses the microphone)."""
    if not name:
        return UNKNOWN
    first = name.strip().splitlines()[0].strip().lower() if name.strip() else ""
    for start, word, state in WORDS:
        if first.startswith(start) and word in first.replace(":", " ").split():
            return state
    return UNKNOWN


class Reading:
    """The state as it goes: the last valid one kept when a reading fails
    (one in 500 does, and the next is right), UNKNOWN when the indicator is
    not there or its words are not known."""

    def __init__(self) -> None:
        self.state = UNKNOWN

    def read(self, name: str | None) -> str:
        self.state = state_of(name)
        return self.state

    def failed(self) -> str:
        return self.state


Span = tuple[float, "float | None"]


def write_spans(spans) -> str:
    """The stretches as a recording's tag keeps them: "12.3-45.6;78-" (an
    open end: to the end of the recording)."""
    return ";".join(f"{start:g}-{'' if end is None else f'{end:g}'}" for start, end in spans)


def read_spans(text: str | None) -> tuple[Span, ...]:
    """The stretches of write_spans; what cannot be read is left out."""
    out = []
    for part in (text or "").split(";"):
        start, sep, end = part.strip().partition("-")
        try:
            span = (float(start), float(end) if end else None)
        except ValueError:
            continue
        if sep and span[0] >= 0 and (span[1] is None or span[1] > span[0]):
            out.append(span)
    return tuple(out)


def outside(speech: list[tuple[float, float]], muted) -> list[tuple[float, float]]:
    """The speech stretches with the muted stretches taken out (a stretch
    cut in two by a short mute gives two)."""
    out = list(speech)
    for start, end in muted:
        stop = float("inf") if end is None else end
        kept = []
        for s, e in out:
            if e <= start or s >= stop:
                kept.append((s, e))
                continue
            if s < start:
                kept.append((s, start))
            if e > stop:
                kept.append((stop, e))
        out = kept
    return out


def taken(speech: list[tuple[float, float]], muted) -> float:
    """Seconds of speech that `outside` leaves out."""
    return sum(e - s for s, e in speech) - sum(e - s for s, e in outside(speech, muted))
