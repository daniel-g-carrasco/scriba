"""The preferences (Adw.PreferencesDialog) and the About window
(Adw.AboutDialog, with the licences of the engines and the models in its
legal sections)."""

from __future__ import annotations

import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from scriba import APP_ID, APP_NAME, WEBSITE, WINDOWS, __version__  # noqa: E402
from scriba.app import config, credits, jobs, models, recording  # noqa: E402
from scriba.core import terms  # noqa: E402
from scriba.i18n import _, ngettext  # noqa: E402

from . import about_widgets  # noqa: E402
from .secondaries import fit_preferences, header_bars_below, present_secondary  # noqa: E402
from .sizing import available_size  # noqa: E402
from .style_values import SPACE_XL  # noqa: E402
from .ui import (choose_folder, escape, file_dialog_done, recordings_folder, report, run_in_thread,  # noqa: E402
                 toolkit_versions)

QUALITIES = ("turbo", "large")
ELSEWHERE = ("scriba", "beside")    # app/config.py, elsewhere
LICENCES = {"MIT": Gtk.License.MIT_X11, "Apache-2.0": Gtk.License.APACHE_2_0, "BSD-3-Clause": Gtk.License.BSD_3,
            "GPL-3.0-or-later": Gtk.License.GPL_3_0, "LGPL-2.1-or-later": Gtk.License.LGPL_2_1}
CUSTOM_LICENCES = {"CC-BY-4.0": ("Creative Commons Attribution 4.0 International",
                                 "https://creativecommons.org/licenses/by/4.0/"),
                   "CC-PDDC": ("Public domain (Creative Commons Public Domain Dedication and Certification)",
                               "https://creativecommons.org/licenses/publicdomain/")}
PUBLIC_DOMAIN = ("CC-PDDC",)        # nobody's: no © before the name


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window, app):
        super().__init__(title=_("Preferences"))
        self.window, self.app = window, app
        self.backend = app.backend
        cfg = app.cfg
        if WINDOWS:
            fit_preferences(self, window, lambda parent: available_size(parent, SPACE_XL))
            self.connect("realize", lambda *_: GLib.idle_add(self._use_native_title))
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(title=escape(_("Appearance")))
        themes = [("system", _("Follow the System")), ("light", _("Light")), ("dark", _("Dark"))]
        theme = Adw.ComboRow(use_markup=False, title=_("Theme"))
        theme.set_model(Gtk.StringList.new([label for _code, label in themes]))
        theme.set_selected(next((i for i, (code, _l) in enumerate(themes) if code == cfg.theme), 0))
        theme.connect("notify::selected", lambda r, _p: self._set_theme(themes[r.get_selected()][0]))
        group.add(theme)
        languages = [("", _("Desktop language")), ("en", "English"), ("it", "Italiano")]
        language = Adw.ComboRow(use_markup=False, title=_("Language"), subtitle=_("Applied at the next start"))
        language.set_model(Gtk.StringList.new([label for _code, label in languages]))
        language.set_selected(next((i for i, (code, _l) in enumerate(languages) if code == cfg.language), 0))
        language.connect("notify::selected", lambda r, _p: self._set("language", languages[r.get_selected()][0]))
        group.add(language)
        page.add(group)

        group = Adw.PreferencesGroup(title=escape(_("Transcription")))
        self.my_name = Adw.EntryRow(use_markup=False, title=_("My Name"), text=cfg.my_name)
        self.my_name.connect("changed", lambda row: self._set("my_name", row.get_text()))
        group.add(self.my_name)
        self.device_row = Adw.ComboRow(use_markup=False, title=_("Graphics Card"), subtitle_lines=2)
        self._devices: list[str] = []           # the choices behind the rows: "", names, "cpu"
        self._fill_devices([])
        self.device_row.connect("notify::selected", lambda r, _p: self._chose_device())
        group.add(self.device_row)
        self.test_row = Adw.ActionRow(use_markup=False, title=_("Test of the Graphics Cards"), subtitle_lines=5)
        self.test_spinner = Adw.Spinner(visible=False)
        self.test_row.add_suffix(self.test_spinner)
        self.test_button = Gtk.Button(label=_("Test Again"), valign=Gtk.Align.CENTER)
        self.test_button.connect("clicked", lambda *_: self._test_again())
        self.test_row.add_suffix(self.test_button)
        group.add(self.test_row)
        handler = window.cards.connect("changed", lambda _test: self._show_test())
        self.connect("closed", lambda *_: window.cards.disconnect(handler))
        self._show_test()
        self.quality_row = Adw.ComboRow(use_markup=False, title=_("Usual Quality"),
                                        model=Gtk.StringList.new([_("Fast"), _("Precise, slower")]))
        self.quality_row.set_selected(QUALITIES.index(cfg.quality) if cfg.quality in QUALITIES else 0)
        self.quality_row.connect("notify::selected", lambda r, _p: self._set("quality", QUALITIES[r.get_selected()]))
        group.add(self.quality_row)
        backends = ("auto", "community", "legacy")
        self.voice_backend_row = Adw.ComboRow(use_markup=False, title=_("Voice Method"), subtitle_lines=2,
            subtitle=_("Automatic uses verified community models when available. The previous method remains available."),
            model=Gtk.StringList.new([_("Automatic"), _("Community"), _("Previous method")]))
        self.voice_backend_row.set_selected(backends.index(cfg.diarization_backend))
        self.voice_backend_row.connect("notify::selected",lambda row,_param:
            self._set("diarization_backend", backends[row.get_selected()]))
        group.add(self.voice_backend_row)
        self.models_row = Adw.ActionRow(use_markup=False, title=_("Speech Models"),
                                        subtitle=_("Downloads, progress and the optional Precise model"))
        manage = Gtk.Button(label=_("Manage Models"), valign=Gtk.Align.CENTER)
        manage.connect("clicked", lambda *_: self._open_models())
        self.models_row.add_suffix(manage)
        group.add(self.models_row)
        unsubscribe = app.downloads.subscribe(lambda _state: self._show_test())
        self.connect("closed", lambda *_: unsubscribe())
        self.folder_row = Adw.ActionRow(use_markup=False, title=_("Usual Folder for the Files"), subtitle_lines=2)
        self.folder_reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Next to the Recording"),
                                       valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.folder_reset.connect("clicked", lambda *_: self._set_folder(""))
        self.folder_row.add_suffix(self.folder_reset)
        choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose a Folder…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", lambda *_args: choose_folder(self.window, _("Choose the Usual Folder for the Files"),
                                                           cfg.output_folder or None, self._set_folder, owner_widget=self))
        self.folder_row.add_suffix(choose)
        group.add(self.folder_row)
        self._show_folder()
        self.elsewhere_row = Adw.ComboRow(
            use_markup=False, title=_("Files Kept Elsewhere"), subtitle_lines=3,
            subtitle=_("The transcripts of a file outside the folder for the recordings, when no usual folder is "
                       "chosen: in a folder of their own there, with a link to the file, or next to it"),
            model=Gtk.StringList.new([_("In Scriba’s Folder"), _("Next to the Original File")]))
        self.elsewhere_row.set_selected(ELSEWHERE.index(cfg.elsewhere) if cfg.elsewhere in ELSEWHERE else 0)
        self.elsewhere_row.connect("notify::selected",
                                   lambda r, _p: self._set("elsewhere", ELSEWHERE[r.get_selected()]))
        group.add(self.elsewhere_row)
        page.add(group)

        self.terms_group = Adw.PreferencesGroup(title=escape(_("Terms")))
        self.term_rows: list[Adw.ActionRow] = []
        self.add_terms_row = Adw.ButtonRow(use_markup=False, title=_("Add a File of Terms…"), start_icon_name="list-add-symbolic")
        self.add_terms_row.connect("activated", lambda *_: self._choose_terms_file())
        self.terms_group.add(self.add_terms_row)
        page.add(self.terms_group)
        self._show_term_files()

        group = Adw.PreferencesGroup(title=escape(_("Recording")))
        self.recordings_row = Adw.ActionRow(use_markup=False, title=_("Folder for the Recordings"), subtitle_lines=2)
        self.recordings_reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Back to the Usual Folder"),
                                           valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.recordings_reset.connect("clicked", lambda *_: self._set_recordings(""))
        self.recordings_row.add_suffix(self.recordings_reset)
        choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose a Folder…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", lambda *_args: choose_folder(self.window, _("Choose the Folder for the Recordings"),
                                                           str(recordings_folder(self.app.cfg)),
                                                           self._set_recordings, owner_widget=self))
        self.recordings_row.add_suffix(choose)
        group.add(self.recordings_row)
        self._show_recordings()
        if WINDOWS:
            self.call_mute_row = Adw.SwitchRow(
                use_markup=False, title=_("Follow the Call’s Mute"), subtitle_lines=3,
                subtitle=_("While you are muted in the call, what your microphone hears is recorded but not "
                           "transcribed as yours. Windows 11, for apps that tell Windows, such as Teams; it reads "
                           "Windows’ own words, so it depends on Windows’ language."),
                active=cfg.follow_call_mute)
            self.call_mute_row.connect("notify::active", lambda r, _p: self._set("follow_call_mute", r.get_active()))
            group.add(self.call_mute_row)
            self.teams_names_row = Adw.SwitchRow(
                use_markup=False, title=_("Names from Teams"), active=cfg.teams_names,
                subtitle=_("Propose voice names from Teams speaker borders during recording. "
                           "Unavailable when Teams is minimized or its picture cannot be read."), subtitle_lines=3)
            self.teams_names_row.connect("notify::active", lambda r, _p: self._set("teams_names", r.get_active()))
            group.add(self.teams_names_row)
        page.add(group)

        group = Adw.PreferencesGroup(title=escape(_("Background")))
        background = Adw.SwitchRow(
            use_markup=False, title=_("Keep Running When Closing the Window"),
            subtitle=_("When a tray icon is available. Choose Quit to stop Scriba."),
            active=cfg.keep_running)
        background.connect("notify::active", lambda row, _prop: self._set("keep_running", row.get_active()))
        group.add(background)
        page.add(group)

        if WINDOWS:
            group = Adw.PreferencesGroup(title=escape(_("Updates")))
            updates = Adw.SwitchRow(use_markup=False, title=_("Check for updates automatically"),
                                    subtitle=_("When you open or restore the window, Scriba checks GitHub if the "
                                               "last successful check was at least {minutes} minutes ago."
                                               ).format(minutes=config.UPDATE_CHECK_INTERVAL_MINUTES),
                                    active=cfg.check_updates)
            updates.connect("notify::active", lambda r, _p: self._set("check_updates", r.get_active()))
            group.add(updates)
            page.add(group)
        self.add(page)
        self._devices_cancel = threading.Event()
        app._preferences_dialogs.add(self)
        self.connect("closed", self._closed)
        run_in_thread(lambda: self.backend.devices(cancelled=self._devices_cancel), self._devices_ready)

    def _use_native_title(self) -> bool:
        root = self.get_root()
        if isinstance(root, Gtk.Window) and root is not self.window:
            for header in header_bars_below(self, Adw):
                header.set_title_widget(Gtk.Box())
                header.set_show_start_title_buttons(False)
                header.set_show_end_title_buttons(False)
        return False

    def _closed(self, *_args) -> None:
        self.cancel_device_discovery()
        self.app._preferences_dialogs.discard(self)

    def cancel_device_discovery(self) -> None:
        """Stop the owned discovery before the application waits for workers."""
        self._devices_cancel.set()

    def _devices_ready(self, found, error) -> None:
        if not self._devices_cancel.is_set() and not self.app.closing and error is None:
            self._fill_devices(found or [])

    def _set(self, name: str, value) -> None:
        setattr(self.app.cfg, name, value)
        self.app.save_config()
        self.window.settings_changed()

    def _set_theme(self, theme: str) -> None:
        from .application import apply_theme
        self._set("theme", theme)
        apply_theme(theme)

    # -- the card ------------------------------------------------------------------

    def _fill_devices(self, found) -> None:
        cfg = self.app.cfg
        names = [d.name for d in found]
        if cfg.device and cfg.device != "cpu" and cfg.device not in names:
            names.append(cfg.device)            # chosen before, not seen now: kept, and said
        self._devices = ["", *names, "cpu"]
        labels = [_("Automatic")]
        for name in names:
            labels.append(name if name in (d.name for d in found) else _("{name} (not found)").format(name=name))
        labels.append(_("Processor Only"))
        self._filling = True
        try:
            self.device_row.set_model(Gtk.StringList.new(labels))
            self.device_row.set_selected(self._devices.index(cfg.device) if cfg.device in self._devices else 0)
        finally:
            self._filling = False
        self._show_automatic()

    def _show_automatic(self) -> None:
        measured = self.app.cfg.measured_device
        if measured == "cpu":
            now = _("the processor")
        else:
            now = measured or _("the card with memory of its own, until the test")
        self.device_row.set_subtitle(_("Automatic uses the fastest in the test: {now}").format(now=now))

    def _show_test(self) -> None:
        """The test of the cards as it stands: running, failed, or what it
        chose and why (a faster card left out for its wrong words)."""
        cards = self.window.cards
        running = cards.running is not None
        self.test_spinner.set_visible(running)
        self.test_button.set_sensitive(not running and not self.window.record.busy
                                       and not self.window.transcribe.busy and not self.app.downloads.busy
                                       and self.backend.has(models.WHISPER_TURBO))
        self.test_row.remove_css_class("warning")
        measured = self.app.cfg.measured_device
        if running:
            text = _("Testing each card and the processor on a sample of speech the app carries (Collodi's "
                     "Pinocchio, read for LibriVox)…")
        elif cards.failed:
            self.test_row.add_css_class("warning")
            text = _("The test failed: {reason}").format(reason=cards.failed)
        elif measured:
            chosen = _("the processor") if measured == "cpu" else measured
            text = " ".join([_("The fastest that writes the sample of speech right: {card}.").format(card=chosen),
                             *cards.notes])
        elif not self.backend.has(models.WHISPER_TURBO):
            text = _("The test runs once the models are downloaded.")
        else:
            text = _("Not tested yet.")
        self.test_row.set_subtitle(text)
        self._show_automatic()

    def _test_again(self) -> None:
        if self.window.record.busy or self.window.transcribe.busy or self.app.downloads.busy:
            self.add_toast(Adw.Toast(use_markup=False, title=_("Work is in progress: test the graphics cards after it finishes."),
                                     timeout=6))
            return
        self.window.cards.start()

    def _chose_device(self) -> None:
        if getattr(self, "_filling", False):
            return
        index = self.device_row.get_selected()
        if 0 <= index < len(self._devices):
            self._set("device", self._devices[index])

    def _open_models(self):
        self.close()
        self.window.show_models()

    # -- the files of terms ----------------------------------------------------------

    def _choose_terms_file(self) -> None:
        dialog = Gtk.FileDialog(title=_("Add a File of Terms"), modal=True)
        text = Gtk.FileFilter(name=_("Text files"))
        text.add_suffix("txt")
        text.add_mime_type("text/plain")
        everything = Gtk.FileFilter(name=_("All files"))
        everything.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(text)
        filters.append(everything)
        dialog.set_filters(filters)
        dialog.set_default_filter(text)
        chosen = self.app.cfg.term_file_list()
        if chosen and os.path.isdir(os.path.dirname(chosen[-1])):
            dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(chosen[-1])))
        dialog.open(self.get_native() or self.window, None,
                    file_dialog_done(self.window, "open_finish", self._add_terms_file))

    def _add_terms_file(self, path: str) -> None:
        self._set_term_files([*self.app.cfg.term_file_list(), jobs.full_path(path)])

    def _remove_terms_file(self, path: str) -> None:
        self._set_term_files([p for p in self.app.cfg.term_file_list() if p != path])

    def _set_term_files(self, paths) -> None:
        self.app.cfg.set_term_files(paths)
        self.app.save_config()
        self.window.settings_changed()
        self._show_term_files()

    def _show_term_files(self) -> None:
        """A row for each file, read in a thread: how many terms it has, or
        why it cannot be used (a warning, and a line in the log of errors);
        under them how many whisper gets of all of them."""
        for row in self.term_rows:
            self.terms_group.remove(row)
        self.term_rows = []
        self._term_icons: list[Gtk.Image] = []
        paths = self.app.cfg.term_file_list()
        self._terms_token = token = object()
        for path in paths:
            row = Adw.ActionRow(use_markup=False, title=os.path.basename(path) or path, subtitle=jobs.full_path(path),
                                subtitle_lines=2)
            icon = Gtk.Image(icon_name="text-x-generic-symbolic")
            row.add_prefix(icon)
            self._term_icons.append(icon)
            remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=_("Remove from the List"),
                                valign=Gtk.Align.CENTER, css_classes=["flat"])
            remove.connect("clicked", lambda _b, p=path: self._remove_terms_file(p))
            row.add_suffix(remove)
            self.terms_group.remove(self.add_terms_row)         # the rows above the button
            self.terms_group.add(row)
            self.terms_group.add(self.add_terms_row)
            self.term_rows.append(row)
        self.terms_count = None
        self._describe_terms()
        if paths:
            run_in_thread(lambda: jobs.read_term_files(paths), lambda found, error: self._term_files_read(
                token, found, error))

    def _term_files_read(self, token, found, error) -> None:
        if token is not self._terms_token or error is not None:
            if error is not None:
                report("reading the files of terms", error)
            return
        for row, icon, read in zip(self.term_rows, self._term_icons, found, strict=True):
            if read.error:
                row.set_subtitle(read.error)
                icon.set_from_icon_name("dialog-warning-symbolic")
                icon.add_css_class("warning")
            else:
                count = ngettext("{n} term", "{n} terms", len(read.terms)).format(n=len(read.terms))
                row.set_subtitle(f"{count} · {jobs.full_path(read.path)}")
        chosen = terms.joined((), [f.terms for f in found if not f.error])
        if chosen.left_out:
            self.terms_count = _("Only {used} of these {offered} terms fit in what the engine reads; the meeting’s "
                                 "own come first.").format(used=len(chosen.terms), offered=chosen.offered)
        else:
            self.terms_count = ngettext("The engine reads the {n} term, after the meeting’s own.",
                                        "The engine reads all {n} terms, after the meeting’s own.",
                                        chosen.offered).format(n=chosen.offered)
        self._describe_terms()

    def _describe_terms(self) -> None:
        text = _("Read at every transcription, after the terms typed for the meeting. Plain text, one term or "
                 "phrase a line; lines starting with # are left out.")
        self.terms_group.set_description(escape(f"{text} {self.terms_count}" if self.terms_count else text))

    # -- the folder ----------------------------------------------------------------

    def _set_folder(self, folder: str) -> None:
        self._set("output_folder", folder)
        self._show_folder()

    def _set_recordings(self, folder: str) -> None:
        refused = recording.unsuitable(folder, recording.own_folders(self.app.paths)) if folder else ""
        if refused:
            self.add_toast(Adw.Toast(use_markup=False, title=refused, timeout=8))
            return
        self._set("recordings_folder", folder)
        self._show_recordings()

    def _show_recordings(self) -> None:
        self.recordings_row.set_subtitle(jobs.full_path(recordings_folder(self.app.cfg)))
        self.recordings_reset.set_visible(bool(self.app.cfg.recordings_folder))

    def _show_folder(self) -> None:
        folder = self.app.cfg.output_folder
        shown = jobs.full_path(folder) if folder else _("Next to the recording (the documents folder for a "
                                                        "recording on a server)")
        self.folder_row.set_subtitle(shown)
        self.folder_reset.set_visible(bool(folder))


def _add_credits(dialog):
    for credit in credits.THIRD_PARTY:
        owner = credit.holder if credit.licence in PUBLIC_DOMAIN else f"© {credit.holder}"
        notice = f"{owner}\n{credit.role}: {credit.url}"
        if credit.licence in LICENCES:
            dialog.add_legal_section(credit.name, escape(notice), LICENCES[credit.licence], None)
        else:
            name, url = CUSTOM_LICENCES.get(credit.licence, (credit.licence, credit.url))
            dialog.add_legal_section(credit.name, escape(notice), Gtk.License.CUSTOM,
                                     f'{escape(name)}: <a href="{escape(url)}">{escape(url)}</a>')


def about_dialog(parent, cfg, paths, present: bool = True) -> Adw.AboutDialog:
    metadata = dict(
        application_name=APP_NAME, application_icon=APP_ID, version=__version__,
        developer_name="Daniel Grasso", license_type=Gtk.License.GPL_3_0, website=WEBSITE,
        issue_url=WEBSITE + "/issues")
    dialog = about_widgets.about_dialog(
        metadata, cfg, paths, toolkit_versions(), run_in_thread,
        debug_filename=f"{APP_ID}-debug-info.txt", add_credits=_add_credits)
    dialog.set_comments(escape(_("Transcribes recordings of meetings on your own computer, with speakers and subtitles. "
                                 "Nothing leaves the computer: the models are downloaded once, and checked.")))
    if present:
        present_secondary(dialog, parent)
    return dialog
