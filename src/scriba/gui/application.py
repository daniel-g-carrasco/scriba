"""The GApplication: one instance (a second launch hands its files to the
first), the settings, the window, the notifications, the daily update
check on Windows, and what Windows needs to look right (the system font a
size larger, the title bars in the content's colour, the executable's icon
on every window)."""

from __future__ import annotations

import os
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from scriba import APP_ID, APP_NAME, ENV_PREFIX, PACKAGE_DIR, SLUG, WINDOWS, __version__, i18n  # noqa: E402
from scriba.app import config, jobs, services  # noqa: E402
from scriba.app.backend import Backend  # noqa: E402
from scriba.app.log import complain  # noqa: E402
from scriba.app.paths import in_flatpak  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .ui import load_style, run_in_thread  # noqa: E402
from .preferences import PreferencesDialog, about_dialog  # noqa: E402
from .window import MainWindow  # noqa: E402

SOURCE_ICONS = PACKAGE_DIR.parent.parent / "data" / "icons"
UPDATE_CHECK_DELAY_S = 60


def settings_notice_text(notice: config.Notice) -> str:
    """What load() had to do, in the user's words."""
    if notice.kind == "recovered":
        return _("The settings file was damaged: the previous save was used. "
                 "The damaged file is kept at {path}.").format(path=notice.detail)
    if notice.kind == "fresh":
        return _("The settings file was damaged and there was no previous save: the app starts with its "
                 "defaults. The damaged file is kept at {path}.").format(path=notice.detail)
    if notice.kind == "missing":
        return _("The settings file was missing: the previous save was used.")
    if notice.kind == "newer":
        return _("The settings were saved by a newer version ({version}): "
                 "what this version does not know is kept untouched.").format(version=notice.detail)
    return f"{notice.kind} {notice.detail}"


def larger_system_font(factor: float = 7 / 6) -> int:
    """Windows: the system font a size larger (Segoe UI 9 -> 10.5 points).
    libadwaita is drawn for an 11-point font, and at 100% the smaller text it
    derives from the system's 9 points (subtitles, captions) is hard to read.
    The factor applies to the size Windows gives, so a larger text size
    chosen in Windows' settings is followed, also when it changes. Returns
    the id of the handler that follows it (0: no settings)."""
    from gi.repository import Pango
    settings = Gtk.Settings.get_default()
    if settings is None:
        return 0
    ours = [""]

    def apply(*_args):
        name = settings.props.gtk_font_name
        if not name or name == ours[0]:
            return
        font = Pango.FontDescription.from_string(name)
        size = font.get_size()
        if size <= 0 or font.get_size_is_absolute():
            return
        half_point = Pango.SCALE // 2
        font.set_size(round(size * factor / half_point) * half_point)
        ours[0] = font.to_string()
        settings.props.gtk_font_name = ours[0]

    apply()
    return settings.connect("notify::gtk-font-name", apply)


def native_windows() -> None:
    """Windows: each window of the app, as it shows (the main one and every
    dialog, a window of its own with the system's title bar), gets the title
    bar the content has (dark or light, and on Windows 11 the window's own
    background and text colours: bar and content read as one surface), and
    the exe's icon at the sizes the title bar and Alt+Tab draw (a run from
    the sources has no icon of its own to give)."""
    from scriba.app import win32

    from . import winquirks
    from .ui import window_colours
    style = Adw.StyleManager.get_default()
    exe = os.path.normpath(sys.executable) if getattr(sys, "frozen", False) else ""

    def native(*_args):
        windows = Gtk.Window.list_toplevels()
        background, text = window_colours(windows[0]) if windows else (None, None)
        win32.title_bars(style.get_dark(), background, text)
        if exe:
            win32.window_icons(exe)

    def restyled(*_args):
        # the style's colours follow the scheme a moment later: now for the
        # dark or light bar, again once the new colours can be read
        native()
        GLib.idle_add(lambda: native() and False)

    def added(model, position, _removed, count):
        for i in range(position, position + count):
            window = model.get_item(i)
            window.connect("map", native)
            window.add_css_class("system-titlebar")     # the hairline under the title bar (gui/app.css)
            winquirks.watch(window)     # GTK 4.24's restore and popover quirks (see winquirks.py)

    toplevels = Gtk.Window.get_toplevels()
    added(toplevels, 0, 0, toplevels.get_n_items())
    toplevels.connect("items-changed", added)
    for change in ("notify::dark", "notify::high-contrast", "notify::accent-color"):
        style.connect(change, restyled)


def apply_theme(theme: str) -> None:
    style = Adw.StyleManager.get_default()
    style.set_color_scheme({"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}
                           .get(theme, Adw.ColorScheme.DEFAULT))


class App(Adw.Application):
    def __init__(self):
        # HANDLES_OPEN: a file on the command line, from the file manager or
        # from a second launch, reaches do_open of the running instance
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.paths = services.app_paths()
        # the engines, as the window asks them (the models in the user's
        # cache or SCRIBA_MODELS_DIR, the speeds measured in the state)
        self.backend = Backend(None, self.paths.state / jobs.SPEEDS_FILE)
        self.cfg: config.Config | None = None
        self.window: MainWindow | None = None
        self.installing = False    # reserved before Popen, until Setup closes the app or launch fails
        self._pending_check: bool | None = None
        self._pending_release = None
        self._available_release = None
        self._update_dialog = None
        self._verified_installer = None
        self.toasts = None          # Windows: wintoast.Toasts, when the Windows Runtime answers

    # -- start-up -------------------------------------------------------------------

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.cfg = config.load(self.paths.config_file)
        if self.cfg.language:
            i18n.use(self.cfg.language)         # the preference over the desktop's language
        if WINDOWS:
            larger_system_font()
            native_windows()
        for refused in load_style(Gdk.Display.get_default()):
            print(f"{SLUG}: style: {refused}", file=sys.stderr, flush=True)
        GLib.set_application_name(APP_NAME)
        if not in_flatpak() and SOURCE_ICONS.is_dir():
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(SOURCE_ICONS))
        apply_theme(self.cfg.theme)
        for notice in self.cfg.notices:
            print(f"settings: {notice.kind} {notice.detail} {notice.reason}".rstrip(), file=sys.stderr, flush=True)
            GLib.idle_add(lambda n=notice: self._window().toast(settings_notice_text(n)) and False)

        for name, handler in (("quit", self.quit),
                              ("about", lambda: about_dialog(self._window(), self.cfg, self.paths)),
                              ("preferences", lambda: PreferencesDialog(self._window(), self).present(self._window())),
                              ("check-updates", lambda: self.check_updates(manual=True))):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("window.close", ["<Control>w"])
        self.set_accels_for_action("win.open", ["<Control>o"])

        if WINDOWS:
            self._windows_notifications()
            # once a day, a while after start: the user's work comes first
            GLib.timeout_add_seconds(UPDATE_CHECK_DELAY_S, self._daily_update_check)
            from scriba.app import updates
            project = services.project()
            run_in_thread(lambda: updates.clean_downloads(project), lambda *_: None)    # the installers of earlier updates

    def do_activate(self):
        self._window().present()

    def do_open(self, files, _n_files, _hint):
        """Files and folders given on the command line or by the file
        manager, also to the instance already running."""
        window = self._window()
        window.present()
        paths = [file.get_path() for file in files if file.get_path()]
        if paths:
            window.open_path(paths[0])      # one recording at a time
        if len(paths) > 1:
            window.toast(_("One recording at a time: {name} is open.").format(name=os.path.basename(paths[0])))

    def _window(self) -> MainWindow:
        if self.window is None:
            self.window = MainWindow(self)
            shot = os.environ.get(f"{ENV_PREFIX}_SCREENSHOT")
            if shot:   # developer aid (and the CI's smoke test): the window as PNG after a few seconds
                GLib.timeout_add(int(float(os.environ.get(f"{ENV_PREFIX}_SCREENSHOT_DELAY", "3")) * 1000),
                                 lambda: self._screenshot(shot) and False)
        return self.window

    def _screenshot(self, path: str) -> None:
        """The window as a PNG, taken right after a frame is painted: between
        two frames the window may have a layout pending (a toast going away,
        a count updating its rows) and its picture is then empty."""
        window = self._window()
        clock = window.get_frame_clock()

        def painted(clock=None):
            if clock is not None:
                clock.disconnect(handler)
            self._save_screenshot(window, path)
            if os.environ.get(f"{ENV_PREFIX}_SCREENSHOT_QUIT", "1") == "1":
                self.quit()
        if clock is None:       # not drawn yet: as it is
            painted()
            return
        handler = clock.connect("after-paint", painted)
        window.queue_draw()

    def _save_screenshot(self, window, path: str) -> None:
        width, height = window.get_width(), window.get_height()
        scale = float(os.environ.get(f"{ENV_PREFIX}_SCREENSHOT_SCALE", "1"))
        snapshot = Gtk.Snapshot()
        snapshot.scale(scale, scale)       # drawn at that scale, not stretched afterwards
        Gtk.WidgetPaintable.new(window).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            print("screenshot: the window had nothing to draw", file=sys.stderr, flush=True)
            return
        gi.require_version("Graphene", "1.0")
        from gi.repository import Graphene
        area = Graphene.Rect().init(0, 0, width * scale, height * scale)
        window.get_renderer().render_texture(node, area).save_to_png(path)

    # -- settings ----------------------------------------------------------------------

    def save_config(self) -> None:
        try:
            config.save(self.cfg, self.paths.config_file)
        except OSError as e:        # said: a setting that does not stick is a bug report
            complain("saving the settings", e)
            if self.window is not None:
                self.window.toast(_("The settings could not be saved: {error}").format(error=e.strerror or e))

    # -- notifications -----------------------------------------------------------------

    def _windows_notifications(self) -> None:
        """The toasts of Windows, under the app's own name and icon; without
        the Windows Runtime (an old Windows) the app does without."""
        # Developer screenshots use isolated profiles and must not write HKCU.
        if os.environ.get(f"{ENV_PREFIX}_SCREENSHOT"):
            return
        from scriba.app import wintoast
        try:
            exe = os.path.normpath(sys.executable) if getattr(sys, "frozen", False) else ""
            wintoast.register_app(APP_ID, APP_NAME, f"{exe},0" if exe else "")
            self.toasts = wintoast.Toasts(APP_ID, SLUG)
        except (OSError, wintoast.WinRTError) as e:
            complain("setting up the notifications", e)

    def notify(self, title: str, body: str, tag: str = "", on_click=None) -> None:
        """A notification outside the window: GLib's on Linux (through the
        desktop), the Windows Runtime's on Windows, where GLib has none."""
        if WINDOWS:
            if self.toasts is not None:
                clicked = None if on_click is None else (lambda: GLib.idle_add(lambda: on_click() and False))
                try:
                    self.toasts.show(title, body, tag, clicked)
                except OSError as e:
                    complain("showing a notification", e)
            return
        note = Gio.Notification.new(title)
        note.set_body(body)
        self.send_notification(tag or None, note)

    # -- a newer version (Windows: on Linux the Flatpak brings it) ----------------------

    def _daily_update_check(self) -> bool:
        today = time.strftime("%Y-%m-%d")
        if self.cfg.check_updates and self.cfg.last_update_check != today:
            self.cfg.last_update_check = today
            self.save_config()
            self.check_updates(manual=False)
        return False

    def check_updates(self, manual: bool) -> None:
        from scriba.app import updates
        if self.work_busy or self.installing:
            self._pending_check = bool(self._pending_check) or manual
            if self._pending_release is not None:
                release, asked = self._pending_release
                self._pending_release = (release, asked or manual)
            return
        project = services.project()

        def done(release, error) -> None:
            if error:
                if manual:
                    self._window().toast(_("The check for updates failed: {error}").format(error=error))
                return
            if not updates.is_newer(release.version, __version__):
                if manual:
                    self._window().toast(_("{app} {version} is up to date").format(app=APP_NAME, version=__version__))
                return
            self._offer_update(release, manual)

        run_in_thread(lambda: updates.latest_release(project), done)

    @property
    def work_busy(self) -> bool:
        return self.window is not None and (self.window.record.busy or self.window.transcribe.busy)

    def update_allowed(self, release) -> bool:
        if self.installing:
            return False
        if self.work_busy:
            self._pending_release = (release, True)
            return False
        return True

    def _offer_update(self, release, manual: bool) -> None:
        self._available_release = release
        if self.work_busy or self.installing:
            self._pending_release = (release, manual or bool(self._pending_release and self._pending_release[1]))
            return
        window = self._window()
        window.show_update(release.version, lambda: self._show_update(release))
        if manual:
            self._show_update(release)
        elif not window.is_active():
            self.notify(_("{app} {version} is available").format(app=APP_NAME, version=release.version),
                        _("Click to see what is new and update."), tag="update",
                        on_click=lambda: self._show_update(release))

    def work_changed(self) -> None:
        """Suppress proposals for the whole job, including recording start and finish."""
        if self.window is None:
            return
        self.window.refresh_update_banner()
        if self.work_busy:
            if self._available_release is not None and self._pending_release is None:
                self._pending_release = (self._available_release, False)
            dialog = self._update_dialog
            if dialog is not None:
                self._pending_release = (dialog.release, True)
                self._update_dialog = None
                dialog.close()             # cancels a download; its final callback still checks the gate
            return
        if self.installing or self.window._closing:
            return
        if self._pending_release is not None:
            release, manual = self._pending_release
            self._pending_release = self._pending_check = None
            self._offer_update(release, manual)
        elif self._pending_check is not None:
            manual, self._pending_check = self._pending_check, None
            self.check_updates(manual)

    def remember_installer(self, release, path) -> None:
        # updates.download holds the verified file against changes until exit.
        # Reuse it after a deferral: replacing that held file would fail on Windows.
        self._verified_installer = (release.version, path)
        dialog = self._update_dialog
        if dialog is not None and dialog.release.version == release.version and not dialog._cancel.is_set():
            dialog.ready_installer = path
            dialog.action.set_label(_("Install"))

    def begin_update(self, release) -> bool:
        if not self.update_allowed(release):
            return False
        self.installing = True
        self.work_changed()
        return True

    def update_failed(self) -> None:
        self.installing = False
        self.work_changed()

    def _show_update(self, release) -> None:
        from .update_dialog import UpdateDialog
        if not self.update_allowed(release):
            return
        window = self._window()
        window.present()
        ready = self._verified_installer
        dialog = UpdateDialog(release, app=self,
                              ready_installer=ready[1] if ready and ready[0] == release.version else None)
        self._update_dialog = dialog
        dialog.connect("closed", lambda *_: self._update_closed(dialog))
        dialog.present(window)

    def _update_closed(self, dialog) -> None:
        if self._update_dialog is dialog:
            self._update_dialog = None


def main(argv=None) -> int:
    if WINDOWS:
        # the system title bar on the main window, before GTK starts (see
        # gui/window.py): with GTK's own, Windows does not snap the window
        os.environ.setdefault("GTK_CSD", "0")
    return App().run(sys.argv if argv is None else argv)
