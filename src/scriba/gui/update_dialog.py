"""The common GTK update dialog connected to Scriba's work and exit gates."""

import subprocess
from pathlib import Path

from scriba import APP_ID, APP_NAME
from scriba.app import services, updates
from scriba.i18n import _

from .release_dialog import UpdateDialog as ReleaseDialog, UpdateLayout
from .secondaries import header_bars_below
from .style_values import SIZE_UPDATE_CONTENT, SIZE_UPDATE_HEIGHT, SIZE_UPDATE_WIDTH, SPACE_L, SPACE_XL
from .ui import run_in_thread

from gi.repository import Adw, Gtk  # noqa: E402


def section_title(heading: str) -> str:
    """A heading of the release's notes in the user's language: Keep a
    Changelog's six by name, any other as it is written, none "What Is New".
    The items keep the release's wording."""
    known = {"added": _("Added"), "changed": _("Changed"), "fixed": _("Fixed"),
             "removed": _("Removed"), "security": _("Security"), "deprecated": _("Deprecated")}
    return known.get(heading.strip().lower(), heading) or _("What Is New")


class UpdateDialog(ReleaseDialog):
    def __init__(self, release: updates.Release, app=None, ready_installer: Path | None = None):
        self.app = app
        super().__init__(
            release, project=services.project(), app_name=APP_NAME, app_icon=APP_ID,
            layout=UpdateLayout(SIZE_UPDATE_WIDTH, SIZE_UPDATE_HEIGHT, SIZE_UPDATE_CONTENT, SPACE_L, SPACE_XL),
            channel=updates.distribution_channel(), run_in_thread=run_in_thread,
            can_install=self._allowed, launch=self._launch_installer, verified_installer=ready_installer,
            note_heading=section_title,
        )
        self.header = next(header_bars_below(self.get_child(), Adw))
        self.header.set_title_widget(Gtk.Box())
        self.header.set_show_title(False)
        self.note_groups = list(dict.fromkeys(row.get_ancestor(Adw.PreferencesGroup) for row in self.note_rows))

    @property
    def ready_installer(self):
        return self._verified_installer

    @ready_installer.setter
    def ready_installer(self, path):
        self._verified_installer = path

    def _allowed(self) -> bool:
        return self.app is None or self.app.update_allowed(self.release)

    def _open_page(self) -> None:
        if self._allowed():
            super()._open_page()
        else:
            self._busy()

    def _busy(self) -> None:
        self._failed(_("The update waits until the recording or transcription has finished."))

    def _downloaded(self, installer: Path) -> bool:
        self.ready_installer = installer
        if self.app is not None:
            self.app.remember_installer(self.release, installer)
        return super()._downloaded(installer)

    def _launch_installer(self, installer: Path) -> None:
        if self.app is not None and not self.app.begin_update(self.release):
            self._busy()
            return
        try:
            subprocess.Popen(updates.installer_command(installer, restart=True), close_fds=True)
        except OSError:
            if self.app is not None:
                self.app.update_failed()
            raise
