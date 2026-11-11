"""The terms the user gives whisper (names, acronyms, words of the trade),
as the command line and the window take them: one text, separated by
commas, semicolons or lines."""

from __future__ import annotations

import re


def terms_of(text: str) -> tuple[str, ...]:
    """"QG&UPS, UPS; CRAH" -> the terms, each once, as trascrivi.py split them."""
    return tuple(dict.fromkeys(t.strip() for t in re.split(r"[,;\n]", text or "") if t.strip()))
