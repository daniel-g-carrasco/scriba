"""Desktop access while the main window is hidden.

A window can be hidden only after the shell has accepted a usable tray
icon. Losing that access presents the window again.
"""

from __future__ import annotations

from gi.repository import GLib

from scriba import APP_ID, APP_NAME, WINDOWS
from scriba.i18n import _

from .tray import MenuItem, TrayIcon


class Background:
    def __init__(self, app, icon_theme_path=""):
        self.app = app
        self._timer = 0
        self._stopping = False
        self.hidden = False
        if WINDOWS:
            from .wintray import WinTray
            self.tray = WinTray(
                APP_ID, APP_NAME, self.present, icon_theme_path=icon_theme_path,
                cache=app.paths.cache_folder / "tray-icons",
                on_quit=lambda: app.request_quit(system=True),
                on_prepare_update=app.prepare_update_exit,
                has_quit=lambda: app.exit_ready,
                on_suspend=self._suspend,
                on_availability=self._availability)
        else:
            self.tray = TrayIcon(APP_ID, APP_NAME, self.present,
                                 icon_theme_path=icon_theme_path,
                                 on_availability=self._availability)

    @property
    def available(self):
        return self.tray.available and not self._stopping

    def start(self):
        self.tray.start(self.app.get_dbus_connection())
        self.update()
        self._timer = GLib.timeout_add(500, self.update)

    def stop(self):
        self._stopping = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self.tray.stop()

    def hide(self):
        if not self.available:
            return False
        self.app.window.save_state()
        self.hidden = True
        self.app.window.set_visible(False)
        return True

    def present(self):
        if self.app.closing or self.app.exit_ready:
            return
        self.hidden = False
        self.app._window().present()

    def _availability(self, available):
        if not available and self.hidden and not self._stopping and not self.app.closing:
            self.present()

    def _suspend(self):
        window = self.app.window
        if window is not None and window.record.busy:
            # Finalise before sleep when possible; do not resume recording
            # without the user choosing it after waking.
            window.record.stop()

    def update(self):
        window = self.app.window
        if window is None or self._stopping:
            return False
        record = window.record
        suffix, description = "", _("Ready")
        items = [MenuItem(_("Show Scriba"), self.present, name="show")]
        if record.busy:
            snapshot = record.recorder.snapshot() if record.recorder is not None else None
            paused = snapshot is not None and snapshot.state == "paused"
            suffix = "paused" if paused else "recording"
            description = _("Recording Paused") if paused else _("Recording")
            if record._stopping:
                description = _("Finishing the Recording…")
            elif snapshot is not None:
                items.append(MenuItem(_("Resume Recording") if paused else _("Pause Recording"),
                                      record.pause, name="pause-recording"))
                items.append(MenuItem(_("Stop Recording"), record.stop, name="stop-recording"))
        elif getattr(record, "last_error", ""):
            suffix, description = "error", _("Recording Needs Attention")
        elif window.transcribe.busy:
            suffix, description = "transcribing", _("Transcribing…")
        elif window.transcribe.stack.get_visible_child_name() == "failed":
            suffix, description = "error", _("Transcription Needs Attention")
        elif self.app.downloads.busy:
            suffix, description = "downloading", _("Preparing Transcription…")
        elif self.app.downloads.state.error:
            suffix, description = "error", _("The Model Download Needs Attention")
        elif window.cards.running is not None:
            suffix, description = "transcribing", _("Testing the Graphics Cards…")
        icon = APP_ID + (f"-{suffix}" if suffix else "")
        self.tray.set_icon(icon if WINDOWS else f"{icon}-symbolic")
        self.tray.set_tooltip(APP_NAME, description)
        items += [MenuItem(separator=True), MenuItem(description), MenuItem(separator=True),
                  MenuItem(_("Quit"), self.app.request_quit, name="quit")]
        self.tray.set_menu(items)
        return True
