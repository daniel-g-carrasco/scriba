"""The Transcribe page: the recording, the choices, the expected time, the
work with its steps, and what went wrong said in words.

One page, in four states (a Gtk.Stack): an empty page that asks for a recording (an Adw.StatusPage with
"Open…", and the whole window takes a file dropped on it), the choices
(libadwaita's rows in boxed lists, as GNOME's converters have them:
Switcheroo, Constrict, Video Trimmer), the work (a status page with the
spinner, the bar and one row per step) and a failure (a status page with
the reason). The result is a page of its own, pushed on top
(gui/result.py).

The disk and the engines are never touched from here: the recording is
read, the cards tested and the work done in threads (ui.run_in_thread),
through the window's Backend (app/backend.py); the work's Progress is read
four times a second.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from scriba import APP_NAME  # noqa: E402
from scriba.app import jobs, models  # noqa: E402
from scriba.app.task import Cancelled, Failure  # noqa: E402
from scriba.core import estimate  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import SPACE_XL  # noqa: E402
from .ui import choose_folder, documents_folder, escape, report, run_in_thread  # noqa: E402

CLAMP = 520
TICK_MS = 250
MAX_SPEAKERS = 12
LANGUAGES = ("auto", "it", "en")
QUALITIES = ("turbo", "large")


def language_names() -> list[str]:
    return [_("Automatic"), _("Italian"), _("English")]


def property_row(title: str) -> Adw.ActionRow:
    """A «what: value» row (libadwaita's `property` class): the value is
    the subtitle, shown as the main text."""
    row = Adw.ActionRow(title=escape(title), css_classes=["property"])
    return row


class TranscribePage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.app = window.app
        self.backend = window.backend
        self.source: Path | None = None
        self.media = None                   # ffmpeg.Media of the source, once read
        self.out_override: Path | None = None
        self._reading = 0                   # which reading of a file is the current one
        self._inspect_cancel: threading.Event | None = None
        self._pending_start: tuple[Path, int] | None = None
        self.cards = window.cards           # the test of the graphics cards (gui/cards.py)
        self.job = None                    # (progress, cancel, began, estimated, request) while working
        self._tick_id = 0

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._empty(), "empty")
        self.stack.add_named(self._setup(), "setup")
        self.stack.add_named(self._working(), "working")
        self.stack.add_named(self._failed(), "failed")
        self.set_child(self.stack)
        self.cards.connect("changed", lambda _test: self._cards_changed())
        self._was_missing = bool(self.backend.missing())
        self._unsubscribe_downloads = self.app.downloads.subscribe(self._models_changed)
        self.show_start()
        # the cards whisper sees, once: the estimate and the preferences name them
        run_in_thread(lambda: self.backend.devices(), lambda _found, _error: self.refresh_estimate())

    @property
    def cfg(self):
        return self.app.cfg

    @property
    def busy(self) -> bool:
        return self.job is not None or self._pending_start is not None

    # -- the states ------------------------------------------------------------------

    def show_start(self) -> None:
        """File choices stay accessible while the application prepares the models."""
        if getattr(self.app, "closing", False):
            return
        if not self.busy:
            self.stack.set_visible_child_name("setup" if self.source is not None else "empty")
            self._update_model_gate()
            if not self.backend.missing():
                self.cards.start_if_needed()
        self.window.page_changed()

    def _missing_models(self):
        wanted = (*self.app.downloads.required, models.WHISPER.get(self.cfg.quality, models.WHISPER_TURBO))
        return list({model.key: model for model in wanted if not self.backend.has(model)}.values())

    def _update_model_gate(self):
        missing = self._missing_models()
        self.models_needed.set_visible(bool(missing))
        self.models_needed.set_subtitle(escape(_(
            "Download the missing models, or choose Fast if only the Precise model is missing.")))
        self.start_button.set_sensitive(self.media is not None and not missing and not self.busy)

    def _models_changed(self, _state):
        missing = bool(self.backend.missing())
        if getattr(self.app, "closing", False):
            self._was_missing = missing
            return
        if self._was_missing and not missing:
            self.window.toast(_("The models are ready: open a recording to transcribe it."))
        self._was_missing = missing
        if not self.busy:
            self.show_start()
            self.refresh_estimate()

    def clear(self) -> None:
        """Back to the empty page, for a new recording."""
        self.cancel_inspection()
        self.source, self.media, self.out_override = None, None, None
        self.show_start()

    def settings_changed(self) -> None:
        """A preference changed (the card, the quality, the folder, a model
        downloaded): the rows say it again."""
        if self.source is not None and not self.busy:
            self.cfg.terms = self.terms_row.get_text().strip()      # typed and not saved yet: kept
            self._load_choices()
            self._show_out()
            self.refresh_estimate()
            self._show_device()

    def stop_all(self) -> None:
        """The window closes: the test of the cards and the work stop (their
        programs are terminated, their temporary folders go)."""
        self.cancel_inspection()
        self.cards.stop()
        # A failed recording/export can abort shutdown. Keep model updates
        # connected while the application still owns this page.
        if self.job is not None:
            self.job[1].set()

    def cancel_inspection(self) -> None:
        """Invalidate the reader before requesting termination of its subprocess."""
        self._pending_start = None
        self._reading += 1
        if self._inspect_cancel is not None:
            self._inspect_cancel.set()
            self._inspect_cancel = None

    def _empty(self) -> Gtk.Widget:
        page = Adw.StatusPage(icon_name="audio-x-generic-symbolic", title=_("Transcribe a Recording"),
                              description=escape(_("Open an audio or video file, or drop it here: a Teams mp4, an "
                                                   "OBS mkv, a phone's m4a, an mp3 or a wav. Nothing leaves this "
                                                   "computer.")))
        button = Gtk.Button(label=_("Open…"), halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        button.connect("clicked", lambda *_: self.window.choose_file())
        page.set_child(button)
        self.empty_page = page
        return page

    # -- the choices -----------------------------------------------------------------

    def _setup(self) -> Gtk.Widget:
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup()
        self.file_row = Adw.ActionRow(title_lines=2, subtitle_lines=2)
        self.file_icon = Gtk.Image(icon_name="audio-x-generic-symbolic")
        self.file_row.add_prefix(self.file_icon)
        change = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open Another Recording…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        change.connect("clicked", lambda *_: self.window.choose_file())
        self.file_row.add_suffix(change)
        group.add(self.file_row)
        self.device_row = Adw.ActionRow(title=_("Graphics Card"), subtitle_lines=5)
        self.device_icon = Gtk.Image(icon_name="video-display-symbolic")
        self.device_row.add_prefix(self.device_icon)
        self.device_spinner = Adw.Spinner(visible=False)
        self.device_row.add_suffix(self.device_spinner)
        group.add(self.device_row)
        self.estimate_row = property_row(_("Expected Time"))
        group.add(self.estimate_row)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Transcription"))
        self.language_row = Adw.ComboRow(title=_("Language"), model=Gtk.StringList.new(language_names()))
        self.language_row.connect("notify::selected", lambda r, _p: self._chose(
            "speech_language", LANGUAGES[r.get_selected()]))
        group.add(self.language_row)
        self.quality_row = Adw.ComboRow(title=_("Quality"),
                                        model=Gtk.StringList.new([_("Fast"), _("Precise, slower")]))
        self.quality_row.connect("notify::selected", lambda r, _p: self._chose("quality", QUALITIES[r.get_selected()]))
        group.add(self.quality_row)
        self.voices_row = Adw.ExpanderRow(title=_("Tell the Voices Apart"), show_enable_switch=True,
                                          subtitle=escape(_("Speaker 1, 2, 3…: the names are given afterwards")))
        self.voices_row.connect("notify::enable-expansion", lambda r, _p: self._chose(
            "speakers", r.get_enable_expansion()))
        counts = [_("Counted by the App")] + [str(n) for n in range(2, MAX_SPEAKERS + 1)]
        self.count_row = Adw.ComboRow(title=_("How Many People"), model=Gtk.StringList.new(counts),
                                      subtitle=escape(_("If you know how many, the voices come out better")))
        self.count_row.connect("notify::selected", lambda r, _p: self._chose(
            "speaker_count", 0 if r.get_selected() == 0 else r.get_selected() + 1))
        self.voices_row.add_row(self.count_row)
        group.add(self.voices_row)
        self.video_row = Adw.SwitchRow(title=_("Video with Subtitles"), visible=False,
                                       subtitle=escape(_("A copy of the video with the subtitles inside (mkv)")))
        self.video_row.connect("notify::active", lambda r, _p: self._chose("subtitled_video", r.get_active()))
        group.add(self.video_row)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Terms and Names"),
                                     description=escape(_("Names, acronyms and words of the trade to be written "
                                                          "right, separated by commas: the client, the project's "
                                                          "code, the people in the call.")))
        self.terms_row = Adw.EntryRow(title=_("Terms, separated by commas"))
        group.add(self.terms_row)
        page.add(group)

        group = Adw.PreferencesGroup(title=_("Files"))
        self.folder_row = Adw.ActionRow(title=_("Folder for the Files"), subtitle_lines=2)
        self.folder_row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        self.folder_reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Back to the Usual Folder"),
                                       valign=Gtk.Align.CENTER, css_classes=["flat"], visible=False)
        self.folder_reset.connect("clicked", lambda *_: self._set_out(None))
        self.folder_row.add_suffix(self.folder_reset)
        folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Choose a Folder…"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        folder.connect("clicked", lambda *_: choose_folder(self.window, _("Choose the Folder for the Files"),
                                                           str(self.out_folder()), lambda p: self._set_out(Path(p))))
        self.folder_row.add_suffix(folder)
        group.add(self.folder_row)
        page.add(group)

        group = Adw.PreferencesGroup()
        self.models_needed = Adw.ActionRow(title=_("Models Needed for This Transcription"), subtitle_lines=0)
        manage = Gtk.Button(label=_("Models"), valign=Gtk.Align.CENTER)
        manage.connect("clicked", lambda *_: self.window.show_models())
        self.models_needed.add_suffix(manage)
        group.add(self.models_needed)
        self.start_button = Gtk.Button(label=_("Transcribe"), halign=Gtk.Align.CENTER,
                                       css_classes=["pill", "suggested-action"])
        self.start_button.connect("clicked", lambda *_: self.start())
        group.add(self.start_button)
        page.add(group)
        self.setup_page = page
        return page

    def _load_choices(self) -> None:
        """The rows as the settings have them (remembered from last time)."""
        cfg = self.cfg
        self._loading = True
        try:
            self.language_row.set_selected(LANGUAGES.index(cfg.speech_language)
                                           if cfg.speech_language in LANGUAGES else 1)
            self.quality_row.set_visible(True)
            self.quality_row.set_selected(QUALITIES.index(cfg.quality) if cfg.quality in QUALITIES else 0)
            self.voices_row.set_enable_expansion(cfg.speakers)
            self.count_row.set_selected(0 if cfg.speaker_count < 2 else min(cfg.speaker_count, MAX_SPEAKERS) - 1)
            self.video_row.set_active(cfg.subtitled_video)
            self.terms_row.set_text(cfg.terms)
        finally:
            self._loading = False

    def _chose(self, name: str, value) -> None:
        if getattr(self, "_loading", False):
            return
        setattr(self.cfg, name, value)
        self._update_model_gate()
        self.refresh_estimate()

    def remember(self) -> None:
        """What the rows say, kept in the settings for next time."""
        if self.source is not None:
            self.cfg.terms = self.terms_row.get_text().strip()
        self.app.save_config()

    def out_folder(self) -> Path:
        if self.out_override is not None:
            return self.out_override
        if self.cfg.output_folder:
            return Path(self.cfg.output_folder)
        return jobs.default_folder(self.source, documents_folder()) if self.source else documents_folder()

    def _set_out(self, folder: Path | None) -> None:
        self.out_override = folder
        self._show_out()

    def _show_out(self) -> None:
        self.folder_row.set_subtitle(escape(jobs.full_path(self.out_folder())))
        self.folder_reset.set_visible(self.out_override is not None)

    # -- a recording ---------------------------------------------------------------

    def open(self, path: str) -> None:
        """A recording chosen, dropped or given on the command line."""
        if getattr(self.app, "closing", False):
            return
        if self.job is not None:
            self.window.toast(_("A transcription is in progress: cancel it first."))
            return
        source = Path(path)
        if not source.is_file():
            self.window.toast(_("“{name}” is not a file that can be transcribed.").format(name=source.name or path))
            return
        self.cancel_inspection()
        self.source, self.media, self.out_override = source, None, None
        self.cfg.last_folder = str(source.parent)
        self._load_choices()
        self.file_row.set_title(escape(source.name))
        self.file_row.set_subtitle(escape(_("Reading the recording…")))
        self.file_row.remove_css_class("error")
        self.file_icon.set_from_icon_name("audio-x-generic-symbolic")
        self.video_row.set_visible(False)
        self.start_button.set_sensitive(False)
        self.estimate_row.set_subtitle(escape(_("Reading the recording…")))
        self._show_out()
        self._show_device()
        self.show_start()
        reading = self._reading
        cancel = self._inspect_cancel = threading.Event()
        try:
            run_in_thread(lambda: self.backend.inspect(source, cancelled=cancel),
                          lambda media, error: self._read(reading, media, error))
        except Exception as error:
            self._read(reading, None, error)

    def _read(self, reading: int, media, error) -> None:
        if reading != self._reading or getattr(self.app, "closing", False):
            return                          # another recording was opened meanwhile
        self._inspect_cancel = None
        if isinstance(error, Cancelled):
            return
        if error is not None:
            report("reading the recording", error)
            self.file_row.set_subtitle(escape(str(error) if isinstance(error, Failure) else
                                              _("The recording cannot be read: {error}").format(error=error)))
            self.file_row.add_css_class("error")
            self.estimate_row.set_subtitle("")
            return
        self.media = media
        kind = _("video") if media.video else _("audio")
        self.file_icon.set_from_icon_name("video-x-generic-symbolic" if media.video else "audio-x-generic-symbolic")
        facts = [clock(media.duration), kind]
        if len(media.roles) > 1:
            mine = sum(1 for t in media.roles if t.role == "me")
            facts.append(_("{n} tracks: your microphone and the computer's sound").format(n=len(media.roles))
                         if mine else _("{n} tracks").format(n=len(media.roles)))
        facts.append(jobs.full_path(self.source.parent))
        self.file_row.set_subtitle(escape(" · ".join(facts)))
        self.video_row.set_visible(media.video)
        self._update_model_gate()
        self.refresh_estimate()

    def request(self) -> jobs.Request:
        return self.backend.request(self.cfg, self.source, self.out_folder(), bool(self.media and self.media.video))

    def refresh_estimate(self) -> None:
        if self.source is None or self.media is None:
            return
        if self._missing_models():
            self.estimate_row.set_subtitle(escape(_("Waiting for the models selected above.")))
            return
        seconds = self.backend.estimate(self.media.duration, self.request())
        self.estimate_row.set_subtitle(escape(_("{about} for {duration} of audio").format(
            about=estimate.about(seconds).capitalize(), duration=clock(self.media.duration))))
        self._show_device()

    # -- where whisper works: the test of the cards ---------------------------------

    def _cards_changed(self) -> None:
        """The test of the cards started, ended or failed: the row says it, and
        the estimate follows the speed just measured."""
        if getattr(self.app, "closing", False):
            self._pending_start = None
            self.window.page_changed()
            return
        if self._pending_start is not None and self.cards.running is None:
            pending, self._pending_start = self._pending_start, None
            if pending == (self.source, self._reading):
                self.start()
            else:
                self.show_start()
            return
        if self.source is not None and self.media is not None:
            self.refresh_estimate()
        else:
            self._show_device()
        if not self.busy:
            self.cards.start_if_needed()    # a cancelled test can finish after the transcription
        self.window.page_changed()

    def _show_device(self) -> None:
        cfg = self.cfg
        chosen = cfg.device or cfg.measured_device
        testing = self.cards.running is not None
        self.device_spinner.set_visible(testing)
        self.device_row.remove_css_class("warning")
        if testing and not cfg.device:
            self.device_row.set_subtitle(escape(_("Testing the graphics cards on a sample of speech, to choose "
                                                  "the fastest that writes it right…")))
            return
        if self.cards.failed and not chosen:
            self.device_row.add_css_class("warning")
            self.device_row.set_subtitle(escape(_("The test of the graphics cards failed: {reason}").format(
                reason=self.cards.failed)))
            return
        if not chosen:
            self.device_row.set_subtitle(escape(_("Not tested yet: the test runs once the models are there.")))
            return
        # an hour as most meetings are done: with the voices
        request = replace(self.backend.request(cfg, self.source or Path("meeting"), None, False), speakers=True)
        hour = estimate.about(self.backend.hour(request))
        self.device_icon.set_from_icon_name("computer-symbolic" if chosen == "cpu" else "video-display-symbolic")
        if chosen == "cpu":
            text = _("We will use the processor: an hour of audio in {about}.").format(about=hour)
        else:
            text = _("We will use the {card}: an hour of audio in {about}.").format(card=chosen, about=hour)
        if cfg.device:
            text += " " + _("(chosen in the preferences)")
        else:
            text = " ".join([text, *self.cards.notes])
        self.device_row.set_subtitle(escape(text))

    # -- the work ------------------------------------------------------------------

    def _working(self) -> Gtk.Widget:
        self.work_page = Adw.StatusPage()
        self.work_page.set_paintable(Adw.SpinnerPaintable(widget=self.work_page))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XL)
        self.work_bar = Gtk.ProgressBar(show_text=True)
        box.append(self.work_bar)
        self.steps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        box.append(self.steps)
        self.cancel_button = Gtk.Button(label=_("Cancel"), halign=Gtk.Align.CENTER, css_classes=["pill"])
        self.cancel_button.connect("clicked", lambda *_: self.cancel())
        box.append(self.cancel_button)
        self.work_page.set_child(Adw.Clamp(maximum_size=CLAMP, child=box))
        return self.work_page

    def start(self) -> None:
        if getattr(self.app, "closing", False):
            return
        if self.app.installing:
            self.window.toast(_("An update is starting: wait for {app} to open again.").format(app=APP_NAME))
            return
        if self.busy or self.source is None or self.media is None:
            return
        if self._missing_models():
            self.window.show_models()
            return
        if self.cards.running is not None:
            # The cancel Event is a request, not proof the GPU process exited.
            # Reserve one user intention before stop emits its changed signal.
            self._pending_start = (self.source, self._reading)
            self.work_page.set_title(escape(_("Transcribing {name}").format(name=self.source.name)))
            self.work_page.set_description(escape(_("Waiting for the Graphics Card Test to Stop…")))
            self.work_bar.set_fraction(0.0)
            self.cancel_button.set_sensitive(True)
            self._build_steps(())
            self.stack.set_visible_child_name("working")
            self.cards.stop()
            self.window.page_changed()
            return
        self.cards.stop()           # the work wants the card; the test runs again after, if it never ended
        self.remember()
        request = self.request()
        progress = jobs.Progress()
        cancel = threading.Event()
        estimated = self.backend.estimate(self.media.duration, request)
        self.job = (progress, cancel, time.monotonic(), estimated, request)
        self.work_page.set_title(escape(_("Transcribing {name}").format(name=self.source.name)))
        self.work_page.set_description(escape(_("Starting…")))
        self.work_bar.set_fraction(0.0)
        self.cancel_button.set_sensitive(True)
        self._step_rows: dict[str, tuple] = {}
        self._build_steps(jobs.stages_for(request))
        self.stack.set_visible_child_name("working")
        self.window.page_changed()
        self._tick_id = GLib.timeout_add(TICK_MS, self._tick)
        try:
            run_in_thread(lambda: self.backend.run(request, progress, cancel),
                          lambda result, error: self._finished(progress, result, error))
        except Exception as error:
            self._finished(progress, None, error)

    def _build_steps(self, stages) -> None:
        while (row := self.steps.get_row_at_index(0)) is not None:
            self.steps.remove(row)
        self._step_rows = {}
        names = jobs.stage_names()
        for stage in stages:
            row = Adw.ActionRow(title=escape(names.get(stage, stage)))
            spinner = Adw.Spinner(visible=False)
            done = Gtk.Image(icon_name="object-select-symbolic", css_classes=["success"], visible=False)
            row.add_suffix(spinner)
            row.add_suffix(done)
            row.add_css_class("dim-label")
            self.steps.append(row)
            self._step_rows[stage] = (row, spinner, done)
        self._shown_stages = tuple(stages)

    def _tick(self) -> bool:
        if self.job is None:
            self._tick_id = 0
            return False
        progress, cancel, began, estimated, _request = self.job
        now = progress.snapshot()
        if tuple(now["stages"]) != self._shown_stages:
            self._build_steps(now["stages"])
        overall = progress.overall()
        self.work_bar.set_fraction(overall)
        self.work_bar.set_text(f"{overall:.0%}")
        names = jobs.stage_names()
        for stage, (row, spinner, done) in self._step_rows.items():
            finished, current = stage in now["finished"], stage == now["stage"] and stage not in now["finished"]
            spinner.set_visible(current)
            done.set_visible(finished and stage not in now["skipped"])
            if finished or current:
                row.remove_css_class("dim-label")
            if stage in now["skipped"]:
                row.set_subtitle(escape(_("Not needed")))
            elif finished:
                row.set_subtitle(escape(estimate.span(now["finished"][stage])))
            elif current and now["state"] == "loading":
                row.set_subtitle(escape(_("Loading the model")))
            elif current:
                row.set_subtitle(f"{now['fraction']:.0%}")
        if cancel.is_set():
            self.work_page.set_description(escape(_("Stopping…")))
        elif now["stage"] is not None:
            left = estimate.remaining(estimated, time.monotonic() - began, overall)
            self.work_page.set_description(escape(_("{step}: {about} to go").format(
                step=names.get(now["stage"], now["stage"]), about=estimate.about(left))))
        return True

    def cancel(self) -> None:
        if self._pending_start is not None:
            self._pending_start = None
            self.show_start()
            return
        if self.job is not None:
            self.job[1].set()
            self.cancel_button.set_sensitive(False)
            self.work_page.set_description(escape(_("Stopping…")))

    def _finished(self, progress, result, error) -> None:
        if self.job is None or self.job[0] is not progress:
            return
        _progress, _cancel, _began, _estimated, request = self.job
        self.job = None
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self.stack.set_visible_child_name("setup")
        if getattr(self.app, "closing", False):
            if error is not None and not isinstance(error, Cancelled):
                report("transcribing", error)
                self.app.block_exit(error)
            self.window.page_changed()
            return
        if isinstance(error, Cancelled):
            self.window.toast(_("Cancelled: no file was written."))
        elif error is not None:
            report("transcribing", error)
            text = str(error) if isinstance(error, Failure) else _("Something went wrong: {error}").format(error=error)
            self.failed_page.set_description(escape(text))
            self.stack.set_visible_child_name("failed")
            self._tell(_("Could Not Transcribe"), text)
        else:
            self.refresh_estimate()         # the speed just measured
            self.window.show_result(result, request)
            self._tell(_("Transcript Ready"), request.source.name)
        self.cards.start_if_needed()        # a test the work stopped before it ended
        self.window.page_changed()

    def _tell(self, title: str, body: str) -> None:
        """The end of a long work, said outside the window when the user is
        elsewhere (the app's notification: Windows' toasts, the desktop's on
        Linux); a click brings the window back."""
        if self.window.is_active() or not self.app.get_is_registered():
            return
        self.app.notify(title, body, tag="transcription", on_click=self.window.present)

    # -- a failure -----------------------------------------------------------------

    def _failed(self) -> Gtk.Widget:
        self.failed_page = Adw.StatusPage(icon_name="dialog-error-symbolic", title=_("Could Not Transcribe"))
        back = Gtk.Button(label=_("Back"), halign=Gtk.Align.CENTER, css_classes=["pill"])
        back.connect("clicked", lambda *_: self.show_start())
        self.failed_page.set_child(back)
        return self.failed_page
