"""The Result page, pushed on top of Transcribe: the outcome in one line,
the files made (Open, Show in Folder), the voices with a field for each
name and a stretch to listen to, "Save with the Names" (the files with the
names, brought up to date; never the automatic ones), and the text to read, the doubtful lines faded
and every change of voice set off.

A page of an Adw.NavigationView, as GNOME's apps open a detail: its back
button (and Escape, Alt+Left) goes back to the choices for the same
recording; "New Transcription" goes back to the empty page.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from scriba.app import jobs  # noqa: E402
from scriba.app.task import Cancelled  # noqa: E402
from scriba.core import estimate, speakers  # noqa: E402
from scriba.core.paragraphs import paragraphs  # noqa: E402
from scriba.data import markdown  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _, count, ngettext  # noqa: E402

from .style_values import ICON_TILE, SPACE_L, SPACE_M, SPACE_S, SPACE_XS  # noqa: E402
from .ui import escape, header_bar, open_path, report, reveal, run_in_thread  # noqa: E402

PREVIEW_HEIGHT = 360        # the text's own scrolling area, as tall as half a window
PREVIEW_MAX_LINES = 3000    # past this the preview stops and says so: the files have it all
BALANCE_PREVIEW = "\0balance"   # the balance's preview among what is played (never a voice's label)


def output_names() -> dict[str, str]:
    return {
        "md": _("Transcript (Markdown)"),
        "txt": _("Transcript (plain text)"),
        "srt": _("Subtitles"),
        "video": _("Video with subtitles"),
        "audio-balanced": _("Listening copy with the balance"),
        "md-named": _("Transcript with the names (Markdown)"),
        "txt-named": _("Transcript with the names (plain text)"),
        "srt-named": _("Subtitles with the names"),
    }


OUTPUT_ICONS = {"srt": "media-view-subtitles-symbolic", "srt-named": "media-view-subtitles-symbolic",
                "video": "video-x-generic-symbolic", "audio-balanced": "audio-x-generic-symbolic"}


def where_it_ran(label: str) -> str:
    """"Vulkan: NVIDIA RTX A2000" -> "on NVIDIA RTX A2000"; "cpu" -> "on the
    processor" (two sentences: Italian joins "su" and "il")."""
    if label == "cpu":
        return _("on the processor")
    return _("on {device}").format(device=label.removeprefix("Vulkan: "))


class ResultPage(Adw.NavigationPage):
    def __init__(self, window):
        super().__init__(title=_("Transcript"), tag="result")
        self.window = window
        self._writes = 0
        self.result: jobs.Result | None = None
        self.media = None                   # Gtk.MediaFile of the recording, made on the first Listen
        self.muted = False                  # the tests listen without a sound
        self._playing: str | None = None    # the voice being played
        self._stop_id = 0
        self._listen_token = 0
        self._listen_cancel: threading.Event | None = None
        self._clip = None
        self.view = Adw.ToolbarView()
        header = header_bar()
        self.title = Adw.WindowTitle(title=_("Transcript"))
        header.set_title_widget(self.title)
        header.pack_end(window.menu_button())
        self.view.add_top_bar(header)
        self.set_child(self.view)
        self.connect("hidden", lambda *_: self.stop())

    @property
    def busy(self):
        return self._writes > 0

    def _write_finished(self, error, action):
        self._writes -= 1
        self._saving_changed(self.busy)
        if error is not None:
            report(action, error)
            self.window.toast(str(error))
            self.window.app.block_exit(error)
        self.window.app.work_changed()

    # -- filling it ------------------------------------------------------------------

    def show(self, result: jobs.Result, request: jobs.Request) -> None:
        self._reset_media()
        self.result, self.request = result, request
        self._saving = False
        self.title.set_subtitle(escape(Path(result.source or request.source).name))
        page = Adw.PreferencesPage()
        for group in self._outcome(result, request):
            page.add(group)
        self.files = Adw.PreferencesGroup(title=_("Files"),
                                          description=escape(_("Written in {folder}").format(
                                              folder=jobs.full_path(result.folder) if result.folder else "")))
        self._shown_outputs: set[str] = set()
        for output in result.outputs:
            self._add_output(output)
        self.session_row = Adw.ActionRow(title=_("Meeting File (reopen to change the names)"),
                                         subtitle=escape(result.session.name), subtitle_lines=2)
        locate = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Show in Folder"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        locate.connect("clicked", lambda *_: reveal(self.window, self.result.session))
        self.session_row.add_suffix(locate)
        self.files.add(self.session_row)
        page.add(self.files)
        self.entries: dict[str, Adw.EntryRow] = {}
        self.suggestions: dict[str, tuple] = {}
        self._known = speakers.remembered(self.window.app.cfg.speaker_names, result.names)
        self.headings: dict[str, list[Gtk.Label]] = {}
        self.listen_buttons: dict[str, Gtk.Button] = {}
        self.balance_group = self._balance(result)
        page.add(self.balance_group)
        found = speakers.voices(result.segments)
        if found:
            page.add(self._voices(found))
        page.add(self._text(result))
        group = Adw.PreferencesGroup()
        again = Gtk.Button(label=_("New Transcription"), halign=Gtk.Align.CENTER, css_classes=["pill"])
        again.connect("clicked", lambda *_: self.window.new_transcription())
        group.add(again)
        page.add(group)
        self.page = page
        self.view.set_content(page)
        self._saving_changed(self.busy)

    def _outcome(self, result: jobs.Result, request: jobs.Request) -> list[Adw.PreferencesGroup]:
        """The line at the top: what came out, in two words, and the facts;
        under it what went otherwise than asked (a card that failed)."""
        group = Adw.PreferencesGroup()
        doubtful = sum(1 for s in result.segments if s.doubts)
        facts = [_("{duration} of audio in {time}").format(duration=clock(result.duration),
                                                           time=estimate.span(result.timings.get("total", 0.0))),
                 where_it_ran(result.device)]
        if request.speakers:
            facts.append(ngettext("{n} voice", "{n} voices", result.speakers).format(n=count(result.speakers)))
        facts.append(ngettext("{n} doubtful line", "{n} doubtful lines", doubtful).format(n=count(doubtful))
                     if doubtful else _("no doubtful line"))
        line = Gtk.Box(spacing=SPACE_L)
        icon = Gtk.Image(icon_name="dialog-warning-symbolic" if result.notes else "object-select-symbolic",
                         pixel_size=ICON_TILE, valign=Gtk.Align.START,
                         css_classes=["warning" if result.notes else "success"])
        line.append(icon)
        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XS, hexpand=True)
        self.outcome_title = Gtk.Label(label=_("Transcript Ready"), xalign=0, wrap=True, css_classes=["title-3"])
        words.append(self.outcome_title)
        self.outcome = Gtk.Label(label=" · ".join(facts), xalign=0, wrap=True, css_classes=["dim-label"])
        words.append(self.outcome)
        line.append(words)
        group.add(line)
        groups = [group]
        if result.notes:
            notes = Adw.PreferencesGroup()
            for note in result.notes:
                row = Adw.ActionRow(title=escape(note), title_lines=3)
                row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                notes.add(row)
            groups.append(notes)
        return groups

    def _add_output(self, output) -> None:
        if output.path in self._shown_outputs:
            return                  # the files with the names, saved again: rewritten where they are
        self._shown_outputs.add(output.path)
        name = os.path.basename(output.path)
        row = Adw.ActionRow(title=escape(output_names().get(output.kind, output.kind)), subtitle=escape(name),
                            activatable=True, subtitle_lines=2, tooltip_text=_("Open"))
        row.add_prefix(Gtk.Image(icon_name=OUTPUT_ICONS.get(output.kind, "text-x-generic-symbolic")))
        # Open in words: Adwaita's document-open is drawn as an open folder,
        # the same as the folder's button beside it
        open_button = Gtk.Button(label=_("Open"), valign=Gtk.Align.CENTER, css_classes=["flat"])
        open_button.connect("clicked", lambda *_: open_path(self.window, output.path))
        row.add_suffix(open_button)
        folder = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Show in Folder"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        folder.connect("clicked", lambda *_: reveal(self.window, output.path))
        row.add_suffix(folder)
        row.connect("activated", lambda *_: open_path(self.window, output.path))
        self.files.add(row)

    # -- the listening copy ---------------------------------------------------------

    def _balance(self, result: jobs.Result) -> Adw.PreferencesGroup:
        # what it changes, verified in the code: rebalance() writes a new
        # _balanced.m4a from the original tracks; the recording, the
        # transcript and the earlier copies are only read
        group = Adw.PreferencesGroup(title=_("Balance of the Listening Copy"), visible=False,
                                     description=_("Only the copy to listen to changes: the recording and the "
                                                   "transcript stay as they are. “Listen to a Preview” plays "
                                                   "{seconds} seconds where you and the others both speak, with the "
                                                   "balance chosen; then save a new listening file from the original "
                                                   "tracks. The centre repeats the automatic balance.").format(
                                         seconds=round(speakers.PREVIEW_SECONDS)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_M, margin_top=SPACE_L,
                      margin_bottom=SPACE_L, margin_start=SPACE_L, margin_end=SPACE_L)
        ends = Gtk.Box(spacing=SPACE_M)
        ends.append(Gtk.Label(label=_("More Others"), xalign=0, hexpand=True))
        ends.append(Gtk.Label(label=_("More Me"), xalign=1))
        box.append(ends)
        self.balance_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
        self.balance_scale.set_draw_value(False)
        self.balance_scale.add_mark(0, Gtk.PositionType.BOTTOM, _("Automatic"))
        self.balance_scale.set_value(jobs.listening_balance(result))
        self.balance_scale.set_tooltip_text(_("More others on the left, more me on the right"))
        # a new balance is heard at the next preview: the one playing stops
        self.balance_scale.connect("value-changed",
                                   lambda *_: self.stop() if self._playing == BALANCE_PREVIEW else None)
        box.append(self.balance_scale)
        self.preview_content = Adw.ButtonContent()
        self.preview_button = Gtk.Button(child=self.preview_content, css_classes=["pill"])
        self.preview_button.connect("clicked", lambda *_: self.listen_balance())
        self._show_playing(BALANCE_PREVIEW, False)
        self.balance_button = Gtk.Button(label=_("Save Listening Copy"), css_classes=["pill"])
        self.balance_button.connect("clicked", lambda *_: self.save_balance())
        self.balance_spinner = Adw.Spinner(visible=False)
        box.append(self.balance_spinner)
        buttons = Gtk.Box(spacing=SPACE_M, halign=Gtk.Align.CENTER)
        buttons.append(self.preview_button)
        buttons.append(self.balance_button)
        box.append(buttons)
        group.add(Adw.PreferencesRow(child=box))

        def checked(available, error):
            if self.result is not result:
                return
            if error is not None:
                report("checking the listening balance", error)
                self.window.toast(str(error))
            group.set_visible(bool(available) and error is None)

        run_in_thread(lambda: self.window.backend.can_balance(result), checked)
        return group

    def _saving_changed(self, active: bool, listening: bool = False) -> None:
        self._saving = active
        self.balance_button.set_sensitive(not active)
        self.balance_scale.set_sensitive(not active)
        self.preview_button.set_sensitive(not active)
        self.balance_spinner.set_visible(active and listening)
        if hasattr(self, "save_button"):
            self.save_button.set_sensitive(not active and bool(speakers.clean(self.names()) or self.result.names))

    def save_balance(self) -> None:
        if self.busy or self.window.app.closing:
            return
        result, balance = self.result, self.balance_scale.get_value()
        self.stop()
        self._writes += 1
        self._saving_changed(True, listening=True)
        self.window.app.work_changed()

        def done(saved, error):
            self._write_finished(error, "saving a listening copy")
            if self.result is not result:
                return
            if error is not None:
                return
            self._reset_media()
            self.session_row.set_subtitle(escape(result.session.name))
            for output in saved.outputs:
                self._add_output(output)
            for note in saved.notes:
                self.window.toast(note)
            self.window.toast(_("A new listening copy was saved."))

        try:
            run_in_thread(lambda: self.window.backend.save_balance(result, balance), done)
        except Exception as error:
            self._write_finished(error, "starting a listening-copy save")

    # -- the voices ----------------------------------------------------------------

    def _voices(self, found) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Voices"),
                                     description=escape(_("Give each voice the name of who speaks: “Save with the "
                                                          "Names” writes the files with the names, and the automatic "
                                                          "ones stay as they are.")))
        self._voice_labels = [voice.label for voice in found]
        self.my_voice = Adw.ComboRow(title=_("My Voice"), subtitle=_("Choose which voice is yours"),
                                     model=Gtk.StringList.new([_("Not Chosen"),
                                                               *[speakers.shown_label(v.label) for v in found]]))
        if self.result.me in self._voice_labels:
            self.my_voice.set_selected(self._voice_labels.index(self.result.me) + 1)
        self.my_voice.connect("notify::selected", lambda *_: self._chose_my_voice())
        group.add(self.my_voice)
        for voice in found:
            title = _("{voice}: {n} lines, the first at {time}") if voice.lines != 1 else _("{voice}: 1 line, at {time}")
            entry = Adw.EntryRow(title=escape(title.format(voice=speakers.shown_label(voice.label), n=voice.lines,
                                                           time=clock(voice.first))))
            menu = Gtk.MenuButton(icon_name="pan-down-symbolic", tooltip_text=_("Previous Names"),
                                  valign=Gtk.Align.CENTER, visible=False)
            choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XS)
            popover = Gtk.Popover(child=choices, autohide=False)
            menu.set_popover(popover)
            entry.add_suffix(menu)
            self.suggestions[voice.label] = (menu, choices, popover)
            entry.connect("changed", lambda e, label=voice.label: self._renamed(label, e.get_text()))
            listen = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat"],
                                tooltip_text=_("Listen from {time}").format(time=clock(voice.listen[0])))
            listen.connect("clicked", lambda *_, v=voice: self.listen(v))
            entry.add_suffix(listen)
            group.add(entry)
            self.entries[voice.label] = entry
            self.listen_buttons[voice.label] = listen
        self.save_button = Gtk.Button(label=_("Save with the Names"), halign=Gtk.Align.CENTER, sensitive=False,
                                      margin_top=SPACE_L, css_classes=["pill", "suggested-action"])
        self.save_button.connect("clicked", lambda *_: self.save_names())
        group.add(self.save_button)
        for label, entry in self.entries.items():
            entry.set_text(self.result.names.get(label, ""))
        return group

    def names(self) -> dict[str, str]:
        return {label: entry.get_text() for label, entry in self.entries.items()}

    def _renamed(self, label: str, text: str) -> None:
        shown = " ".join(text.split()) or speakers.shown_label(label)
        for heading in self.headings.get(label, []):
            heading.set_label(shown)
        if hasattr(self, "save_button"):
            self.save_button.set_sensitive(not self._saving and bool(speakers.clean(self.names()) or self.result.names))
        menu, choices, popover = self.suggestions[label]
        child = choices.get_first_child()
        while child is not None:
            choices.remove(child)
            child = choices.get_first_child()
        found = self.suggestion_names(label)
        for name in found:
            button = Gtk.Button(label=name, css_classes=["flat"])
            button.connect("clicked", lambda *_, value=name: self._use_suggestion(label, value))
            choices.append(button)
        menu.set_visible(bool(found))
        if found and text and self.entries[label].get_state_flags() & Gtk.StateFlags.FOCUS_WITHIN:
            popover.popup()
        else:
            popover.popdown()

    def suggestion_names(self, label: str) -> list[str]:
        return speakers.suggestions(self.entries[label].get_text(), self._known)

    def _use_suggestion(self, label: str, name: str) -> None:
        self.entries[label].set_text(name)
        self.suggestions[label][2].popdown()

    def _chose_my_voice(self) -> None:
        selected = self.my_voice.get_selected()
        previous = self.result.me
        name = " ".join(self.window.app.cfg.my_name.split()) or "Io"
        if previous in self.entries and self.entries[previous].get_text() == name:
            self.entries[previous].set_text("")
        self.result.me = self._voice_labels[selected - 1] if 0 < selected <= len(self._voice_labels) else ""
        if self.result.me:
            self.entries[self.result.me].set_text(name)

    def save_names(self) -> None:
        if self.busy or self.window.app.closing:
            return
        result, names = self.result, self.names()
        self._writes += 1
        self._saving_changed(True)
        self.window.app.work_changed()

        def done(named, error) -> None:
            self._write_finished(error, "saving with the names")
            if self.result is not result:
                return
            if error is not None:
                return
            cfg = self.window.app.cfg
            cfg.speaker_names = speakers.remembered(cfg.speaker_names, speakers.clean(names))
            self._known = speakers.remembered(cfg.speaker_names, result.names)
            self.window.app.save_config()
            self.session_row.set_subtitle(escape(Path(result.session).name))
            for output in named.outputs:
                self._add_output(output)
            for note in named.notes:
                self.window.toast(note)
            self.window.toast(_("Saved with the names: the automatic transcript stays as it is."))

        try:
            run_in_thread(lambda: self.window.backend.save_names(result, names), done)
        except Exception as error:
            self._write_finished(error, "starting a names save")

    # -- listening to a voice -------------------------------------------------------

    def listen(self, voice: speakers.Voice) -> None:
        heard = self.result.listen or self.result.source or self.request.source
        self._play(voice.label, lambda cancel: self.window.backend.preview(heard, *voice.listen, cancel))

    def listen_balance(self) -> None:
        """A few seconds of the listening copy with the balance on the slider,
        made again at every request (a temporary WAV: no file of the user's
        is written); the same button stops it."""
        result, balance = self.result, self.balance_scale.get_value()
        self._play(BALANCE_PREVIEW, lambda cancel: self.window.backend.balance_preview(result, balance, cancel))

    def _show_playing(self, key: str, playing: bool) -> None:
        if key == BALANCE_PREVIEW:
            self.preview_content.set_icon_name("media-playback-stop-symbolic" if playing
                                               else "media-playback-start-symbolic")
            self.preview_content.set_label(_("Stop the Preview") if playing else _("Listen to a Preview"))
        elif key in getattr(self, "listen_buttons", {}):
            self.listen_buttons[key].set_icon_name("media-playback-stop-symbolic" if playing
                                                   else "media-playback-start-symbolic")

    def _play(self, key: str, work) -> None:
        """Prepare a short WAV off the main loop (work(cancel) gives the
        Clip), then play it from zero; again on the same key, stop."""
        if self.window.app.closing:
            return
        if self._playing == key:
            self.stop()
            return
        self.stop()
        token = self._listen_token
        cancel = self._listen_cancel = threading.Event()
        self._playing = key
        self._show_playing(key, True)

        def done(clip, error):
            if token != self._listen_token or cancel.is_set():
                if clip is not None:
                    self._discard_clip(clip)
                return
            self._listen_cancel = None
            if error is not None:
                self.stop()
                if not isinstance(error, Cancelled):
                    report("preparing a voice excerpt", error)
                    self.window.toast(str(error))
                return
            self._clip = clip
            media = self.media = Gtk.MediaFile.new_for_filename(str(clip.path))
            media.set_muted(self.muted)
            if self.muted:
                media.set_volume(0.0)
            media.connect("notify::error", lambda m, _p: self._cannot_play(m))
            def ended(m, _property):
                if m is self.media and m.get_ended():
                    # Let the native EOS handler return before clearing its stream.
                    GLib.idle_add(finished, m)

            def finished(m):
                if token == self._listen_token and m is self.media:
                    self.stop()
                return False

            media.connect("notify::ended", ended)
            started = [False]

            def prepared(m, *_args):
                if token != self._listen_token or m is not self.media or started[0] or not m.is_prepared():
                    return
                started[0] = True
                m.play()                   # the excerpt starts at zero: no seek in tagged media
                self._stop_id = GLib.timeout_add(max(1, round(clip.duration * 1000) + 250),
                                                 lambda: self.stop() or False)

            media.connect("notify::prepared", prepared)
            if media.get_error() is not None:
                self._cannot_play(media)
            else:
                prepared(media)

        self._preview_worker(lambda: work(cancel), done)

    def _preview_worker(self, work, done) -> None:
        # The last window may close during decoding. Keep the main loop
        # alive until cancellation/cleanup and its final callback finish.
        app = self.window.app
        app.hold()

        def complete(result, error):
            try:
                done(result, error)
            finally:
                app.release()
        try:
            run_in_thread(work, complete)
        except BaseException:
            app.release()
            raise

    def _discard_clip(self, clip) -> None:
        def done(_result, error):
            if error is not None:
                report("removing a voice excerpt", error)
        self._preview_worker(clip.close, done)

    def _reset_media(self) -> None:
        self.stop()

    def stop(self) -> None:
        self._listen_token += 1
        if self._listen_cancel is not None:
            self._listen_cancel.set()
            self._listen_cancel = None
        if self._stop_id:
            GLib.source_remove(self._stop_id)
            self._stop_id = 0
        media, self.media = self.media, None
        if media is not None:
            media.pause()
            media.clear()                  # release GTK's reader before removing its WAV
        clip, self._clip = self._clip, None
        if clip is not None:
            self._discard_clip(clip)
        if self._playing is not None:
            self._show_playing(self._playing, False)
        self._playing = None

    def _cannot_play(self, media) -> None:
        if media is not self.media:
            return
        error = media.get_error()
        if error is None:
            return
        self.stop()
        self.window.toast(_("This recording cannot be played here ({error}): the times are in the text.").format(
            error=error.message))

    # -- the text --------------------------------------------------------------------

    def _text(self, result: jobs.Result) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Text"))
        if any(s.doubts for s in result.segments):
            group.set_description(escape(_("Doubtful lines are marked (?) in the files and faded here: the engine "
                                           "was unsure of them. Check them against the audio.")))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XS, margin_top=SPACE_L,
                      margin_bottom=SPACE_L, margin_start=SPACE_L, margin_end=SPACE_L)
        reasons = markdown.reasons()
        previous, first = None, True
        voiced = any(s.speaker for s in result.segments)
        lines = paragraphs(result.segments)      # as the files have them; a doubtful line on its own
        for paragraph in lines[:PREVIEW_MAX_LINES]:
            seg = paragraph[0]
            row = Gtk.Box(spacing=SPACE_M)
            if voiced and seg.speaker != previous:
                shown = speakers.display_names([seg.speaker], self.names()).get(seg.speaker) if seg.speaker else "?"
                heading = Gtk.Label(label=shown, xalign=0,
                                    css_classes=["heading", "accent"])
                if not first:
                    heading.set_margin_top(SPACE_M)
                box.append(heading)
                self.headings.setdefault(seg.speaker, []).append(heading)
                previous = seg.speaker
            elif not first:
                row.set_margin_top(SPACE_S)     # the gap between two paragraphs of one voice
            first = False
            row.append(Gtk.Label(label=clock(seg.start), yalign=0, valign=Gtk.Align.START,
                                 css_classes=["dim-label", "numeric", "caption"]))
            text = Gtk.Label(label=" ".join(" ".join(s.text.split()) for s in paragraph), xalign=0, wrap=True,
                             hexpand=True)
            doubts = tuple(dict.fromkeys(code for s in paragraph for code in s.doubts))
            if doubts:
                text.set_label(f"{markdown.DOUBT_MARK} {text.get_label()}")
                text.add_css_class("dim-label")
                text.set_tooltip_text(", ".join(reasons.get(code, code) for code in doubts))
            row.append(text)
            box.append(row)
        if len(lines) > PREVIEW_MAX_LINES:
            box.append(Gtk.Label(label=_("The rest is in the files."), xalign=0, css_classes=["dim-label"]))
        if not lines:
            box.append(Gtk.Label(label=_("No speech was found in the recording."), css_classes=["dim-label"]))
        scroll = Gtk.ScrolledWindow(child=box, min_content_height=PREVIEW_HEIGHT, max_content_height=PREVIEW_HEIGHT,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["card"])
        group.add(scroll)
        return group
