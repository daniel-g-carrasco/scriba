"""The transcript as plain text (.txt), for whoever pastes it into a mail
or opens it in Notepad: the same header sentences and rows as the Markdown,
without its marks (no `#`, no stars, no rule).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from . import files
from .lines import Line
from .markdown import Header, body, facts, shown


def render(header: Header, lines: Iterable[Line], names: Mapping[str, str] | None = None) -> str:
    lines = shown(lines, names)
    return "\n".join([*facts(header, lines), "", *body(lines, bold=False)]) + "\n"


def write(folder: str | os.PathLike, stem: str, header: Header, lines: Iterable[Line],
          names: Mapping[str, str] | None = None) -> Path:
    """The transcript in a new file `stem.txt` in `folder` (or `stem_R01.txt`:
    never over a file that is there); its path."""
    return files.write_new(folder, stem, ".txt", render(header, lines, names))
