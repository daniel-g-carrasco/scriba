"""What the window and the dialogs share: threads that report back on
the main loop, the style sheets, markup escaping, a file shown in the
file manager, the toolkit's versions for a report."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from scriba.app.paths import nearest_folder  # noqa: E402


def run_in_thread(work: Callable, done: Callable) -> None:
    """work() in a thread, done(result, error) back on the main loop. The
    window never does the disk's or the network's work itself: a file of
    gigabytes or a server that does not answer would stop it."""
    def target():
        try:
            result, error = work(), None
        except Exception as e:  # shown to the user, never swallowed
            result, error = None, e
        GLib.idle_add(lambda: done(result, error) and False)
    threading.Thread(target=target, daemon=True).start()


def toolkit_versions() -> str:
    """One line for a report of a problem (app/diagnostics.py)."""
    return (f"GLib {GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}, "
            f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}, "
            f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}")


def window_colours(widget) -> tuple[tuple[int, int, int], tuple[int, int, int]] | tuple[None, None]:
    """The window's background and text colours as the style has them now,
    each (red, green, blue) 0-255, for what is drawn outside GTK in the
    same colours (Windows' title bar); (None, None) when the style does
    not name them. libadwaita's text colour is translucent: it is given as
    it looks over the background."""
    import warnings
    with warnings.catch_warnings():
        # GTK 4.10 deprecates the style context and gives no other way to
        # read a named colour of the style
        warnings.simplefilter("ignore", DeprecationWarning)
        context = widget.get_style_context()
        has_background, background = context.lookup_color("window_bg_color")
        has_text, text = context.lookup_color("window_fg_color")
    if not (has_background and has_text):
        return None, None
    back = (background.red, background.green, background.blue)
    fore = tuple(t * text.alpha + b * (1 - text.alpha)
                 for t, b in zip((text.red, text.green, text.blue), back, strict=True))
    return tuple(round(c * 255) for c in back), tuple(round(c * 255) for c in fore)


# the style's values as variables (made by tools/make_style.py), then the
# app's own rules, which use them
STYLE_SHEETS = ("style.css", "app.css")


def load_style(display, folder: Path | None = None) -> list[str]:
    """The app's style sheets for every window of the display, above
    libadwaita's. Returns what could not be read, as "app.css:3: ..."; the
    app starts all the same, with libadwaita's looks."""
    folder = folder or Path(__file__).resolve().parent
    refused = []
    for name in STYLE_SHEETS:
        if not (folder / name).is_file():
            refused.append(f"{name}: missing")
            continue
        provider = Gtk.CssProvider()
        provider.connect("parsing-error", lambda _provider, section, error, name=name: refused.append(
            f"{name}:{section.get_start_location().lines + 1}: {error.message}"))
        provider.load_from_path(str(folder / name))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return refused


def escape(text: str) -> str:
    """For every libadwaita title and subtitle: they read Pango markup, and
    a file named "<b>" or "a & b" would break them."""
    return GLib.markup_escape_text(text)


def reveal(window, path: str) -> None:
    """Show the file in the file manager, selected (the OpenURI portal's
    OpenDirectory inside the sandbox); a file that is not there any more
    opens the nearest folder that is."""
    if os.path.lexists(path):
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).open_containing_folder(window, None, None, None)
        return
    Gtk.FileLauncher.new(Gio.File.new_for_path(nearest_folder(path))).launch(window, None, None, None)


def open_path(window, path: str) -> None:
    """The file or folder with the default app."""
    Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(window, None, None, None)


def header_bar() -> Adw.HeaderBar:
    """A page's header bar. On Windows a toolbar under the system title bar,
    which has the window buttons."""
    from scriba import WINDOWS
    if WINDOWS:
        return Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    return Adw.HeaderBar()


def report(where: str, error: BaseException) -> None:
    """A failure the window shows, written to stderr as well: an engine's
    Failure with the engine's own words (its sentence is on screen, and its
    traceback says nothing), anything else with its traceback."""
    from scriba.app.log import complain
    from scriba.app.task import Failure
    if isinstance(error, Failure):
        print(f"{where}: {error} [{error.detail}]", file=sys.stderr, flush=True)
    else:
        complain(where, error)


def documents_folder() -> Path:
    """The user's documents folder, as the desktop names it (Documenti on an
    Italian Windows), or the home when there is none."""
    found = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
    return Path(found) if found else Path.home()


RECORDINGS = "Scriba"       # the recordings' folder in the user's Music


def recordings_folder(cfg) -> Path:
    """Where the recordings go: the folder chosen in the preferences, else
    a Scriba folder in the user's Music (Musica on an Italian Windows):
    they are sound, and the user's own place for sound is there."""
    if cfg.recordings_folder:
        return Path(cfg.recordings_folder)
    found = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    return (Path(found) if found else Path.home() / "Music") / RECORDINGS


def choose_folder(window, title: str, initial: str | None, chosen: Callable[[str], None]) -> None:
    """The file dialog for a folder; chosen(path) when one was."""
    dialog = Gtk.FileDialog(title=title, modal=True)
    if initial and os.path.isdir(initial):
        dialog.set_initial_folder(Gio.File.new_for_path(initial))

    def done(dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:      # dismissed
            return
        if folder is not None and folder.get_path():
            chosen(folder.get_path())
    dialog.select_folder(window, None, done)
