"""Where a produced file goes, and how it gets there.

Two rules hold for every file Scriba writes for the user. It never takes
the place of a file that is already there: a name that is taken becomes
`name_R01.ext`, `name_R02.ext`... (as trascrivi.py did, the convention of
Daniel's revisions). And it appears whole or not at all: the text goes to
a temporary file in the same folder (so that the rename never crosses a
disk), is flushed to the disk, and only then takes its name, so a crash or
a full disk leaves at most a stray temporary file, never half a transcript
under the real name.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

MAX_REVISIONS = 9999        # past this a folder is not a folder of revisions any more: something is wrong


def _dotted(ext: str) -> str:
    return ext if not ext or ext.startswith(".") else "." + ext


def free_name(folder: str | os.PathLike, stem: str, ext: str) -> Path:
    """The first of `stem.ext`, `stem_R01.ext`, `stem_R02.ext`... that is not
    in `folder`. `ext` may come with or without its dot. The answer is true
    when it is given: the writers below still take care that a file made in
    the meantime by someone else is not overwritten."""
    folder, ext = Path(folder), _dotted(ext)
    candidate = folder / f"{stem}{ext}"
    n = 0
    while os.path.lexists(candidate):
        n += 1
        if n > MAX_REVISIONS:
            raise FileExistsError(f"{folder / (stem + ext)}: {MAX_REVISIONS} revisions already there")
        candidate = folder / f"{stem}_R{n:02d}{ext}"
    return candidate


def _temporary(folder: Path, text: str) -> str:
    """The text in a new temporary file in `folder`, on the disk: its path."""
    fd, tmp = tempfile.mkstemp(prefix=".scriba-", suffix=".tmp", dir=folder)
    try:
        # newline="": the text is written as it is, "\n" on Windows too, so
        # that the same transcript is the same bytes on both systems
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _discard(tmp)
        raise
    return tmp


def _discard(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass


def free_stem(folder: str | os.PathLike, stem: str, exts) -> str:
    """The first of `stem`, `stem_R01`, `stem_R02`... under which none of
    the extensions `exts` is taken in `folder`: the files of one
    transcription (md, txt, srt, the meeting's own) then share their name,
    as a set, instead of one being `_R01` and its neighbour not."""
    folder = Path(folder)
    for n in range(MAX_REVISIONS + 1):
        candidate = stem if n == 0 else f"{stem}_R{n:02d}"
        if not any(os.path.lexists(folder / f"{candidate}{_dotted(ext)}") for ext in exts):
            return candidate
    raise FileExistsError(f"{folder / stem}: {MAX_REVISIONS} revisions already there")


def claim(tmp: str | os.PathLike, folder: str | os.PathLike, stem: str, ext: str) -> Path:
    """A finished temporary file of `folder` under the first free name (see
    free_name); the path it got. It takes its name with a hard link, which
    fails rather than replace a file that appeared between the choice of
    the name and the write: then the next free name is taken. Where the
    disk has no hard links (FAT, some network shares) the rename is used,
    after looking once more that the name is still free. The temporary
    file is gone afterwards, also when this fails."""
    folder, tmp = Path(folder), os.fspath(tmp)
    try:
        while True:
            target = free_name(folder, stem, ext)
            try:
                os.link(tmp, target)
            except FileExistsError:
                continue
            except OSError:
                if os.path.lexists(target):
                    continue
                os.replace(tmp, target)
                return target
            _discard(tmp)
            return target
    except BaseException:
        _discard(tmp)
        raise


def temporary_name(folder: str | os.PathLike, suffix: str) -> Path:
    """A new empty file in `folder` for a program to write into (ffmpeg's
    video), to be given its name with claim() when it is whole."""
    fd, tmp = tempfile.mkstemp(prefix=".scriba-", suffix=suffix, dir=folder)
    os.close(fd)
    return Path(tmp)


def write_new(folder: str | os.PathLike, stem: str, ext: str, text: str) -> Path:
    """`text` in a new file in `folder` under the first free name (see
    free_name and claim), atomically; the path it got."""
    folder = Path(folder)
    return claim(_temporary(folder, text), folder, stem, ext)


def replace(path: str | os.PathLike, text: str) -> None:
    """`text` as the new content of `path`, atomically: for a file of the
    app's own that is updated in place (a session), never for one of the
    user's outputs (write_new)."""
    path = Path(path)
    tmp = _temporary(path.parent, text)
    try:
        os.replace(tmp, path)
    except BaseException:
        _discard(tmp)
        raise
