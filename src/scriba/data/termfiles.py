"""Files of terms the user keeps for every transcription (Preferences,
Terms; `scriba-cli transcribe --terms-file`): plain text, one term or phrase
a line, empty lines and lines starting with `#` left out. The same format
as the lists of the earlier transcription script (`--termini-file`).

Only the reading is here; which terms whisper gets, and in what order, is
core/terms.py's (joined)."""

from __future__ import annotations

import os
from pathlib import Path

from scriba.i18n import _

# A list of terms is a few kilobytes; a file far larger was chosen by
# mistake (a recording, a document), and reading it whole would be slow
MAX_BYTES = 1024 * 1024


class TermsFileError(Exception):
    """A file of terms that cannot be used: its sentence says why."""


def parse(text: str) -> list[str]:
    """The terms of a file's text, in their order, each trimmed."""
    out = []
    for line in text.splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            out.append(term)
    return out


def read(path: str | os.PathLike) -> list[str]:
    """The terms of the file at `path`. TermsFileError when it is not there,
    cannot be read, is too large or is not text in UTF-8 (with or without
    the mark Notepad puts at the start)."""
    path = Path(path)
    try:
        if not path.is_file():
            raise TermsFileError(_("{name} is not there.").format(name=path.name))
        if path.stat().st_size > MAX_BYTES:
            raise TermsFileError(_("{name} is too large for a list of terms.").format(name=path.name))
        data = path.read_bytes()
    except OSError as e:
        raise TermsFileError(_("{name} cannot be read: {reason}").format(
            name=path.name, reason=e.strerror or e)) from e
    try:
        return parse(data.decode("utf-8-sig"))
    except UnicodeDecodeError as e:
        raise TermsFileError(_("{name} is not text in UTF-8: save it again as UTF-8.").format(name=path.name)) from e
