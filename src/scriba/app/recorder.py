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
           ! audiorate ! level ! audioconvert ! S24 ! flacenc ! filesink

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
from scriba.core import devices as dev, levels, recording as rec  # noqa: E402
from scriba.data import files  # noqa: E402
from scriba.i18n import _  # noqa: E402

from .log import complain  # noqa: E402
from .recording import (ADDED, DISK_FULL, DISK_LOW, FAILED, GONE, SWITCHED, Event, Meters, Status,  # noqa: E402
                        Take, file_words)
from .task import Failure  # noqa: E402

SOURCE = "wasapi2src" if WINDOWS else "pulsesrc"
NEEDED = (SOURCE, "queue", "audioconvert", "audioresample", "capsfilter", "audiorate", "level", "flacenc",
          "filesink", "fakesink")
INTERVAL = 100 * Gst.MSECOND        # the meters' pace: ten levels a second
WATCH_EVERY = 2.0                   # seconds between two looks at the devices
SPACE_EVERY = 5.0                   # and at the disk
PEAKS_FOR = 3.0                     # seconds of peaks an output's "sound now" looks at
STOP_WAIT = 10.0                    # seconds the files have to close after the stop, before they are cut
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
    return _make("pulsesrc", device=device.id)


class _Branch:
    """One device's way through the pipeline, to a file or to nothing (a
    meter only)."""

    def __init__(self, pipeline: Gst.Pipeline, key: str, device: dev.Device, path: Path | None) -> None:
        self.key, self.device, self.path = key, device, path
        self.source = _source(device)
        chain = [self.source, _make("queue"), _make("audioconvert"), _make("audioresample"),
                 _make("capsfilter", caps=Gst.Caps.from_string(TRACK_CAPS)), _make("audiorate"),
                 _make("level", name=f"level:{key}", interval=INTERVAL, post_messages=True)]
        if path is not None:
            chain += [_make("audioconvert"), _make("capsfilter", caps=Gst.Caps.from_string(FILE_CAPS)),
                      _make("flacenc"), _make("filesink", location=os.fspath(path), buffer_mode="unbuffered", sync=False,
                                              async_=False)]
        else:
            chain.append(_make("fakesink", sync=False, async_=False))
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
        self.folder.mkdir(parents=True, exist_ok=True)
        together = f"_{_('together')}.m4a"
        self.stem = files.free_stem(self.folder, stem, (".mka", together, f"_{self._words['me']}.flac",
                                                        f"_{self._words['others']} 1.flac"))
        self._lock = threading.Lock()
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
        self._eos = threading.Event()
        self._quit = threading.Event()
        self._threads: list[threading.Thread] = []
        self._left = math.inf
        self._counts = {"me": 0, "others": 0}

    # -- the tracks ----------------------------------------------------------------

    def _path_for(self, role: str, device: dev.Device) -> Path:
        """A new file for the track, reserved at once (created empty, never
        over one that is there): "<stem>_microfono.flac", "<stem>_uscita 1
        Cuffie (Free BYRD).flac"."""
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
        with self._lock:
            self._branches[key] = branch
            self._takes[key] = take
            self._watch.track(key, role == "me")
        return take

    # -- start, pause, stop ----------------------------------------------------------

    def start(self) -> None:
        """Failure when the disk has no room for ten minutes, or the pipeline
        does not start (nothing is left behind then)."""
        init()
        count = (1 if self.plan.input else 0) + len(self.plan.outputs)
        free = shutil.disk_usage(self.folder).free
        if not rec.may_start(free, count):
            raise Failure(_("There is not enough room on the disk for ten minutes of recording."),
                          f"{free} bytes free in {self.folder}")
        self._pipeline = Gst.Pipeline.new("scriba-recorder")
        # Every device follows a clock that survives another source stopping.
        # A device's audio clock also starts late by its own capture latency:
        # using it as the master shifts that track relative to the others.
        self._pipeline.use_clock(Gst.SystemClock.obtain())
        try:
            if self.plan.input is not None:
                self._add(self.plan.input, "me", 0.0)
            for output in self.plan.outputs:
                self._add(output, "others", 0.0)
            if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                detail = self._first_error()
                raise Failure(_("The recording could not start: {reason}").format(reason=detail or "GStreamer"),
                              detail)
        except BaseException:
            self._pipeline.set_state(Gst.State.NULL)
            for take in self._takes.values():
                take.path.unlink(missing_ok=True)
            raise
        self._began = time.monotonic()
        self._state = "recording"
        self._left = rec.seconds_left(free, count)
        for target, name in ((self._read_bus, "scriba-recorder-bus"), (self._watch_devices, "scriba-recorder-watch")):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def _first_error(self) -> str:
        message = self._pipeline.get_bus().timed_pop_filtered(0, Gst.MessageType.ERROR)
        return _error_text(message)[1] if message is not None else ""

    def elapsed(self) -> float:
        if self._state == "new":
            return 0.0
        end = self._paused_at if self._paused_at is not None else time.monotonic()
        return max(end - self._began - self._paused_for, 0.0)

    def pause(self) -> None:
        if self._state == "recording":
            self._pipeline.set_state(Gst.State.PAUSED)
            self._paused_at = time.monotonic()
            self._state = "paused"

    def resume(self) -> None:
        if self._state == "paused":
            self._pipeline.set_state(Gst.State.PLAYING)
            self._paused_for += time.monotonic() - self._paused_at
            self._paused_at = None
            self._state = "recording"

    def stop(self) -> list[Take]:
        """The files closed properly (each source told it is the end, and the
        end waited for, at most STOP_WAIT seconds: after that they are cut
        where they are, still readable); the tracks, with their loudest
        peaks."""
        if self._state in ("recording", "paused", "stopping"):
            self.resume()               # a paused source passes on nothing, the end neither
            self._state = "stopping"
            with self._lock:
                branches = list(self._branches.values())
            for branch in branches:
                branch.source.send_event(Gst.Event.new_eos())
            if not self._eos.wait(STOP_WAIT):
                self._error = self._error or _("The files took too long to close: they were cut where they were.")
            self._finish()
        return self.takes()

    def _finish(self) -> None:
        self._quit.set()
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
        current = threading.current_thread()
        for thread in self._threads:
            if thread is not current:
                thread.join(5)
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
            alerts = tuple(self._watch.warnings(now)) if self._state == "recording" else ()
            return Status(self._state, self.elapsed(), tuple(replace(t) for t in self._takes.values()),
                          dict(self._now), alerts, tuple(self._events), self._left, self._error)

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
                    if found is not None and self._state == "recording":
                        key, rms, peak = found
                        with self._lock:
                            self._now[key] = (rms, peak)
                            self._watch.feed(key, time.monotonic(), rms, peak)
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
                    self._look_at_devices()
        except Exception as e:      # the thread's border: said, and the recording goes on unwatched
            complain("watching the devices", e)

    def _look_at_disk(self) -> None:
        with self._lock:
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

    def _look_at_devices(self) -> None:
        try:
            present = self._list()
        except Failure as e:
            complain("listing the devices", e)
            return
        ids = {d.id for d in present}
        at = self.elapsed()
        with self._lock:
            live = [(key, self._branches[key].device, take) for key, take in self._takes.items() if take.ended is None]
        lost_input = False
        for key, device, take in live:
            if device.id not in ids:
                take.ended = at
                with self._lock:
                    self._watch.forget(key)
                    self._now.pop(key, None)
                self._say(GONE, device.name)
                lost_input = lost_input or take.role == "me"
        recorded = {device.id for _key, device, take in live if take.ended is None}
        if lost_input and self.plan.automatic_input:
            choice = dev.choose_input([d for d in present if d.id not in recorded])
            if choice.device is not None:
                self._add_live(choice.device, "me", at, SWITCHED)
        if self.plan.automatic_output and self.plan.outputs:
            for device in present:
                if device.kind == dev.OUTPUT and device.id not in recorded:
                    self._add_live(device, "others", at, ADDED)

    def _add_live(self, device: dev.Device, role: str, at: float, kind: str) -> None:
        try:
            take = self._add(device, role, at)
            self._branches[take.key].start()
        except Failure as e:
            complain(f"adding {device.name}", e)
            return
        self._say(kind, device.name)
