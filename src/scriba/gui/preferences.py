"""The preferences (Adw.PreferencesDialog) and the About window
(Adw.AboutDialog, with the licences of the engines and the models in its
legal sections)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from scriba import APP_ID, APP_NAME, WEBSITE, WINDOWS, __version__  # noqa: E402
from scriba.app import credits, diagnostics, jobs, models, recording  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_XL  # noqa: E402
from .ui import choose_folder, escape, recordings_folder, run_in_thread, toolkit_versions  # noqa: E402

QUALITIES = ("turbo", "large")
LICENCES = {"MIT": Gtk.License.MIT_X11, "Apache-2.0": Gtk.License.APACHE_2_0, "BSD-3-Clause": Gtk.License.BSD_3,
            "GPL-3.0-or-later": Gtk.License.GPL_3_0, "LGPL-2.1-or-later": Gtk.License.LGPL_2_1}
CUSTOM_LICENCES = {"CC-BY-4.0": ("Creative Commons Attribution 4.0 International",
                                 "https://creativecommons.org/licenses/by/4.0/"),
                   "CC-PDDC": ("Public domain (Creative Commons Public Domain Dedication and Certification)",
                               "https://creativecommons.org/licenses/publicdomain/")}
PUBLIC_DOMAIN = ("CC-PDDC",)        # nobody's: no © before the name


def preference_size(width, height, available_width, available_height):
    """Bound the initial content to the actual work area, without a size floor."""
    return min(width, max(1, available_width)), min(height, max(1, available_height))


def _win32_workarea(monitor):
    # GTK exports this API, but its Windows typelib omits the binding.
    # Use GTK's own application-pixel work area, including fractional DPI.
    import ctypes

    class Rectangle(ctypes.Structure):
        _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int),
                    ("width", ctypes.c_int), ("height", ctypes.c_int)]
    unpack = ctypes.pythonapi.PyCapsule_GetPointer
    unpack.argtypes = [ctypes.py_object, ctypes.c_char_p]
    unpack.restype = ctypes.c_void_p
    gtk = ctypes.CDLL("libgtk-4-1.dll")
    workarea = gtk.gdk_win32_monitor_get_workarea
    workarea.argtypes = [ctypes.c_void_p, ctypes.POINTER(Rectangle)]
    workarea.restype = None
    area = Rectangle()
    workarea(unpack(monitor.__gpointer__, None), ctypes.byref(area))
    return area


def _available_size(window):
    display = window.get_display()
    surface = window.get_surface()
    monitor = display.get_monitor_at_surface(surface) if surface is not None else display.get_monitors().get_item(0)
    if monitor is None:
        return 600, 640
    if WINDOWS:
        area = _win32_workarea(monitor)
    else:
        area = monitor.get_geometry()
    # Space for the native frame, in the same application pixels as GTK.
    return area.width - 2 * SPACE_XL, area.height - 2 * SPACE_XL


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, window, app):
        super().__init__(title=_("Preferences"))
        self.window, self.app = window, app
        self.backend = app.backend
        cfg = app.cfg
        if WINDOWS:
            width, height = preference_size(600, 640, *_available_size(window))
            self.set_content_width(width)
            self.set_content_height(height)
            self.connect("realize", self._prepare_window)
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(title=_("Appearance"))
        themes = [("system", _("Follow the System")), ("light", _("Light")), ("dark", _("Dark"))]
        theme = Adw.ComboRow(title=_("Theme"))
        theme.set_model(Gtk.StringList.new([label for _code, label in themes]))
        theme.set_selected(next((i for i, (code, _l) in enumerate(themes) if code == cfg.theme), 0))
        theme.connect("notify::selected", lambda r, _p: self._set_theme(themes[r.get_selected()][0]))
        group.add(theme)
        languages = [("", _("Desktop language")), ("en", "English"), ("it", "Italiano")]
        language = Adw.ComboRow(title=_("Language"), subtitle=_("Applied at the next start"))
        language.set_model(Gtk.StringList.new([label for _code, label in languages]))
        language.set_selected(next((i for i, (code, _l) in enumerate(languages) if code == cfg.language), 0))
        language.connect("notify::selected", lambda r, _p: self._set("language", languages[r.get_selected()][0]))
        group.add(language)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Transcription"))
        self.my_name = Adw.EntryRow(title=_("My Name"), text=cfg.my_name)
        self.my_name.connect("changed", lambda row: self._set("my_name", row.get_text()))
        group.add(self.my_name)
        self.device_row = Adw.ComboRow(title=_("Graphics Card"), subtitle_lines=2)
        self._devices: list[str] = []           # the choices behind the rows: "", names, "cpu"
        self._fill_devices([])
        self.device_row.connect("notify::selected", lambda r, _p: self._chose_device())
        group.add(self.device_row)
        self.test_row = Adw.ActionRow(title=_("Test of the Graphics Cards"), subtitle_lines=5)
        self.test_spinner = Adw.Spinner(visible=False)
        self.test_row.add_suffix(self.test_spinner)
        self.test_button = Gtk.Button(label=_("Test Again"), valign=Gtk.Align.CENTER)
        self.test_button.connect("clicked", lambda *_: self._test_again())
        self.test_row.add_suffix(self.test_button)
        group.add(self.test_row)
        handler = window.cards.connect("changed", lambda _test: self._show_test())
        self.connect("closed", lambda *_: window.cards.disconnect(handler))
        self._show_test()
        self.quality_row = Adw.ComboRow(title=_("Usual Quality"),
                                        model=Gtk.StringList.new([_("Fast"), _("Precise, slower")]))
        self.quality_row.set_selected(QUALITIES.index(cfg.quality) if cfg.quality in QUALITIES else 0)
        self.quality_row.connect("notify::selected", lambda r, _p: self._set("quality", QUALITIES[r.get_selected()]))
        group.add(self.quality_row)
        self.models_row = Adw.ActionRow(title=_("Speech Models"),
                                        subtitle=_("Downloads, progress and the optional Precise model"))
        manage = Gtk.Button(label=_("Manage Models"), valign=Gtk.Align.CENTER)
        manage.connect("clicked", lambda *_: self._open_models())
        self.models_row.add_suffix(manage)
        group.add(self.models_row)
        unsubscribe = app.downloads.subscribe(lambda _state: self._show_test())
        self.connect("closed", lambda *_: unsubscribe())
        self.folder_row = Adw.ActionRow(title=_("Usual Folder for the Files"), subtitle_lines=2)
        self.folder_reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Next to the Recording"),
                                       valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.folder_reset.connect("clicked", lambda *_: self._set_folder(""))
        self.folder_row.add_suffix(self.folder_reset)
        choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose a Folder…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", lambda *_: choose_folder(self.window, _("Choose the Usual Folder for the Files"),
                                                           cfg.output_folder or None, self._set_folder, owner_widget=self))
        self.folder_row.add_suffix(choose)
        group.add(self.folder_row)
        self._show_folder()
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Recording"))
        self.recordings_row = Adw.ActionRow(title=_("Folder for the Recordings"), subtitle_lines=2)
        self.recordings_reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Back to the Usual Folder"),
                                           valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.recordings_reset.connect("clicked", lambda *_: self._set_recordings(""))
        self.recordings_row.add_suffix(self.recordings_reset)
        choose = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose a Folder…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        choose.connect("clicked", lambda *_: choose_folder(self.window, _("Choose the Folder for the Recordings"),
                                                           str(recordings_folder(self.app.cfg)),
                                                           self._set_recordings, owner_widget=self))
        self.recordings_row.add_suffix(choose)
        group.add(self.recordings_row)
        self._show_recordings()
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Background"))
        background = Adw.SwitchRow(
            title=_("Keep Running When Closing the Window"),
            subtitle=_("When a tray icon is available. Choose Quit to stop Scriba."),
            active=cfg.keep_running)
        background.connect("notify::active", lambda row, _prop: self._set("keep_running", row.get_active()))
        group.add(background)
        page.add(group)

        if WINDOWS:
            group = Adw.PreferencesGroup(title=_("Updates"))
            updates = Adw.SwitchRow(title=_("Check for updates at start-up"),
                                    subtitle=_("Once a day, from the project's releases on GitHub; "
                                               "nothing about you or your files is sent"),
                                    active=cfg.check_updates)
            updates.connect("notify::active", lambda r, _p: self._set("check_updates", r.get_active()))
            group.add(updates)
            page.add(group)
        self.add(page)
        run_in_thread(lambda: self.backend.devices(), lambda found, _error: self._fill_devices(found or []))

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
        self.device_row.set_subtitle(escape(_("Automatic uses the fastest in the test: {now}").format(now=now)))

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
        self.test_row.set_subtitle(escape(text))
        self._show_automatic()

    def _test_again(self) -> None:
        if self.window.record.busy or self.window.transcribe.busy or self.app.downloads.busy:
            self.add_toast(Adw.Toast(title=escape(_("Work is in progress: test the graphics cards after it finishes.")),
                                     timeout=6))
            return
        self.window.cards.start()

    def _chose_device(self) -> None:
        if getattr(self, "_filling", False):
            return
        index = self.device_row.get_selected()
        if 0 <= index < len(self._devices):
            self._set("device", self._devices[index])

    def _prepare_window(self, _dialog):
        root = self.get_root()
        if isinstance(root, Gtk.Window) and root is not self.window:
            root.set_resizable(True)
            size = preference_size(self.get_content_width(), self.get_content_height(), *_available_size(self.window))
            root.set_default_size(*size)

    def _open_models(self):
        self.close()
        self.window.show_models()

    # -- the folder ----------------------------------------------------------------

    def _set_folder(self, folder: str) -> None:
        self._set("output_folder", folder)
        self._show_folder()

    def _set_recordings(self, folder: str) -> None:
        refused = recording.unsuitable(folder, recording.own_folders(self.app.paths)) if folder else ""
        if refused:
            self.add_toast(Adw.Toast(title=escape(refused), timeout=8))
            return
        self._set("recordings_folder", folder)
        self._show_recordings()

    def _show_recordings(self) -> None:
        self.recordings_row.set_subtitle(escape(jobs.full_path(recordings_folder(self.app.cfg))))
        self.recordings_reset.set_visible(bool(self.app.cfg.recordings_folder))

    def _show_folder(self) -> None:
        folder = self.app.cfg.output_folder
        shown = jobs.full_path(folder) if folder else _("Next to the recording (the documents folder for a "
                                                        "recording on a server)")
        self.folder_row.set_subtitle(escape(shown))
        self.folder_reset.set_visible(bool(folder))


def about_dialog(parent, cfg, paths, present: bool = True) -> Adw.AboutDialog:
    dialog = Adw.AboutDialog(
        application_name=APP_NAME, application_icon=APP_ID, version=__version__,
        developer_name="Daniel Grasso", license_type=Gtk.License.GPL_3_0, website=WEBSITE,
        issue_url=WEBSITE + "/issues",
        comments=_("Transcribes recordings of meetings on your own computer, with speakers and subtitles. "
                   "Nothing leaves the computer: the models are downloaded once, and checked."))
    # the engines and the models, each with its licence: what they ask to be
    # said (app/credits.py)
    for credit in credits.THIRD_PARTY:
        owner = credit.holder if credit.licence in PUBLIC_DOMAIN else f"© {credit.holder}"
        notice = f"{owner}\n{credit.role}: {credit.url}"
        if credit.licence in LICENCES:
            dialog.add_legal_section(credit.name, notice, LICENCES[credit.licence], None)
        else:
            name, url = CUSTOM_LICENCES.get(credit.licence, (credit.licence, credit.url))
            dialog.add_legal_section(credit.name, notice, Gtk.License.CUSTOM,
                                     f'{escape(name)}: <a href="{escape(url)}">{escape(url)}</a>')
    # Troubleshooting: what a report needs, with its Save button; the slow
    # part (the room the app's folders take) follows, from a thread
    head = diagnostics.summary(APP_NAME, __version__, cfg, paths, toolkit_versions())
    dialog.set_debug_info_filename(f"{APP_ID}-debug-info.txt")
    dialog.set_debug_info(head + "\n" + _("Measuring the space taken by the app's files…"))
    run_in_thread(lambda: diagnostics.storage(paths),
                  lambda text, error: dialog.set_debug_info(head + "\n" + (text if error is None else str(error))))
    if present:
        dialog.present(parent)
    return dialog
