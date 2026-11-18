"""Shared test helpers: put src/ on sys.path, find the fixtures, tell the
platform. Every test module starts with `import _setup`. The tests copied
from the model's comune/ (their files are in impronte/comune.json) ask it
for PACKAGE and shared(name), so that they never name the package."""

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

PACKAGE = "scriba"
WINDOWS = sys.platform == "win32"


def shared(name: str):
    """The shared module of that name ("i18n", "app.updates"), as the
    common tests ask for it: scriba.<name>."""
    return importlib.import_module(f"{PACKAGE}.{name}")


# No test writes in the user's own folders (tests/_userfolders.py, a copy of
# the model's). They are watched as the environment names them now, before
# anything changes it: a run that writes next to the installed Scriba's own
# files fails at its exit. Then the tests get folders of their own: the four
# XDG variables (config, state, data, cache) point into one temporary
# folder, removed at the exit (app/paths.py lets them win on Windows too).
# The toolkit reads them as well, so it shows its own colours and not the
# user's: a desktop that themes GTK through ~/.config/gtk-4.0/gtk.css
# (Noctalia, matugen) would make the tests on colours fail on that computer
# alone. Done here, before GTK starts.
import _userfolders  # noqa: E402
from scriba import EXE_NAME, SLUG  # noqa: E402

_userfolders.watch(SLUG, EXE_NAME)
_userfolders.isolate()

if WINDOWS and os.environ.get("SCRIBA_GSTPLAY_BUILD"):
    # Source CI and its preview subprocesses select the verified DLL before GI.
    sys.path.insert(0, str(ROOT / "tools"))
    from gstplay_bootstrap import preload
    preload()


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def gtk_or_skip():
    """GTK 4 and libadwaita with a display, or unittest.SkipTest: the
    dialog tests run where they can (the CI's xvfb job, Windows), and say
    why not elsewhere. GTK sets the user's locale on init: it is put back,
    so that the other tests read numbers as in C."""
    import locale
    import unittest
    before = locale.setlocale(locale.LC_ALL)
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, Gtk
    except (ImportError, ValueError):
        raise unittest.SkipTest("GTK 4 and libadwaita") from None
    initialised = Gtk.init_check()
    locale.setlocale(locale.LC_ALL, before)
    if not initialised or Gdk.Display.get_default() is None:
        raise unittest.SkipTest("no display")
    Adw.init()
    return Gtk, Adw


def spin(cond, timeout: float = 10.0) -> bool:
    """The main loop run until cond() holds, or the timeout: for what a
    dialog does through GLib.idle_add and threads."""
    import time

    from gi.repository import GLib
    end = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < end:
        if cond():
            return True
        # A busy display (120 Hz on Niri) must not starve idle callbacks
        # by dispatching just one frame every 10 ms. Rest when no event ran.
        if not context.iteration(False):
            time.sleep(0.01)
    return cond()


os.environ.setdefault("LANGUAGE", "en")        # the tests read the source strings
