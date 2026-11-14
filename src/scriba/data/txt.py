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
from .markdown import Group, Header, body, facts, shown, warning


def render(header: Header, lines: Iterable[Line], names: Mapping[str, str] | None = None,
           group: Group | None = None) -> str:
    lines = shown(lines, names)
    title, *rest = facts(header, lines)
    alert = warning(header)
    return "\n".join([title, *([alert] if alert else []), *rest, "", *body(lines, bold=False, group=group)]) + "\n"


def write(folder: str | os.PathLike, stem: str, header: Header, lines: Iterable[Line],
          names: Mapping[str, str] | None = None, group: Group | None = None) -> Path:
    """The transcript in a new file `stem.txt` in `folder` (or `stem_R01.txt`:
    never over a file that is there); its path."""
    return files.write_new(folder, stem, ".txt", render(header, lines, names, group))
