"""A recording's durable checkpoint and the startup search for unfinished takes."""

from __future__ import annotations

import math
import re
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from scriba.app import meeting_folders, recording
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.data import atomic
from scriba.i18n import _

SUFFIX = ".scriba-recording.json"
DATE = meeting_folders.DATE
ORPHAN = re.compile(
    rf"(?P<stem>{DATE} (?:recording|registrazione)(?:_R\d+)?)_"
    r"(?:(?P<mic>microphone|microfono)(?: \d+)?|(?:output|uscita) \d+ (?P<device>.+?))(?:_R\d+)?\.flac",
    re.IGNORECASE)


class Busy(Failure):
    pass


class Journal:
    def __init__(self, folder: Path, stem: str, *, orphan=False):
        self.stem = stem
        self.bind_folder(folder)
        try:
            self.when = datetime.strptime(stem[:19], "%Y-%m-%d %H-%M-%S")
        except ValueError:
            self.when = datetime.fromtimestamp(folder.stat().st_mtime)
        self.orphan = orphan
        self.summary = (0.0, [])
        self.teams_minutes: list[dict] = []
        self.choice_done = False

    def bind_folder(self, folder: Path) -> None:
        self.folder = folder.resolve()
        self.path = self.folder / f".{self.stem}{SUFFIX}"

    @contextmanager
    def lock(self, path=None):
        """The OS releases the lock if the writer dies, including on Windows."""
        import os
        with (path or self.path.with_suffix(".lock")).open("a+b") as handle:
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
            data.pop("teams_people", None)    # one maximum for the whole recording
            data.pop("teams_peer", None)
            data["path"] = take.path.resolve().relative_to(self.folder).as_posix()
            if not math.isfinite(take.loudest):
                data["loudest"] = None
            output.append(data)
        return output

    def save(self, phase, takes, elapsed, *, joined=None, mix_takes=None, together=None, retained=(), cut=None,
             join_id=None, call_range=None, excerpt=None, original=None, mix_streams=None):
        data = {"version": 1, "phase": phase, "elapsed": elapsed, "takes": self._encoded(takes)}
        people = max((take.teams_people for take in takes), default=0)
        if people:
            data["teams_people"] = people
        if peer := next((take.teams_peer for take in takes if take.teams_peer), ""):
            data["teams_peer"] = peer
        if self.teams_minutes:
            data["teams_minutes"] = self.teams_minutes
        if self.choice_done:
            data["choice_done"] = True
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
            data["mix_mute_applied"] = True
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
        self.teams_minutes = data.get("teams_minutes", [])
        self.choice_done = bool(data.get("choice_done"))
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
            values["teams_people"] = data.get("teams_people", 0)
            values["teams_peer"] = data.get("teams_peer", "")
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
                                        meeting_title=media.meeting_title, teams_people=media.teams_people,
                                        teams_peer=media.teams_peer))
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
            if path.is_symlink():
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
        before_cut = previous.get("phase", "recording") in (
            "recording", "finishing:closing", "finishing:joining", "finishing:waiting")
        if excerpt is None and choose is not None and not self.choice_done and before_cut:
            media = ffmpeg.probe(joined, cancelled)
            duration = media.duration
            tail = metadata.get("cut")
            proposal = (0, tail[1]) if tail and 0 < tail[1] < duration else (0, duration)
            call = None
            if call_range:
                start, end = call_range[0], min(call_range[1], duration)
                if 0 <= start < end:
                    call = proposal = start, end
            if duration > 0:
                self.save("finishing:waiting", takes, elapsed, joined=joined, **metadata)
                progress(recording.WAITING)
                check(cancelled)
                selection = choose(proposal, cancelled, duration, call)
                self.choice_done = True
                if selection:
                    left, right = selection if isinstance(selection, tuple) else proposal
                    if 0 <= left < right <= duration and (left, right) != (0, duration):
                        excerpt = left, right
                if excerpt is None:
                    self.save("finishing:cleaning", takes, elapsed, joined=joined, **metadata)
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
        if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(DATE + r"(?:_R\d+| .+)?", folder.name):
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


@contextmanager
def _finishing_folder(journal, takes=None, elapsed=0.0):
    """Lock by directory during work and by parent while the address changes."""
    old = journal.folder
    with ExitStack() as lease:
        with journal.lock(old.parent / ".scriba-finishing.lock"):
            with journal.lock():
                try:
                    data = journal.read()
                    orphan = False
                except (OSError, ValueError):
                    if takes is None and not journal.orphan:
                        raise
                    data, orphan = {"phase": "recording", "elapsed": 0.0}, takes is None
                date = journal.when.strftime("%Y-%m-%d %H-%M-%S")
                if takes is not None and "takes" not in data and takes:
                    journal.save("finishing:closing", takes, elapsed)
                    data = journal.read()
                state = meeting_folders.read(old)
                saved = takes if takes is not None else journal.takes(None if orphan else data)
                title = next((take.meeting_title for take in saved if take.meeting_title), "")
            # Windows needs all file handles inside the directory closed for the rename.
            notes = []
            stem = journal.stem
            if state is not None:
                folder, notes = meeting_folders.place(old, state)
            elif saved and re.fullmatch(DATE + r"(?:_R\d+)?", old.name):
                state = meeting_folders.MeetingFolder(date)
                if title:
                    folder, notes = meeting_folders.place(old, state, title=title)
                else:
                    folder = old
                    try:
                        state.save(folder)
                    except OSError:
                        pass    # An untitled recording can stay in its dated folder.
            else:
                folder = old
            if state is not None and folder.name.startswith(f"{date} "):
                stem = folder.name
            if folder.name != old.name:
                journal.bind_folder(folder)
                saved = [replace(take, path=meeting_folders.rebase(take.path, old, journal.folder))
                         for take in saved]
            lease.enter_context(journal.lock())
        yield data, saved, orphan, stem, notes


def resume(journal, cancelled=None, progress=None, *, fraction=None, choose=None):
    with _finishing_folder(journal) as (data, saved, orphan, stem, notes):
        joined = journal.verified_join(data)
        if data["phase"] == "done":
            if joined is None:
                raise Failure(_("The saved recording could not be read."), str(journal.path))
            return recording.Finished(joined, None, notes=notes)
        if data.get("excerpt") and joined is None:
            raise Failure(_("The saved recording could not be read."), str(journal.path))
        takes = [take for take in saved if take.path.is_file() and take.path.stat().st_size > 4]
        elapsed = data.get("elapsed", 0.0) if data["phase"] != "recording" else 0.0
        if joined is not None and (data.get("call_range") or data.get("excerpt")):
            takes = saved
        elif joined is not None and len(takes) != len(data.get("takes", ())):
            if progress is not None:
                progress(recording.MIXING)
            together = recording.listening_mix(joined, cancelled)
            journal.save("done", saved, elapsed, joined=joined, together=together, excerpt=data.get("excerpt"))
            return recording.Finished(joined, together, notes=notes)
        finished = recording.finish(takes, journal.folder, stem, cancelled, progress, elapsed=elapsed,
                                    fraction=fraction, journal=journal, previous=data, joined=joined, choose=choose)
        finished.notes[:0] = notes
        if orphan:
            finished.notes.insert(0, _("Recovered tracks from their filenames and tags. Device changes and intervals "
                                       "without tags could not be restored."))
        return finished


def finalize(journal, takes, cancelled=None, progress=None, *, elapsed=0.0, mix_progress=None, fraction=None,
             choose=None):
    with _finishing_folder(journal, takes, elapsed) as (data, saved, _orphan, stem, notes):
        joined = journal.verified_join(data)
        if data.get("phase") == "done" and joined is not None:
            return recording.Finished(joined, None, notes=notes)
        finished = recording.finish(saved, journal.folder, stem, cancelled, progress, elapsed=elapsed,
                                    mix_progress=mix_progress, fraction=fraction, journal=journal,
                                    previous=data, joined=joined, choose=choose)
        finished.notes[:0] = notes
        return finished
