"""The main window: Record and Transcribe, and the Result pushed on top.

The layout is libadwaita's for an app with a few top-level views and a
detail: an Adw.NavigationView whose first page holds an Adw.ViewStack
(Record first, then Transcribe; the view switcher is the header bar's
title, and in a narrow window an Adw.ViewSwitcherBar at the bottom takes
its place), and the Result as a second page with its back button. The
models' download and the work are states of the Transcribe view, not
windows of their own; a recording, once stopped, opens in Transcribe.

On Windows the main window is a plain GTK window, so that it keeps the
system title bar (an AdwApplicationWindow always draws its own, and with
it Windows does not snap the window to the screen edges, nor on
Win+arrows); its content is libadwaita's all the same, and the dialogs
then open as windows of their own, with the system's title bar too
(application.native_windows gives them the content's colour and the
app's icon).
"""

from __future__ import annotations

import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from scriba import APP_NAME, WINDOWS  # noqa: E402
from scriba.app import errorlog  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .cards import CardTest  # noqa: E402
from .models_page import ModelsPage, progress_text  # noqa: E402
from .record import RecordPage  # noqa: E402
from .result import ResultPage  # noqa: E402
from .style_values import SPACE_S  # noqa: E402
from .transcribe import TranscribePage  # noqa: E402
from .ui import escape, file_dialog_done, header_bar, report, run_in_thread  # noqa: E402
from .ui import open_path as ui_open_path  # noqa: E402

STATE_VERSION = 1       # window.json of another layout is ignored
NARROW = "max-width: 500sp"     # under this the view switcher goes to the bottom of the window
# the kinds of file the dialog offers first; any file can still be chosen
MEDIA_TYPES = ("audio/*", "video/*")
MEDIA_SUFFIXES = ("mkv", "mp4", "m4a", "mp3", "wav", "webm", "ogg", "opus", "flac", "mov", "aac", "wma")

_WindowBase = Gtk.ApplicationWindow if WINDOWS else Adw.ApplicationWindow


class MainWindow(_WindowBase):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=640, default_height=820,
                         width_request=360, height_request=480)
        self.app = app
        self.backend = app.backend
        self._state_file = app.paths.window_state_file
        self._restore_state()
        self.connect("close-request", self._on_close)
        self._closing = False

        self.toasts = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        # the test of the graphics cards: Transcribe starts it once the models
        # are there, the preferences run it again
        self.cards = CardTest(app, self.backend)
        self.transcribe = TranscribePage(self)
        self.record = RecordPage(self)
        self.nav.add(self._main_page())
        self.result = ResultPage(self)
        self.nav.connect("popped", lambda *_: self._view_changed())
        self.toasts.set_child(self.nav)
        if WINDOWS:
            self.set_child(self.toasts)     # the title bar is Windows' own
        else:
            self.set_content(self.toasts)

        # a recording dropped anywhere on the window is opened, as one chosen
        target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        target.connect("drop", self._dropped)
        self.add_controller(target)
        # Ctrl+O, as GNOME's apps open a file (gui/application.py sets the key)
        action = Gio.SimpleAction.new("open", None)
        action.connect("activate", lambda *_: self.transcribe.busy or self.choose_file())
        self.add_action(action)
        # the meters run while the Record page is on screen, and only then
        self.views.connect("notify::visible-child-name", lambda *_: self._view_changed())
        self.connect("map", lambda *_: self._view_changed())
        self.connect("unmap", lambda *_: self.record.shown(False))
        self.page_changed()

    def _view_changed(self) -> None:
        on_screen = self.get_mapped() and self.nav.get_visible_page() is self.nav.find_page("main")
        self.record.shown(bool(on_screen) and self.views.get_visible_child_name() == "record")
        self.page_changed()

    def _dropped(self, _target, file, _x, _y) -> bool:
        if file is None or not file.get_path():
            self.toast(_("Only a file on this computer can be transcribed."))
            return False
        self.open_path(file.get_path())
        return True

    # -- the frame -----------------------------------------------------------------

    def menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        section = Gio.Menu()
        if WINDOWS:
            section.append(_("Check for Updates…"), "app.check-updates")
        section.append(_("Models"), "app.models")
        section.append(_("Preferences"), "app.preferences")
        menu.append_section(None, section)
        about = Gio.Menu()
        about.append(_("Open the Error Log"), "app.error-log")
        about.append(_("About {app}").format(app=APP_NAME), "app.about")
        menu.append_section(None, about)
        button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, primary=True,
                                tooltip_text=_("Main Menu"))
        if WINDOWS:
            # with the system's title bar the button is the last thing before
            # the window's edge: the menu's arrow would stand on the menu's
            # own rounded corner (and off it, with the window half off the
            # screen). No arrow, the menu's edge on the button's, and the
            # gap the arrow would have left between the two
            button.get_popover().set_has_arrow(False)
            button.get_popover().set_halign(Gtk.Align.END)
            button.get_popover().set_offset(0, SPACE_S)
        return button

    def _main_page(self) -> Adw.NavigationPage:
        view = self.toolbar_view = Adw.ToolbarView()
        self.models = ModelsPage(self)
        self.models_banner = Adw.Banner(button_label=_("Models"), revealed=False)
        self.models_banner.connect("button-clicked", lambda *_: self.show_models())
        self.app.downloads.subscribe(self._models_changed)
        header = header_bar()
        self.views = Adw.ViewStack()
        self.views.add_titled_with_icon(self.record, "record", _("Record"), "media-record-symbolic")
        self.views.add_titled_with_icon(self.transcribe, "transcribe", _("Transcribe"), "document-edit-symbolic")
        # the switcher is the title; in a narrow window a bar at the bottom
        # takes its place (libadwaita's own pattern, through a breakpoint)
        self.switcher = Adw.ViewSwitcher(stack=self.views, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(self.switcher)
        self.switcher_bar = Adw.ViewSwitcherBar(stack=self.views)
        self.open_button = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open a Recording or Meeting…"))
        self.open_button.connect("clicked", lambda *_: self.choose_file())
        header.pack_start(self.open_button)
        header.pack_end(self.menu_button())
        view.add_top_bar(header)
        # a newer version: said here for as long as it is there, whatever
        # else announced it (a notification is gone in a few seconds)
        self.update_banner = Adw.Banner(button_label=_("Update…"), revealed=False)
        self.update_banner.connect("button-clicked", lambda *_: self._update_asked())
        self._update_asked = lambda: None
        self._update_version = ""
        view.add_top_bar(self.update_banner)
        view.add_top_bar(self.models_banner)
        view.set_content(self.views)
        view.add_bottom_bar(self.switcher_bar)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(NARROW))
        narrow.add_setter(self.switcher_bar, "reveal", True)
        narrow.add_setter(self.switcher, "visible", False)
        frame = Adw.BreakpointBin(child=view, width_request=360, height_request=480)
        frame.add_breakpoint(narrow)
        return Adw.NavigationPage(child=frame, title=APP_NAME, tag="main")

    def show_models(self):
        if self.nav.find_page("models") is None:
            self.nav.add(self.models)
        self.nav.pop_to_tag("main")
        self.nav.push(self.models)

    def _models_changed(self, state):
        self.models_banner.set_title(_("Preparing Transcription") + " · " + progress_text(state))
        self.models_banner.set_revealed(self.app.downloads.busy or state.phase in ("paused", "error"))
        self.app.work_changed()

    def page_changed(self) -> None:
        """The header's Open follows the page: there where a recording can be
        opened, including saved meetings while models are missing, but not
        while a transcription is running."""
        if not hasattr(self, "open_button"):
            return
        state = self.transcribe.stack.get_visible_child_name()
        self.open_button.set_visible(state in ("models", "setup", "failed") and
                                     self.views.get_visible_child_name() == "transcribe")
        self.app.work_changed()

    def refresh_update_banner(self) -> None:
        self.update_banner.set_revealed(bool(self._update_version) and not self.record.busy
                                        and not self.transcribe.busy and not self.app.downloads.busy and not self.app.installing)

    def show_update(self, version: str, asked) -> None:
        """The banner of a newer version; asked() opens what it brings."""
        self._update_asked = asked
        self._update_version = version
        self.update_banner.set_title(_("{app} {version} is available").format(app=APP_NAME, version=version))
        self.refresh_update_banner()

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=escape(text), timeout=5))     # (a toast reads markup)

    def settings_changed(self) -> None:
        """A preference changed: what the page shows from it, again."""
        self.transcribe.settings_changed()

    def show_error_log(self) -> None:
        """The file the app's errors go to (app/errorlog.py), with the
        default app for text; a toast when nothing went wrong yet."""
        path = errorlog.log_file(self.app.paths.state)
        if not path.is_file():
            self.toast(_("No errors have been recorded."))
            return
        ui_open_path(self, str(path))

    # -- recordings --------------------------------------------------------------------

    def choose_file(self) -> None:
        if self.app.closing:
            return
        dialog = Gtk.FileDialog(title=_("Open a Recording or Meeting"), modal=True)
        media = Gtk.FileFilter(name=_("Recordings and saved meetings"))
        media.add_suffix("json")
        for kind in MEDIA_TYPES:
            media.add_mime_type(kind)
        for suffix in MEDIA_SUFFIXES:
            media.add_suffix(suffix)
        everything = Gtk.FileFilter(name=_("All files"))
        everything.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(media)
        filters.append(everything)
        dialog.set_filters(filters)
        dialog.set_default_filter(media)
        last = self.app.cfg.last_folder
        if last and os.path.isdir(last):
            dialog.set_initial_folder(Gio.File.new_for_path(last))

        dialog.open(self, None, file_dialog_done(self, "open_finish", self.open_path))

    def open_path(self, path: str) -> None:
        """A recording, from the dialog, a drop, the command line or a
        second launch: the choices for it, on the Transcribe page."""
        if self.app.closing:
            return
        if self.transcribe.busy:
            self.toast(_("A transcription is in progress: cancel it first."))
            return
        if self.nav.get_visible_page() is not self.nav.find_page("main"):
            self.nav.pop_to_tag("main")
        self.views.set_visible_child_name("transcribe")
        if path.lower().endswith(".json"):
            self._open_meeting(path)
        else:
            self.transcribe.open(path)
        self.page_changed()

    def _open_meeting(self, path: str) -> None:
        self.transcribe.cancel_inspection()
        reading = self.transcribe._reading

        def done(opened, error):
            if self._closing or reading != self.transcribe._reading:
                return
            if error is not None:
                report("opening a saved meeting", error)
                self.toast(str(error))
                return
            self.app.cfg.last_folder = os.path.dirname(path)
            self.app.save_config()
            self.show_result(*opened)

        run_in_thread(lambda: self.backend.open_session(path), done)

    def recorded(self, finished) -> None:
        """A recording made whole (app/recording.py, Finished): opened in
        Transcribe, with what the user should know about it."""
        for note in finished.notes:
            self.toast(note)
        if self.transcribe.busy:
            self.toast(_("Recorded in {path}: open it once the transcription in progress is done.").format(
                path=finished.recording))
            return
        self.open_path(str(finished.recording))

    def show_result(self, result, request) -> None:
        self.result.show(result, request)
        self.nav.push(self.result)
        self.page_changed()

    def new_transcription(self) -> None:
        self.nav.pop_to_tag("main")
        self.transcribe.clear()
        self.page_changed()

    # -- closing and the remembered size ----------------------------------------------

    def _on_close(self, *_args) -> bool:
        if self.app.exit_ready:
            return False
        if not self.app.closing and self.app.cfg.keep_running and self.app.background is not None:
            if self.app.background.hide():
                return True
        self.app.request_quit()
        return True

    def _restore_state(self) -> None:
        try:
            with open(self._state_file, encoding="utf-8") as f:
                state = json.load(f)
            if state.get("version") != STATE_VERSION:
                return
            self.set_default_size(int(state["width"]), int(state["height"]))
            if state.get("maximized"):
                if WINDOWS:     # by Windows, not by GTK: see gui/winquirks.py
                    from . import winquirks
                    winquirks.maximize_at_start(self)
                else:
                    self.maximize()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass

    def save_state(self) -> bool:
        width, height = self.get_default_size()
        if WINDOWS:     # not the maximized size the workaround lends to GTK while maximized
            from . import winquirks
            width, height = winquirks.normal_size(self)
        state = {"version": STATE_VERSION, "width": width, "height": height, "maximized": self.is_maximized()}
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(json.dumps(state), encoding="utf-8")
        except OSError as error:
            report("saving the window state", error)
            return False
        return True
