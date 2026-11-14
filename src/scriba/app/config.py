"""The settings, stored as JSON in the config folder (paths.py).

No secret is ever here: a password lives in the keyring (extras/secrets).
Writes are atomic (temporary file, fsync, rename) so a crash or a full disk
can never leave a half-written file behind, and the file as it was before
stays next to it (config.json.bak): a damaged file, or one that is gone, is
replaced by that previous save rather than by nothing, and what happened is
told (Config.notices). The file carries a format version: an older format
is migrated on reading; a newer one (an app downgraded) is read for what
this version knows and written back whole, the unknown settings kept.

Adding a setting: a field with a default of the right type (bool, int or
str: a value of another type in the file counts as damage). Renaming or
changing one: a migration step in MIGRATIONS and CONFIG_VERSION + 1.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

CONFIG_VERSION = 1
# from one format version to the next, in order: {from_version: step}
MIGRATIONS: dict[int, Callable[[dict], dict]] = {}


@dataclass
class Config:
    theme: str = "system"                # "system", "light", "dark"
    language: str = ""                   # "" = the desktop's; "en", "it"
    keep_running: bool = True            # keep working when the window is closed, if the desktop supports it
    models_download_paused: bool = False # a deliberate pause survives the next launch
    check_updates: bool = True           # Windows: a newer release, at start and when the window comes back
    # ISO date and time of the last check made on its own (updates.check_stamp;
    # a date alone, as 0.1.2 wrote it, is read as its midnight)
    last_update_check: str = ""
    last_folder: str = ""                # the folder of the last recording opened, for the next file dialog
    # the transcription's choices, remembered from one to the next
    speech_language: str = "it"          # "it", "en", "auto"
    quality: str = "turbo"               # a key of models.WHISPER: "turbo" (fast), "large" (precise)
    speakers: bool = True                # tell the voices apart
    speaker_count: int = 0               # how many voices; 0: found by the engine
    subtitled_video: bool = False        # a copy of a video with the subtitles inside
    my_name: str = "Io"                 # the microphone's name
    speaker_names: str = ""             # previously used names, one per line
    terms: str = ""                      # names and acronyms, separated by commas
    term_files: str = ""                 # files of terms read at every transcription, one path a line, in order
    output_folder: str = ""              # "": next to the recording
    # with no output_folder, a file outside the recordings' folder: "scriba",
    # a folder of its own there with a link to the file (jobs.home_folder);
    # "beside", next to the file
    elsewhere: str = "scriba"
    device: str = ""                     # where whisper works: "" the best measured, "cpu", or a card's name
    measured_device: str = ""            # what the test of the cards chose: "" not tested yet, "cpu", a card
    # the recorder's choices
    recordings_folder: str = ""          # "": a Scriba folder in the user's Music (gui/ui.py, recordings_folder)
    record_input: bool = True            # the microphone
    record_output: bool = True           # the computer's sound (the others in a call)
    input_device: str = ""               # "": chosen by the app (core/devices.py); else the device's id
    output_device: str = ""              # the same for the output
    follow_call_mute: bool = True        # Windows 11: the microphone's speech not transcribed while muted in the call
    # settings of a newer version this one does not know, written back as they were
    extra: dict = field(default_factory=dict, compare=False, repr=False)
    # what load() had to do to read the file (Notice); never saved
    notices: list = field(default_factory=list, compare=False, repr=False)

    def term_file_list(self) -> list[str]:
        """The files of terms, in their order, each once."""
        return list(dict.fromkeys(line.strip() for line in self.term_files.splitlines() if line.strip()))

    def set_term_files(self, paths) -> None:
        self.term_files = "\n".join(dict.fromkeys(str(p).strip() for p in paths if str(p).strip()))

    def to_json(self) -> dict:
        data = asdict(self)
        extra = data.pop("extra")
        del data["notices"]
        out = {**extra, **data}
        out["version"] = CONFIG_VERSION
        return out

    @classmethod
    def from_json(cls, data) -> "Config":
        """ValueError or TypeError when `data` is not a configuration."""
        if not isinstance(data, dict):
            raise ValueError("not a configuration")
        own = {"extra", "notices", "version"}
        known = {f.name for f in fields(cls)} - own
        cfg = cls(**_typed(cls, {k: v for k, v in data.items() if k in known}))
        cfg.extra = {k: v for k, v in data.items() if k not in known and k not in own}
        return cfg


def _typed(cls, kwargs: dict) -> dict:
    """ValueError when a value has not the type of the field's default (a
    number for a switch, a word for a number): such a file is damaged, and
    the previous save is a better start than a crash later on."""
    for f in fields(cls):
        if f.name not in kwargs or f.default is None or f.default.__class__ not in (bool, int, str):
            continue
        value = kwargs[f.name]
        if f.default.__class__ is bool and value.__class__ is not bool:
            raise ValueError(f"{f.name}: {value!r} is not a switch")
        if f.default.__class__ is int and value.__class__ is not int:
            raise ValueError(f"{f.name}: {value!r} is not a number")
        if f.default.__class__ is str and value.__class__ is not str:
            raise ValueError(f"{f.name}: {value!r} is not text")
    return kwargs


@dataclass(frozen=True)
class Notice:
    """What load() had to do: "recovered" (the file was damaged: the previous
    save was used; `detail` is where the damaged file was put), "fresh" (damaged
    and nothing to recover: the app starts anew; `detail` as above), "missing"
    (the file was gone: the previous save was used), "newer" (saved by a
    newer version; `detail` is its format version)."""
    kind: str
    detail: str = ""
    reason: str = ""


class Damaged(Exception):
    """The file is there and is not a configuration."""


def backup_file(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def migrate(data: dict) -> dict:
    """An older format brought to this one, one version at a time."""
    version = data.get("version", 1)
    if version.__class__ is not int:
        raise ValueError(f"format version {version!r}")
    while version < CONFIG_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValueError(f"no migration from format version {version}")
        data = step(data)
        version += 1
        data["version"] = version
    return data


def _read(path: Path) -> tuple[Config, int]:
    """The configuration in `path` and its format version; FileNotFoundError,
    or Damaged with the reason."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as e:          # bad JSON and bad UTF-8 are ValueErrors
        raise Damaged(f"{type(e).__name__}: {e}") from e
    try:
        if not isinstance(data, dict):
            raise ValueError("not a configuration")
        version = data.get("version", 1)
        return Config.from_json(migrate(data)), version if version.__class__ is int else 1
    except (TypeError, ValueError) as e:
        raise Damaged(f"{type(e).__name__}: {e}") from e


def _set_aside(path: Path) -> Path:
    """The damaged file, kept next to the settings under another name."""
    aside = path.with_name(path.name + time.strftime(".broken-%Y%m%d-%H%M%S"))
    try:
        os.replace(path, aside)
    except OSError:
        pass
    return aside


def load(path: Path) -> Config:
    """The settings, whatever the file's state (see the module's docstring);
    cfg.notices says what had to be done."""
    notices: list[Notice] = []
    version = CONFIG_VERSION
    recovered = False
    try:
        cfg, version = _read(path)
    except FileNotFoundError:
        try:
            cfg, version = _read(backup_file(path))
            notices.append(Notice("missing"))
            recovered = True
        except (FileNotFoundError, Damaged):
            cfg = Config()
    except Damaged as e:
        aside = _set_aside(path)
        try:
            cfg, version = _read(backup_file(path))
            notices.append(Notice("recovered", str(aside), str(e)))
            recovered = True
        except (FileNotFoundError, Damaged):
            cfg = Config()
            notices.append(Notice("fresh", str(aside), str(e)))
    if version > CONFIG_VERSION:
        notices.append(Notice("newer", str(version)))
    if not path.exists() or recovered:
        try:
            save(cfg, path)
        except OSError:
            pass
    cfg.notices = notices
    return cfg


def save(cfg: Config, path: Path) -> None:
    """Atomic: the new file is complete before it takes the old one's place,
    and the old one stays as the backup (a hard link where the disk allows,
    a copy elsewhere: never a moment without a settings file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg.to_json(), f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        _keep_previous(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _keep_previous(path: Path) -> None:
    bak = backup_file(path)
    try:
        if not path.exists():
            return
        bak.unlink(missing_ok=True)
        try:
            os.link(path, bak)
        except OSError:
            shutil.copyfile(path, bak)
    except OSError:
        pass                    # a backup that cannot be made must not stop the save
