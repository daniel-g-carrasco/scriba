# SPDX-License-Identifier: GPL-3.0-or-later
"""Move a meeting directory whole, preserving references to its previous locations."""

from __future__ import annotations

import errno
import os
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from scriba.core.recording import safe
from scriba.data import atomic, files
from scriba.i18n import _

DATE = r"\d{4}-\d\d-\d\d \d\d-\d\d-\d\d"
STATE = ".scriba-meeting.json"


@dataclass(frozen=True)
class MeetingFolder:
    date: str
    title: str = ""
    target: str = ""
    former_paths: tuple[str, ...] = ()
    external_source: str = ""
    export_base: str = ""

    def save(self, folder: Path) -> None:
        if not folder.is_dir() or folder.is_symlink():
            raise FileNotFoundError(folder)
        atomic.write_json(folder / STATE, asdict(self), keep_backup=False)


def read(folder: Path) -> MeetingFolder | None:
    marker = folder / STATE
    if marker.is_symlink():
        return None
    try:
        data, _warning = atomic.read_json(marker)
        state = MeetingFolder(**data)
        if (re.fullmatch(DATE, state.date) and isinstance(state.title, str) and isinstance(state.target, str)
                and (not state.target or state.target == name(state.date, state.title))
                and isinstance(state.former_paths, list)
                and isinstance(state.external_source, str) and isinstance(state.export_base, str)
                and not any(c in state.export_base for c in '/\\:\0')
                and all(isinstance(path, str) and Path(path).is_absolute() for path in state.former_paths)):
            return replace(state, former_paths=tuple(state.former_paths))
    except (OSError, TypeError, ValueError):
        pass
    return None


def find(folder: Path) -> tuple[Path, MeetingFolder] | None:
    # Previous revisions and the old transcript subfolder can sit below the meeting.
    for parent in (folder, *folder.parents[:2]):
        if state := read(parent):
            return parent, state
    return None


def name(date: str, title: str) -> str:
    if not title:
        return date
    # Leave room for the transcript and revision suffixes on Linux, where
    # a filename's limit counts bytes.
    piece = safe(title, limit=72).encode("utf-8")[:144].decode("utf-8", errors="ignore").rstrip(" .")
    return f"{date} {piece}"


def rename_directory(source: Path, target: Path) -> None:
    """One same-parent rename, including when another process creates the target."""
    if os.name == "nt":
        os.rename(source, target)    # Windows refuses to replace an existing directory.
        return
    if sys.platform != "linux":
        raise OSError(errno.ENOTSUP, "exclusive directory rename")
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "renameat2"):
        raise OSError(errno.ENOTSUP, "renameat2")
    at_fdcwd, rename_noreplace = -100, 1
    if libc.renameat2(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(target), rename_noreplace) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def place(folder: Path, state: MeetingFolder, *, title: str | None = None) -> tuple[Path, list[str]]:
    """Checkpoint the desired title and old location before moving the directory."""
    if title is not None:
        state = replace(state, title=title, target=name(state.date, title))
    suffix = r"(?P<revision>_R\d+)?"
    pattern = re.escape(state.target) + suffix
    try:
        if not state.target or re.fullmatch(pattern, folder.name):
            if title is not None:
                state.save(folder)
            return folder, []
        former_paths = tuple(dict.fromkeys((*state.former_paths, str(folder))))
        replace(state, former_paths=former_paths).save(folder)
        if os.name == "nt" and (match := re.fullmatch(pattern, folder.name, re.IGNORECASE)):
            candidate = folder.with_name(state.target + (match["revision"] or ""))
            rename_directory(folder, candidate)
            return candidate, []
        while True:
            candidate = files.free_name(folder.parent, state.target, "")
            try:
                rename_directory(folder, candidate)
            except FileExistsError:
                continue
            return candidate, []
    except OSError:
        return folder, [_("Could not rename the meeting folder. Scriba will try again next time.")]


def rebase(path: Path, old: Path, new: Path) -> Path:
    """Return the same path within a moved directory, leaving external paths alone."""
    # UCRT64's pathlib can put both slash styles in paths returned by the filesystem.
    original = path
    if os.name == "nt":
        path = Path(str(path).replace("\\", "/"))
        old = Path(str(old).replace("\\", "/"))
        new = Path(str(new).replace("\\", "/"))
    if path.is_relative_to(old):
        return new / path.relative_to(old)
    return original
