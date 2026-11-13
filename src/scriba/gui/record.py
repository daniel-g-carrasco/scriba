"""The Record page: what is recorded, with a meter on every device, the big
Record button, the time going, the warnings, and at the end the recording
handed to Transcribe.

Modelled on GNOME's Sound Recorder (the one big round button, red, that
becomes Pause and Stop; the time in large figures; nothing starts by
itself), with what a call needs on top: the microphone and the computer's
sound, each an Adw.ExpanderRow whose switch says whether it is recorded,
whose one-line summary identifies the choice, and whose details explain
why and list "Automatic" and every device with its Gtk.LevelBar, to choose
by hand (core/devices.py decides; this page shows). The two source rows
stay in place while recording; their details show tracks with meters, and
their switches turn the side off and on in Scriba (silence on its track,
Recorder.switch), not in the settings. The warnings of
core/levels.py come in the banner at the top; what happens to the devices
(earphones unplugged, a device that comes) is said in a toast and in the
track's row.

The page works nothing out and touches no device: the Monitor and the
Recorder of the window's Backend do (app/recorder.py), in their own
threads; the page reads their snapshot ten times a second. The meters run
only while the page is on screen.
"""

from __future__ import annotations

import datetime
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from scriba import APP_NAME, WINDOWS  # noqa: E402
from scriba.app import jobs, recording  # noqa: E402
from scriba.app.task import Failure  # noqa: E402
from scriba.core import devices as dev, echo, levels, recording as rec  # noqa: E402
from scriba.data.timing import clock  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .style_values import ICON_RECORD, SIZE_METER, SPACE_L, SPACE_XL, SPACE_XS  # noqa: E402
from .ui import choose_folder, escape, open_path, recordings_folder, report, run_in_thread  # noqa: E402

TICK_MS = 100
ICONS = {dev.INPUT: "audio-input-microphone-symbolic", dev.OUTPUT: "audio-speakers-symbolic"}


def meter() -> Gtk.LevelBar:
    """A level meter as a recorder has it: -90 to 0 dBFS, in the accent's
    colour (GTK's own low and high marks are for a battery)."""
    bar = Gtk.LevelBar(min_value=0.0, max_value=1.0, width_request=SIZE_METER, valign=Gtk.Align.CENTER)
    for mark in (Gtk.LEVEL_BAR_OFFSET_LOW, Gtk.LEVEL_BAR_OFFSET_HIGH, Gtk.LEVEL_BAR_OFFSET_FULL):
        bar.remove_offset_value(mark)
    return bar


def reason_words(reason: str, device: dev.Device, output: dev.Device | None = None) -> str:
    """Why the app chose a device, in a few words."""
    programs = ", ".join(dict.fromkeys(dev.program_label(p) for p in device.programs))
    return {
        dev.R_CALL: _("in use by {programs}").format(programs=programs),
        dev.R_BROWSER: _("in use by {programs}").format(programs=programs),
        dev.R_PROGRAM: _("in use by {programs}").format(programs=programs),
        dev.R_SAME_DEVICE: _("same device as {device}").format(device=output.name if output else ""),
        dev.R_SOUND: _("sound playing now"),
        dev.R_VOICE: _("hears you loudest"),
        dev.R_COMMUNICATIONS: _("default for calls"),
        dev.R_DEFAULT: _("the system's default"),
        dev.R_ONLY: _("the only one"),
    }.get(reason, reason)


def device_tags(device: dev.Device) -> str:
    """What a device's row says under its name."""
    tags = []
    if device.default:
        tags.append(_("default"))
    if device.communications and not device.default:
        tags.append(_("default for calls"))
    if device.bluetooth:
        tags.append("Bluetooth")
    if device.programs:
        tags.append(_("in use by {programs}").format(
            programs=", ".join(dict.fromkeys(dev.program_label(p) for p in device.programs))))
    return " · ".join(tags)


def alert_words(alert: levels.Alert, names: dict[str, str]) -> str:
    if alert.kind == levels.CLIPPING:
        return _("The microphone is clipping (peaks at 0 dB): turn its level down in the system's sound "
                 "settings.")
    if alert.kind == echo.ECHO:
        return _("The microphone hears the computer's sound: with earphones the transcript comes out better. "
                 "Scriba will take that sound out of your track.")
    if alert.kind == levels.QUIET:
        return _("The microphone hears you faintly ({level:.0f} dB): is it the one you speak into? A headset has "
                 "its own.").format(level=alert.level)
    return _("No sound from the computer for {seconds:.0f} s: if the others are speaking, the call plays on "
             "another device.").format(seconds=alert.seconds)


def event_words(event: recording.Event, minutes: int = 0) -> str:
    at = clock(event.at)
    if event.kind in (recording.OFF, recording.ON):
        mine = event.detail == "me"
        if event.kind == recording.OFF:
            text = _("From {time} the microphone is off in Scriba: its track records silence.") if mine else \
                _("From {time} the computer's sound is off in Scriba: its track records silence.")
        else:
            text = _("From {time} the microphone is recorded again.") if mine else \
                _("From {time} the computer's sound is recorded again.")
        return text.format(time=at)
    if event.kind == recording.FOLLOWED:
        return _("Now recording from the microphone {program} is using: {device}.").format(
            program=event.detail, device=event.device)
    if event.kind == recording.CALL_ELSEWHERE:
        return _("{program} is using another microphone, {device}: Scriba goes on with the one you chose.").format(
            program=event.detail, device=event.device)
    return {
        recording.GONE: _("{device} went away at {time}: what it recorded is kept, the rest goes on."),
        recording.SWITCHED: _("From {time} the microphone is {device}."),
        recording.ADDED: _("{device} appeared at {time}: it is recorded on a track of its own."),
        recording.DISK_LOW: _("The disk is filling up: about {minutes} minutes are left."),
        recording.DISK_FULL: _("The disk is almost full: the recording was stopped at {time}."),
        recording.FAILED: _("The recording stopped at {time}: {detail}"),
    }.get(event.kind, event.kind).format(device=event.device, time=at, detail=event.detail, minutes=minutes)


def finishing_words(step: str) -> str:
    """What the app is doing after the stop, with the step's place: the
    time recorded stands still meanwhile."""
    what = {
        recording.CLOSING: _("closing the recording files…"),
        recording.JOINING: _("joining the tracks in one file…"),
        recording.CLEANING: _("checking whether the microphone heard the computer's sound…"),
        recording.MIXING: _("levelling the voices for the copy to listen to…"),
    }.get(step, step)
    number = recording.FINISHING.index(step) + 1 if step in recording.FINISHING else 1
    return _("Recording stopped · step {n} of {total}: {what}").format(n=number, total=len(recording.FINISHING),
                                                                         what=what)


class Side:
    """The microphone, or the computer's sound: an expander row with its
    switch, "Automatic" and every device with its meter."""

    def __init__(self, page: "RecordPage", kind: str) -> None:
        self.page, self.kind = page, kind
        is_input = kind == dev.INPUT
        self.row = Adw.ExpanderRow(title=_("Microphone") if is_input else _("Computer Sound"),
                                   subtitle=_("Looking for the devices…"), show_enable_switch=False,
                                   subtitle_lines=1, title_lines=1)
        self.row.add_prefix(Gtk.Image(icon_name=ICONS[kind]))
        self.meter = meter()
        self.row.add_suffix(self.meter)
        self.switch_button = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        self.switch_button.update_property([Gtk.AccessibleProperty.LABEL], [self.row.get_title()])
        self.switch_button.connect("notify::active", lambda button, _p:
                                   self.row.set_enable_expansion(button.get_active()))
        self.row.add_suffix(self.switch_button)
        self.row.connect("notify::enable-expansion", lambda r, _p: self._switched(r.get_enable_expansion()))
        self.auto = Adw.ActionRow(title=_("Automatic Choice"), subtitle_lines=0, activatable=True)
        self.auto_check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.auto.add_prefix(self.auto_check)
        self.auto.set_activatable_widget(self.auto_check)
        self.auto_check.connect("toggled", lambda b: b.get_active() and self._chose(""))
        self.row.add_row(self.auto)
        self.rows: dict[str, tuple[Adw.ActionRow, Gtk.CheckButton, Gtk.LevelBar]] = {}
        self._loading = False
        self._recording = False

    @property
    def setting(self) -> str:
        return "input_device" if self.kind == dev.INPUT else "output_device"

    @property
    def switch(self) -> str:
        return "record_input" if self.kind == dev.INPUT else "record_output"

    @property
    def role(self) -> str:
        return "me" if self.kind == dev.INPUT else "others"

    def load(self) -> None:
        self._loading = True
        try:
            self.row.set_enable_expansion(getattr(self.page.cfg, self.switch))
        finally:
            self._loading = False

    def _switched(self, on: bool) -> None:
        if self.switch_button.get_active() != on:
            self.switch_button.set_active(on)
        if self._loading:
            return
        if self._recording:
            # while recording the switch is this recording's: silence on the
            # track, the settings for the next one untouched
            if self.page.recorder is not None:
                self.page.recorder.switch(self.role, on)
            return
        setattr(self.page.cfg, self.switch, on)
        self.page.app.save_config()
        self.page.refresh()

    def _chose(self, device_id: str) -> None:
        if not self._loading:
            setattr(self.page.cfg, self.setting, device_id)
            self.page.app.save_config()
            self.page.refresh()

    def show(self, meters: recording.Meters, plan: dev.Plan, call_output: dev.Device | None) -> None:
        found = [d for d in meters.devices if d.kind == self.kind]
        self._sync_devices(found)
        chosen = getattr(self.page.cfg, self.setting)
        self._loading = True
        try:
            if chosen in self.rows:
                self.rows[chosen][1].set_active(True)
            else:
                self.auto_check.set_active(True)
        finally:
            self._loading = False
        for device in found:
            row, _check, bar = self.rows[device.id]
            tags = device_tags(device)
            if device.id in meters.closed:
                tags = " · ".join(t for t in (tags, _("its level shows once a program uses it: opening it would "
                                                       "switch the headset to telephone quality")) if t)
            elif device.id in meters.failed:
                tags = " · ".join(t for t in (tags, meters.failed[device.id]) if t)
            if row.get_title() != escape(device.name):
                row.set_title(escape(device.name))
            if row.get_subtitle() != escape(tags):
                row.set_subtitle(escape(tags))
            bar.set_value(self._value(meters, device.id))
            bar.set_sensitive(device.id not in meters.closed and device.id not in meters.failed)
        self._summary(meters, plan, call_output, found)

    def _value(self, meters: recording.Meters, device_id: str) -> float:
        level = meters.levels.get(device_id)
        return levels.fraction(level[1]) if level else 0.0

    def _summary(self, meters, plan: dev.Plan, call_output, found) -> None:
        on = self.row.get_enable_expansion()
        if self.kind == dev.INPUT:
            device, automatic = plan.input, plan.automatic_input
            choice = dev.choose_input(list(meters.devices), call_output, meters.voices) if automatic else None
        else:
            device, automatic = (plan.outputs[0] if plan.outputs else call_output), plan.automatic_output
            choice = dev.choose_output(list(meters.devices), meters.peaks) if automatic else None
        why = ""
        if choice is not None and choice.device is not None:
            # a call program and a browser on one device are one reason in words
            why = ", ".join(dict.fromkeys(reason_words(r, choice.device, call_output) for r in choice.reasons))
        if not on:
            text = _("Not recorded")
        elif not found:
            text = _("No device found") if meters.listed else _("Looking for the devices…")
        elif self.kind == dev.OUTPUT and automatic:
            text = _("All outputs · {device}").format(device=device.name) if device else _("All outputs")
        elif automatic:
            text = _("Automatic · {device}").format(device=device.name) if device else _("Automatic")
        else:
            text = _("Chosen · {device}").format(device=device.name) if device else _("No device found")
        self.row.set_subtitle(escape(text))
        self.row.set_tooltip_text(text)
        auto = _("Chosen now: {device} ({why})").format(device=choice.device.name, why=why) \
            if choice is not None and choice.device is not None else _("The app chooses the device where the call is")
        if self.kind == dev.OUTPUT:
            auto = _("Every output is recorded, and the silent ones are left out at the end.") + " " + auto
        self.auto.set_subtitle(escape(auto))
        self.meter.set_value(self._value(meters, device.id) if device is not None and on else 0.0)

    def _sync_devices(self, found: list[dev.Device]) -> None:
        """Only hardware coming or going changes the widget tree."""
        present = {device.id for device in found}
        for device_id in self.rows.keys() - present:
            row, _check, _bar = self.rows.pop(device_id)
            self.row.remove(row)
        for device in found:
            if device.id in self.rows:
                continue
            row = Adw.ActionRow(title=escape(device.name), title_lines=0, subtitle_lines=0, activatable=True)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER, group=self.auto_check)
            check.connect("toggled", lambda b, d=device.id: b.get_active() and self._chose(d))
            row.add_prefix(check)
            row.set_activatable_widget(check)
            bar = meter()
            row.add_suffix(bar)
            row.set_visible(not self._recording)
            self.row.add_row(row)
            self.rows[device.id] = (row, check, bar)

    def set_recording(self, active: bool) -> None:
        """The source stays expandable; choices are frozen before recording
        starts. The switch works on once a track of this side is recorded
        (show_recording); at the end it says the settings again."""
        self._recording = active
        self.switch_button.set_sensitive(not active)
        if not active:
            self.load()
        self.auto.set_visible(not active)
        self.auto_check.set_sensitive(not active)
        for row, check, _bar in self.rows.values():
            row.set_visible(not active)
            check.set_sensitive(not active)

    def show_recording(self, status: recording.Status) -> None:
        role = self.role
        takes = [take for take in status.tracks if take.role == role and take.ended is None]
        off = role in status.off
        # a side not recorded from the start has no track to switch
        self.switch_button.set_sensitive(self._recording and any(t.role == role for t in status.tracks))
        if not getattr(self.page.cfg, self.switch):
            text = _("Not recorded")
        elif off:
            text = _("Switched off in Scriba · silence on its track")
        elif role == "me" and status.call_muted:
            text = _("Muted in the call")
        elif self.kind == dev.OUTPUT and getattr(self.page.cfg, self.setting) == "":
            text = _("All outputs · {count} active").format(count=len(takes))
        elif takes:
            text = _("Recording · {device}").format(device=takes[0].device)
        else:
            text = _("No active device")
        self.row.set_subtitle(escape(text))
        self.row.set_tooltip_text(text)
        peaks = [levels.fraction(status.levels[take.key][1]) for take in takes if take.key in status.levels]
        self.meter.set_value(max(peaks, default=0.0) if status.state == "recording" and not off else 0.0)


class RecordPage(Adw.Bin):
    def __init__(self, window):
        super().__init__()
        self.window, self.app, self.backend = window, window.app, window.backend
        self.monitor = None
        self.recorder = None
        self.folder = None
        self._tick_id = 0
        self._starting = self._stopping = False
        self._finishing = recording.CLOSING     # the step after the stop, set by the worker (one assignment)
        self._stop_requested = False
        self.last_error = ""
        self.last_finished: recording.Finished | None = None
        self._events = 0
        self._then = None               # what to do once the recording is finished (the window's close)
        self._track_rows: dict[str, tuple[Adw.ActionRow, Gtk.LevelBar]] = {}
        self._track_sides: dict[str, Side] = {}
        self.missing = self.backend.recorder_missing()

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._unavailable(), "unavailable")
        self.stack.add_named(self._page(), "page")
        self.stack.set_visible_child_name("unavailable" if self.missing else "page")
        self.set_child(self.stack)
        self._show_idle()

    @property
    def cfg(self):
        return self.app.cfg

    @property
    def busy(self) -> bool:
        return self.recorder is not None or self._starting or self._stopping

    # -- the parts ---------------------------------------------------------------------

    def _unavailable(self) -> Gtk.Widget:
        return Adw.StatusPage(icon_name="microphone-disabled-symbolic", title=_("Recording Is Not Possible Here"),
                              description=escape(_("GStreamer lacks {pieces}: Scriba can still transcribe "
                                                   "recordings made elsewhere.").format(
                                  pieces=", ".join(self.missing) or "?")))

    def _page(self) -> Gtk.Widget:
        view = Adw.ToolbarView()
        self.banner = Adw.Banner(revealed=False)
        view.add_bottom_bar(self.banner)
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup()
        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=SPACE_L, margin_top=SPACE_XL,
                       margin_bottom=SPACE_L, halign=Gtk.Align.CENTER)
        self.time_label = Gtk.Label(label=clock(0), css_classes=["title-1", "numeric"])
        hero.append(self.time_label)
        self.state_label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, css_classes=["dim-label"])
        states = Gtk.Stack(vhomogeneous=True, hhomogeneous=True)
        states.add_named(self.state_label, "state")
        states.add_named(Gtk.Label(label=_("Press the button to record: nothing starts by itself."),
                                   wrap=True, justify=Gtk.Justification.CENTER,
                                   css_classes=["dim-label"]), "measure")
        states.set_visible_child_name("state")
        hero.append(states)
        self.controls = Gtk.Stack(vhomogeneous=True, hhomogeneous=True, halign=Gtk.Align.CENTER,
                                  margin_top=SPACE_XS)
        idle_buttons = Gtk.Box(halign=Gtk.Align.CENTER)
        recording_buttons = Gtk.Box(spacing=SPACE_XL, halign=Gtk.Align.CENTER)
        self.record_button = self._round("media-record-symbolic", _("Record"), ["destructive-action"], self.start)
        self.pause_button = self._round("media-playback-pause-symbolic", _("Pause"), [], self.pause)
        self.stop_button = self._round("media-playback-stop-symbolic", _("Stop"), ["suggested-action"], self.stop)
        idle_buttons.append(self.record_button)
        recording_buttons.append(self.pause_button)
        recording_buttons.append(self.stop_button)
        self.controls.add_named(idle_buttons, "idle")
        self.controls.add_named(recording_buttons, "recording")
        hero.append(self.controls)
        group.add(hero)
        page.add(group)

        self.sources = Adw.PreferencesGroup(title=_("What Is Recorded"),
                                            description=escape(_("Your voice from the microphone and the others' "
                                                                 "from the computer, on separate tracks.")))
        self.input_side, self.output_side = Side(self, dev.INPUT), Side(self, dev.OUTPUT)
        self.sources.add(self.input_side.row)
        self.sources.add(self.output_side.row)
        page.add(self.sources)

        group = Adw.PreferencesGroup()
        self.folder_row = Adw.ActionRow(title=_("Folder for the Recordings"), subtitle_lines=2)
        self.folder_row.add_prefix(Gtk.Image(icon_name="folder-music-symbolic"))
        # Open in words, as the Result page has it: the folder's icon is the choice's
        show = Gtk.Button(label=_("Open Folder"), tooltip_text=_("Open the Folder"), valign=Gtk.Align.CENTER,
                          css_classes=["flat"])
        show.connect("clicked", lambda *_: self._open_folder())
        self.folder_row.add_suffix(show)
        self.choose_button = Gtk.Button(label=_("Change…"), tooltip_text=_("Choose a Folder…"),
                                        valign=Gtk.Align.CENTER, css_classes=["flat"])
        self.choose_button.connect("clicked", lambda *_args: choose_folder(
            self.window, _("Choose the Folder for the Recordings"), str(recordings_folder(self.cfg)),
            self._chose_folder))
        self.folder_row.add_suffix(self.choose_button)
        group.add(self.folder_row)
        page.add(group)
        self.page = page
        view.set_content(page)
        return view

    def _round(self, icon: str, tip: str, classes: list[str], clicked) -> Gtk.Button:
        button = Gtk.Button(child=Gtk.Image(icon_name=icon, pixel_size=ICON_RECORD), tooltip_text=tip,
                            css_classes=["circular", "record-button", *classes], valign=Gtk.Align.CENTER)
        button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        button.connect("clicked", lambda *_: clicked())
        return button

    # -- on screen or not --------------------------------------------------------------

    def shown(self, visible: bool) -> None:
        """The page came on screen (the meters start) or went off it (they
        stop: no device stays open for nothing)."""
        if getattr(self.app, "closing", False):
            self.stop_all()
            return
        if self.missing:
            return
        if visible and self.recorder is None and not self._starting and self.monitor is None:
            self.input_side.load()
            self.output_side.load()
            self._show_folder()
            self.monitor = self.backend.monitor()
            self.monitor.start()
        elif not visible and self.monitor is not None and not self._starting:
            self._stop_monitor()
        self._ticking()

    def _stop_monitor(self) -> None:
        monitor, self.monitor = self.monitor, None
        if monitor is not None:
            def stopped(_result, error):
                if error is not None:
                    report("stopping the meters", error)
                    if getattr(self.app, "closing", False):
                        self.app.block_exit(error)
            run_in_thread(monitor.stop, stopped)

    def _ticking(self) -> None:
        wanted = self.monitor is not None or self.recorder is not None
        if wanted and not self._tick_id:
            self._tick_id = GLib.timeout_add(TICK_MS, self._tick)
        elif not wanted and self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0

    def stop_all(self) -> None:
        """Stop meter polling now; the registered worker owns monitor cleanup.

        The application requests recording finalization separately with stop().
        A monitor join must never block the GTK thread or escape the exit barrier.
        """
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._stop_monitor()

    def refresh(self) -> None:
        if self.monitor is not None:
            self._show_meters(self.monitor.snapshot())

    # -- what the page shows -----------------------------------------------------------

    def plan(self, meters: recording.Meters) -> dev.Plan:
        cfg = self.cfg
        return dev.plan(list(meters.devices), cfg.record_input, cfg.record_output, cfg.input_device,
                        cfg.output_device, meters.peaks, meters.voices)

    def _show_idle(self) -> None:
        self.time_label.set_label(clock(0))
        self.state_label.set_label(_("Press the button to record: nothing starts by itself."))
        self.controls.set_visible_child_name("idle")
        self.pause_button.set_sensitive(True)
        self.stop_button.set_sensitive(True)
        self.sources.set_visible(True)
        self.input_side.set_recording(False)
        self.output_side.set_recording(False)
        for row, _bar in self._track_rows.values():
            row.set_visible(False)
        self.choose_button.set_sensitive(True)
        self.banner.set_revealed(False)
        self._show_folder()

    def _show_meters(self, meters: recording.Meters) -> None:
        if meters.error:
            self._banner(meters.error)
        plan = self.plan(meters)
        call = dev.choose_output(list(meters.devices), meters.peaks).device
        self.input_side.show(meters, plan, call)
        self.output_side.show(meters, plan, call)
        ready = plan.input is not None or bool(plan.outputs)
        self.record_button.set_sensitive(ready and not self._starting)
        if not ready and meters.listed:
            self.state_label.set_label(_("Switch on the microphone or the computer's sound to record."))
        elif ready:
            self.state_label.set_label(_("Press the button to record: nothing starts by itself."))

    def _show_folder(self) -> None:
        self.folder_row.set_subtitle(escape(jobs.full_path(recordings_folder(self.cfg))))

    def _chose_folder(self, folder: str) -> None:
        if getattr(self.app, "closing", False):
            return
        refused = recording.unsuitable(folder, recording.own_folders(self.app.paths))
        if refused:
            self.window.toast(refused)
            return
        self.cfg.recordings_folder = folder
        self.app.save_config()
        self._show_folder()

    def _open_folder(self) -> None:
        if getattr(self.app, "closing", False):
            return
        folder = recordings_folder(self.cfg)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.window.toast(_("The folder cannot be made: {error}").format(error=e))
            return
        open_path(self.window, str(folder))

    def _banner(self, text: str) -> None:
        self.banner.set_title(escape(text))
        self.banner.set_revealed(True)

    def _remember_error(self, message: str) -> None:
        changed = message != self.last_error
        self.last_error = message
        background = getattr(self.app, "background", None)
        if changed and background is not None and background.hidden and not getattr(self.app, "closing", False):
            try:
                self.app.notify(_("Recording Needs Attention"), message, tag="recording-error",
                                on_click=background.present)
            except (GLib.Error, OSError) as error:
                report("notifying about the recording", error)

    def _tick(self) -> bool:
        if self.recorder is not None:
            self._show_status(self.recorder.snapshot())
        elif self.monitor is not None:
            self._show_meters(self.monitor.snapshot())
        else:
            self._tick_id = 0
            return False
        return True

    # -- recording ---------------------------------------------------------------------

    def start(self) -> None:
        if getattr(self.app, "closing", False):
            return
        if self.app.installing:
            self.window.toast(_("An update is starting: wait for {app} to open again.").format(app=APP_NAME))
            return
        if self.busy or self.monitor is None:
            return
        meters = self.monitor.snapshot()
        plan = self.plan(meters)
        folder = recordings_folder(self.cfg)
        refused = recording.unsuitable(folder, recording.own_folders(self.app.paths))
        if refused:
            self._banner(refused)
            return
        now = datetime.datetime.now()
        stem = rec.stem(now, recording.file_words()["recording"])
        monitor, self.monitor = self.monitor, None
        self._starting = True
        self._stop_requested = False
        self.last_error = ""
        self.window.page_changed()
        self.input_side.set_recording(True)
        self.output_side.set_recording(True)
        self.choose_button.set_sensitive(False)
        self.record_button.set_sensitive(False)
        self.state_label.set_label(_("Starting…"))

        meeting = []

        def work():
            monitor.stop()          # the meters' streams closed before the recording's open
            # a folder of its own for each recording, with every file of it (the transcripts too)
            meeting.append(recording.meeting_folder(folder, now))
            recorder = self.backend.recorder(plan, meeting[0], stem)
            recorder.follow_call = WINDOWS and self.cfg.follow_call_mute
            recorder.start()
            return recorder

        run_in_thread(work, lambda recorder, error: self._started(recorder, error, meeting[0] if meeting else folder))

    def _started(self, recorder, error, folder) -> None:
        self._starting = False
        closing = getattr(self.app, "closing", False)
        if error is not None:
            self._stop_requested, self._then = False, None
            self._show_idle()
            report("starting the recording", error)
            message = str(error) if isinstance(error, Failure) else \
                _("The recording could not start: {reason}").format(reason=error)
            self._remember_error(message)
            self._banner(message)
            self.record_button.set_sensitive(True)
            if closing:
                self.app.block_exit(error)
            else:
                self.shown(self.window.views.get_visible_child_name() == "record")
            self.window.page_changed()
            return
        self.recorder, self.folder = recorder, folder
        self._events = 0
        self.banner.set_revealed(False)
        self.controls.set_visible_child_name("recording")
        self.input_side.set_recording(True)
        self.output_side.set_recording(True)
        self.choose_button.set_sensitive(False)
        self._build_tracks(recorder.snapshot())
        self._show_status(recorder.snapshot())
        if self._stop_requested or closing:
            self.stop()
            return
        self._ticking()
        self.window.page_changed()

    def _build_tracks(self, status: recording.Status) -> None:
        for key, (row, _bar) in self._track_rows.items():
            self._track_sides[key].row.remove(row)
        self._track_rows = {}
        self._track_sides = {}
        for take in status.tracks:
            self._add_track(take)

    def _add_track(self, take: recording.Take) -> None:
        row = Adw.ActionRow(title=escape(take.device), title_lines=0, subtitle_lines=0)
        row.add_prefix(Gtk.Image(icon_name=ICONS[dev.INPUT if take.role == "me" else dev.OUTPUT]))
        bar = meter()
        row.add_suffix(bar)
        side = self.input_side if take.role == "me" else self.output_side
        side.row.add_row(row)
        self._track_rows[take.key] = (row, bar)
        self._track_sides[take.key] = side

    def _show_status(self, status: recording.Status) -> None:
        self.time_label.set_label(clock(status.elapsed))
        words = {"recording": _("Recording in progress"), "paused": _("Paused"), "stopping": _("Closing the files…"),
                 "stopped": _("Stopped"), "failed": _("Stopped")}
        state = words.get(status.state, status.state)
        if self._stopping:
            state = finishing_words(self._finishing)
        elif status.seconds_left < rec.WARN_MINUTES * 60:
            state += " · " + _("room on the disk for about {minutes} minutes").format(
                minutes=max(int(status.seconds_left // 60), 0))
        self.state_label.set_label(state)
        paused = status.state == "paused"
        self.pause_button.get_child().set_from_icon_name("media-record-symbolic" if paused else
                                                         "media-playback-pause-symbolic")
        tip = _("Go On") if paused else _("Pause")
        self.pause_button.set_tooltip_text(tip)
        self.pause_button.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        for take in status.tracks:
            if take.key not in self._track_rows:
                self._add_track(take)
            row, bar = self._track_rows[take.key]
            what = _("Microphone: your voice") if take.role == "me" else _("Computer sound: the others")
            if take.began > 0:
                what += " · " + _("from {time}").format(time=clock(take.began))
            if take.ended is not None:
                what += " · " + (_("until {time}") if take.replaced else _("went away at {time}")).format(
                    time=clock(take.ended))
                row.add_css_class("dim-label")
            row.set_subtitle(escape(what))
            level = status.levels.get(take.key)
            # paused, nothing comes in: the meters say so
            bar.set_value(levels.fraction(level[1]) if level and take.ended is None and not paused else 0.0)
        self.input_side.show_recording(status)
        self.output_side.show_recording(status)
        for event in status.events[self._events:]:
            self.window.toast(event_words(event, max(int(status.seconds_left // 60), 0)))
        self._events = len(status.events)
        names = {t.key: t.device for t in status.tracks}
        order = {levels.CLIPPING: 0, levels.QUIET: 1, levels.SILENT: 2, echo.ECHO: 3}
        alerts = sorted(status.alerts, key=lambda a: order.get(a.kind, 9))
        if status.error:
            self._remember_error(status.error)
            self._banner(status.error)
        elif alerts:
            self._banner(alert_words(alerts[0], names))
        else:
            self.banner.set_revealed(False)
        if status.state in ("stopped", "failed") and not self._stopping:
            self.stop()             # stopped by itself (the disk, an error): the files are made whole

    def pause(self) -> None:
        if getattr(self.app, "closing", False) or self.recorder is None or self._stopping:
            return
        if self.recorder.snapshot().state == "paused":
            self.recorder.resume()
        else:
            self.recorder.pause()

    def stop(self, then=None) -> None:
        """The recording stopped, its files closed and made whole (in a
        thread). A stop requested during start is consumed by _started.
        The first then() is called once on success only; finalization errors
        block an application exit and leave the recording files reviewable.
        """
        if then is not None and self._then is None:
            self._then = then
        self._stop_requested = True
        if self._starting or self._stopping:
            return
        recorder = self.recorder
        if recorder is None:
            self._stop_requested = False
            completed, self._then = self._then, None
            if completed is not None:
                completed()
            return
        self._stop_requested = False
        self._stopping = True
        self._finishing = recording.CLOSING
        self.state_label.set_label(finishing_words(self._finishing))
        self.pause_button.set_sensitive(False)
        self.stop_button.set_sensitive(False)
        self.window.page_changed()

        def step(name: str) -> None:
            self._finishing = name          # read by the tick on the main loop

        def work():
            takes = recorder.stop()
            status = recorder.snapshot()
            return self.backend.finish(takes, recorder.folder, recorder.stem, progress=step), status

        run_in_thread(work, self._stopped)

    def _stopped(self, result, error) -> None:
        self.recorder, self._stopping = None, False
        self._stop_requested = False
        closing = getattr(self.app, "closing", False)
        self.stack.set_visible_child_name("page")
        self._show_idle()
        self._ticking()
        then, self._then = self._then, None
        if error is not None:
            report("finishing the recording", error)
            message = str(error) if isinstance(error, Failure) else \
                _("The recording could not be finished: {error}").format(error=error)
            if self.folder is not None:
                message += " " + _("Check the recording files in {folder}.").format(folder=self.folder)
            self._remember_error(message)
            self._banner(message)
            self.window.toast(message)
            if closing:
                self.app.block_exit(error)
        else:
            finished, status = result
            self.last_finished = finished
            if status.error:
                self._remember_error(status.error)
                self.window.toast(status.error)
            if closing:
                for note in finished.notes:
                    print(f"finishing the recording: {note}", file=sys.stderr, flush=True)
            if then is None and not closing:
                self.window.recorded(finished)
        if error is None and then is not None:
            then()
            return
        if not closing:
            self.shown(self.window.views.get_visible_child_name() == "record")
        self.window.page_changed()
