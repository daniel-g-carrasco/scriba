"""The first start: the models Scriba needs, what they are, how much they
weigh and where they come from, and their download with a bar and a
Cancel (GNOME's pattern for a page with nothing to show yet: an
Adw.StatusPage with the one action under it)."""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from scriba.app import models  # noqa: E402
from scriba.app.backend import needed_models  # noqa: E402
from scriba.app.task import Cancelled, Failure  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_L, SPACE_XL  # noqa: E402
from .ui import escape, report, run_in_thread  # noqa: E402

CLAMP = 520                 # the list's widest, as libadwaita's preferences have it
REFRESH_S = 0.1             # the bar is redrawn at most this often while the bytes arrive
SITES = {"huggingface.co": "Hugging Face", "raw.githubusercontent.com": "GitHub"}


def site_of(model: models.Model) -> str:
    host = urlsplit(model.url).hostname or ""
    return SITES.get(host, host)


class ModelsPage(Adw.Bin):
    def __init__(self, window, ready):
        super().__init__()
        self.window = window
        self.backend = window.backend
        self._ready = ready                 # called once every model is there
        self._cancel: threading.Event | None = None

        self.status = Adw.StatusPage(icon_name="folder-download-symbolic", title=_("Download the Speech Models"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XL)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        box.append(self.list)
        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        box.append(self.progress)
        self.error = Gtk.Label(wrap=True, visible=False, css_classes=["error"], justify=Gtk.Justification.CENTER)
        box.append(self.error)
        buttons = Gtk.Box(spacing=SPACE_L, halign=Gtk.Align.CENTER)
        self.cancel_button = Gtk.Button(label=_("Cancel"), css_classes=["pill"], visible=False)
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        buttons.append(self.cancel_button)
        self.download_button = Gtk.Button(label=_("Download"), css_classes=["pill", "suggested-action"])
        self.download_button.connect("clicked", lambda *_: self.download())
        buttons.append(self.download_button)
        box.append(buttons)
        self.status.set_child(Adw.Clamp(maximum_size=CLAMP, child=box))
        self.set_child(self.status)
        self.refresh()

    def refresh(self) -> None:
        """The rows and the sentence for what is missing now."""
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        missing = self.backend.missing()
        for model in needed_models():
            row = Adw.ActionRow(title=escape(models.purpose(model)),
                                subtitle=escape(_("{size}, from {site}, licence {licence}").format(
                                    size=models.megabytes(model.size), site=site_of(model), licence=model.licence)))
            if model not in missing:
                row.add_suffix(Gtk.Image(icon_name="object-select-symbolic", css_classes=["success"],
                                         tooltip_text=_("Downloaded")))
            self.list.append(row)
        size = sum(m.size for m in missing)
        self.status.set_description(escape(_(
            "Scriba works on this computer and sends nothing anywhere. To understand speech it needs these "
            "models: {size} to download once, from their authors' own pages, each checked before it is used.")
            .format(size=models.megabytes(size))))

    # -- the download --------------------------------------------------------------

    def download(self) -> None:
        missing = self.backend.missing()
        if not missing:
            self._ready()
            return
        total = sum(m.size for m in missing)
        cancel = self._cancel = threading.Event()
        self.error.set_visible(False)
        self.download_button.set_visible(False)
        self.cancel_button.set_visible(True)
        self.cancel_button.set_sensitive(True)
        self.progress.set_visible(True)
        self._show(0, total)
        shown = {"at": 0.0, "pending": False, "done": 0}

        def report(done: float) -> None:            # the worker's thread
            shown["done"] = done
            now = time.monotonic()
            if not shown["pending"] and now - shown["at"] >= REFRESH_S:
                shown["pending"], shown["at"] = True, now

                def draw():
                    shown["pending"] = False
                    if self._cancel is cancel:
                        self._show(shown["done"], total)
                    return False
                GLib.idle_add(draw)

        def work():
            base = 0
            for model in missing:
                self.backend.download(model, lambda d, _t, base=base: report(base + d), cancel)
                base += model.size
            return base

        run_in_thread(work, lambda _r, error: self._finished(cancel, error))

    def _show(self, done: float, total: float) -> None:
        self.progress.set_fraction(done / total if total else 0.0)
        self.progress.set_text(_("{done} of {total}").format(done=models.megabytes(done), total=models.megabytes(total)))

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.cancel_button.set_sensitive(False)
            self.progress.set_text(_("Stopping…"))

    def _finished(self, cancel: threading.Event, error: Exception | None) -> None:
        if self._cancel is not cancel:
            return
        self._cancel = None
        self.progress.set_visible(False)
        self.cancel_button.set_visible(False)
        self.download_button.set_visible(True)
        self.refresh()
        if error is None:
            self._ready()
            return
        if isinstance(error, Cancelled):
            text = _("Download cancelled: what arrived is kept, and the next download goes on from there.")
        else:
            report("downloading the models", error)
            text = str(error) if isinstance(error, Failure) else _("The download failed: {error}").format(error=error)
        self.download_button.set_label(_("Try Again"))
        self.error.set_label(text)
        self.error.set_visible(True)
