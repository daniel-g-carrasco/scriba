"""The Result page, pushed on top of Transcribe: the outcome in one line,
the files made (Open, Show in Folder), the voices with a field for each
name and a stretch to listen to, "Save with the Names" (the files with the
names, brought up to date; never the automatic ones), and the first lines
of the text, the doubtful ones faded and every change of voice set off,
with "Read the Whole Transcript" under them: the text in full is a page of
its own (gui/transcript.py), never a scrolling area inside this one.

A page of an Adw.NavigationView, as GNOME's apps open a detail: its back
button (and Escape, Alt+Left) goes back to the choices for the same
recording; "New Transcription" goes back to the empty page.
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from scriba.app import jobs  # noqa: E402
from scriba.app.preview import BalanceClip, Clip  # noqa: E402
from scriba.app.task import Cancelled  # noqa: E402
from scriba.core import balance_excerpt, estimate, segments, speakers  # noqa: E402
from scriba.core.paragraphs import paragraphs  # noqa: E402
from scriba.data import edits, markdown  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _, count, ngettext  # noqa: E402

from .balance_playback import BalancePlayback  # noqa: E402
from .style_values import SPACE_L, SPACE_M, SPACE_S, SPACE_XS  # noqa: E402
from .transcript import TranscriptPage  # noqa: E402
from .ui import escape, header_bar, open_path, report, reveal, run_in_thread  # noqa: E402

EXCERPT_PARAGRAPHS = 3      # the first paragraphs on the page; the rest on a page of its own (gui/transcript.py)
EXCERPT_LINES = 3           # a long one cut there, with an ellipsis
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
        self._draft_lease = None
        self._saved_voice_fields = None
        self.media = None                   # Gtk.MediaFile of the recording, made on the first Listen
        self.muted = False                  # the tests listen without a sound
        self._playing: str | None = None    # the voice being played
        self._stop_id = 0
        self._listen_token = 0
        self._listen_cancel: threading.Event | None = None
        self._clip = None
        self._balance_player: BalancePlayback | None = None
        self.view = Adw.ToolbarView()
        header = header_bar()
        self.title = Adw.WindowTitle(title=_("Transcript"))
        header.set_title_widget(self.title)
        header.pack_end(window.menu_button())
        self.view.add_top_bar(header)
        # a transcript largely doubtful says so above everything, as GNOME's
        # apps tell what holds for the whole page (an Adw.Banner, no dialog)
        self.banner = Adw.Banner(use_markup=False)
        self.view.add_top_bar(self.banner)
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
        self.release_name_draft()
        self._saved_voice_fields = None
        self.result, self.request = result, request
        self._saving = False
        self.title.set_subtitle(result.meeting_title or Path(result.source or request.source).name)
        share = segments.doubt_warning(result.segments)
        if share is not None:
            self.banner.set_title(_("{percent}% of the text is doubtful: listen to the recording again before "
                                           "relying on it").format(percent=round(share * 100)))
        self.banner.set_revealed(share is not None)
        page = Adw.PreferencesPage()
        for group in self._outcome(result, request):
            page.add(group)
        self.files = Adw.PreferencesGroup(title=escape(_("Files")),
                                          description=escape(_("Written in {folder}").format(
                                              folder=jobs.full_path(result.folder) if result.folder else "")))
        self._shown_outputs: set[str] = set()
        self._output_rows: dict[str, Adw.ActionRow] = {}
        self.meeting_title_entry = Adw.EntryRow(use_markup=False, title=_("Meeting Title"), text=result.meeting_title)
        self.meeting_title_entry.set_max_length(72)
        self.meeting_title_button = Gtk.Button(label=_("Save Title"), valign=Gtk.Align.CENTER)
        self.meeting_title_button.connect("clicked", lambda *_: self.save_meeting_title())
        self.meeting_title_entry.add_suffix(self.meeting_title_button)
        self.files.add(self.meeting_title_entry)
        for output in result.outputs:
            self._add_output(output)
        self.session_row = Adw.ActionRow(use_markup=False, title=_("Meeting File (reopen to change the names)"),
                                         subtitle=result.session.name, subtitle_lines=2)
        locate = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text=_("Show in Folder"),
                            valign=Gtk.Align.CENTER, css_classes=["flat"])
        locate.connect("clicked", lambda *_: reveal(self.window, self.result.session))
        self.session_row.add_suffix(locate)
        self.files.add(self.session_row)
        page.add(self.files)
        self.entries: dict[str, Adw.EntryRow] = {}
        self._committed_names: dict[str, str] = {}
        self.headings: dict[str, list[Gtk.Label]] = {}
        self.transcript = None              # the whole text's page, made when first asked
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
        self._saved_voice_fields = (speakers.clean(self.names()), result.me)
        self._loaded_session = self._session_stamp()
        self._saving_changed(self.busy)

    def save_meeting_title(self) -> None:
        if self.busy or self.window.app.closing:
            return
        result, request = self.result, self.request
        title = self.meeting_title_entry.get_text()
        old_folder, old_source = result.folder, result.source
        notes_before = len(result.notes)
        self.stop()
        self._writes += 1
        self._saving_changed(True)
        self.window.app.work_changed()

        def done(saved, error):
            self._write_finished(error, "saving a meeting title")
            if error is None and self.result is result:
                if str(result.folder) != str(old_folder):
                    names = self.names()
                    balance = self.balance_scale.get_value()
                    self.window.transcribe.update_moved_source(old_source, result.source)
                    self.show(result, replace(request, source=result.source or request.source))
                    for label, name in names.items():
                        self.entries[label].set_text(name)
                    self.balance_scale.set_value(balance)
                else:
                    self.meeting_title_entry.set_text(saved)
                    self.title.set_subtitle(saved or Path(result.source or request.source).name)
                    self._loaded_session = self._session_stamp()
                for note in result.notes[notes_before:]:
                    self.window.toast(note)

        try:
            run_in_thread(lambda: jobs.save_meeting_title(result, title), done)
        except Exception as error:
            self._write_finished(error, "starting a meeting title save")

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
        line = Gtk.Grid(column_spacing=SPACE_M, row_spacing=SPACE_XS)
        information = [note for note in result.notes if jobs.recording_information(note)]
        warnings = [note for note in result.notes if note not in information]
        warned = bool(warnings) or segments.doubt_warning(result.segments) is not None
        icon = Gtk.Image(icon_name="dialog-warning-symbolic" if warned else "object-select-symbolic",
                         valign=Gtk.Align.CENTER,
                         css_classes=["warning" if warned else "success"])
        self.outcome_icon = icon
        line.attach(icon, 0, 0, 1, 1)
        self.outcome_title = Gtk.Label(label=_("Transcript Ready"), xalign=0, wrap=True, css_classes=["title-3"])
        line.attach(self.outcome_title, 1, 0, 1, 1)
        self.outcome = Gtk.Label(label=" · ".join(facts), xalign=0, wrap=True, hexpand=True,
                                 css_classes=["dim-label"])
        line.attach(self.outcome, 1, 1, 1, 1)
        group.add(line)
        groups = [group]
        if result.notes:
            notes = Adw.PreferencesGroup()
            for note in warnings:
                row = Adw.ActionRow(use_markup=False, title=note, title_lines=3)
                row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
                notes.add(row)
            if information:
                details = Adw.ExpanderRow(use_markup=False, title=_("Recording Notes"),
                                          subtitle=ngettext("{n} note", "{n} notes", len(information)).format(
                                              n=count(len(information))), expanded=False)
                details.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
                for note in information:
                    row = Adw.ActionRow(use_markup=False, title=note)
                    row.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
                    details.add_row(row)
                notes.add(details)
            groups.append(notes)
        return groups

    def _add_output(self, output) -> None:
        if output.path in self._shown_outputs:
            return                  # the files with the names, saved again: rewritten where they are
        self._shown_outputs.add(output.path)
        name = os.path.basename(output.path)
        row = Adw.ActionRow(use_markup=False, title=output_names().get(output.kind, output.kind), subtitle=name,
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
        self._output_rows[output.path] = row

    def _sync_outputs(self) -> None:
        current = {output.path for output in self.result.outputs}
        for path in self._shown_outputs - current:
            self.files.remove(self._output_rows.pop(path))
            self._shown_outputs.remove(path)
        for output in self.result.outputs:
            self._add_output(output)

    # -- the listening copy ---------------------------------------------------------

    def _balance(self, result: jobs.Result) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=escape(_("Balance of the Listening Copy")), visible=False,
                                     description=escape(_("Preview up to {seconds} seconds. Save updates the listening "
                                                          "copy. The centre restores the automatic balance.").format(
                                         seconds=round(balance_excerpt.SECONDS))))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_M, margin_top=SPACE_L,
                      margin_bottom=SPACE_L, margin_start=SPACE_L, margin_end=SPACE_L)
        ends = Gtk.Box(spacing=SPACE_M)
        ends.append(Gtk.Label(label=_("More Others"), xalign=0, hexpand=True))
        ends.append(Gtk.Label(label=_("More Me"), xalign=1))
        box.append(ends)
        self.balance_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -1, 1, 0.05)
        self.balance_scale.set_increments(0.05, 0.05)
        # Replace Range's wheel controller: otherwise returning False from ours
        # still changes the balance before the parent can scroll the page.
        controllers = self.balance_scale.observe_controllers()
        for index in range(controllers.get_n_items()):
            controller = controllers.get_item(index)
            if isinstance(controller, Gtk.EventControllerScroll):
                controller.set_propagation_phase(Gtk.PropagationPhase.NONE)
        wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL |
                                              Gtk.EventControllerScrollFlags.DISCRETE)
        wheel.connect("scroll", self._balance_scroll)
        self.balance_scale.add_controller(wheel)
        self.balance_scale.set_draw_value(False)
        self.balance_scale.add_mark(0, Gtk.PositionType.BOTTOM, _("Automatic"))
        self.balance_scale.set_value(jobs.listening_balance(result))
        self.balance_scale.set_tooltip_text(_("More others on the left, more me on the right"))
        self.balance_scale.connect("value-changed", self._balance_changed)
        box.append(self.balance_scale)
        self.preview_content = Gtk.Stack(hhomogeneous=True)
        self.preview_content.add_named(Adw.ButtonContent(label=_("Listen to a Preview"),
                                                         icon_name="media-playback-start-symbolic"), "play")
        self.preview_content.add_named(Adw.ButtonContent(label=_("Stop the Preview"),
                                                         icon_name="media-playback-stop-symbolic"), "stop")
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

    def _balance_changed(self, scale: Gtk.Scale) -> None:
        if self._balance_player is not None:
            self._balance_player.balance = scale.get_value()

    def _balance_scroll(self, _controller, _dx: float, dy: float) -> bool:
        # Clicking the slider (or selecting it with Tab) opts into wheel edits.
        # Hover alone leaves the event for the PreferencesPage's scroller.
        if not self.balance_scale.is_focus():
            return False
        adjustment = self.balance_scale.get_adjustment()
        value = self.balance_scale.get_value() - dy * adjustment.get_step_increment()
        self.balance_scale.set_value(max(adjustment.get_lower(), min(adjustment.get_upper(), value)))
        return True

    def _saving_changed(self, active: bool, listening: bool = False) -> None:
        self._saving = active
        self.balance_button.set_sensitive(not active)
        self.meeting_title_entry.set_sensitive(not active)
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
            self.session_row.set_subtitle(result.session.name)
            self._loaded_session = self._session_stamp()
            self._sync_outputs()
            for note in saved.notes:
                self.window.toast(note)
            self.window.toast(_("Listening copy updated"))

        try:
            run_in_thread(lambda: self.window.backend.save_balance(result, balance), done)
        except Exception as error:
            self._write_finished(error, "starting a listening-copy save")

    # -- the voices ----------------------------------------------------------------

    def _voices(self, found) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=escape(_("Voices")),
                                     description=escape(_("Name the voices, then save to update the transcript files.")))
        self._voice_labels = [voice.label for voice in found]
        self.my_voice = Adw.ComboRow(use_markup=False, title=_("My Voice"), subtitle=_("Choose which voice is yours"),
                                     model=Gtk.StringList.new([_("Not Chosen"),
                                                               *[speakers.shown_label(v.label) for v in found]]))
        if self.result.me in self._voice_labels:
            self.my_voice.set_selected(self._voice_labels.index(self.result.me) + 1)
        self._my_voice_handler = self.my_voice.connect("notify::selected", lambda *_: self._chose_my_voice())
        group.add(self.my_voice)
        for voice in found:
            title = _("{voice}: {n} lines, the first at {time}") if voice.lines != 1 else _("{voice}: 1 line, at {time}")
            entry = Adw.EntryRow(use_markup=False, title=title.format(voice=speakers.shown_label(voice.label), n=voice.lines,
                                                           time=clock(voice.first)))
            entry.connect("changed", lambda e, label=voice.label: self._renamed(label, e.get_text()))
            entry.connect("entry-activated", lambda e, label=voice.label: self._save_edited_my_name(label, e))
            focus = Gtk.EventControllerFocus.new()
            focus.connect("leave", lambda c, label=voice.label: self._save_edited_my_name(label, c.get_widget()))
            entry.add_controller(focus)
            listen = Gtk.Button(icon_name="media-playback-start-symbolic", valign=Gtk.Align.CENTER,
                                css_classes=["flat"],
                                tooltip_text=_("Listen from {time}").format(time=clock(voice.listen[0])))
            listen.connect("clicked", lambda *_, v=voice: self.listen(v))
            entry.add_suffix(listen)
            proposal = self.result.proposals.get(voice.label)
            if proposal and voice.label not in self.result.names:
                if proposal.source == "voice":
                    reason = _("from Teams, voice match")
                else:
                    reason = _("from Teams, {heard} of {total}").format(
                        heard=estimate.span(proposal.seconds), total=estimate.span(proposal.seconds / proposal.share))
                badge = Gtk.Label(label=reason, css_classes=["dim-label"], tooltip_text=reason)
                entry.add_suffix(badge)
                entry.connect("changed", lambda e, b=badge, name=proposal.name: b.set_visible(e.get_text() == name))
            group.add(entry)
            self.entries[voice.label] = entry
            self.listen_buttons[voice.label] = listen
        self.save_button = Gtk.Button(label=_("Save with the Names"), halign=Gtk.Align.CENTER, sensitive=False,
                                      margin_top=SPACE_L, css_classes=["pill", "suggested-action"])
        self.save_button.connect("clicked", lambda *_: self.save_names())
        group.add(self.save_button)
        for label, entry in self.entries.items():
            proposal = self.result.proposals.get(label)
            entry.set_text(self.result.names.get(label, proposal.name if proposal else ""))
            self._committed_names[label] = " ".join(entry.get_text().split())
        return group

    def names(self) -> dict[str, str]:
        return {label: entry.get_text() for label, entry in self.entries.items()}

    def _renamed(self, label: str, text: str) -> None:
        shown = " ".join(text.split()) or speakers.shown_label(label)
        for heading in self.headings.get(label, []):
            heading.set_label(shown)
        if self.transcript is not None:
            self.transcript.rename(label, shown)
        if hasattr(self, "save_button"):
            self.save_button.set_sensitive(not self._saving and bool(speakers.clean(self.names()) or self.result.names))
        self._refresh_draft()

    def _session_stamp(self):
        try:
            info = self.result.session.stat()
            return info.st_ino, info.st_size, info.st_mtime_ns
        except OSError:
            return None

    def release_name_draft(self) -> None:
        if self._draft_lease is not None:
            self._draft_lease.__exit__(None, None, None)
            self._draft_lease = None

    def _refresh_draft(self, *, force: bool = False) -> bool:
        if self._saved_voice_fields is None:
            return True
        current = speakers.clean(self.names()), self.result.me
        if current == self._saved_voice_fields and not force:
            if not self.busy:
                self.release_name_draft()
            return True
        if self._draft_lease is None:
            lease = edits.draft_lease(self.window.app.paths.state, self.result.source or self.result.session)
            try:
                lease.__enter__()
            except OSError:
                return False
            self._draft_lease = lease
        return True

    def _save_edited_my_name(self, label: str, entry: Adw.EntryRow) -> None:
        app = self.window.app
        if self.busy or app.closing or label != self.result.me or self.entries.get(label) is not entry:
            return
        name = " ".join(entry.get_text().split())
        if self._committed_names.get(label) == name:
            return
        self._committed_names[label] = name
        previous = app.cfg.my_name
        name = speakers.name_to_remember(previous, name)
        if not name:
            return
        app.cfg.my_name = name
        if not app.save_config():
            app.cfg.my_name = previous
            self._committed_names.pop(label)
            return

        def undo(_toast):
            if app.cfg.my_name == name:
                app.cfg.my_name = previous
                if not app.save_config():
                    app.cfg.my_name = name

        toast = Adw.Toast(use_markup=False, title=_("Name saved in Preferences"), button_label=_("Undo"), timeout=5)
        toast.connect("button-clicked", undo)
        self.window.toasts.add_toast(toast)

    def _chose_my_voice(self) -> None:
        selected = self.my_voice.get_selected()
        label = self._voice_labels[selected - 1] if 0 < selected <= len(self._voice_labels) else ""
        name = " ".join(self.window.app.cfg.my_name.split()) or "Io"
        if label and speakers.is_default_name(name):
            self._ask_my_name(label)
        else:
            self._assign_my_voice(label, name, name)

    def _ask_my_name(self, label: str) -> None:
        row = self.my_voice
        previous = self._voice_labels.index(self.result.me) + 1 if self.result.me in self._voice_labels else 0
        dialog = Adw.AlertDialog(heading=_("My Voice"),
                                 body=_("Enter your name to label this voice and save it in Preferences."))
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("save", _("Save Name"))
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("save", False)

        entry = Adw.EntryRow(title=_("My Name"), use_markup=False, activates_default=True)
        entry.connect("changed", lambda e: dialog.set_response_enabled("save", bool(e.get_text().strip())))
        box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        box.append(entry)
        dialog.set_extra_child(box)
        dialog.set_focus(entry)

        def answered(_dialog, response):
            if row is not self.my_voice:
                return
            name = " ".join(entry.get_text().split())
            if response == "save" and name:
                app = self.window.app
                previous_name = app.cfg.my_name
                app.cfg.my_name = name
                if app.save_config():
                    self._assign_my_voice(label, name, " ".join(previous_name.split()) or "Io")
                    return
                app.cfg.my_name = previous_name
            with row.handler_block(self._my_voice_handler):
                row.set_selected(previous)

        dialog.connect("response", answered)
        dialog.present(self.window)

    def _assign_my_voice(self, label: str, name: str, previous_name: str) -> None:
        previous = self.result.me
        if previous != label and previous in self.entries and self.entries[previous].get_text() == previous_name:
            self.entries[previous].set_text("")
        self.result.me = label
        if label:
            self.entries[label].set_text(name)
            self._committed_names[label] = name
        self._refresh_draft()

    def save_names(self) -> None:
        if self.busy or self.window.app.closing:
            return
        if not self._refresh_draft(force=True):
            self.window.toast(_("This meeting is being edited in Scriba."))
            return
        if self._loaded_session != self._session_stamp():
            self.window.toast(_("This meeting changed. Reopen it before saving."))
            return
        label = self.result.me
        if label in self.entries:
            self._save_edited_my_name(label, self.entries[label])
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
            self._loaded_session = self._session_stamp()
            self._saved_voice_fields = speakers.clean(names), result.me
            self._refresh_draft()
            self.session_row.set_subtitle(Path(result.session).name)
            self._sync_outputs()
            for note in named.notes:
                self.window.toast(note)
            self.window.toast(_("Transcript updated"))

        try:
            run_in_thread(lambda: self.window.backend.save_names(result, names), done)
        except Exception as error:
            self._write_finished(error, "starting a names save")

    # -- listening to a voice -------------------------------------------------------

    def listen(self, voice: speakers.Voice) -> None:
        source = self.result.source or self.request.source
        heard = self.result.listen or source
        self._play(voice.label, lambda cancel: self.window.backend.preview_listening_copy(
            source, heard, *voice.listen, cancel))

    def listen_balance(self) -> None:
        result = self.result
        self._play(BALANCE_PREVIEW, lambda cancel: self.window.backend.balance_preview(result, cancel))

    def _show_playing(self, key: str, playing: bool) -> None:
        if key == BALANCE_PREVIEW:
            self.preview_content.set_visible_child_name("stop" if playing else "play")
            label = self.preview_content.get_visible_child().get_label()
            self.preview_button.update_property([Gtk.AccessibleProperty.LABEL], [label])
        elif key in getattr(self, "listen_buttons", {}):
            self.listen_buttons[key].set_icon_name("media-playback-stop-symbolic" if playing
                                                   else "media-playback-start-symbolic")

    def _play(self, key: str, work) -> None:
        """Prepare an excerpt off the main loop, then play it from zero.
        Pressing the same button again stops the preview."""
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
                if isinstance(clip, Clip):
                    self._discard_clip(clip)
                return
            self._listen_cancel = None
            if error is not None:
                self.stop()
                if not isinstance(error, Cancelled):
                    report("preparing a voice excerpt", error)
                    self.window.toast(str(error))
                return
            if isinstance(clip, BalanceClip):
                self._play_balance(clip, token)
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

    def _play_balance(self, clip: BalanceClip, token: int) -> None:
        def finished(error):
            if token == self._listen_token:
                self.stop()
                if error is not None:
                    self._playback_error(error)

        try:
            self._balance_player = BalancePlayback(clip, self.balance_scale.get_value(), self.muted, finished)
            self._balance_player.play()
        except (GLib.Error, RuntimeError) as error:
            report("starting the balance preview", error)
            finished(str(error))

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
        player, self._balance_player = self._balance_player, None
        if player is not None:
            player.close()
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
        self._playback_error(error.message)

    def _playback_error(self, message: str) -> None:
        self.window.toast(_("This recording cannot be played here ({error}): the times are in the text.").format(
            error=message))

    # -- the text --------------------------------------------------------------------

    def _text(self, result: jobs.Result) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=escape(_("Text")))
        if any(s.doubts for s in result.segments):
            group.set_description(escape(_("Doubtful lines are marked (?) in the files and faded here: the engine "
                                           "was unsure of them. Check them against the audio.")))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_XS, margin_top=SPACE_L,
                      margin_bottom=SPACE_L, margin_start=SPACE_L, margin_end=SPACE_L)
        reasons = markdown.reasons()
        previous, first = None, True
        voiced = any(s.speaker for s in result.segments)
        lines = paragraphs(result.segments)      # as the files have them; a doubtful line on its own
        for paragraph in lines[:EXCERPT_PARAGRAPHS]:
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
                             hexpand=True, lines=EXCERPT_LINES, ellipsize=Pango.EllipsizeMode.END)
            doubts = tuple(dict.fromkeys(code for s in paragraph for code in s.doubts))
            if doubts:
                text.set_label(f"{markdown.DOUBT_MARK} {text.get_label()}")
                text.add_css_class("dim-label")
                text.set_tooltip_text(", ".join(reasons.get(code, code) for code in doubts))
            row.append(text)
            box.append(row)
        if not lines:
            box.append(Gtk.Label(label=_("No speech was found in the recording."), css_classes=["dim-label"]))
        # the first lines, without a scrolling area of their own (the page has
        # one); the text in full one row below, on a page of its own
        group.add(Adw.PreferencesRow(child=box, activatable=False))
        if lines:
            more = Adw.ActionRow(use_markup=False, title=_("Read the Whole Transcript"), activatable=True)
            more.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            more.connect("activated", lambda *_: self.read_all())
            group.add(more)
        return group

    def read_all(self) -> None:
        """The text in full, on a page pushed over this one (back: Escape)."""
        if self.result is None:
            return
        if self.transcript is None:
            self.transcript = TranscriptPage(self.window, self.result, self.names())
        self.window.nav.push(self.transcript)
