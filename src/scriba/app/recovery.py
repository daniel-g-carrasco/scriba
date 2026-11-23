"""A recording's durable checkpoint and the startup search for unfinished takes."""

from __future__ import annotations

import math
import re
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scriba.app import recording
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.core.callend import MIN_OUTSIDE
from scriba.data import atomic
from scriba.i18n import _

SUFFIX = ".scriba-recording.json"
DATE = r"\d{4}-\d\d-\d\d \d\d-\d\d-\d\d"
ORPHAN = re.compile(
    rf"(?P<stem>{DATE} (?:recording|registrazione)(?:_R\d+)?)_"
    r"(?:(?P<mic>microphone|microfono)(?: \d+)?|(?:output|uscita) \d+ (?P<device>.+?))(?:_R\d+)?\.flac",
    re.IGNORECASE)


class Busy(Failure):
    pass


class Journal:
    def __init__(self, folder: Path, stem: str, *, orphan=False):
        self.folder = folder.resolve()
        self.stem = stem
        self.path = self.folder / f".{stem}{SUFFIX}"
        try:
            self.when = datetime.strptime(stem[:19], "%Y-%m-%d %H-%M-%S")
        except ValueError:
            self.when = datetime.fromtimestamp(folder.stat().st_mtime)
        self.orphan = orphan
        self.summary = (0.0, [])

    @contextmanager
    def lock(self):
        """The OS releases the lock if the writer dies, including on Windows."""
        import os
        with self.path.with_suffix(".lock").open("a+b") as handle:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise Busy(_("This recording is open in another instance."), str(error)) from error
            yield

    def _encoded(self, takes):
        output = []
        for take in takes:
            data = asdict(take)
            data["path"] = take.path.resolve().relative_to(self.folder).as_posix()
            if not math.isfinite(take.loudest):
                data["loudest"] = None
            output.append(data)
        return output

    def save(self, phase, takes, elapsed, *, joined=None, mix_takes=None, together=None, retained=(), cut=None,
             join_id=None, call_range=None, excerpt=None, original=None, mix_streams=None):
        data = {"version": 1, "phase": phase, "elapsed": elapsed, "takes": self._encoded(takes)}
        if cut is not None:
            data["cut"] = cut
        if call_range is not None:
            data["call_range"] = call_range
        if excerpt is not None:
            data["excerpt"] = excerpt
        if original is not None:
            data["original"] = original.name
        if join_id is not None:
            data["join_id"] = join_id
        if joined is not None:
            data["joined"] = [joined.name, joined.stat().st_size]
        if mix_takes is not None:
            data["mix_takes"] = self._encoded(mix_takes)
        if mix_streams is not None:
            data["mix_streams"] = mix_streams
        if retained:
            data["retained"] = [path.name for path in sorted(retained)]
        if together is not None:
            data["together"] = together.name
        atomic.write_json(self.path, data, backup_optional=True)

    def read(self):
        data, _warning = atomic.read_json(self.path)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("unknown recording checkpoint")
        return data

    def local(self, name):
        """Only files in this meeting or its own finishing workspace."""
        relative = Path(name)
        parts = relative.parts
        if relative.anchor or ".." in parts or not (
                len(parts) == 1 or len(parts) == 2 and parts[0].startswith(".scriba-work-")):
            raise ValueError("invalid path in recording checkpoint")
        path = self.folder / relative
        if not path.resolve().is_relative_to(self.folder.resolve()):
            raise ValueError("checkpoint path leaves recording folder")
        return path

    def takes(self, data=None, key="takes"):
        if self.orphan and data is None:
            return self._orphans()
        data = self.read() if data is None else data
        takes = []
        for saved in data.get(key, ()):
            values = dict(saved)
            values["path"] = self.local(values["path"])
            for field in "off", "muted", "named_spans":
                values[field] = tuple(map(tuple, values.get(field, ())))
            if values.get("loudest") is None:
                values["loudest"] = 0.0 if data["phase"] == "recording" else -math.inf
            takes.append(recording.Take(**values))
        return takes

    def _orphans(self):
        takes = []
        for path in sorted(self.folder.glob("*.flac")):
            match = ORPHAN.fullmatch(path.name)
            if (match is None or match["stem"] != self.stem or path.is_symlink()
                    or not path.is_file() or path.stat().st_size <= 4):
                continue
            with path.open("rb") as handle:
                if handle.read(4) != b"fLaC":
                    continue
            try:
                media = ffmpeg.probe(path)
            except Failure:
                continue
            tracks = media.roles
            mic = bool(match["mic"])
            role = "me" if mic else "others"
            device = match["device"] or _("Microphone")
            track = tracks[0] if tracks else None
            takes.append(recording.Take(f"orphan-{len(takes)}", track.role if track else role,
                                        track.title if track and track.title else device, path, 0.0,
                                        muted=track.muted if track else (), named_spans=track.named_spans if track else (),
                                        meeting_title=media.meeting_title))
        return takes

    def verified_join(self, data):
        if "joined" not in data:
            return self.published_join(data.get("join_id"))
        name, size = data["joined"]
        path = self.local(name)
        try:
            if path.stat().st_size == size and ffmpeg.probe(path).roles:
                return path
        except (OSError, Failure):
            pass
        return None

    def published_join(self, join_id):
        # A crash can leave the validated MKA published just before the next
        # checkpoint. Its identifier was saved before starting the join.
        if not join_id:
            return None
        for path in self.folder.glob("*.mka"):
            if path.is_symlink() or not path.stem.startswith(self.stem):
                continue
            try:
                media = ffmpeg.probe(path)
            except (OSError, Failure):
                continue
            if media.roles and media.join_id == join_id:
                return path
        return None

    def keep_excerpt(self, joined, takes, elapsed, previous, choose, progress, cancelled, **metadata):
        """Checkpoint the answer and the excerpt before moving the complete MKA."""
        excerpt = previous.get("excerpt")
        original = previous.get("original")
        call_range = metadata.get("call_range")
        if excerpt is None and choose is not None and call_range:
            media = ffmpeg.probe(joined, cancelled)
            start, end = call_range
            end = min(end, media.duration)
            if 0 <= start < end and start + media.duration - end >= MIN_OUTSIDE:
                self.save("finishing:waiting", takes, elapsed, joined=joined, **metadata)
                progress(recording.WAITING)
                check(cancelled)
                if choose((start, end), cancelled):
                    excerpt = start, end
                else:
                    self.save("finishing:cleaning", takes, elapsed, joined=joined,
                              retained=metadata["retained"], cut=metadata["cut"])
        if excerpt is None:
            return joined, None
        if previous.get("phase") == "finishing:excerpt":
            original = self.local(original) if original else None
        elif previous.get("phase") != "finishing:mixing":
            progress(recording.TRIMMING)
            excerpt_id = previous.get("join_id") if previous.get("excerpt") else uuid.uuid4().hex
            original = joined
            self.save("finishing:trimming", takes, elapsed, joined=original, excerpt=excerpt,
                      join_id=excerpt_id, **metadata)
            joined = self.published_join(excerpt_id) or recording.copy_excerpt(
                original, *excerpt, cancelled, join_id=excerpt_id)
            self.save("finishing:excerpt", takes, elapsed, joined=joined, excerpt=excerpt, original=original,
                      **metadata)
        if isinstance(original, Path):
            from scriba.app import jobs

            recording.archive_recording(original, jobs.revisions_folder())
        return joined, excerpt


def pending(root: Path) -> list[Journal]:
    if not root.is_dir():
        return []
    out = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(DATE + r"(?:_R\d+)?", folder.name):
            continue
        states = {p.name.removesuffix(".bak") for p in folder.glob(f".*{SUFFIX}*")
                  if p.name.endswith((SUFFIX, SUFFIX + ".bak"))}
        known = set()
        for name in states:
            stem = name[1:-len(SUFFIX)]
            try:
                journal = Journal(folder, stem)
                with journal.lock():
                    try:
                        data = journal.read()
                        known.add(stem)
                        if data["phase"] != "done":
                            takes = journal.takes(data)
                            if data.get("joined") or any(t.path.is_file() for t in takes):
                                excerpt = data.get("excerpt")
                                duration = excerpt[1] - excerpt[0] if excerpt else data.get("elapsed", 0.0)
                                journal.summary = (duration, [t.role for t in takes])
                                out.append(journal)
                    except (OSError, KeyError, TypeError, ValueError):
                        pass  # may still be recognizable as an orphan
            except Busy:
                known.add(stem)
            except (OSError, ValueError):
                continue
        stems = {m["stem"] for path in folder.glob("*.flac") if (m := ORPHAN.fullmatch(path.name)) is not None}
        for stem in stems - known:
            try:
                journal = Journal(folder, stem, orphan=True)
                # Probe on completion only; a startup scan should not open the audio.
                with journal.lock():
                    out.append(journal)
            except (OSError, ValueError, Busy):
                continue
    return sorted(out, key=lambda item: (item.when, item.stem))


def resume(journal, cancelled=None, progress=None, *, fraction=None, choose=None):
    with journal.lock():
        try:
            data = journal.read()
            orphan = False
        except (OSError, ValueError):
            if not journal.orphan:
                raise
            data, orphan = {"phase": "recording", "elapsed": 0.0}, True
        joined = journal.verified_join(data)
        if data["phase"] == "done":
            if joined is None:
                raise Failure(_("The saved recording could not be read."), str(journal.path))
            return recording.Finished(joined, None)
        if data.get("excerpt") and joined is None:
            raise Failure(_("The saved recording could not be read."), str(journal.path))
        saved = journal.takes(None if orphan else data)
        takes = [take for take in saved if take.path.is_file() and take.path.stat().st_size > 4]
        elapsed = data.get("elapsed", 0.0) if data["phase"] != "recording" else 0.0
        if joined is not None and (data.get("call_range") or data.get("excerpt")):
            takes = saved
        elif joined is not None and len(takes) != len(data.get("takes", ())):
            if progress is not None:
                progress(recording.MIXING)
            together = recording.listening_mix(joined, cancelled)
            journal.save("done", takes, elapsed, joined=joined, together=together)
            return recording.Finished(joined, together)
        finished = recording.finish(takes, journal.folder, journal.stem, cancelled, progress, elapsed=elapsed,
                                    fraction=fraction, journal=journal, previous=data, joined=joined, choose=choose)
        if orphan:
            finished.notes.insert(0, _("Recovered tracks from their filenames and tags. Device changes and intervals "
                                       "without tags could not be restored."))
        return finished


def finalize(journal, takes, cancelled=None, progress=None, *, elapsed=0.0, mix_progress=None, fraction=None,
             choose=None):
    with journal.lock():
        try:
            data = journal.read()
        except (OSError, ValueError):
            data = {}
        joined = journal.verified_join(data)
        if data.get("phase") == "done" and joined is not None:
            return recording.Finished(joined, None)
        return recording.finish(takes, journal.folder, journal.stem, cancelled, progress, elapsed=elapsed,
                                mix_progress=mix_progress, fraction=fraction, journal=journal,
                                previous=data, joined=joined, choose=choose)
