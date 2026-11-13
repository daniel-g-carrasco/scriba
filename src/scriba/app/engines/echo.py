"""The computer's sound heard by the microphone: measured, and taken out.

Measured with numpy (core/echo.py says what the numbers mean): the
correlation of each WINDOW of the microphone with the computer's sound,
through the FFT, at the delay where it is highest.

Taken out by WebRTC's echo canceller, through GStreamer (webrtcdsp and
webrtcechoprobe, gst-plugins-bad, on the webrtc-audio-processing library):
the computer's sound is the reference the canceller subtracts, once it has
learnt the way from the loudspeakers to the microphone. Echo cancelling
only: no noise suppression, no gain, no filter, so that where the computer
is silent the microphone comes out as it went in (0.05 dB of difference
measured on 13/11/2026; 42 dB of echo taken out where the computer plays).

Off line it works only with the two signals held together. Two files read
by two sources run apart (the canceller saw the reference minutes ahead of
the microphone, or behind) and took out 3 dB; one file of two channels
(the microphone left, the computer's sound right) read by one source and
split by deinterleave, with queues of two buffers, keeps them in step as
a live call does, and gives the same 42 dB, at 0.8 s of work per minute of
audio at 16 kHz. GStreamer streams the file: memory does not grow with
its length. The first seconds go through twice (WARM), so that the start
is cleaned as well as the rest.

Gst is imported inside the functions (tests/test_strati.py,
WINDOWLESS_FILES): the measure needs none of it, and nothing here shows a
window.
"""

from __future__ import annotations

import math
import os
import threading
import wave
from pathlib import Path

from scriba.app.task import Failure, Progress, check, tell
from scriba.core import echo
from scriba.i18n import _

# what the cleaning pipeline is made of
ELEMENTS = ("filesrc", "wavparse", "concat", "audioconvert", "capsfilter", "deinterleave", "queue",
            "webrtcechoprobe", "webrtcdsp", "fakesink", "wavenc", "filesink")
# the canceller's own rates (webrtcdsp's pads)
RATES = (8000, 16000, 32000, 48000)
# samples the cleaned track comes out late, by rate: the canceller works in
# frames of 10 ms and splits 48 kHz into bands. Measured on 13/11/2026
# (webrtc-audio-processing 1.3, GStreamer 1.28.8) with noise and a silent
# computer: 128 at 16 kHz (8.0 ms), 430 at 48 kHz (9.0 ms), the same on the
# real meeting. splice() takes them off, so that the cleaned and the raw
# track change over in step; tests/test_echo.py measures them again.
LATENCY = {16000: 128, 48000: 430}
# The canceller learns the way from the loudspeakers to the microphone in
# the first seconds: on the real meeting it took out 19 dB in the first
# second, 3 dB in the second, 33 to 54 dB from the third on. So the first
# WARM seconds go through it twice: once to learn, then for real, and the
# first pass is thrown away (the way is the same: the same loudspeakers,
# the same room).
WARM = 5.0
BLOCK = 10.0                # seconds read or written at a time, when a file goes through Python
PIPELINE = (
    "concat name=joined ! audioconvert ! audio/x-raw,format=S16LE,channels=2,rate={rate} "
    "! deinterleave name=split "
    "filesrc name=lead ! wavparse ! joined. "
    "filesrc name=source ! wavparse ! joined. "
    "split.src_1 ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 ! audioconvert "
    "! audio/x-raw,format=S16LE,channels=1,rate={rate} ! webrtcechoprobe name=probe ! fakesink sync=false "
    "split.src_0 ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 ! audioconvert "
    "! audio/x-raw,format=S16LE,channels=1,rate={rate} "
    "! webrtcdsp name=canceller probe=probe echo-cancel=true noise-suppression=false gain-control=false "
    "high-pass-filter=false voice-detection=false "
    "! audioconvert ! audio/x-raw,format=S16LE,channels=1,rate={rate} ! wavenc ! filesink name=dest sync=false"
)

_init_lock = threading.Lock()


# -- the measure ------------------------------------------------------------------------

def level(samples) -> float:
    """dBFS, RMS of int16 samples (or floats in -1..1)."""
    import numpy as np
    given = np.asarray(samples)
    x = given.astype(np.float64) / 32768.0 if given.dtype.kind in "iu" else given.astype(np.float64)
    if not len(x):
        return -math.inf
    rms = math.sqrt(float(np.mean(x * x)))
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def correlate(mic, output, rate: int, reach: float = echo.REACH) -> tuple[float, float]:
    """The highest normalised correlation of the two (as long as each
    other), within `reach` seconds of delay either way, and that delay
    (positive: the microphone later than the computer's sound)."""
    import numpy as np
    a = np.asarray(mic, dtype=np.float64)
    b = np.asarray(output, dtype=np.float64)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if n == 0 or norm == 0:
        return 0.0, 0.0
    lag = min(int(reach * rate), n - 1)
    size = 1 << (n + lag).bit_length()     # room enough for the delays looked at, no more
    cc = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    around = np.concatenate([cc[-lag:], cc[:lag + 1]]) if lag else cc[:1]
    k = int(np.argmax(np.abs(around)))
    return float(abs(around[k]) / norm), (k - lag) / rate


def window(mic, output, rate: int, start: float) -> echo.Window:
    """One window measured: its levels, and the correlation where it says
    something (core/echo.py, verdict)."""
    mic_db, out_db = level(mic), level(output)
    end = start + len(mic) / rate
    if out_db < echo.PLAYING_ABOVE or mic_db < echo.MIC_ABOVE:
        return echo.Window(start, end, None, None, mic_db, out_db)
    value, delay = correlate(mic, output, rate)
    return echo.Window(start, end, value, delay, mic_db, out_db)


def read_pair(path: str | os.PathLike, block: float = BLOCK):
    """(start in seconds, microphone, computer) blocks of a two-channel
    WAV, int16, read a block at a time; the rate first."""
    import numpy as np
    with wave.open(os.fspath(path), "rb") as w:
        if w.getnchannels() != 2 or w.getsampwidth() != 2:
            raise Failure(_("The audio prepared for the echo check is not as expected."),
                          f"{path}: {w.getnchannels()} channels, {w.getsampwidth()} bytes")
        rate = w.getframerate()
        yield rate
        frames = int(block * rate)
        start = 0
        while True:
            data = w.readframes(frames)
            if not data:
                return
            both = np.frombuffer(data, dtype="<i2").reshape(-1, 2)
            yield start / rate, both[:, 0], both[:, 1]
            start += len(both)


def measure(path: str | os.PathLike, progress: Progress | None = None,
            cancelled: threading.Event | None = None) -> tuple[list[echo.Window], float]:
    """Every WINDOW of a two-channel WAV (write_pair) measured, and the
    file's length in seconds. A tail shorter than half a window is left
    out of the measure (core/echo.py, stretches, gives it the last state)."""
    blocks = read_pair(path, echo.WINDOW)
    rate = next(blocks)
    with wave.open(os.fspath(path), "rb") as w:
        duration = w.getnframes() / rate
    out = []
    for start, mic, output in blocks:
        check(cancelled)
        if len(mic) >= rate * echo.WINDOW / 2:
            out.append(window(mic, output, rate, start))
        tell(progress, min(start + echo.WINDOW, duration), duration)
    return out, duration


def write_pair(path: str | os.PathLike, mic, outputs, rate: int) -> Path:
    """The microphone (left) and the computer's sound (right: the outputs
    summed, as loud as they were) in one WAV of two channels, a block at a
    time, as long as the microphone."""
    import numpy as np
    n = len(mic)
    step = int(BLOCK * rate)
    with wave.open(os.fspath(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(0, n, step):
            j = min(i + step, n)
            both = np.zeros((j - i, 2), dtype=np.int32)
            both[:, 0] = mic[i:j]
            for track in outputs:
                piece = track[i:j]
                both[:len(piece), 1] += piece
            w.writeframes(np.clip(both, -32768, 32767).astype("<i2").tobytes())
    return Path(path)


def pair_files(mic: str | os.PathLike, outputs: list[str | os.PathLike], dest: str | os.PathLike,
               cancelled: threading.Event | None = None) -> Path:
    """write_pair from mono WAV files of one rate (ffmpeg.decode's), a
    block at a time: a recording of an hour is never all in memory."""
    import numpy as np
    readers = []
    try:
        for path in (mic, *outputs):
            readers.append(wave.open(os.fspath(path), "rb"))
        rate = readers[0].getframerate()
        if any((r.getnchannels(), r.getsampwidth(), r.getframerate()) != (1, 2, rate) for r in readers):
            raise Failure(_("The audio prepared for the echo check is not as expected."), f"{mic}, {outputs}")
        step = int(BLOCK * rate)
        with wave.open(os.fspath(dest), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            while True:
                check(cancelled)
                left = np.frombuffer(readers[0].readframes(step), dtype="<i2")
                if not len(left):
                    break
                both = np.zeros((len(left), 2), dtype=np.int32)
                both[:, 0] = left
                for reader in readers[1:]:
                    piece = np.frombuffer(reader.readframes(len(left)), dtype="<i2")
                    both[:len(piece), 1] += piece
                w.writeframes(np.clip(both, -32768, 32767).astype("<i2").tobytes())
    finally:
        for reader in readers:
            reader.close()
    return Path(dest)


def splice(pair: str | os.PathLike, cleaned: str | os.PathLike, parts: list[echo.Stretch],
           dest: str | os.PathLike, cancelled: threading.Event | None = None) -> Path:
    """The microphone's track as it will be heard: the cleaned one in the
    stretches with echo, the raw one (the left channel of `pair`) in the
    others, the change a short ramp (core/echo.py, blend_points). As long
    as the raw track; a mono WAV at its rate, written a block at a time.
    The cleaned track's first pass over the start (WARM) and the
    canceller's own delay (LATENCY) are taken off it (ahead)."""
    import numpy as np
    xs, ys = echo.blend_points(parts)
    blocks = read_pair(pair)
    rate = next(blocks)
    with wave.open(os.fspath(pair), "rb") as w:
        frames = w.getnframes()
    with wave.open(os.fspath(cleaned), "rb") as c, wave.open(os.fspath(dest), "wb") as out:
        if (c.getnchannels(), c.getsampwidth(), c.getframerate()) != (1, 2, rate):
            raise Failure(_("The microphone without echo is not as expected."),
                          f"{cleaned}: {c.getnchannels()}, {c.getsampwidth()}, {c.getframerate()}")
        c.readframes(ahead(frames, rate))
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        for start, raw, _output in blocks:
            check(cancelled)
            clean = np.frombuffer(c.readframes(len(raw)), dtype="<i2")
            if len(clean) < len(raw):           # the canceller's last frame may be short: the raw goes on
                clean = np.concatenate([clean, raw[len(clean):]])
            t = start + np.arange(len(raw)) / rate
            weight = np.interp(t, xs, ys)
            mixed = weight * clean.astype(np.float64) + (1 - weight) * raw.astype(np.float64)
            out.writeframes(np.clip(np.round(mixed), -32768, 32767).astype("<i2").tobytes())
    return Path(dest)


class Live:
    """The echo looked for while recording (app/recorder.py feeds it the
    tracks' samples as they come): each time WINDOW seconds more of every
    track are there, the last WINDOW is measured as window() does after the
    recording, at 16 kHz (three samples of 48 kHz averaged: enough for a
    correlation), and core/echo.py's Follow says whether the microphone
    hears the computer now. Nothing is cleaned while recording: the raw
    tracks are the recording, and the cleaning comes after, for this
    recording as for the ones made before. Thread-safe: the streaming
    threads feed, the recorder's watch looks."""

    def __init__(self, rate: int) -> None:
        self.rate = rate
        self.step = rate // 16000 if rate % 16000 == 0 else 1
        self._lock = threading.Lock()
        self._chunks: dict[str, list] = {}         # key -> [(first sample, float32 samples)], the last seconds only
        self._measured = 0                          # the sample the next window starts after
        self.follow = echo.Follow(echo.LIVE_CLEAN_AFTER)    # the warning: steady, see LIVE_CLEAN_AFTER
        self.last: echo.Window | None = None

    def feed(self, key: str, first: int, samples) -> None:
        keep = int(echo.WINDOW * 1.5 * self.rate)
        with self._lock:
            chunks = self._chunks.setdefault(key, [])
            chunks.append((first, samples))
            end = first + len(samples)
            while chunks and chunks[0][0] + len(chunks[0][1]) < end - keep:
                chunks.pop(0)

    def forget(self, key: str) -> None:
        with self._lock:
            self._chunks.pop(key, None)

    def _end(self, key: str) -> int:
        chunks = self._chunks.get(key)
        return chunks[-1][0] + len(chunks[-1][1]) if chunks else 0

    def _piece(self, key: str, start: int, stop: int):
        import numpy as np
        out = np.zeros(stop - start, dtype=np.float32)
        for first, samples in self._chunks.get(key, ()):
            a, b = max(first, start), min(first + len(samples), stop)
            if a < b:
                out[a - start:b - start] = samples[a - first:b - first]
        return out

    def look(self, mic: str, outputs: list[str]) -> bool:
        """Whether the microphone `mic` hears the outputs now, measuring a
        new window when every track has WINDOW seconds more."""
        import numpy as np
        size = int(echo.WINDOW * self.rate)
        with self._lock:
            if not outputs or mic not in self._chunks:
                return self.follow.echo
            end = min(self._end(key) for key in (mic, *outputs))
            if end - max(self._measured, 0) < size:
                return self.follow.echo
            start = end - size
            heard = self._piece(mic, start, end)
            played = sum(self._piece(key, start, end) for key in outputs)
            self._measured = end
        if self.step > 1:
            usable = len(heard) // self.step * self.step
            heard = heard[:usable].reshape(-1, self.step).mean(axis=1)
            played = np.asarray(played)[:usable].reshape(-1, self.step).mean(axis=1)
        self.last = window(heard, played, self.rate // self.step, start / self.rate)
        return self.follow.feed(self.last.verdict)


# -- the canceller -------------------------------------------------------------------------

def _gst():
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    with _init_lock:
        if not Gst.is_initialized():
            Gst.init(None)
    return Gst


def missing() -> list[str]:
    """The pieces of GStreamer the canceller needs and this computer lacks
    (["GStreamer"] when there is no GStreamer at all)."""
    try:
        Gst = _gst()
    except (ImportError, ValueError):
        return ["GStreamer"]
    return [name for name in ELEMENTS if Gst.ElementFactory.find(name) is None]


def lead_frames(frames: int, rate: int) -> int:
    """The frames of the start that go through the canceller twice (WARM)."""
    return min(int(WARM * rate), frames)


def ahead(frames: int, rate: int) -> int:
    """The frames of cancel()'s output before the cleaned track of a pair
    of `frames` frames: the first pass over the start, and the delay."""
    return lead_frames(frames, rate) + LATENCY.get(rate, 0)


def cancel(pair: str | os.PathLike, dest: str | os.PathLike, progress: Progress | None = None,
           cancelled: threading.Event | None = None) -> Path:
    """The microphone of a two-channel WAV (write_pair) with the computer's
    sound taken out, as a mono WAV at the same rate in `dest`, preceded by
    `ahead()` frames to be skipped (splice does). Failure when the canceller
    is missing or GStreamer stops on an error; Cancelled when asked (the
    pipeline stopped at once, `dest` removed)."""
    lacking = missing()
    if lacking:
        raise Failure(_("GStreamer's echo canceller is missing ({names}).").format(names=", ".join(lacking)),
                      ", ".join(lacking))
    Gst = _gst()
    with wave.open(os.fspath(pair), "rb") as w:
        rate, frames = w.getframerate(), w.getnframes()
        if rate not in RATES:
            raise Failure(_("The echo canceller cannot work at {rate} Hz.").format(rate=rate), str(rate))
        lead = Path(dest).with_name(Path(dest).stem + "-lead.wav")
        with wave.open(os.fspath(lead), "wb") as first:
            first.setparams(w.getparams())
            first.writeframes(w.readframes(lead_frames(frames, rate)))
    duration = (frames + lead_frames(frames, rate)) / rate
    try:
        pipeline = Gst.parse_launch(PIPELINE.format(rate=rate))
    except RuntimeError as e:          # GLib.Error: an element that would not be made
        lead.unlink(missing_ok=True)
        raise Failure(_("The echo canceller could not start."), str(e)) from e
    pipeline.get_by_name("lead").set_property("location", os.fspath(lead))
    pipeline.get_by_name("source").set_property("location", os.fspath(pair))
    pipeline.get_by_name("dest").set_property("location", os.fspath(dest))
    bus = pipeline.get_bus()
    done = False
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise Failure(_("The echo canceller could not start."), _first_error(Gst, bus))
        while True:
            check(cancelled)
            message = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if message is None:
                ok, position = pipeline.query_position(Gst.Format.TIME)
                if ok and duration > 0:
                    tell(progress, min(position / Gst.SECOND, duration), duration)
                continue
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                raise Failure(_("The echo canceller stopped: {reason}").format(reason=error.message),
                              f"{message.src.get_name() if message.src else '?'}: {error.message} ({debug or ''})")
            done = True
            break
    finally:
        pipeline.set_state(Gst.State.NULL)
        lead.unlink(missing_ok=True)
        if not done:
            Path(dest).unlink(missing_ok=True)
    tell(progress, duration, duration)
    return Path(dest)


def _first_error(Gst, bus) -> str:
    message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR)
    if message is None:
        return "GStreamer"
    error, debug = message.parse_error()
    return f"{error.message} ({debug or ''})"
