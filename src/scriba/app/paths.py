"""Where the app keeps its own files.

Inside the Flatpak the XDG variables already point to ~/.var/app/<app-id>/,
outside they are the usual ~/.config, ~/.local/state and ~/.local/share. On
Windows, %APPDATA%\\<App> for the settings (they roam with the profile),
%LOCALAPPDATA%\\<App> for the rest (it belongs to this computer). Nothing
that matters lives in ~/.cache: a cache cleaner may throw it away at any
time, so only what can be made again goes there (the speech models, which
are downloaded again and checked against their SHA-256).

The module knows no app: from_env() is told the two names the folders
take (app/services.py gives this app's).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _xdg(var: str, fallback: str, environ) -> Path:
    value = environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path(environ.get("HOME", os.path.expanduser("~"))) / fallback


@dataclass(frozen=True)
class Paths:
    config: Path   # settings
    state: Path    # what the app remembers between runs (the window's size, logs)
    data: Path     # what the user would miss (documents the app keeps, backups)
    cache: Path | None = None   # what can be made again (downloads); None: next to state

    @classmethod
    def from_env(cls, slug: str, exe_name: str, environ=None) -> "Paths":
        """`slug` names the folders on Linux (~/.config/<slug>), `exe_name`
        the ones on Windows (%APPDATA%\\<ExeName>)."""
        environ = os.environ if environ is None else environ
        if os.name == "nt" and not environ.get("XDG_CONFIG_HOME"):
            # (the XDG variables still win on Windows, for the tests)
            roaming = Path(environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            local = Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            return cls(config=roaming / exe_name, state=local / exe_name / "state", data=local / exe_name / "data",
                       cache=local / exe_name / "cache")
        return cls(
            config=_xdg("XDG_CONFIG_HOME", ".config", environ) / slug,
            state=_xdg("XDG_STATE_HOME", ".local/state", environ) / slug,
            data=_xdg("XDG_DATA_HOME", ".local/share", environ) / slug,
            cache=_xdg("XDG_CACHE_HOME", ".cache", environ) / slug,
        )

    @property
    def config_file(self) -> Path:
        return self.config / "config.json"

    @property
    def window_state_file(self) -> Path:
        return self.state / "window.json"

    @property
    def cache_folder(self) -> Path:
        """The cache, or a folder inside the state for Paths made by hand
        (the tests) without one."""
        return self.cache if self.cache is not None else self.state / "cache"


def nearest_folder(path: str) -> str:
    """The closest folder above `path` that exists, for a file that is gone."""
    folder = os.path.dirname(path)
    while not os.path.isdir(folder) and folder != os.path.dirname(folder):
        folder = os.path.dirname(folder)
    return folder


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")
