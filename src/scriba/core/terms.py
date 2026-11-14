"""The terms the user gives whisper (names, acronyms, words of the trade):
the ones typed for a meeting, as the command line and the window take them
(one text, separated by commas, semicolons or lines), and the ones of the
files kept for every transcription, joined into what whisper reads."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# What whisper reads before each window is at most 224 tokens (n_text_ctx /
# 2); a longer prompt loses its first tokens, which are the meeting's own
# terms. Counted in characters, since the tokenizer is the engine's: on
# 14/11/2026 the vocabulary of large-v3-turbo gave 3.0 characters a token
# for a real list of 39 terms of the trade (514 characters, 169 tokens) and
# 2.6 for a made-up list with acronyms and codes [I: the longest match on
# the vocabulary's bytes, close to the engine's own split]. At 2.5 a token
# the 560 characters stay under 224 tokens.
PROMPT_MAX_CHARACTERS = 560
SEPARATOR = ", "


def terms_of(text: str) -> tuple[str, ...]:
    """"QG&UPS, UPS; CRAH" -> the terms, each once, as trascrivi.py split them."""
    return tuple(dict.fromkeys(t.strip() for t in re.split(r"[,;\n]", text or "") if t.strip()))


@dataclass(frozen=True)
class Joined:
    terms: tuple[str, ...]      # what whisper gets, in order: the meeting's first
    offered: int                # the different terms there were in all

    @property
    def left_out(self) -> int:
        return self.offered - len(self.terms)


def joined(meeting: Sequence[str], files: Iterable[Sequence[str]],
           limit: int = PROMPT_MAX_CHARACTERS) -> Joined:
    """The meeting's terms first, then those of each file in its order; a
    term already there (case and spacing aside) is not repeated, and keeps
    the spelling it had first, the meeting's over a file's. Cut at the last
    whole term that fits in `limit` characters, joined with SEPARATOR: what
    does not fit is the files' (the meeting's come first) and is counted in
    `left_out`, so that it can be said."""
    found: dict[str, str] = {}
    for group in (meeting, *files):
        for term in group:
            term = " ".join(term.split())
            if term and term.casefold() not in found:
                found[term.casefold()] = term
    kept: list[str] = []
    length = 0
    for term in found.values():
        more = len(term) + (len(SEPARATOR) if kept else 0)
        if length + more > limit:
            break
        kept.append(term)
        length += more
    return Joined(tuple(kept), len(found))
