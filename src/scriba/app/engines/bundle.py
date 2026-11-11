"""Where the engines' programs and libraries are found: whisper-cli and
whisper-bench, ffmpeg and ffprobe, onnxruntime's library.

- The frozen build (PyInstaller, Windows) carries them beside its own
  libraries, in the folder PyInstaller unpacks to (`_internal`, which is
  sys._MEIPASS): there and only there. The engines' DLLs are in the same
  folder, so a program finds them without a PATH, and whisper's ggml
  loads its card and processor back ends from its own folder. The
  environment is not asked: neither the developer's variables below nor
  the PATH, which on a developer's computer leads to MSYS2's programs
  (the template's rule: what any program of the user's can set does not
  choose the app's engines).
- From the sources: the developer's variable for that engine when it is
  set (a program elsewhere), else the engines built in the checkout
  (`build/ffmpeg-lgpl/bin`, made by tools/build_ffmpeg.sh: the ffmpeg that
  the Windows build carries), else the PATH (MSYS2's packages).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from scriba import PACKAGE_DIR

FROZEN = bool(getattr(sys, "frozen", False))
# engines built from their sources in the checkout, ahead of the PATH (tools/build_ffmpeg.sh)
LOCAL_BUILDS = (PACKAGE_DIR.parent.parent / "build" / "ffmpeg-lgpl" / "bin",)


def folder() -> Path | None:
    """The frozen build's own folder of libraries and engines; None from the sources."""
    if not FROZEN:
        return None
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" and not name.endswith(".exe") else name


def program(name: str, variable: str = "", sibling_of: str = "") -> Path | None:
    """The program `name` ("ffmpeg", "whisper-cli"...), as the docstring
    says; None when there is none. `variable` names the developer's
    variable for it; `sibling_of`, when given, means the variable names that
    other program and `name` is looked for next to it (ffprobe beside the
    variable's ffmpeg)."""
    bundled = folder()
    if bundled is not None:
        found = bundled / _exe(name)
        return found if found.is_file() else None
    given = os.environ.get(variable) if variable else None
    if given:
        candidate = Path(given).with_name(name + Path(given).suffix) if sibling_of else Path(given)
        return candidate if candidate.is_file() else None
    for build in LOCAL_BUILDS:
        if (build / _exe(name)).is_file():
            return build / _exe(name)
    found = shutil.which(name)
    return Path(found) if found else None


def library(names, variable: str = "", extra=()) -> Path | None:
    """A shared library under one of `names`: in the frozen build its own
    folder only; from the sources the variable, then `extra` folders (the
    Python's own, where MSYS2 keeps its DLLs), then the PATH."""
    bundled = folder()
    if bundled is not None:
        return next((bundled / n for n in names if (bundled / n).is_file()), None)
    given = os.environ.get(variable) if variable else None
    if given:
        return Path(given) if os.path.isfile(given) else None
    # Flatpak installs Python under /app/share/<slug>/<package> and the
    # C libraries under /app/lib; these are not directories on PATH.
    folders = ([PACKAGE_DIR.parents[2] / "lib"]
               if PACKAGE_DIR.parent.parent.name == "share" else [])
    folders += [Path(p) for p in extra]
    folders += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for place in folders:
        for name in names:
            if (place / name).is_file():
                return place / name
    return None
