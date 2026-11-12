"""A newer version: what it brings, and its installer downloaded and
started (the installed build), or its page opened (the portable build)."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from scriba import APP_NAME, __version__  # noqa: E402
from scriba.app import services, updates  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_L, SPACE_M, SPACE_S, SPACE_XXL  # noqa: E402
from .ui import run_in_thread  # noqa: E402


class UpdateDialog(Adw.Dialog):
    def __init__(self, release: updates.Release, app=None, ready_installer: Path | None = None):
        super().__init__(title=_("Update Available"), content_width=460)
        self.release = release
        self.app, self.ready_installer = app, ready_installer
        self.project = services.project()
        self.installer = updates.installer_of(self.project, release) if updates.installed_build() else None
        self._cancel = threading.Event()
        self.connect("closed", lambda *_: self.cancel_download())

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_L, margin_top=SPACE_L,
                      margin_bottom=SPACE_XXL, margin_start=SPACE_XXL, margin_end=SPACE_XXL)
        heading = Gtk.Label(label=_("{app} {version} is available").format(app=APP_NAME, version=release.version),
                            xalign=0, wrap=True, css_classes=["title-2"])
        box.append(heading)
        box.append(Gtk.Label(label=_("You are using version {version}.").format(version=__version__),
                             xalign=0, css_classes=["dim-label"]))
        notes = release.notes.strip()
        if notes:
            label = Gtk.Label(label=notes[:4000], xalign=0, yalign=0, wrap=True, selectable=True,
                              margin_top=SPACE_M, margin_bottom=SPACE_M, margin_start=SPACE_M, margin_end=SPACE_M)
            scroll = Gtk.ScrolledWindow(child=label, min_content_height=120, max_content_height=260,
                                        propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
            box.append(Gtk.Frame(child=scroll))
        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        box.append(self.progress)
        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label"])
        box.append(self.status)

        buttons = Gtk.Box(spacing=SPACE_L, halign=Gtk.Align.END, margin_top=SPACE_S)
        later = Gtk.Button(label=_("Later"))
        later.connect("clicked", lambda *_: self.close())
        buttons.append(later)
        if self.installer is not None:
            self.action = Gtk.Button(label=_("Install") if ready_installer else _("Download and Install"), css_classes=["suggested-action"])
            self.action.connect("clicked", lambda *_: self._download())
            self.status.set_label(_("{app} closes while the new version is installed, and opens again.")
                                  .format(app=APP_NAME))
        else:
            self.action = Gtk.Button(label=_("Open the Release Page"), css_classes=["suggested-action"])
            self.action.connect("clicked", lambda *_: self._open_page())
        buttons.append(self.action)
        box.append(buttons)
        view.set_content(box)
        self.set_child(view)
        # the focus on the action, not on the first widget that takes it:
        # a selectable label given the focus has all its text selected
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
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.status.set_label(_("Downloading…"))

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
            self.progress.set_text(f"{done / 1e6:.1f} / {total / 1e6:.1f} MB")
        else:
            self.progress.pulse()
        return False

    def _failed(self, message: str) -> bool:
        self.progress.set_visible(False)
        self.status.set_label(message)
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
        self.status.set_label(_("Starting the installer…"))
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
