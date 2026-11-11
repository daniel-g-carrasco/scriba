"""How far a text heard is from the text expected, word by word: the test
of the graphics cards checks that a card writes what the sample says,
not only that it is fast: different devices can produce different words.

The measure is the word error rate: the fewest words to put in, take out
or change to turn the expected text into the one heard, over the words
expected. Both texts are normalised first, so that what whisper writes
one way or another without changing what was said does not count: case,
punctuation, apostrophes ("C'era" and "cera": turbo wrote the sample
without them), accents (è and e), and the dashes and dots between words.
"""

from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = re.compile(r"['’‘ʼ`´]")
_NOT_A_WORD = re.compile(r"[^\w]+")


def normalised(text: str) -> list[str]:
    """The words of `text`, lower case, without accents, punctuation or
    apostrophes ("C'era una volta... Un re!" -> ["cera", "una", "volta",
    "un", "re"])."""
    folded = unicodedata.normalize("NFKD", text or "").casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _APOSTROPHES.sub("", folded)
    return [w for w in _NOT_A_WORD.sub(" ", folded).replace("_", " ").split() if w]


def edits(expected: list[str], heard: list[str]) -> int:
    """The fewest words put in, taken out or changed to make `heard` out of
    `expected` (Levenshtein's distance on words, two rows at a time)."""
    previous = list(range(len(heard) + 1))
    for i, word in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(heard, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1]


def difference(expected: str, heard: str) -> float:
    """The word error rate of `heard` against `expected`, 0 (the same
    words) and up: 1 is as many errors as words expected (nothing heard),
    and it can go past 1 when whisper adds words of its own. An empty
    expected text: 0 when nothing was heard either, else 1."""
    want, got = normalised(expected), normalised(heard)
    if not want:
        return 0.0 if not got else 1.0
    return edits(want, got) / len(want)
