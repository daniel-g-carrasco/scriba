"""The original recording and the current selection shown by the trim control."""

from __future__ import annotations

import os
import re
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from scriba.app import jobs, process, recording
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.data import atomic, edits, files
from scriba.i18n import _, translation_for


@dataclass(frozen=True)
class Cut:
    duration: float
    selection: tuple[float, float] | None
    original: Path | None
    call: tuple[float, float] | None
    trimmed: bool
    proposal: tuple[float, float]


def _words(key: str) -> set[str]:
    return {translation_for(language).gettext(key) for language in ("en", "it")} | {_(key)}


def _archived(source: Path, media: ffmpeg.Media) -> Path | None:
    expected = Path(media.trim_original).stem
    if not expected:
        terms = "|".join(re.escape(word) for word in _words("trimmed"))
        expected = re.sub(rf"_(?:{terms})(?:_R\d+)?$", "", source.stem)
    for revisions in ("Previous revisions", "Revisioni precedenti", jobs.revisions_folder()):
        folder = source.parent / revisions
        if not folder.is_dir() or folder.is_symlink():
            continue
        for path in sorted(folder.glob("*.mka")):
            if not path.is_symlink() and re.fullmatch(re.escape(expected) + r"(?:_R\d+)*", path.stem):
                return path
    return None


def _checkpoint(source: Path, duration: float) -> tuple[float, float] | None:
    for path in source.parent.glob(".*.scriba-recording.json*"):
        try:
            data, _warning = atomic.read_json(path)
            if data["joined"][0] == source.name:
                start, end = data["excerpt"]
                if 0 <= start < end <= duration:
                    return start, end
        except (KeyError, IndexError, OSError, TypeError, ValueError):
            continue
    return None


def _matched_packets(source: Path, original: Path, length: float, duration: float, cancelled=None):
    """Older manual cuts sometimes have only the trim flag. Match their copied packets."""
    def read(path, accept, limit=()):
        def line(value):
            try:
                at, digest = value.strip().split(",", maxsplit=1)
                if digest.startswith("SHA256:"):
                    accept(float(at), digest)
            except ValueError:
                pass

        args = [str(ffmpeg.need("ffprobe")), "-v", "error", "-select_streams", "a:0", *limit,
                "-show_packets", "-show_entries", "packet=pts_time,data_hash", "-show_data_hash", "sha256",
                "-of", "csv=p=0", str(path)]
        return process.run(args, on_stdout=line, cancelled=cancelled).returncode == 0

    beginning = []
    try:
        if not read(source, lambda at, digest: beginning.append((at, digest)), ("-read_intervals", "%+#8")):
            return None
        if len(beginning) < 2:
            return None
        wanted = tuple(digest for _at, digest in beginning)
        recent = deque(maxlen=len(wanted))
        matched = set()

        def match(at, digest):
            recent.append((at, digest))
            if tuple(value for _position, value in recent) == wanted:
                start = round(recent[0][0] - beginning[0][0], 3)
                if -recording.DURATION_SLACK <= start <= duration - length + recording.DURATION_SLACK:
                    matched.add(max(0.0, start))

        if read(original, match) and len(matched) == 1:
            start = matched.pop()
            return start, min(duration, round(start + length, 3))
    except (Failure, OSError):
        pass
    return None


def inspect(source: Path, media: ffmpeg.Media, cancelled=None) -> Cut:
    source = source.resolve()
    if not media.trimmed:
        proposal = media.suggested_range or (0, media.suggested_cut[1] if media.suggested_cut else media.duration)
        return Cut(media.duration, (0, media.duration), source, media.suggested_range, False, proposal)
    original = _archived(source, media)
    try:
        full = ffmpeg.probe(original, cancelled) if original is not None else None
    except Failure:
        original, full = None, None
    if full is not None and (not full.roles or full.trimmed):
        original, full = None, None
    call = full.suggested_range if full is not None else None
    duration = full.duration if full is not None else media.duration
    selection = None
    if media.trim_selection is not None:
        start, end, duration = media.trim_selection
        selection = start, end
    elif full is not None:
        selection = _checkpoint(source, duration)
        if selection is None and call and abs(media.duration - (call[1] - call[0])) <= recording.DURATION_SLACK:
            selection = call
        if selection is None:
            selection = _matched_packets(source, original, media.duration, duration, cancelled)
    return Cut(duration, selection, original, call, True, selection or call or (0, duration))


def _preserve(source: Path) -> None:
    revisions = source.parent / jobs.revisions_folder()
    revisions.mkdir(exist_ok=True)
    if revisions.is_symlink():
        raise OSError(revisions)
    for index in range(1, files.MAX_REVISIONS + 1):
        target = revisions / f"{source.stem}_R{index:02}.mka"
        try:
            os.link(source, target)
            return
        except FileExistsError:
            continue
        except OSError:
            temp = files.temporary_name(revisions, ".mka")
            try:
                shutil.copyfile(source, temp)
                with temp.open("rb+") as handle:
                    os.fsync(handle.fileno())
                files.claim(temp, revisions, target.stem, ".mka")
                return
            finally:
                temp.unlink(missing_ok=True)
    raise FileExistsError(revisions)


def change(source: Path, start: float, end: float, cancelled=None) -> Path:
    """Replace the working selection and its listening copy from the complete MKA."""
    source = source.resolve()
    folder = source.parent
    edits.recover(folder)
    media = ffmpeg.probe(source, cancelled)
    cut = inspect(source, media, cancelled)
    if cut.original is None:
        raise Failure(_("The complete recording is missing."), str(source))
    if not 0 <= start < end <= cut.duration:
        raise Failure(_("Choose an interval inside the recording."), f"{start}–{end}")
    if cut.selection == (start, end):
        return source
    full = (start, end) == (0, cut.duration)
    suffixes = _words("together")
    candidates = [folder / f"{source.stem}_{suffix}.m4a" for suffix in sorted(suffixes)]
    mix = next((path for path in candidates if path.is_file()), folder / f"{source.stem}_{_('together')}.m4a")
    suffix = "|".join(re.escape(word) for word in sorted(suffixes))
    derived_mix = re.compile(rf"{re.escape(source.stem)}_(?:{suffix})(?:_R\d+)?\.m4a")
    earlier_copies = [path for path in folder.glob("*.m4a")
                      if path.name != mix.name and not path.is_symlink() and derived_mix.fullmatch(path.name)]
    balance = jobs.saved_listening_balance(mix)
    with edits.prepare(folder) as stage:
        if full:
            staged = stage / source.name
            shutil.copyfile(cut.original, staged)
        else:
            staged = recording.copy_excerpt(cut.original, start, end, cancelled, destination=stage / source.name)
        heard = (recording.rebalance(staged, stage, source.stem, balance, cancelled)
                 if balance is not None else recording.listening_mix(staged, cancelled))
        check(cancelled)
        if cut.original == source:
            _preserve(source)
        files.to_revisions(folder, jobs.transcript_stem(source), ("", f"_{_('names')}", f"_{_('balanced')}"),
                           (".md", ".txt", ".srt", ".json"), jobs.revisions_folder())
        edits.apply(stage, {source: staged, mix: heard}, earlier_copies)
    return source
