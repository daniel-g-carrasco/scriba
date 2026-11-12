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
from scriba.core.activity import Activities  # noqa: E402
from scriba.i18n import _  # noqa: E402


activities = Activities()


def run_in_thread(work: Callable, done: Callable, *, activities: Activities = activities) -> None:
    """work() in a thread, done(result, error) back on the main loop. The
    window never does the disk's or the network's work itself: a file of
    gigabytes or a server that does not answer would stop it.

    The process registry owns work through the end of its callback, including
    early returns and exceptions. An explicit scope can isolate another owner.
    Call from the main loop so registry observers can update their views there.
    """
    ticket = activities.acquire()
    completed = False

    def complete(result, error):
        nonlocal completed
        if completed:
            return False
        completed = True
        try:
            done(result, error)
        finally:
            activities.finish(ticket)
        return False

    def target():
        try:
            result, error = work(), None
        except BaseException as e:  # a worker exit must also reach its owner and release its ticket
            result, error = None, e
        GLib.idle_add(complete, result, error)

    try:
        threading.Thread(target=target, daemon=True).start()
    except BaseException:
        activities.finish(ticket)
        raise


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
    _desktop_file(window, path, reveal=True)


def open_path(window, path: str) -> None:
    """The file or folder with the default app."""
    _desktop_file(window, path, reveal=False)


def dialog_cancelled(error: GLib.Error) -> bool:
    return (error.matches(Gtk.DialogError.quark(), Gtk.DialogError.CANCELLED) or
            error.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED) or
            error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED))


def _desktop_file(window, path: str, reveal: bool) -> None:
    """Validate outside the UI thread, then finish the desktop's async call.

    Windows' ShellExecute can keep an error dialog open for a missing file;
    checking first lets Scriba report the problem itself without a silent wait.
    """
    def prepare():
        native = os.path.abspath(path)
        if reveal:
            return (native, True) if os.path.lexists(native) else (nearest_folder(native), False)
        os.stat(native)
        return native, False

    def failed(error):
        if isinstance(error, GLib.Error) and dialog_cancelled(error):
            return
        report("opening a file in the desktop", error)
        detail = error.message if isinstance(error, GLib.Error) else str(error)
        window.toast(_("The file or folder could not be opened: {error}").format(error=detail))

    def prepared(result, error):
        if error is not None:
            failed(error)
            return
        native, containing = result
        def done(_launcher, answer, *_data):
            try:
                if not finish(answer):
                    raise OSError(_("The desktop did not confirm the operation."))
            except (GLib.Error, OSError) as error:
                failed(error)

        try:
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(native))
            finish = launcher.open_containing_folder_finish if containing else launcher.launch_finish
            if containing:
                launcher.open_containing_folder(window, None, done, None)
            else:
                launcher.launch(window, None, done, None)
        except GLib.Error as error:
            failed(error)
    run_in_thread(prepare, prepared)


def file_dialog_done(window, finish_method: str, chosen: Callable[[str], None]) -> Callable:
    """Complete a file/folder selection; only actual cancellation is silent."""
    def done(dialog, result, *_data):
        try:
            file = getattr(dialog, finish_method)(result)
        except GLib.Error as error:
            if not dialog_cancelled(error):
                report("choosing a file or folder", error)
                window.toast(_("The file or folder could not be selected: {error}").format(error=error.message))
            return
        if file is not None:
            path = file.get_path()
            if path:
                chosen(path)
            else:
                window.toast(_("Choose a file or folder on this computer."))
    return done


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


def choose_folder(window, title: str, initial: str | None, chosen: Callable[[str], None],
                  *, owner_widget=None) -> None:
    """Choose a folder, owned by the actual initiating dialog's native window.

    GTK 4.24.1 on Windows sends escaped URIs to SHCreateItemFromParsingName,
    which fails for accented paths. The native adapter uses UTF-16 paths.
    """
    from scriba import WINDOWS
    owner_widget = owner_widget if owner_widget is not None else window
    owner = owner_widget.get_native()
    if WINDOWS:
        _choose_folder_windows(window, owner_widget, owner, title, initial, chosen)
        return
    dialog = Gtk.FileDialog(title=title, modal=True)
    if initial and os.path.isdir(initial):
        dialog.set_initial_folder(Gio.File.new_for_path(initial))
    dialog.select_folder(owner, None, file_dialog_done(window, "select_folder_finish", chosen))


def _choose_folder_windows(window, owner_widget, owner, title, initial, chosen) -> None:
    from scriba.app.win32 import choose_folder_native
    from scriba.gui.winquirks import hwnd_of
    if getattr(window, "_folder_picker_pending", False) or getattr(window.app, "closing", False):
        return
    cancelled = threading.Event()
    timer = None

    def failed(error):
        report("choosing a folder", error)
        window.toast(_("The file or folder could not be selected: {error}").format(error=str(error)))

    try:
        hwnd = hwnd_of(owner) if owner is not None else 0
        if not hwnd:
            raise OSError("The folder picker has no native owner window.")
    except (OSError, ValueError, AttributeError) as error:
        failed(error)
        return
    window._folder_picker_pending = True

    def watch_owner():
        if getattr(window.app, "closing", False) or not owner_widget.get_mapped():
            cancelled.set()
            return False
        return True

    def done(path, error):
        window._folder_picker_pending = False
        # The timer may have removed itself when cancellation was requested.
        if timer is not None and GLib.MainContext.default().find_source_by_id(timer) is not None:
            GLib.source_remove(timer)
        if cancelled.is_set() or getattr(window.app, "closing", False):
            return
        if error is not None:
            failed(error)
        elif path:
            chosen(path)

    timer = GLib.timeout_add(100, watch_owner)
    try:
        run_in_thread(lambda: choose_folder_native(title, initial, hwnd, cancelled), done)
    except BaseException:
        window._folder_picker_pending = False
        GLib.source_remove(timer)
        raise
