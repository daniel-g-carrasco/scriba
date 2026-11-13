"""A meeting's own file (.json, next to its outputs): what was transcribed,
how, what came out of it, the names given to the voices and how long each
step took. It is what lets the result page open again tomorrow, and the
names be changed without transcribing again.

The reader preserves optional additions, including the transcript snapshot
written by app/jobs.py. Names given to the voices are kept in the same
meeting file, rewritten whole (save_names); a new transcription of the
same audio moves the previous meeting file and its transcripts to the
folder of the previous revisions (app/jobs.py), never over a file.
The low-level save() below can replace an app-owned file atomically.

Read as app/config.py reads the settings: keys this version does not know
(written by a newer one, or by hand) are kept and written back as they
were; the options are kept whole, whatever they hold. Unlike the settings,
a session has no default to fall back on: a damaged file, or one written
in a newer format, is refused with SessionError, whose text the window can
show as it is. An older format is brought up to this one, a step at a
time (MIGRATIONS).
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scriba.i18n import _

from . import files

FORMAT_VERSION = 1
KIND = "meeting-session"            # what the file says it is: any other JSON is not one
# from one format version to the next, in order: {from_version: step}
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


class SessionError(Exception):
    """The file cannot be opened as a session. str() is the sentence to
    show; `detail` is what went wrong, for the log."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


@dataclass(frozen=True)
class Output:
    kind: str                       # "md", "txt", "srt", "video", "md-named"...
    path: str
    extra: dict = field(default_factory=dict, compare=False, repr=False)


@dataclass
class Session:
    source: str = ""                # the recording, as a full path
    duration: float = 0.0           # seconds of audio
    created: str = ""               # ISO date and time of the transcription
    options: dict = field(default_factory=dict)            # language, model, voices...: kept whole
    outputs: list[Output] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)    # "Speaker 2" -> "Anna"
    timings: dict[str, float] = field(default_factory=dict)  # step -> seconds of work ("transcribe": 312.5)
    # keys of a newer version this one does not know, written back as they were
    extra: dict = field(default_factory=dict, compare=False, repr=False)

    def to_json(self) -> dict:
        own = {
            "kind": KIND,
            "version": FORMAT_VERSION,
            "source": self.source,
            "duration": self.duration,
            "created": self.created,
            "options": self.options,
            "outputs": [{**o.extra, "kind": o.kind, "path": o.path} for o in self.outputs],
            "names": self.names,
            "timings": self.timings,
        }
        return {**self.extra, **own}


_OWN = ("kind", "version", "source", "duration", "created", "options", "outputs", "names", "timings")


def _number(value) -> bool:
    return value.__class__ in (int, float) and math.isfinite(value)


def _text_map(value, what: str) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{what}: not a map of texts")
    return value


def from_json(data) -> Session:
    """The session in `data` (a format of this version: see migrate).
    ValueError when it is not one, with the reason."""
    if not isinstance(data, dict) or data.get("kind") != KIND:
        raise ValueError("not a session")
    source, created = data.get("source", ""), data.get("created", "")
    if not isinstance(source, str) or not isinstance(created, str):
        raise ValueError("source or created: not text")
    duration = data.get("duration", 0.0)
    if not _number(duration) or duration < 0:
        raise ValueError(f"duration: {duration!r}")
    options = data.get("options", {})
    if not isinstance(options, dict):
        raise ValueError("options: not a map")
    listed = data.get("outputs", [])
    if not isinstance(listed, list):
        raise ValueError("outputs: not a list")
    outputs = []
    for item in listed:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) or not isinstance(item.get("path"), str):
            raise ValueError(f"outputs: {item!r}")
        outputs.append(Output(item["kind"], item["path"], {k: v for k, v in item.items() if k not in ("kind", "path")}))
    names = _text_map(data.get("names", {}), "names")
    timings = data.get("timings", {})
    if not isinstance(timings, dict) or not all(isinstance(k, str) and _number(v) for k, v in timings.items()):
        raise ValueError("timings: not a map of seconds")
    return Session(source, float(duration), created, options, outputs, dict(names),
                   {k: float(v) for k, v in timings.items()},
                   {k: v for k, v in data.items() if k not in _OWN})


def migrate(data: dict) -> dict:
    """An older format brought to this one, one version at a time;
    SessionError for a newer one (an app downgraded, or a colleague's
    newer Scriba): read for what this version knows, it would lose the
    rest on the next save."""
    version = data.get("version")
    if version.__class__ is not int or version < 1:
        raise ValueError(f"format version {version!r}")
    if version > FORMAT_VERSION:
        raise SessionError(_("This meeting was saved by a newer version of the app (format {found}; this one "
                             "reads up to {known}): update the app to open it.").format(found=version,
                                                                                       known=FORMAT_VERSION),
                           f"format version {version}")
    while version < FORMAT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValueError(f"no migration from format version {version}")
        data = step(data)
        version += 1
        data["version"] = version
    return data


def load(path: str | os.PathLike) -> Session:
    """The session in `path`. SessionError, with a sentence to show, when
    it cannot be read, is damaged, or is of a newer format."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise SessionError(_("The meeting file could not be read: {reason}").format(reason=e.strerror or e),
                           f"{type(e).__name__}: {e}") from e
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("kind") != KIND:
            raise ValueError("not a session")       # before the version: another app's newer file is not ours
        return from_json(migrate(data))
    except SessionError:
        raise
    except (TypeError, ValueError) as e:            # bad JSON and bad UTF-8 are ValueErrors too
        raise SessionError(_("The meeting file is damaged and cannot be opened: {name}").format(name=path.name),
                           f"{type(e).__name__}: {e}") from e


def dumps(session: Session) -> str:
    # allow_nan=False: a time that is not a number fails here, at the save,
    # rather than as a damaged file at the next opening
    return json.dumps(session.to_json(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def save(session: Session, path: str | os.PathLike) -> None:
    """The session in `path`, whole or not at all. The first time, the
    caller takes a free name (files.free_name) so as not to replace another
    meeting's file."""
    files.replace(path, dumps(session))
