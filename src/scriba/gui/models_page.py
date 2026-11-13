"""The single models page; transfers belong to app.downloads, never to a widget."""

from __future__ import annotations

from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from scriba.app import models  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_L, SPACE_XS  # noqa: E402
from .ui import escape, header_bar  # noqa: E402

SITES = {"huggingface.co": "Hugging Face", "raw.githubusercontent.com": "GitHub"}


def site_of(model):
    host = urlsplit(model.url).hostname or ""
    return SITES.get(host, host)


def progress_text(state):
    phases = {"idle": _("Preparing Transcription"), "connecting": _("Connecting…"),
              "downloading": _("Downloading…"), "checking": _("Checking Downloaded Data…"),
              "verifying": _("Checking the Model…"), "paused": _("Downloads Paused"),
              "stopping": _("Stopping…"), "error": _("Download Failed"), "ready": _("Models Ready")}
    text = phases.get(state.phase, state.phase)
    if state.total and state.phase not in ("ready", "idle"):
        text += " · " + _("{done} of {total}").format(done=models.megabytes(state.done),
                                                       total=models.megabytes(state.total))
    return text


class ModelsPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Models"), tag="models")
        self.window, self.backend = window, window.backend
        self.downloads = window.app.downloads
        view = Adw.ToolbarView()
        header = header_bar()
        header.pack_end(window.menu_button())
        view.add_top_bar(header)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Preparing Transcription"), description=_(
            "These models are needed to transcribe speech. You can record immediately while they download. "
            "Files are checked before use and stay on this computer."))
        # A group's own box puts no space between widgets that are not rows:
        # the bar, the error and the buttons get it from a box of their own
        # (the button touched the bar: 13/11/2026)
        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_L, margin_top=SPACE_XS)
        group.add(status)
        self.progress = Gtk.ProgressBar(show_text=True)
        status.append(self.progress)
        self.error = Gtk.Label(wrap=True, visible=False, css_classes=["error"])
        status.append(self.error)
        buttons = Gtk.Box(spacing=SPACE_L, halign=Gtk.Align.CENTER)
        self.cancel_button = Gtk.Button(label=_("Pause Downloads"))
        self.cancel_button.connect("clicked", lambda *_: self.downloads.pause())
        self.download_button = Gtk.Button(label=_("Resume"), css_classes=["suggested-action"])
        self.download_button.connect("clicked", lambda *_: self.download())
        buttons.append(self.cancel_button)
        buttons.append(self.download_button)
        status.append(buttons)
        page.add(group)
        self.rows = {}
        base = Adw.PreferencesGroup(title=_("Required for Transcription"))
        for model in self.downloads.required:
            base.add(self._row(model))
        page.add(base)
        optional = Adw.PreferencesGroup(title=_("Precise Quality, Optional"), description=_(
            "A larger, slower model. Download it only if you want to choose Precise for a transcription."))
        row = self._row(models.WHISPER_LARGE)
        self.precise_button = Gtk.Button(label=_("Download"), valign=Gtk.Align.CENTER)
        self.precise_button.connect("clicked", lambda *_: self.download_precise())
        row.add_suffix(self.precise_button)
        optional.add(row)
        page.add(optional)
        view.set_content(page)
        self.set_child(view)
        self._unsubscribe = self.downloads.subscribe(self.refresh)
        self.refresh(self.downloads.state)

    def _row(self, model):
        row = Adw.ExpanderRow(title=escape(models.purpose(model)))
        details = Adw.ActionRow(title=_("Source and Licence"), subtitle=escape(
            f"{site_of(model)} · {model.licence}\n{model.publisher}\n{model.page}"), subtitle_lines=0)
        row.add_row(details)
        row.add_row(Adw.ActionRow(title=_("File"), subtitle=escape(str(self.backend.models_dir / model.filename)),
                                  subtitle_lines=0))
        self.rows[model.key] = row
        return row

    def refresh(self, state=None):
        state = self.downloads.state if state is None else state
        queued = self.downloads.queued_keys
        for model in (*self.downloads.required, models.WHISPER_LARGE):
            present = self.backend.has(model)
            status = _("Downloaded") if present else (_("Queued") if model.key in queued else _("Not Downloaded"))
            if state.model_key == model.key and self.downloads.busy:
                status = progress_text(state)
            self.rows[model.key].set_subtitle(escape(models.megabytes(model.size) + " · " + status))
        self.progress.set_text(progress_text(state))
        self.progress.set_fraction(min(1.0, state.done / state.total) if state.total else 0)
        self.progress.set_visible(self.downloads.busy or state.phase in ("paused", "error", "ready"))
        self.error.set_label(state.error)
        self.error.set_visible(bool(state.error))
        self.cancel_button.set_visible(self.downloads.busy)
        self.cancel_button.set_sensitive(state.phase != "stopping")
        self.download_button.set_visible(not self.downloads.busy and state.phase in ("paused", "error", "idle"))
        self.download_button.set_label(_("Try Again") if state.phase == "error" else _("Resume"))
        self.precise_button.set_visible(not self.backend.has(models.WHISPER_LARGE))
        self.precise_button.set_sensitive(models.WHISPER_LARGE.key not in queued)
        self.precise_button.set_label(_("Queued") if models.WHISPER_LARGE.key in queued else _("Download"))

    def download(self):
        if self.downloads.state.phase in ("paused", "error"):
            self.downloads.resume()
        else:
            self.downloads.start_required()

    def download_precise(self):
        self.downloads.download(models.WHISPER_LARGE)

    def close(self):
        self._unsubscribe()
