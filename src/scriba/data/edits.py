"""Recoverable replacement of the files that make up a saved meeting edit."""

from __future__ import annotations

import os
import shutil
from hashlib import sha256
from contextlib import contextmanager
from pathlib import Path

from . import atomic

DIRECTORY = ".scriba-edit"
MANIFEST = "pending.json"
OWNED = ".scriba-edit-owned"
OUTPUT_SUFFIXES = {".md", ".txt", ".srt", ".m4a", ".mka", ".json"}


@contextmanager
def _lease(path: Path):
    with path.open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield


def _copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)
        with destination.open("rb+") as handle:
            os.fsync(handle.fileno())


def _local(name: str) -> bool:
    return bool(name and name not in (".", "..") and not any(char in name for char in "/\\:\0"))


def _recover(stage: Path) -> None:
    if stage.is_symlink() or stage.is_junction():
        raise OSError(f"Unsafe edit folder: {stage}")
    if stage.is_dir() and not (stage / OWNED).is_file():
        stage.rmdir()   # A foreign nonempty directory is refused.
        return
    if (stage / MANIFEST).is_file():
        rows, _warning = atomic.read_json(stage / MANIFEST)
        if not isinstance(rows, list) or any(
            not isinstance(row, list) or len(row) != 3 or not isinstance(row[0], str) or not _local(row[0])
            or Path(row[0]).suffix.lower() not in OUTPUT_SUFFIXES or not isinstance(row[1], str)
            or (row[1] and not _local(row[1])) or type(row[2]) is not bool for row in rows
        ):
            raise OSError(f"Invalid edit journal: {stage}")
        for index, (name, incoming, existed) in reversed(list(enumerate(rows))):
            pending = stage / incoming if incoming else stage / f"removed-{index}"
            if pending.exists() == bool(incoming):
                continue   # This operation had not yet taken effect.
            target = stage.parent / name
            if existed:
                undo = stage / f"undo-{index}"
                undo.unlink(missing_ok=True)
                _copy(stage / f"old-{index}", undo)
                atomic.replace_patiently(undo, target)
            else:
                target.unlink(missing_ok=True)
        (stage / MANIFEST).unlink()
    if stage.is_dir():
        shutil.rmtree(stage)


def recover(folder: Path) -> None:
    """Restore a previous set left by an interrupted save, before reading it."""
    folder = folder.resolve()
    stage = folder / DIRECTORY
    if os.path.lexists(stage):
        with _lease(folder / ".scriba-edit.lock"):
            _recover(stage)


def pending(folder: Path) -> bool:
    return (folder / DIRECTORY / MANIFEST).is_file()


@contextmanager
def draft_lease(state: Path, meeting: Path):
    """The UI and CLI share a lease for editing one meeting's speaker names."""
    address = os.path.normcase(str(meeting.resolve()).replace("\\", "/"))
    key = sha256(os.fsencode(address)).hexdigest()
    folder = state / "meeting-drafts"
    folder.mkdir(parents=True, exist_ok=True)
    with _lease(folder / f"{key}.lock"):
        yield


@contextmanager
def prepare(folder: Path):
    """Generate the new files beside their destinations while holding the folder lease."""
    folder = folder.resolve()
    with _lease(folder / ".scriba-edit.lock"):
        stage = folder / DIRECTORY
        _recover(stage)
        stage.mkdir()
        (stage / OWNED).touch(exist_ok=False)
        try:
            yield stage
        finally:
            if stage.is_dir() and not (stage / MANIFEST).exists():
                shutil.rmtree(stage)


def apply(stage: Path, replacements: dict[Path, Path], removals=()) -> None:
    """Publish verified files. Keep the old set on a failed or interrupted publication."""
    rows = []
    entries = [*replacements.items(), *((path, None) for path in removals)]
    for target, incoming in entries:
        if (target.parent.resolve() != stage.parent or target.suffix.lower() not in OUTPUT_SUFFIXES
                or target.is_symlink() or (target.exists() and not target.is_file())):
            raise OSError(f"Unsafe edit path: {target}")
        if incoming is not None:
            if incoming.parent.resolve() != stage or not incoming.is_file():
                raise OSError(f"Edit not prepared: {incoming}")
            with incoming.open("rb+") as handle:
                os.fsync(handle.fileno())
        existed = target.is_file()
        if not existed and incoming is None:
            continue
        if existed:
            _copy(target, stage / f"old-{len(rows)}")
        rows.append([target.name, incoming.name if incoming else "", existed])
    if not rows:
        return
    atomic.write_json(stage / MANIFEST, rows, keep_backup=False)
    try:
        for index, (name, incoming, _existed) in enumerate(rows):
            target = stage.parent / name
            if incoming:
                atomic.replace_patiently(stage / incoming, target)
            else:
                atomic.replace_patiently(target, stage / f"removed-{index}")
        (stage / MANIFEST).unlink()
    except OSError:
        _recover(stage)
        raise
