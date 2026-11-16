"""A newer version: what it brings, and its installer downloaded and
started (the installed build), or its page opened (the portable build).

The layout is libadwaita's own (the template's docs/01-filosofia.md, "Come
si presenta"): "Later" and the one action in the header bar, the app's icon
with the version and what happens next on a compact status page, what is
new as boxed lists under the headings of the changelog (the release's notes
are the version's section of CHANGELOG.md: tools/release_notes.py), the
download as a progress bar in a list of its own, what went wrong or what
waits in a banner. No label is selectable, and the focus starts on the
action: nothing is highlighted before the user selects it.

Scriba's own: nothing is offered or started while a recording, a
transcription or a model's download is going (App.update_allowed and
begin_update), and an installer already downloaded and verified is reused
(ready_installer) instead of downloaded again."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from scriba import APP_ID, APP_NAME, __version__  # noqa: E402
from scriba.app import services, updates  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_L, SPACE_XL  # noqa: E402
from .ui import run_in_thread  # noqa: E402

CONTENT_WIDTH = 440         # the lists under the status page: libadwaita's own clamp


def section_title(heading: str) -> str:
    """A heading of the release's notes in the user's language: Keep a
    Changelog's six by name, any other as it is written, none "What Is New".
    The items stay as the changelog writes them (in English)."""
    known = {"added": _("Added"), "changed": _("Changed"), "fixed": _("Fixed"),
             "removed": _("Removed"), "security": _("Security"), "deprecated": _("Deprecated")}
    return known.get(heading.strip().lower(), heading) or _("What Is New")


class UpdateDialog(Adw.Dialog):
    def __init__(self, release: updates.Release, app=None, ready_installer: Path | None = None):
        super().__init__(title=_("Update Available"), content_width=480, content_height=580)
        self.release = release
        self.app, self.ready_installer = app, ready_installer
        self.project = services.project()
        self.installer = updates.installer_of(self.project, release) if updates.installed_build() else None
        self._cancel = threading.Event()
        self.connect("closed", lambda *_args: self.cancel_download())

        view = Adw.ToolbarView()
        # no title in the bar: between the two buttons it was cut ("Aggiornamento
        # disp..."), and the status page says it already; the dialog keeps it
        # for the screen readers (GNOME's dialogs with buttons in the bar)
        self.header = header = Adw.HeaderBar(show_title=False, show_start_title_buttons=False,
                                             show_end_title_buttons=False)
        self.later = Gtk.Button(label=_("Later"))
        self.later.connect("clicked", lambda *_args: self.close())
        header.pack_start(self.later)
        if self.installer is not None:
            self.action = Gtk.Button(label=_("Install Update"), css_classes=["suggested-action"])
            self.action.connect("clicked", lambda *_args: self._download())
        else:
            self.action = Gtk.Button(label=_("Open Release Page"), css_classes=["suggested-action"])
            self.action.connect("clicked", lambda *_args: self._open_page())
        header.pack_end(self.action)
        view.add_top_bar(header)
        self.banner = Adw.Banner()          # what went wrong or what waits, until the next try
        view.add_top_bar(self.banner)

        what = _("You have version {version}.").format(version=__version__)
        if self.installer is not None:
            size = getattr(self.installer, "size", 0)
            if size and ready_installer is None:
                what += " " + _("The download is {size} MB.").format(size=round(size / 1e6))
            what += "\n" + _("{app} closes while the new version is installed, and opens again.").format(app=APP_NAME)
        self.page = Adw.StatusPage(icon_name=APP_ID, description=what, css_classes=["compact"],
                                   title=_("{app} {version} Is Available").format(app=APP_NAME, version=release.version))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XL)
        # the download: a list of its own, shown once it starts
        self.progress_group = Adw.PreferencesGroup(visible=False)
        self.progress = Gtk.ProgressBar(show_text=True, margin_top=SPACE_L, margin_bottom=SPACE_L,
                                        margin_start=SPACE_L, margin_end=SPACE_L)
        frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        frame.append(Gtk.ListBoxRow(child=self.progress, activatable=False, focusable=False))
        self.progress_group.add(frame)
        content.append(self.progress_group)
        self.note_rows: list[Adw.ActionRow] = []
        self.note_groups: list[Adw.PreferencesGroup] = []
        for heading, items in updates.notes_sections(release.notes):
            group = Adw.PreferencesGroup(title=section_title(heading))
            self.note_groups.append(group)
            for item in items:
                row = Adw.ActionRow(title=item, title_lines=0, use_markup=False)
                group.add(row)
                self.note_rows.append(row)
            content.append(group)
        if self.installer is not None:
            group = Adw.PreferencesGroup()
            page = Adw.ActionRow(title=_("Release Page"), subtitle=_("This version on GitHub, with its files"),
                                 activatable=True)
            page.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            page.connect("activated", lambda *_args: Gio.AppInfo.launch_default_for_uri(self.release.url, None))
            group.add(page)
            content.append(group)
        self.page.set_child(Adw.Clamp(child=content, maximum_size=CONTENT_WIDTH))
        view.set_content(self.page)
        self.set_child(view)
        # the focus on the action: nothing else takes it, and nothing is selected
        self.set_default_widget(self.action)
        self.set_focus(self.action)

    def _open_page(self) -> None:
        if self.app is not None and not self.app.update_allowed(self.release):
            self._failed(_("The update waits until the recording or transcription has finished."))
            return
        Gio.AppInfo.launch_default_for_uri(self.release.url, None)
        self.close()

    def _download(self) -> None:
        if self._cancel.is_set():
            return
        if self.app is not None and not self.app.update_allowed(self.release):
            self._failed(_("The update waits until the recording or transcription has finished."))
            return
        if self.ready_installer is not None:
            self._downloaded(self.ready_installer)
            return
        self.action.set_sensitive(False)
        self.banner.set_revealed(False)
        self.later.set_label(_("Cancel"))
        self.progress_group.set_visible(True)
        self.progress.set_fraction(0)
        self.progress.set_text(_("Downloading…"))

        def progress(done: int, total: int) -> None:
            GLib.idle_add(self._progress, done, total)

        def work() -> Path:
            dest = updates.download_folder(self.project) / self.installer.name
            updates.download(self.project, self.installer, dest, progress, self._cancel)
            return dest

        def done(dest, error) -> None:
            if error is not None:
                if not self._cancel.is_set():
                    self._failed(_("Download failed: {error}").format(error=error))
                return
            self._downloaded(dest)

        try:
            run_in_thread(work, done)
        except Exception as error:
            self._failed(_("Download failed: {error}").format(error=error))

    def cancel_download(self) -> None:
        """Request cancellation; the worker registry owns the actual completion."""
        self._cancel.set()

    def _progress(self, done: int, total: int) -> bool:
        if self._cancel.is_set():
            return False
        if total:
            self.progress.set_fraction(min(done / total, 1.0))
            self.progress.set_text(_("Downloading: {done} of {total} MB").format(
                done=f"{done / 1e6:.1f}", total=f"{total / 1e6:.1f}"))
        else:
            self.progress.pulse()
        return False

    def _failed(self, message: str) -> bool:
        self.progress_group.set_visible(False)
        self.banner.set_title(message)
        self.banner.set_revealed(True)
        self.later.set_label(_("Later"))
        self.action.set_sensitive(True)
        return False

    def _downloaded(self, installer: Path) -> bool:
        self.ready_installer = installer
        if self.app is not None:
            self.app.remember_installer(self.release, installer)
        if self._cancel.is_set():
            # "Later" once the last chunk was in: not started (the file goes
            # with the other downloads, at the next start)
            return False
        if self.app is not None and not self.app.begin_update(self.release):
            self._failed(_("The update waits until the recording or transcription has finished."))
            return False
        self.action.set_sensitive(False)
        self.banner.set_revealed(False)
        self.progress_group.set_visible(True)
        self.progress.set_fraction(1.0)
        self.progress.set_text(_("Starting the installer…"))
        try:
            # Setup asks the app to quit, replaces the files and starts it
            # again; its progress shows
            args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
            if updates.per_user_install():
                args.append("/CURRENTUSER")         # as it was installed: no elevation
            subprocess.Popen(args, close_fds=True)
        except OSError as e:
            if self.app is not None:
                self.app.update_failed()
            self._failed(_("Cannot start the installer: {error}").format(error=e.strerror or e))
        return False
