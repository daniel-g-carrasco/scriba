# SPDX-License-Identifier: GPL-3.0-or-later
"""A newer version: what it brings, and its installer downloaded and
started (the installed build), or its page opened (the portable build).

The layout is libadwaita's own (docs/01-filosofia.md, "Come si presenta"):
"Later" and the one action in the header bar, the app's icon with the
version and what happens next on a compact status page, what is new as
boxed lists under the headings of the changelog (the release's notes are
the version's section of CHANGELOG.md: tools/release_notes.py), the
download as a progress bar in a list of its own, what went wrong in a
banner. No label is selectable, and the focus starts on the action:
nothing is highlighted before the user selects it."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from html import escape
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..app import updates  # noqa: E402
from ..i18n import _  # noqa: E402


@dataclass(frozen=True)
class UpdateLayout:
    width: int
    height: int
    content_width: int
    row_padding: int
    section_gap: int


class UpdateDialog(Adw.Dialog):
    def __init__(self, release: updates.Release, *, project: updates.Project, app_name: str, app_icon: str,
                 layout: UpdateLayout, channel: str, run_in_thread, can_install, launch,
                 verified_installer: Path | None = None, note_heading=str):
        """The optional installer has already been verified for this release."""
        super().__init__(title=_("Update Available"), content_width=layout.width, content_height=layout.height)
        self.release = release
        self.project = project
        self.installer = updates.installer_of(project, release) if channel == "installed" else None
        self._verified_installer = verified_installer if self.installer is not None else None
        self._run_in_thread = run_in_thread
        self._can_install = can_install
        self._launch = launch
        self._cancel = threading.Event()
        self.connect("closed", lambda *_args: self.cancel_download())

        view = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
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
        self.banner = Adw.Banner(use_markup=False)  # what went wrong, until the next try
        view.add_top_bar(self.banner)

        what = _("You have version {version}.").format(version=project.version)
        if self.installer is not None:
            size = getattr(self.installer, "size", 0)
            if size:
                what += " " + _("The download is {size} MB.").format(size=round(size / 1e6))
            what += "\n" + _("{app} closes while the new version is installed, and opens again.").format(app=app_name)
        self.page = Adw.StatusPage(icon_name=app_icon, description=escape(what), css_classes=["compact"],
                                   title=_("{app} {version} Is Available").format(app=app_name, version=release.version))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=layout.section_gap)
        # the download: a list of its own, shown once it starts
        self.progress_group = Adw.PreferencesGroup(visible=False)
        self.progress = Gtk.ProgressBar(show_text=True, margin_top=layout.row_padding, margin_bottom=layout.row_padding,
                                        margin_start=layout.row_padding, margin_end=layout.row_padding)
        frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        frame.append(Gtk.ListBoxRow(child=self.progress, activatable=False, focusable=False))
        self.progress_group.add(frame)
        content.append(self.progress_group)
        self.note_rows: list[Adw.ActionRow] = []
        if release.notes_in_english:
            content.append(Gtk.Label(label=_("Release notes in English"), xalign=0, wrap=True,
                                     css_classes=["dim-label"]))
        for heading, items in updates.notes_sections(release.notes):
            group = Adw.PreferencesGroup(title=escape(note_heading(heading) or _("What Is New")))
            for item in items:
                row = Adw.ActionRow(title=item, title_lines=0, use_markup=False)
                group.add(row)
                self.note_rows.append(row)
            content.append(group)
        if self.installer is not None:
            group = Adw.PreferencesGroup()
            page = Adw.ActionRow(use_markup=False, title=_("Release Page"),
                                 subtitle=_("This version on GitHub, with its files"),
                                 activatable=True)
            page.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic"))
            page.connect("activated", lambda *_args: Gio.AppInfo.launch_default_for_uri(self.release.url, None))
            group.add(page)
            content.append(group)
        self.page.set_child(Adw.Clamp(child=content, maximum_size=layout.content_width))
        view.set_content(self.page)
        self.set_child(view)
        # the focus on the action: nothing else takes it, and nothing is selected
        self.set_default_widget(self.action)
        self.set_focus(self.action)

    def _open_page(self) -> None:
        Gio.AppInfo.launch_default_for_uri(self.release.url, None)
        self.close()

    def _download(self) -> None:
        if self._cancel.is_set() or self.installer is None or not self.action.get_sensitive():
            return
        if not self._can_install():
            self._busy()
            return
        if self._verified_installer is not None:
            self._downloaded(self._verified_installer)
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
            self._run_in_thread(work, done)
        except Exception as error:
            self._failed(_("Download failed: {error}").format(error=error))

    def cancel_download(self) -> None:
        """Request cancellation; the registry owns work through its callback."""
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

    def _busy(self) -> None:
        self._failed(_("Finish the current work before installing the update."))

    def _downloaded(self, installer: Path) -> bool:
        if self._cancel.is_set():
            # "Later" once the last chunk was in: not started (the file goes
            # with the other downloads, at the next start)
            return False
        self._verified_installer = installer
        if not self._can_install():
            self._busy()
            return False
        self.action.set_sensitive(False)
        self.banner.set_revealed(False)
        self.progress_group.set_visible(True)
        self.progress.set_fraction(1.0)
        self.progress.set_text(_("Starting the installer…"))
        try:
            self._launch(installer)
        except OSError as e:
            self._failed(_("Cannot start the installer: {error}").format(error=e.strerror or e))
        return False
