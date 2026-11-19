# SPDX-License-Identifier: GPL-3.0-or-later
"""The settings' mechanism: a JSON file read whatever its state, written
whole or not at all, in sections that each have their version.

The module knows no setting. An app gives it a dataclass of fields with
their defaults, a format version and the migrations (app/config.py), and
gets load(), save() and adopt():

    @dataclass
    class Config(settings.Fields):
        theme: str = "system"
        last_folder: str = ""

    STORE = settings.Store(Config, version=CONFIG_VERSION, migrations=MIGRATIONS)
    cfg = STORE.load(path); cfg.theme = "dark"; STORE.save(cfg, path)

No secret is ever here: a password lives in the keyring.

The file. The app's own section is the root of the JSON object, with its
"version": the file every app has always written. Any other section (a
graphic kit's, an assistant's) is an object under its name, with a
"version" of its own, so that it changes format without the app's version
moving, and is the same in every app:

    {"theme": "dark", "last_folder": "", "kit": {"accent": "", "version": 1}, "version": 1}

A field's default gives the type the file must hold: bool, int, float
(finite; a whole number is taken), str, list, dict; metadata={"check": f}
on the field adds a check of its own (list_of, table_of). A section is a
field whose default_factory is the section's dataclass, named in
Store(sections={...}).

Reading (load). A file that is gone is replaced by the previous save
(<name>.bak); a damaged one (bad JSON, a value of another type, a version
with no migration) is set aside as <name>.broken-<time> and the previous
save is used, or the defaults. An older format goes through the section's
migrations, one version at a time. The keys this version does not know
are kept and written back as they were. What had to be done is told in
cfg.notices (Notice).

A file of a newer version (the app was downgraded), section by section:
what this version knows is read and the rest is kept; a known field that
holds another type is not damage (the newer version may have changed its
shape): the default stands for this run, and the file's value is written
back unless the user changes that very setting. The file keeps its newer
version number when it is written back: with this version's lower number
the newer app would run its migrations again over data that is already
in its format.

Writing (save). Through data/atomic.py: a temporary file, fsync, a
rename that is patient on Windows; the file as it was stays as
<name>.bak. A setting this object did not change since it read or wrote
the file is never taken back to its older value: the file is read again,
and what another holder of the same file wrote meanwhile (a second
window, a part of the app that kept a copy of the settings) is taken from
the disk, key by key, also inside a section; the object in hand is
brought up to date. When both changed the same key, the last save wins.

A copy of the settings is made with copy.deepcopy, which carries the
unknown keys and what the object saw on the disk; dataclasses.replace
does not.
"""

from __future__ import annotations

import copy
import json
import math
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import MISSING, dataclass, fields, is_dataclass
from pathlib import Path

from ..data import atomic

VERSION = "version"
_STATE = "_settings_state"
_STORE = "__settings_store__"
_ABSENT = object()


@dataclass(frozen=True)
class Notice:
    """What load() had to do: "recovered" (the file was damaged: the previous
    save was used; `detail` is where the damaged file was put), "fresh" (damaged
    and nothing to recover: the app starts anew; `detail` as above), "missing"
    (the file was gone: the previous save was used), "newer" (saved by a
    newer version; `detail` is its format version, `section` the section's
    name, "" for the app's own)."""
    kind: str
    detail: str = ""
    reason: str = ""
    section: str = ""


class Damaged(Exception):
    """The file is there and is not a configuration."""


def backup_file(path: Path) -> Path:
    return Path(path).with_name(Path(path).name + ".bak")


# ---- what the mechanism remembers about an object of fields ----------------

class _State:
    def __init__(self):
        self.extra: dict = {}           # keys this version does not know, written back as they were
        self.held: dict = {}            # a newer version's values of known fields this one cannot read
        self.notices: list = []         # what load() had to do; never saved
        self.file_version = 0           # the file's format version when it is newer than this one's
        self.on_disk = None             # (path, data): what this object read from, or last wrote to, the file


def _state(obj) -> _State:
    state = obj.__dict__.get(_STATE)
    if state is None:
        state = obj.__dict__[_STATE] = _State()
    return state


class Fields:
    """The base of an app's dataclass of settings (optional: it gives
    cfg.notices, cfg.extra, cfg.to_json() and Config.from_json())."""

    @property
    def notices(self) -> list:
        return _state(self).notices

    @notices.setter
    def notices(self, value) -> None:
        _state(self).notices = list(value)

    @property
    def extra(self) -> dict:
        return _state(self).extra

    def to_json(self) -> dict:
        return _store_of(type(self)).to_json(self)

    @classmethod
    def from_json(cls, data):
        """ValueError or TypeError when `data` is not a configuration."""
        return _store_of(cls).from_json(data)


def _store_of(cls) -> "Store":
    store = cls.__dict__.get(_STORE)
    if store is None:
        raise TypeError(f"no settings.Store was made for {cls.__name__}")
    return store


# ---- checks ----------------------------------------------------------------

def list_of(kind: type) -> Callable:
    """A check for a field that is a list of `kind` (str, int...)."""
    def check(value) -> None:
        if value.__class__ is not list or any(v.__class__ is not kind for v in value):
            raise ValueError(f"is not a list of {kind.__name__}")
    return check


def table_of(kind: type) -> Callable:
    """A check for a field that is a table (a dict) with text keys and
    values of `kind`."""
    def check(value) -> None:
        if value.__class__ is not dict or any(k.__class__ is not str or v.__class__ is not kind
                                              for k, v in value.items()):
            raise ValueError(f"is not a table of {kind.__name__}")
    return check


_KINDS = {bool: "a switch", int: "a number", float: "a number", str: "text", list: "a list", dict: "a table"}


def _check(name: str, kind: type | None, check, value) -> None:
    """ValueError when `value` is not what the field holds: such a file is
    damaged, and the previous save is a better start than a crash later."""
    wrong = False
    if kind is float:
        wrong = value.__class__ not in (int, float) or not math.isfinite(value)
    elif kind in _KINDS:
        wrong = value.__class__ is not kind
    if wrong:
        raise ValueError(f"{name}: {value!r} is not {_KINDS[kind]}")
    if check is not None:
        try:
            check(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name}: {value!r} {error}") from None


def move_to_section(data: dict, section: str, names: Iterable[str], version: int = 1) -> dict:
    """For a migration of the app's own section: the keys `names` leave
    the root and go into the section `section` (an object made with
    `version` when it is not there yet). A key the section already holds
    is left as it is there."""
    target = data.get(section)
    if not isinstance(target, dict):
        target = data[section] = {VERSION: version}
    for name in names:
        if name in data:
            value = data.pop(name)
            target.setdefault(name, value)
    return data


# ---- a section -------------------------------------------------------------

class Section:
    """A group of fields with its own format version and migrations.
    `migrations` is {from_version: step}, each step taking the section's
    dict to the next version; the dict given is kept (not copied), so an
    app may fill it after."""

    def __init__(self, fields_class: type, version: int = 1, migrations: dict | None = None,
                 sections: dict | None = None):
        if not (isinstance(fields_class, type) and is_dataclass(fields_class)):
            raise TypeError("the fields of a section are a dataclass")
        self.fields_class, self.version = fields_class, version
        self.migrations = migrations if migrations is not None else {}
        self.sections: dict[str, Section] = dict(sections or {})
        self.kinds: dict[str, type | None] = {}
        self.checks: dict[str, Callable | None] = {}
        self.defaults: dict[str, Callable] = {}
        for f in fields(fields_class):
            if f.name == VERSION:
                raise ValueError(f"{fields_class.__name__}: no setting can be named {VERSION!r}")
            if f.default is MISSING and f.default_factory is MISSING:
                raise ValueError(f"{fields_class.__name__}.{f.name}: a setting has a default")
            make = f.default_factory if f.default_factory is not MISSING else (lambda value=f.default: value)
            self.defaults[f.name] = make
            if f.name in self.sections:
                if f.default_factory is not self.sections[f.name].fields_class:
                    raise ValueError(f"{fields_class.__name__}.{f.name}: the field of a section is "
                                     f"field(default_factory={self.sections[f.name].fields_class.__name__})")
                continue
            kind = make().__class__
            self.kinds[f.name] = kind if kind in _KINDS else None
            self.checks[f.name] = f.metadata.get("check")
        missing = sorted(set(self.sections) - {f.name for f in fields(fields_class)})
        if missing:
            raise ValueError(f"{fields_class.__name__}: no field for the section {', '.join(missing)}")
        for name, section in self.sections.items():
            if section.sections:
                raise ValueError(f"{name}: a section holds no sections of its own")

    def migrate(self, data: dict, version: int, name: str = "") -> dict:
        """An older format brought to this one, one version at a time."""
        while version < self.version:
            step = self.migrations.get(version)
            if step is None:
                raise ValueError(f"no migration from format version {version}" + (f" of {name}" if name else ""))
            data = step(data)
            version += 1
            data[VERSION] = version
        return data

    def read(self, data, notices: list, name: str = ""):
        """The object of fields for `data` (a dict as the file holds it);
        ValueError or TypeError when it is not this section."""
        if not isinstance(data, dict):
            raise ValueError("not a configuration" if not name else f"{name}: not a section")
        version = data.get(VERSION, 1)
        if version.__class__ is not int:
            raise ValueError(f"format version {version!r}" + (f" of {name}" if name else ""))
        data = dict(data)
        if version < self.version:
            data = self.migrate(data, version, name)
        newer = version > self.version
        obj = self.fields_class()
        state = _state(obj)
        state.file_version = version if newer else 0
        for key, value in data.items():
            if key == VERSION:
                continue
            try:
                if key in self.sections:
                    setattr(obj, key, self.sections[key].read(value, notices, key))
                elif key in self.kinds:
                    _check(key, self.kinds[key], self.checks[key], value)
                    setattr(obj, key, value)
                else:
                    state.extra[key] = value
            except (TypeError, ValueError):
                if not newer:
                    raise
                state.held[key] = value         # a newer format's value: the default now, the value written back
        if newer:
            notices.append(Notice("newer", str(version), section=name))
        return obj

    def write(self, obj) -> dict:
        """The dict the file holds for `obj`: the unknown keys first, as
        they were, then the fields, then the version."""
        state = _state(obj)
        out = dict(state.extra)
        for f in fields(obj):
            value = getattr(obj, f.name)
            held = f.name in state.held and self._is_default(f.name, value)
            if held:
                out[f.name] = state.held[f.name]
            elif f.name in self.sections:
                out[f.name] = self.sections[f.name].write(value)
            else:
                out[f.name] = copy.deepcopy(value)
        out[VERSION] = max(self.version, state.file_version)
        return out

    def _is_default(self, name: str, value) -> bool:
        return value == self.defaults[name]()

    def adopt(self, obj, saved) -> None:
        """`obj` brought to `saved`: only the settings that differ are
        set, so the others keep their objects."""
        for f in fields(obj):
            if f.name in self.sections:
                self.sections[f.name].adopt(getattr(obj, f.name), getattr(saved, f.name))
            elif getattr(obj, f.name) != getattr(saved, f.name):
                setattr(obj, f.name, copy.deepcopy(getattr(saved, f.name)))
        mine, theirs = _state(obj), _state(saved)
        mine.extra, mine.held, mine.file_version = dict(theirs.extra), dict(theirs.held), theirs.file_version

    def merged(self, data: dict, before: dict, now: dict) -> dict:
        """`data` (what this holder writes) with, for every key it did not
        change since `before`, the value the file holds `now`. A section
        of another format on the disk is not merged."""
        if now.get(VERSION, 1) != data.get(VERSION):
            return data
        out = dict(data)
        for key, value in now.items():
            if key == VERSION:
                continue
            mine, seen = data.get(key, _ABSENT), before.get(key, _ABSENT)
            if key in self.sections and all(isinstance(x, dict) for x in (mine, seen, value)):
                out[key] = self.sections[key].merged(mine, seen, value)
            elif mine == seen and value != seen:
                out[key] = value
        return out


# ---- the file --------------------------------------------------------------

def _set_aside(path: Path) -> Path:
    """The damaged file, kept next to the settings under another name."""
    aside = path.with_name(path.name + time.strftime(".broken-%Y%m%d-%H%M%S"))
    try:
        os.replace(path, aside)
    except OSError:
        pass
    return aside


def _same(path) -> str:
    return os.path.normcase(os.path.abspath(path))


class Store(Section):
    """The app's own section, the sections it holds, and their file."""

    def __init__(self, fields_class: type, version: int = 1, migrations: dict | None = None,
                 sections: dict | None = None):
        super().__init__(fields_class, version, migrations, sections)
        setattr(fields_class, _STORE, self)

    def from_json(self, data, notices: list | None = None):
        """ValueError or TypeError when `data` is not a configuration."""
        return self.read(data, [] if notices is None else notices)

    def to_json(self, cfg) -> dict:
        return self.write(cfg)

    def _read_file(self, path: Path):
        """The settings in `path` and the notices of its sections;
        FileNotFoundError, or Damaged with the reason."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as e:          # bad JSON and bad UTF-8 are ValueErrors
            raise Damaged(f"{type(e).__name__}: {e}") from e
        notices: list[Notice] = []
        try:
            return self.read(data, notices), notices
        except (TypeError, ValueError) as e:
            raise Damaged(f"{type(e).__name__}: {e}") from e

    def peek(self, path: Path):
        """Read settings for early startup without writing or setting files aside.

        Use load() before editing or saving settings.
        """
        path = Path(path)
        for candidate in (path, backup_file(path)):
            try:
                cfg, _notices = self._read_file(candidate)
                return cfg
            except (FileNotFoundError, Damaged):
                continue
        return self.fields_class()

    def load(self, path: Path):
        """The settings, whatever the file's state (see the module's
        docstring); cfg.notices says what had to be done."""
        path = Path(path)
        notices: list[Notice] = []
        newer: list[Notice] = []
        recovered = False
        try:
            cfg, newer = self._read_file(path)
        except FileNotFoundError:
            try:
                cfg, newer = self._read_file(backup_file(path))
                notices.append(Notice("missing"))
                recovered = True
            except (FileNotFoundError, Damaged):
                cfg = self.fields_class()
        except Damaged as e:
            aside = _set_aside(path)
            try:
                cfg, newer = self._read_file(backup_file(path))
                notices.append(Notice("recovered", str(aside), str(e)))
                recovered = True
            except (FileNotFoundError, Damaged):
                cfg = self.fields_class()
                notices.append(Notice("fresh", str(aside), str(e)))
        if not path.exists() or recovered:
            try:
                self.save(cfg, path)
            except OSError:
                pass
        else:
            _state(cfg).on_disk = (_same(path), self.write(cfg))
        _state(cfg).notices = notices + newer
        return cfg

    def save(self, cfg, path: Path) -> None:
        """Atomic: the new file is complete before it takes the old one's
        place, and the old one stays as the backup. A setting this object
        did not change is never taken back to its older value."""
        path = Path(path)
        data = self._with_the_disk(cfg, path, self.write(cfg))
        atomic.write_json(path, data, indent=2, backup_optional=True)
        _state(cfg).on_disk = (_same(path), data)

    def _with_the_disk(self, cfg, path: Path, data: dict) -> dict:
        seen = _state(cfg).on_disk
        if seen is None or seen[0] != _same(path):
            return data
        try:
            with open(path, encoding="utf-8") as f:
                now = json.load(f)
        except (OSError, ValueError):
            return data
        if not isinstance(now, dict):
            return data
        merged = self.merged(data, seen[1], now)
        if merged == data:
            return data
        try:
            newer = self.read(merged, [])
        except (TypeError, ValueError):
            return data
        self.adopt(cfg, newer)          # the holder too: its next save starts from what is on the disk
        return self.write(cfg)

    def adopt(self, cfg, saved) -> None:
        """`cfg` brought to `saved`, a copy of it that was saved (for who
        saves a copy, so that a save that fails leaves the settings as
        they were): the changes, what the save took from the disk, and
        what the file now holds."""
        super().adopt(cfg, saved)
        if _state(saved).on_disk is not None:
            _state(cfg).on_disk = _state(saved).on_disk
