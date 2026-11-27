"""The recorder: a meeting's sound from the computer, through GStreamer.

Two pieces. The Monitor runs while the Record page is open and nothing is
recorded: it lists the devices every WATCH_EVERY seconds, keeps a meter on
each one that may be opened (core/devices.py, may_open) and works out the
automatic choice from what it hears. The Recorder records: one pipeline,
so one clock for every track (they start together and stay together: a
device that comes later is filled with silence from the start by
audiorate, so every track's sample 0 is the recording's start), one
branch per device, each branch to a FLAC file of its own:

    source ! queue ! audioconvert ! audioresample ! F32 48 kHz mono
           ! audiorate ! volume ! level ! audioconvert ! S24 ! flacenc ! filesink

The `volume` is Scriba's own switch while recording (Recorder.switch): at
1.0 it passes the sound as it is, muted it passes zeros with the same
timestamps, so the track goes on, as long as the others, with digital
silence where it was off (the meter after it falls to nothing). The mute
inside a call program is not in the sound: that program sends nothing, and
the microphone's device goes on hearing the room. On Windows 11, with the
preference on (`follow_call`), Windows' microphone indicator is read once a
second (app/callmute.py): the stretches muted in the call are kept in the
microphone's `muted`, its sound untouched, and the transcript leaves out
the microphone's speech there (core/callmute.py, app/jobs.py).

The signal is never touched: no gain, no automatic level, float until the
file and 24 bits in it (144 dB of room above the noise: a voice at -50 dB
keeps all its detail, and the volume is put right afterwards, on the
file: app/recording.py). 48 kHz whatever the device's own rate (44.1 kHz
earphones, 16 kHz for a headset in a call): every track the same, so that
they mix and join without another resampling, and resampling up loses
nothing; the transcription brings it to 16 kHz itself. One channel: the
voices of a call are one channel, and the file is half as heavy. The file
is written unbuffered (filesink's buffer-mode 2): a silent output's track
compresses to a few bytes a second, and with the default 64 kB buffer a
process killed after 7 s had left it empty (tried, 11/11/2026).

The two ways in:

- Windows: wasapi2src (GStreamer's WASAPI, plugins-bad), the device by its
  endpoint id, the outputs in loopback; `continue-on-error`, so that a
  device that goes away (earphones unplugged, Bluetooth out of range) goes
  on giving silence instead of stopping the whole recording, and the watch
  says it and goes on with what is left. The devices and what decides the
  choice come from Core Audio (app/winaudio.py).
- Linux: pulsesrc (plugins-good; PipeWire answers it as PulseAudio does),
  the input by its source's name, an output by its monitor
  ("<sink>.monitor"); the devices from PulseAudio's own GStreamer provider.
  Without a sound server there are no devices. pulsesrc has no
  continue-on-error: a device that goes away
  stops the recording, its files closed properly.

The watch also follows the call's microphone (core/devices.py, Follow): in
the automatic choice, when a call program opens a microphone other than
the one recorded (Record pressed before the call began), a new track
starts from that one and the old one's source is told it is the end, so
its file closes where it is; the files of the microphone are joined at the
end as the user's, one after the other.

The raw tracks are the recording. Echo removal works on the listening
copy and the audio prepared for transcription (app/recording.py, app/jobs.py).

GStreamer's bus is read in threads of this module (no main loop): the
window reads snapshot() ten times a second and draws. Gst is the part of
the toolkit's library this module may use (tests/test_strati.py,
WINDOWLESS); nothing of a window.
"""

from __future__ import annotations

import math
import os
import shutil
import threading
import time
from collections import deque
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from scriba import WINDOWS  # noqa: E402
from scriba.app import recovery  # noqa: E402
from scriba.core import callend, callmute, devices as dev, echo, levels, recording as rec, spans, teams  # noqa: E402
from scriba.data import files  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .log import complain  # noqa: E402
from .recording import (ADDED, CALL_ELSEWHERE, DISK_FULL, DISK_LOW, FAILED, FOLLOWED, GONE, OFF, ON,  # noqa: E402
                        SWITCHED, Event, Meters, Status, Take, file_words)
from .task import Failure  # noqa: E402

SOURCE = "wasapi2src" if WINDOWS else "pulsesrc"
NEEDED = (SOURCE, "queue", "audioconvert", "audioresample", "capsfilter", "audiorate", "volume", "level",
          "flacenc", "filesink", "fakesink")
INTERVAL = 100 * Gst.MSECOND        # the meters' pace: ten levels a second
LATENCY_TIME = 50_000               # pulsesrc's read size, in microseconds (see _source)
WATCH_EVERY = 2.0                   # seconds between two looks at the devices
SPACE_EVERY = 5.0                   # and at the disk
PEAKS_FOR = 3.0                     # seconds of peaks an output's "sound now" looks at
STOP_WAIT = 10.0                    # seconds the files have to close after the stop, before they are cut
NULL_WAIT = 3.0                     # seconds to wait for a driver to release the pipeline
TRACK_CAPS = f"audio/x-raw,format=F32LE,rate={rec.RATE},channels=1"
FILE_CAPS = "audio/x-raw,format=S24LE"

_init_lock = threading.Lock()


def init() -> None:
    with _init_lock:
        if not Gst.is_initialized():
            Gst.init(None)


def missing() -> list[str]:
    """The GStreamer elements the recorder needs and this computer lacks."""
    init()
    return [name for name in NEEDED if Gst.ElementFactory.find(name) is None]


def _make(factory: str, **properties) -> Gst.Element:
    element = Gst.ElementFactory.make(factory, None)
    if element is None:
        raise Failure(_("GStreamer's {element} is missing: Scriba needs it to record.").format(element=factory),
                      factory)
    for name, value in properties.items():
        # `async_` for GObject's `async`, a keyword of Python's
        name = name.rstrip("_").replace("_", "-")
        if isinstance(value, str) and name == "buffer-mode":
            Gst.util_set_object_arg(element, name, value)      # an enumeration, by its nick
        else:
            element.set_property(name, value)
    return element


# -- the devices -----------------------------------------------------------------------

def list_devices() -> list[dev.Device]:
    """The inputs and outputs there are now; none if PulseAudio is unavailable."""
    if WINDOWS:
        from . import winaudio
        try:
            return winaudio.endpoints()
        except OSError as e:
            raise Failure(_("Windows does not list its sound devices."), str(e)) from e
    return _pulse_devices()


def _pulse_devices() -> list[dev.Device]:
    """PulseAudio's sources (PipeWire's too): a monitor source is an output."""
    init()
    # DeviceMonitor starts unrelated providers too; GStreamer 1.28.2 can abort
    # when stopping it after one failed. Only Pulse devices back pulsesrc.
    provider = Gst.DeviceProviderFactory.get_by_name("pulsedeviceprovider")
    if provider is None or not provider.start():
        return []
    try:
        found = provider.get_devices() or []
    finally:
        provider.stop()
    out = []
    for device in found:
        element = device.create_element(None)
        if element is None or element.get_factory().get_name() != "pulsesrc":
            continue
        props = device.get_properties()

        def text(key: str, props=props) -> str:
            return (props.get_string(key) or "") if props is not None and props.has_field(key) else ""

        default = bool(props is not None and props.has_field("is-default") and props.get_value("is-default"))
        kind = dev.OUTPUT if text("device.class") == "monitor" else dev.INPUT
        out.append(dev.Device(id=element.get_property("device") or "", name=device.get_display_name(), kind=kind,
                              default=default, communications=default,
                              container=text("api.bluez5.address") or text("device.bus_path") or text("alsa.card"),
                              bluetooth=text("device.bus") == "bluetooth" or text("device.api") == "bluez5"))
    return [d for d in out if d.id]


def _source(device: dev.Device) -> Gst.Element:
    if WINDOWS:
        return _make("wasapi2src", device=device.id, loopback=device.kind == dev.OUTPUT, continue_on_error=True)
    # 50 ms reads, not GStreamer's 10 ms: a recorder gains nothing from a
    # shorter wait, and PipeWire runs the whole graph at the smallest latency
    # a stream asks for, so 10 ms forced every other program's audio into
    # 5 ms blocks, and effects that miss them crackle (EasyEffects while
    # windows animate, 2026-11-22). Every track waits the same, so they stay
    # in step; the meters' 100 ms pace is unchanged.
    return _make("pulsesrc", device=device.id, latency_time=LATENCY_TIME)


class _Branch:
    """One device's way through the pipeline, to a file or to nothing (a
    meter only)."""

    def __init__(self, pipeline: Gst.Pipeline, key: str, device: dev.Device, path: Path | None) -> None:
        self.key, self.device, self.path = key, device, path
        self.source = _source(device)
        self.volume = _make("volume")
        chain = [self.source, _make("queue"), _make("audioconvert"), _make("audioresample"),
                 _make("capsfilter", caps=Gst.Caps.from_string(TRACK_CAPS)), _make("audiorate"), self.volume,
                 _make("level", name=f"level:{key}", interval=INTERVAL, post_messages=True)]
        if path is not None:
            chain += [_make("audioconvert"), _make("capsfilter", caps=Gst.Caps.from_string(FILE_CAPS)),
                      _make("flacenc"), _make("filesink", location=os.fspath(path), buffer_mode="unbuffered", sync=False,
                                              async_=False)]
        else:
            chain.append(_make("fakesink", sync=False, async_=False))
        self.level = chain[7]
        self.elements = chain
        for element in chain:
            pipeline.add(element)
        for a, b in zip(chain, chain[1:], strict=False):      # each to the next
            if not a.link(b):
                raise Failure(_("The recording could not be set up."), f"{a.get_name()} -> {b.get_name()}")

    def start(self) -> None:
        """Into a pipeline that is going already, the sink first."""
        for element in reversed(self.elements):
            element.sync_state_with_parent()


def _level(message: Gst.Message) -> tuple[str, float, float] | None:
    """(key, rms, peak) of a level element's message."""
    structure = message.get_structure()
    if structure is None or structure.get_name() != "level":
        return None
    name = message.src.get_name() if message.src is not None else ""
    if not name.startswith("level:"):
        return None
    rms, peak = structure.get_value("rms"), structure.get_value("peak")
    if not rms or not peak:
        return None
    return name.split(":", 1)[1], float(rms[0]), float(peak[0])


def _pop(bus: Gst.Bus, wait: float) -> Gst.Message | None:
    return bus.timed_pop_filtered(int(wait * Gst.SECOND), Gst.MessageType.ELEMENT | Gst.MessageType.ERROR
                                  | Gst.MessageType.WARNING | Gst.MessageType.EOS)


def _error_text(message: Gst.Message) -> tuple[str, str]:
    error, debug = message.parse_error()
    return error.message, f"{message.src.get_name() if message.src else '?'}: {error.message} ({debug or ''})"


# -- the meters while nothing is recorded ----------------------------------------------

class Monitor:
    """Devices and meters, in a thread of its own (start, stop)."""

    def __init__(self, lister=list_devices) -> None:
        self._list = lister
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pipelines: dict[str, Gst.Pipeline] = {}
        self._watch = levels.Watch()
        self._now: dict[str, tuple[float, float]] = {}
        self._peaks: dict[str, deque] = {}
        self._state = Meters()

    def start(self) -> None:
        if self._thread is None:
            init()
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="scriba-meters", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None

    def snapshot(self) -> Meters:
        with self._lock:
            now = time.monotonic()
            peaks = {key: max((p for t, p in values if t >= now - PEAKS_FOR), default=-math.inf)
                     for key, values in self._peaks.items()}
            voices = {key: v for key in self._now if (v := self._watch.voice(key)) is not None}
            return replace(self._state, levels=dict(self._now), peaks=peaks, voices=voices)

    def _run(self) -> None:
        try:
            listed_at = -math.inf
            while not self._stop.is_set():
                if time.monotonic() - listed_at >= WATCH_EVERY:
                    listed_at = time.monotonic()
                    self._relist()
                busy = False
                for key, pipeline in list(self._pipelines.items()):
                    message = _pop(pipeline.get_bus(), 0)
                    while message is not None:
                        busy = True
                        self._handle(key, message)
                        message = _pop(pipeline.get_bus(), 0)
                if not busy:
                    self._stop.wait(0.05)
        except Exception as e:      # the thread's border: said, and the meters stop
            complain("the meters", e)
            with self._lock:
                self._state = replace(self._state, error=str(e))
        finally:
            for pipeline in self._pipelines.values():
                pipeline.set_state(Gst.State.NULL)
            self._pipelines.clear()

    def _relist(self) -> None:
        try:
            found = self._list()
        except Failure as e:
            with self._lock:
                self._state = replace(self._state, error=str(e), listed=True)
            return
        closed = frozenset(d.id for d in found if not dev.may_open(d))
        ids = {d.id for d in found if d.id not in closed}
        for gone in set(self._pipelines) - ids:
            self._pipelines.pop(gone).set_state(Gst.State.NULL)
            with self._lock:
                self._now.pop(gone, None)
        failed = dict(self._state.failed)
        for device in found:
            if device.id in closed or device.id in self._pipelines or device.id in failed:
                continue
            pipeline = Gst.Pipeline.new(None)
            try:
                _Branch(pipeline, device.id, device, None)
                if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                    raise Failure(_("The device cannot be opened."), device.name)
            except Failure as e:
                pipeline.set_state(Gst.State.NULL)
                failed[device.id] = str(e)
                continue
            self._pipelines[device.id] = pipeline
            self._watch.track(device.id, device.kind == dev.INPUT)
        with self._lock:
            self._state = replace(self._state, devices=tuple(found), closed=closed, failed=failed, error="",
                                  listed=True)

    def _handle(self, key: str, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.ERROR:
            text, detail = _error_text(message)
            print(f"meter of {key}: {detail}", flush=True)
            pipeline = self._pipelines.pop(key, None)
            if pipeline is not None:
                pipeline.set_state(Gst.State.NULL)
            with self._lock:
                self._state = replace(self._state, failed={**self._state.failed, key: text})
                self._now.pop(key, None)
            return
        found = _level(message) if message.type == Gst.MessageType.ELEMENT else None
        if found is None:
            return
        _key, rms, peak = found
        now = time.monotonic()
        with self._lock:
            self._now[key] = (rms, peak)
            self._watch.feed(key, now, rms, peak)
            values = self._peaks.setdefault(key, deque())
            values.append((now, peak))
            while values and values[0][0] < now - PEAKS_FOR:
                values.popleft()


# -- the recording ---------------------------------------------------------------------

class Recorder:
    """One recording, from start() to stop(). The files are FLAC, one per
    track, in `folder` under names that share `stem` (made free there:
    never over a file that is there)."""

    def __init__(self, plan: dev.Plan, folder: Path, stem: str, lister=list_devices) -> None:
        if plan.input is None and not plan.outputs:
            raise Failure(_("Nothing to record: switch on the microphone or the computer's sound."), "empty plan")
        self.plan, self.folder = plan, Path(folder)
        self._list = lister
        self._words = file_words()
        self._made_folder = not self.folder.exists()    # removed again, empty, when the start fails
        self.folder.mkdir(parents=True, exist_ok=True)
        together = f"_{_('together')}.m4a"
        self.stem = files.free_stem(self.folder, stem, (".mka", together, f"_{self._words['me']}.flac",
                                                        f"_{self._words['others']} 1.flac"))
        self._lock = threading.Lock()
        self.journal = recovery.Journal(self.folder, self.stem)
        self._journal_lock = None
        self._last_save_error = ""
        self._stop_lock = threading.Lock()
        self._device_changes = threading.Lock()
        self._skip_close = threading.Event()
        self._pipeline: Gst.Pipeline | None = None
        self._branches: dict[str, _Branch] = {}
        self._takes: dict[str, Take] = {}
        self._watch = levels.Watch()
        self._now: dict[str, tuple[float, float]] = {}
        self._events: list[Event] = []
        self._state = "new"
        self._error = ""
        self._began = 0.0
        self._paused_at: float | None = None
        self._paused_for = 0.0
        self._ended_at: float | None = None     # when the stop was asked: the sources take no more sound
        self._eos = threading.Event()
        self._quit = threading.Event()
        self._left = math.inf
        self._counts = {"me": 0, "others": 0}
        self._off: set[str] = set()             # the roles switched off in Scriba while recording
        self._follow = dev.Follow()             # the microphone a call program opens (_follow_the_call)
        self._told: set[str] = set()            # the microphones said to be the call's, not followed
        self.follow_call = False                # Windows: follow the call's mute (set before start)
        self.call_indicator = None              # the indicator's opener for app/callmute.follow (tests)
        self._call_muted = False                # the user is muted in the call now
        self.teams_names = False                # enabled by the Windows preference before start
        self.call_stop_minutes = None     # set by the GUI on platforms with Teams observations
        self._call_end = callend.CallEnd()
        self._last_output_sample = None
        self._last_output_sound = None
        self._silence_from = 0.0
        self.teams_reader = None                # replaceable opener, as for the call indicator
        self._named = spans.Builder()
        self._meeting_title = ""
        self._teams_state, self._teams_people = "", 0
        self._teams_remotes: dict[str, str] = {}
        self._teams_summary = teams.ReadingSummary()

    # -- the tracks ----------------------------------------------------------------

    def _path_for(self, role: str, device: dev.Device) -> Path:
        """A new file for the track, reserved at once (created empty, never
        over one that is there): "<stem>_microfono.flac", "<stem>_uscita 1
        Cuffie (Headset BT).flac"."""
        self._counts[role] += 1
        word = self._words[role]
        if role == "me":
            name = f"{self.stem}_{word}" if self._counts[role] == 1 else f"{self.stem}_{word} {self._counts[role]}"
        else:
            name = f"{self.stem}_{word} {self._counts[role]} {rec.safe(device.name)}"
        while True:
            path = files.free_name(self.folder, name, ".flac")
            try:
                with open(path, "xb"):
                    return path
            except FileExistsError:
                continue

    def _add(self, device: dev.Device, role: str, at: float) -> Take:
        key = f"{role}-{len(self._takes) + 1}"
        path = self._path_for(role, device)
        try:
            branch = _Branch(self._pipeline, key, device, path)
        except Failure:
            path.unlink(missing_ok=True)
            raise
        take = Take(key, role, device.name, path, began=at)
        if role in self._off:           # a device that comes while its side is off starts silent
            branch.volume.set_property("mute", True)
            take.off = ((at, None),)
        if role == "me" and self._call_muted:
            take.muted = ((at, None),)
        with self._lock:
            self._branches[key] = branch
            self._takes[key] = take
            self._watch.track(key, role == "me")
            if self._state == "recording":
                self._save_locked()
        return take

    # -- start, pause, stop ----------------------------------------------------------

    def start(self) -> None:
        """Failure when the disk has no room for ten minutes, or the pipeline
        does not start (nothing is left behind then)."""
        init()
        count = (1 if self.plan.input else 0) + len(self.plan.outputs)
        free = shutil.disk_usage(self.folder).free
        if not rec.may_start(free, count):
            self._unmake_folder()
            raise Failure(_("There is not enough room on the disk for ten minutes of recording."),
                          f"{free} bytes free in {self.folder}")
        self._pipeline = Gst.Pipeline.new("scriba-recorder")
        # Every device follows a clock that survives another source stopping.
        # A device's audio clock also starts late by its own capture latency:
        # using it as the master shifts that track relative to the others.
        self._pipeline.use_clock(Gst.SystemClock.obtain())
        try:
            lease = self.journal.lock()
            lease.__enter__()
            self._journal_lock = lease
            if self.plan.input is not None:
                self._add(self.plan.input, "me", 0.0)
            for output in self.plan.outputs:
                self._add(output, "others", 0.0)
            if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                detail = self._first_error()
                raise Failure(_("The recording could not start: {reason}").format(reason=detail or "GStreamer"),
                              detail)
        except BaseException:
            self._release_journal()
            self._pipeline.set_state(Gst.State.NULL)
            for take in self._takes.values():
                take.path.unlink(missing_ok=True)
            self._unmake_folder()
            raise
        self._began = time.monotonic()
        self._state = "recording"
        self._left = rec.seconds_left(free, count)
        with self._lock:
            self._save_locked()
        watches = [(self._read_bus, "scriba-recorder-bus"), (self._watch_devices, "scriba-recorder-watch")]
        if self.follow_call and (WINDOWS or self.call_indicator is not None):
            watches.append((self._watch_call, "scriba-recorder-call"))
        if (self.teams_names or self.call_stop_minutes is not None) and (WINDOWS or self.teams_reader is not None):
            watches.append((self._watch_teams, "scriba-recorder-teams"))
        for target, name in watches:
            threading.Thread(target=target, name=name, daemon=True).start()

    def _save_locked(self, phase="recording"):
        if self._journal_lock is None:
            return
        at = self.elapsed()
        named = self._named.snapshot(at)
        title, peer = self._teams_metadata()
        takes = [replace(take, named_spans=named, meeting_title=title, teams_people=self._teams_people, teams_peer=peer,
                         loudest=self._watch.loudest.get(key, -math.inf))
                 for key, take in self._takes.items()]
        try:
            silent_since = self._output_silence(at) if self._last_output_sound is not None else None
            cut = rec.tail_cut(at, self._call_end.ended, silent_since)
            self.journal.teams_minutes = self._teams_summary.minutes
            self.journal.save(phase, takes, at, cut=cut, call_range=self._call_end.interval(at))
            self._last_save_error = ""
        except OSError as error:
            if str(error) != self._last_save_error:
                self._last_save_error = str(error)
                complain("saving recording recovery state", error)

    def _release_journal(self):
        lease, self._journal_lock = self._journal_lock, None
        if lease is not None:
            lease.__exit__(None, None, None)

    def _unmake_folder(self) -> None:
        """The folder made for this recording, gone again when it did not
        start: only when empty (rmdir), never one that was there."""
        if self._made_folder:
            try:
                self.folder.rmdir()
            except OSError as e:
                complain("removing the folder of a recording that did not start", e)

    def _first_error(self) -> str:
        message = self._pipeline.get_bus().timed_pop_filtered(0, Gst.MessageType.ERROR)
        return _error_text(message)[1] if message is not None else ""

    def elapsed(self) -> float:
        """The time recorded: it stops when the stop is asked, not when the
        files are closed and joined (that can take a minute for an hour)."""
        if self._state == "new":
            return 0.0
        if self._ended_at is not None:
            end = self._ended_at
        else:
            end = self._paused_at if self._paused_at is not None else time.monotonic()
        return max(end - self._began - self._paused_for, 0.0)

    def pause(self) -> None:
        if self._state == "recording":
            self._pipeline.set_state(Gst.State.PAUSED)
            self._paused_at = time.monotonic()
            self._state = "paused"
            with self._lock:
                self._named.unavailable(self.elapsed())
                self._save_locked()

    def resume(self) -> None:
        if self._state == "paused":
            self._pipeline.set_state(Gst.State.PLAYING)
            self._paused_for += time.monotonic() - self._paused_at
            self._paused_at = None
            self._state = "recording"

    def switch(self, role: str, on: bool) -> None:
        """Scriba's own switch for the microphone ("me") or the computer's
        sound ("others") while recording: off, their tracks get silence
        (the file goes on, in step with the others); on, the sound again.
        Each change is kept in the tracks' `off` and said as an event."""
        if self._state not in ("recording", "paused"):
            return
        at = self.elapsed()
        with self._lock:
            if (role not in self._off) == on:
                return
            (self._off.discard if on else self._off.add)(role)
            if role == "others":
                self._last_output_sample = self._last_output_sound = None
                self._silence_from = at
                if not on:
                    self._named.unavailable(at)
            for key, take in self._takes.items():
                if take.role != role or take.ended is not None:
                    continue
                self._branches[key].volume.set_property("mute", not on)
                if on and take.off and take.off[-1][1] is None:
                    take.off = take.off[:-1] + ((take.off[-1][0], at),)
                elif not on:
                    take.off = take.off + ((at, None),)
            if on and role == "others":
                self._watch.resume(time.monotonic())   # the silence while it was off is not the call's
            self._save_locked()
        self._say(ON if on else OFF, detail=role)

    def call_state(self, state: str) -> None:
        """The call's mute as Windows' indicator says it (core/callmute.py):
        MUTED opens a stretch in each microphone's `muted`, anything else
        closes it (UNKNOWN changes nothing it has not opened). The sound is
        not touched."""
        muted = state == callmute.MUTED
        if self._state not in ("recording", "paused") or muted == self._call_muted:
            return
        at = self.elapsed()
        with self._lock:
            self._call_muted = muted
            for take in self._takes.values():
                if take.role != "me" or take.ended is not None:
                    continue
                if muted:
                    take.muted = take.muted + ((at, None),)
                elif take.muted and take.muted[-1][1] is None:
                    take.muted = take.muted[:-1] + ((take.muted[-1][0], at),)

            self._save_locked()

    def _watch_call(self) -> None:
        try:
            from . import callmute as indicator
            indicator.follow(self.call_state, self._quit, indicator.EVERY_S, self.call_indicator or indicator.Indicator)
        except Exception as e:      # the thread's border: said, and the recording goes on
            complain("following the call's mute", e)

    def stop(self) -> list[Take]:
        """Return the recorded tracks, with bounded waits for EOS and NULL."""
        with self._stop_lock:
            return self._stop_recording()

    def skip_close(self) -> None:
        """Stop waiting for audio even if an automatic stop started first."""
        self._skip_close.set()

    def _stop_recording(self):
        if self._state in ("recording", "paused"):
            self._ended_at = self._paused_at if self._paused_at is not None else time.monotonic()
            paused = self._state == "paused"
            self._state = "stopping"
            try:
                if self._pipeline is not None:
                    self._close_audio(self._pipeline, paused)
                self._finish()
            finally:
                self._release_journal()
        return self.takes()

    def _wait_for_audio(self, event, deadline):
        while not event.is_set() and not self._skip_close.is_set():
            left = deadline - time.monotonic()
            if left <= 0:
                break
            event.wait(min(left, .05))
        return event.is_set()

    def _close_audio(self, pipeline, paused):
        closing, closed = threading.Event(), threading.Event()
        deadline = time.monotonic() + STOP_WAIT
        with self._lock:
            devices = ", ".join(f"{key}: {take.device}" for key, take in self._takes.items())
        stage = f"waiting for device changes ({devices})"
        drained = closed_ok = False

        def close():
            nonlocal stage, drained, closed_ok
            with self._device_changes:
                try:
                    if paused and not self._skip_close.is_set():
                        stage = f"resuming the paused pipeline ({devices})"
                        pipeline.set_state(Gst.State.PLAYING)
                    with self._lock:
                        branches = list(self._branches.values())
                    for branch in branches:
                        if self._skip_close.is_set() or time.monotonic() >= deadline:
                            break
                        stage = f"sending EOS to {branch.key}: {branch.device.name}"
                        branch.source.send_event(Gst.Event.new_eos())
                    stage = f"waiting for EOS ({devices})"
                    drained = self._wait_for_audio(self._eos, deadline)
                except Exception as error:
                    complain(stage, error)
                finally:
                    # The bus reader must run up to EOS. After this point,
                    # only this daemon owns the potentially blocked native call.
                    self._quit.set()
                    stage = f"setting the recording pipeline to NULL ({devices})"
                    closing.set()
                    try:
                        if pipeline.set_state(Gst.State.NULL) == Gst.StateChangeReturn.FAILURE:
                            raise RuntimeError("GStreamer refused the NULL state")
                    except Exception as error:
                        complain(stage, error)
                    else:
                        closed_ok = True
                    finally:
                        closed.set()

        threading.Thread(target=close, name="scriba-recorder-close", daemon=True).start()
        reached_null = self._wait_for_audio(closing, deadline)
        reported = None
        if not self._skip_close.is_set() and (not reached_null or not drained):
            reported = f"waiting for EOS ({devices})" if reached_null else stage
            self._note_close_timeout(reported)
        if not self._wait_for_audio(closed, time.monotonic() + NULL_WAIT):
            if not self._skip_close.is_set() and stage != reported:
                self._note_close_timeout(stage)
        elif not closed_ok:
            self.journal.audio_close_timed_out = True

    def _note_close_timeout(self, stage):
        self.journal.audio_close_timed_out = True
        complain("closing recording audio", TimeoutError(stage))

    def teams_reading(self, reading) -> None:
        with self._lock:
            if self._state not in ("recording", "paused"):
                return
            self._teams_state = reading.state
            if self.teams_names:
                self._teams_people = max(self._teams_people, reading.people)
                for name in reading.remote_names:
                    self._teams_remotes.setdefault(" ".join(name.split()).casefold(), name)
                if reading.title:
                    self._meeting_title = reading.title
            at = self.elapsed()
            if self._state == "recording":
                state = "error" if reading.error else "no-meeting" if reading.state == "ended" else reading.state
                tiles = reading.people if reading.tiles is None else reading.tiles
                self._teams_summary.observe(at, state, tiles, bool(reading.active), reading.capture_source,
                                            reading.covered, reading.border_sides, reading.border_bands)
            else:
                self._teams_summary.interrupt()
            self._call_end.teams("present" if getattr(reading, "meeting", False) else reading.state, at)
            if self.teams_names and reading.state == "ready" and self._state == "recording" and "others" not in self._off:
                self._named.read(at, reading.active)
            else:
                self._named.unavailable(at)

    def _teams_metadata(self) -> tuple[str, str]:
        title = self._meeting_title
        if self._teams_people not in (1, 2) or len(self._teams_remotes) != 1:
            return title, ""
        label, peer = next(iter(self._teams_remotes.items()))
        name = peer.partition(" - ")[0].strip() or peer
        if " ".join(title.split()).casefold() in (label, " ".join(name.split()).casefold()):
            title = _("Call with {name}").format(name=name)
        return title, peer

    def _watch_teams(self) -> None:
        from scriba.app import teams
        try:
            opener = self.teams_reader or (lambda: teams.Reader(names=self.teams_names))
            teams.follow(self.teams_reading, self._quit, opener=opener)
        except Exception as error:  # worker boundary: recording continues, failure is visible
            complain("following Teams names", error)
            self.teams_reading(teams.Reading("unavailable", error=True))

    def _finish(self) -> None:
        self._quit.set()
        with self._lock:
            named = self._named.finish(self.elapsed())
            title, peer = self._teams_metadata()
            for take in self._takes.values():
                take.named_spans = named
                take.meeting_title = title
                take.teams_people = self._teams_people
                take.teams_peer = peer
            self._save_locked("finishing:closing")
        self._state = "failed" if self._error and self._state == "failed" else "stopped"

    def takes(self) -> list[Take]:
        with self._lock:
            out = []
            for key, take in self._takes.items():
                take.loudest = self._watch.loudest.get(key, -math.inf)
                out.append(take)
            return out

    def snapshot(self) -> Status:
        with self._lock:
            now = time.monotonic()
            elapsed = self.elapsed()
            # no warning about a side switched off here: its silence is wanted
            # nor about a microphone muted in the call: the user is not speaking in it
            alerts = tuple(a for a in self._watch.warnings(now)
                           if ("others" if a.kind == levels.SILENT else "me") not in self._off
                           and (a.kind == levels.SILENT or not self._call_muted)) \
                if self._state == "recording" else ()
            return Status(self._state, elapsed, tuple(replace(t) for t in self._takes.values()),
                          dict(self._now), alerts, tuple(self._events), self._left, self._error,
                          frozenset(self._off), self._call_muted, self._teams_state if self.teams_names else "",
                          self._teams_people, self._call_end.ended, self._call_end.prompt,
                          self._output_silence(elapsed) if self._last_output_sound is not None else None,
                          self._call_end.remaining(elapsed, self.call_stop_minutes), self._teams_summary.without_border)

    # -- the threads -------------------------------------------------------------------

    def _say(self, kind: str, device: str = "", detail: str = "") -> None:
        with self._lock:
            self._events.append(Event(self.elapsed(), kind, device, detail))

    def _read_bus(self) -> None:
        bus = self._pipeline.get_bus()
        try:
            while not self._quit.is_set():
                message = _pop(bus, 0.1)
                if message is None:
                    continue
                if message.type == Gst.MessageType.EOS:
                    self._eos.set()
                elif message.type == Gst.MessageType.ERROR:
                    text, detail = _error_text(message)
                    print(f"recording: {detail}", flush=True)
                    self._error = _("The recording stopped: {reason}").format(reason=text)
                    self._say(FAILED, detail=text)
                    # the other tracks closed properly: what was recorded stays readable
                    threading.Thread(target=self._stop_after_error, daemon=True).start()
                elif message.type == Gst.MessageType.WARNING:
                    warning, debug = message.parse_warning()
                    print(f"recording, warning: {warning.message} ({debug or ''})", flush=True)
                elif message.type == Gst.MessageType.ELEMENT:
                    found = _level(message)
                    if found is not None:
                        key, rms, peak = found
                        with self._lock:
                            if self._state != "recording":
                                continue
                            self._now[key] = (rms, peak)
                            self._watch.feed(key, time.monotonic(), rms, peak)
                            if self._takes[key].role == "others" and "others" not in self._off:
                                self._last_output_sample = self.elapsed()
                                if rms >= echo.PLAYING_ABOVE or peak >= echo.PLAYING_ABOVE + 10:
                                    self._last_output_sound = self._last_output_sample
        except Exception as e:      # the thread's border: said; the recording is stopped by the window
            complain("the recording's bus", e)

    def _stop_after_error(self) -> None:
        self.stop()
        self._state = "failed"

    def _watch_devices(self) -> None:
        looked = spaced = time.monotonic()
        try:
            while not self._quit.wait(0.25):
                now = time.monotonic()
                if now - spaced >= SPACE_EVERY:
                    spaced = now
                    self._look_at_disk()
                if now - looked >= WATCH_EVERY and self._state == "recording":
                    looked = now
                    with self._device_changes:
                        if self._state == "recording":
                            self._look_at_devices()
        except Exception as e:      # the thread's border: said, and the recording goes on unwatched
            complain("watching the devices", e)

    def _look_at_disk(self) -> None:
        with self._lock:
            if self._state in ("recording", "paused"):
                self._save_locked()
            tracks = sum(1 for t in self._takes.values() if t.ended is None)
        try:
            free = shutil.disk_usage(self.folder).free
        except OSError as e:
            complain("the room on the disk", e)
            return
        self._left = rec.seconds_left(free, tracks)
        state = rec.room(free, tracks)
        if state == "stop" and self._state in ("recording", "paused"):
            self._say(DISK_FULL)
            self._error = _("The disk is almost full: the recording was stopped and its files closed.")
            threading.Thread(target=self.stop, daemon=True).start()
        elif state == "low" and not any(e.kind == DISK_LOW for e in self._events):
            self._say(DISK_LOW)

    def _output_silence(self, at):
        if (self._last_output_sample is None or "others" in self._off
                or at - self._last_output_sample > WATCH_EVERY + 1):
            return None
        return self._last_output_sound if self._last_output_sound is not None else self._silence_from

    def continue_after_call(self, ended):
        with self._lock:
            if self._call_end.ended != ended or not self._call_end.prompt:
                return False
            self._call_end.continue_call()
            return True

    def _check_call(self, devices, at):
        if self.call_stop_minutes is None:
            return False
        with self._lock:
            open_mic = any(d.kind == dev.INPUT and dev.call_program(d) for d in devices)
            silent_since = self._output_silence(at)
            playing = silent_since is not None and at - silent_since < callend.SILENCE_SECONDS
            playing = playing and self._last_output_sound is not None
            self._call_end.update(at, open_mic, silent_since, playing)
            return self._call_end.due(at, self.call_stop_minutes)

    def _look_at_devices(self) -> None:
        try:
            present = self._list()
        except Failure as e:
            complain("listing the devices", e)
            return
        if self._state != "recording":
            return
        ids = {d.id for d in present}
        at = self.elapsed()
        if self._check_call(present, at):
            threading.Thread(target=self.stop, daemon=True).start()
            return
        with self._lock:
            live = [(key, self._branches[key].device, take) for key, take in self._takes.items() if take.ended is None]
        lost_input = False
        for key, device, take in live:
            if device.id not in ids:
                with self._lock:
                    take.ended = at
                    self._watch.forget(key)
                    self._now.pop(key, None)
                    self._save_locked()
                self._say(GONE, device.name)
                lost_input = lost_input or take.role == "me"
        recorded = {device.id for _key, device, take in live if take.ended is None}
        if lost_input and self.plan.automatic_input:
            choice = dev.choose_input([d for d in present if d.id not in recorded])
            if choice.device is not None:
                self._add_live(choice.device, "me", at, SWITCHED)
        elif not lost_input:
            mic = next(((key, device) for key, device, take in live if take.role == "me" and take.ended is None),
                       None)
            if mic is not None:
                self._follow_the_call(*mic, present, at)
        if self.plan.automatic_output and self.plan.outputs:
            for device in present:
                if device.kind == dev.OUTPUT and device.id not in recorded:
                    self._add_live(device, "others", at, ADDED)

    def _follow_the_call(self, key: str, recorded: dev.Device, present: list[dev.Device], at: float) -> None:
        """A call program has had another microphone open for a while
        (core/devices.py, Follow): in the automatic choice the recording
        goes on from that one, on a new track in step with the others, and
        the track of the one before is closed where it is (still readable,
        joined with the others at the end: the microphone's tracks are all
        the user's). A microphone chosen by hand stays; that is said, once
        for each microphone the call takes."""
        with self._lock:
            if self.call_stop_minutes is not None and self._call_end.blocks_follow:
                self._follow.look((), recorded.id)
                return
        found = self._follow.look(present, recorded.id)
        if found is None:
            return
        program = dev.call_program(found)
        if not self.plan.automatic_input:
            if found.id not in self._told:
                self._told.add(found.id)
                self._say(CALL_ELSEWHERE, found.name, program)
            return
        if self._add_live(found, "me", at, FOLLOWED, program) is None:
            return
        with self._lock:
            take, branch = self._takes[key], self._branches[key]
            take.ended, take.replaced = at, True
            self._watch.forget(key)
            self._now.pop(key, None)
            self._save_locked()
        branch.source.send_event(Gst.Event.new_eos())

    def _add_live(self, device: dev.Device, role: str, at: float, kind: str, detail: str = "") -> Take | None:
        try:
            take = self._add(device, role, at)
            self._branches[take.key].start()
        except Failure as e:
            complain(f"adding {device.name}", e)
            return None
        self._say(kind, device.name, detail)
        return take
