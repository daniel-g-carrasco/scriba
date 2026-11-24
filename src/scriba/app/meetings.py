"""Reading and editing saved meetings, shared by the command-line commands."""

from __future__ import annotations

import datetime
import re
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from scriba.app import config, jobs, meeting_folders
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure
from scriba.core import speakers, timeline
from scriba.data import edits, session
from scriba.i18n import _

DATE_FORMAT = "%Y-%m-%d %H-%M-%S"
DATE_PATTERN = re.compile(meeting_folders.DATE)


def default_folder(cfg: config.Config) -> Path:
    if cfg.recordings_folder:
        return Path(cfg.recordings_folder)
    try:
        from gi.repository import GLib

        music = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    except ImportError:
        music = None
    return (Path(music) if music else Path.home() / "Music") / "Scriba"


def _dated(text: str) -> datetime.datetime | None:
    if date := DATE_PATTERN.match(text):
        try:
            return datetime.datetime.strptime(date[0], DATE_FORMAT)
        except ValueError:
            pass
    return None


@dataclass(frozen=True)
class Meeting:
    folder: Path
    source: Path | None
    path: Path | None
    date: datetime.datetime
    title: str
    duration: float

    def open(self) -> jobs.Result:
        if self.path is None:
            raise Failure(_("This meeting has no transcript."), jobs.full_path(self.folder))
        return jobs.open_session(self.path, read_only=True)[0]

    def summary(self, result: jobs.Result | None = None) -> dict:
        title = (result.meeting_title or self.title) if result is not None else self.title
        return {"date": self.date.isoformat(timespec="seconds"), "title": title, "duration": self.duration,
                "transcribed": self.path is not None, "voices": result.speakers if result else 0,
                "names": dict(result.names) if result else {}, "path": jobs.full_path(self.folder)}


def _meeting(folder: Path, path: Path | None, source: Path | None = None) -> Meeting:
    stored = session.load(path) if path is not None else None
    state = meeting_folders.read(folder)
    when = _dated(folder.name) or (_dated(source.stem) if source else None)
    if when is None and stored and stored.created:
        try:
            when = datetime.datetime.fromisoformat(stored.created).replace(tzinfo=None)
        except ValueError:
            pass
    if when is None:
        when = datetime.datetime.fromtimestamp((source or path or folder).stat().st_mtime)
    title = state.title if state and state.target else stored.extra.get("meeting_title", "") if stored else ""
    if not isinstance(title, str) or not title:
        title = folder.name[len(when.strftime(DATE_FORMAT)):].strip() if _dated(folder.name) else folder.name
    duration = stored.duration if stored else 0.0
    if stored is None and source:
        try:
            media = ffmpeg.probe(source)
            duration = media.duration
            title = media.meeting_title or title
        except Failure:
            pass
    return Meeting(folder, source, path, when, title, duration)


def _sessions(folder: Path, nested: bool) -> list[Path]:
    subfolders = [folder]
    if nested:
        subfolders.extend(p for p in folder.iterdir() if p.is_dir() and not p.is_symlink()
                          and not p.name.startswith(".") and p.name not in {"Previous revisions", "Revisioni precedenti"})
    found = []
    for subfolder in subfolders:
        for path in subfolder.glob("*.json"):
            if path.name.startswith(".") or path.is_symlink():
                continue
            try:
                session.load(path)
            except session.SessionError:
                continue
            found.append(path)
    return sorted(found, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)


def in_folder(folder: Path, source: Path | None = None) -> Meeting | None:
    folder = folder.resolve()
    possible = _sessions(folder, nested=True)
    recordings = sorted(p for p in folder.glob("*.mka") if p.is_file() and not p.is_symlink())
    if source is not None:
        possible = [path for path in possible if Path(session.load(path).source).resolve() == source
                    or Path(session.load(path).source).name == source.name]
    path = possible[0] if possible else None
    if source is None and recordings:
        preferred = Path(session.load(path).source).name if path is not None else ""
        source = next((p for p in recordings if p.name == preferred), recordings[0])
    return _meeting(folder, path, source) if path is not None or source is not None else None


def list_meetings(root: Path) -> list[Meeting]:
    if not root.is_dir():
        return []
    meetings = []
    for path in root.iterdir():
        if path.is_dir() and not path.is_symlink() and not path.name.startswith("."):
            found = in_folder(path)
        elif path.suffix.lower() == ".mka" and path.is_file() and not path.is_symlink():
            found = in_folder(root, path.resolve())
        else:
            continue
        if found is not None:
            meetings.append(found)
    return sorted(meetings, key=lambda item: (item.date, item.title.casefold(), str(item.folder)))


def resolve(query: str, root: Path) -> Meeting:
    candidate = Path(query).expanduser().resolve()
    found = None
    if candidate.is_dir():
        found = in_folder(candidate)
    elif candidate.is_file():
        if candidate.suffix.lower() == ".json":
            scope = meeting_folders.find(candidate.parent)
            source = Path(session.load(candidate).source)
            folder = scope[0] if scope else source.resolve().parent if source.is_file() else candidate.parent
            found = _meeting(folder, candidate, source if source.is_file() else None)
        elif candidate.suffix.lower() == ".mka":
            found = in_folder(candidate.parent, candidate)
    if found is not None:
        return found
    wanted = query.strip().casefold()
    matches = [m for m in list_meetings(root) if wanted and (
        wanted in m.title.casefold() or m.date.isoformat(timespec="seconds").startswith(wanted)
        or wanted in m.folder.name.casefold())]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise Failure(_("No meeting matched “{query}”.").format(query=query))
    candidates = "\n".join(f"{item.date:%Y-%m-%d %H:%M}  {item.title}  {jobs.full_path(item.folder)}" for item in matches)
    raise Failure(_("More than one meeting matched “{query}”.").format(query=query), candidates)


def lines(result: jobs.Result) -> list[dict]:
    return [{"start": line.start, "end": line.end, "speaker": line.speaker,
             "name": result.names.get(line.speaker), "text": line.text, "doubts": list(line.doubts)}
            for line in result.segments]


def voices(result: jobs.Result) -> list[dict]:
    found = []
    for voice in speakers.voices(result.segments):
        pieces = timeline.pack((s.start, s.end) for s in result.segments if s.speaker == voice.label)
        proposal = result.proposals.get(voice.label)
        found.append({"label": voice.label, "name": result.names.get(voice.label), "me": voice.label == result.me,
                      "lines": voice.lines, "first": voice.first,
                      "minutes": round(sum(piece.length for piece in pieces) / 60, 6),
                      "proposal": ({"name": proposal.name, "origin": proposal.source,
                                    "heard_seconds": proposal.seconds, "coverage": proposal.share}
                                   if proposal else None)})
    return found


def edit_names(meeting: Meeting, settings, cfg, assignments, cleared, mine) -> jobs.Result:
    first = meeting.open()
    with ExitStack() as stack:
        try:
            stack.enter_context(edits.draft_lease(settings.state, first.source or first.session))
        except OSError as error:
            raise Failure(_("This meeting is being edited in Scriba.")) from error
        result = meeting.open()
        aliases = {alias.casefold(): voice.label for voice in speakers.voices(result.segments)
                   for alias in (voice.label, speakers.shown_label(voice.label),
                                 voice.label.replace("Speaker ", "Parlante "))}

        def label(value):
            try:
                return aliases[value.strip().casefold()]
            except KeyError as error:
                raise Failure(_("Unknown voice “{voice}”.").format(voice=value)) from error

        names = dict(result.names)
        changed = {}
        for item in assignments:
            voice, seen, name = item.partition("=")
            if not seen or not name.strip():
                raise Failure(_("Use --set with a voice and a name, for example “Speaker 1=Ada”."))
            changed[label(voice)] = " ".join(name.split())
        names.update(changed)
        for value in cleared:
            names.pop(label(value), None)
        if mine:
            result.me = label(mine)
        jobs.save_names(result, names, allow_unnamed=True)
        if result.me in changed and result.me in names:
            remembered = speakers.name_to_remember(cfg.my_name, names[result.me])
            if remembered:
                cfg.my_name = remembered
                config.save(cfg, settings.config_file)
        return result
