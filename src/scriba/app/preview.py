"""A disposable PCM WAV excerpt for listening to a voice.

ffmpeg reads the source formats and extracts at most fifteen seconds.
This bounds temporary space and lets GStreamer start from the beginning
of the WAV. The balance preview prepares two
PCM excerpts for live mixing instead. Originals are only read.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import wave
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scriba.app import recording
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.core import balance_excerpt, callmute, echo, levels, loudness
from scriba.core.speakers import LISTEN_MAX
from scriba.i18n import _

if TYPE_CHECKING:
    import numpy as np
    from scriba.app.jobs import Result


@dataclass(frozen=True)
class Clip:
    """A WAV and its owned temporary directory, released after the reader closes."""
    path: Path
    duration: float

    def close(self) -> None:
        try:
            shutil.rmtree(self.path.parent)
        except FileNotFoundError:
            return


def prepare(source: str | Path, start: float, end: float, cancelled: threading.Event | None = None, *,
            tracks: tuple[ffmpeg.Track, ...] = ()) -> Clip:
    check(cancelled)
    start = max(start, 0.0)
    length = min(end - start, LISTEN_MAX)
    if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty preview interval")
    folder = Path(tempfile.mkdtemp(prefix="scriba-preview-"))
    path = folder / "voice.wav"
    try:
        if tracks:
            import numpy as np

            pcm = BalanceClip(*recording.balance_preview(source, start, length, folder, cancelled, tracks=tracks))
            samples = pcm.render(0, pcm.frames, 0, 0)
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, recording.PREVIEW_RATE, 0, "NONE", "not compressed"))
                wav.writeframes(np.round(samples * 32768).astype("<i2").tobytes())
        else:
            ffmpeg.decode(source, path, cancelled=cancelled, start=start, length=length)
        check(cancelled)
        with wave.open(str(path), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        if duration <= 0:
            raise Failure(_("No audio was found at this point in the recording."), "empty preview WAV")
        return Clip(path, duration)
    except BaseException:
        shutil.rmtree(folder)
        raise


@dataclass(frozen=True)
class BalanceClip:
    """One excerpt, with the two levelled roles aligned at the mix's rate."""
    me: np.ndarray
    others: np.ndarray

    @property
    def frames(self) -> int:
        return len(self.me)

    def render(self, start: int, end: int, previous: float, balance: float) -> np.ndarray:
        """Mix one playback buffer, ramping the gains when the slider moves."""
        import numpy as np

        combined = np.zeros(end - start, dtype="<f4")
        for role, samples in (("me", self.me), ("others", self.others)):
            gain = 10 ** (loudness.balance_gain(role, balance) / 20)
            if previous != balance:
                old = 10 ** (loudness.balance_gain(role, previous) / 20)
                gain = np.linspace(old, gain, len(combined), dtype=np.float32)
            combined += samples[start:end] * gain
        peak = np.max(np.abs(combined), initial=0)
        if peak > recording.LIMIT:
            combined *= recording.LIMIT / peak
        return combined


def balanced(source: str | Path, start: float, end: float, cancelled: threading.Event | None = None, *,
             tracks: tuple[ffmpeg.Track, ...] | None = None) -> BalanceClip:
    """Read a bounded excerpt of the original, retaining the roles in memory."""
    check(cancelled)
    length = end - max(start, 0.0)
    if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty preview interval")
    with tempfile.TemporaryDirectory(prefix="scriba-preview-") as work:
        me, others = recording.balance_preview(source, max(start, 0.0), min(length, balance_excerpt.SECONDS),
                                              Path(work), cancelled, tracks=tracks)
    check(cancelled)
    if len(me) == 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty balance excerpt")
    return BalanceClip(me, others)


def for_meeting(result: Result, cancelled: threading.Event | None = None) -> BalanceClip:
    if result.source is None:
        raise Failure(_("The original recording is missing: a new listening copy cannot be made."), "no source")
    source = result.source
    media = recording.balance_media(source, cancelled)
    duration = media.duration or result.duration
    speech, strong = result.balance_activity, result.balance_echo
    if result.segments and result.recorded_voice is not None:
        speech = [balance_excerpt.Speech(line.start, line.end,
                                         "me" if line.speaker == result.recorded_voice else "others",
                                         line.level, bool(line.doubts))
                  for line in result.segments if line.text.strip()]
    if duration > balance_excerpt.SECONDS and (speech is None or strong is None):
        measured, echoes = _measure_roles(source, media.roles, cancelled)
        if speech is None:
            speech = measured
        if strong is None:
            strong = echoes
    unavailable = balance_excerpt.microphone_unavailable(
        ((*t.muted, *t.off) for t in media.roles if t.role == "me"), duration)
    start, end = balance_excerpt.choose(speech or (), duration, unavailable=unavailable, echo=strong or ())
    return balanced(source, start, end, cancelled, tracks=media.roles)


def _measure_roles(source: Path, tracks, cancelled) -> tuple[list[balance_excerpt.Speech], list[tuple[float, float]]]:
    """Read levels and correlation in bounded blocks when the session lacks them."""
    import numpy as np
    from scriba.app.engines import echo as engine

    rate = 8000
    block = round(rate * echo.WINDOW)
    speech, echoes = [], []
    with tempfile.TemporaryDirectory(prefix="scriba-levels-") as work, ExitStack() as stack:
        readers = []
        for track in tracks:
            path = ffmpeg.decode(source, Path(work) / f"{track.index}.wav", cancelled=cancelled,
                                 stream=track.index, rate=rate)
            readers.append(stack.enter_context(wave.open(str(path), "rb")))
        for frame in range(0, max(reader.getnframes() for reader in readers), block):
            check(cancelled)
            start = frame / rate
            channels = {role: np.zeros(block, dtype=np.float32) for role in balance_excerpt.ROLES}
            frames = 0
            for track, reader in zip(tracks, readers, strict=True):
                pcm = np.frombuffer(reader.readframes(block), dtype="<i2")
                frames = max(frames, len(pcm))
                active = callmute.outside([(start, start + len(pcm) / rate)], (*track.muted, *track.off))
                for left, right in active:
                    lo, hi = round((left - start) * rate), round((right - start) * rate)
                    channels[track.role][lo:hi] += pcm[lo:hi] / 32768
            mine, others = (channels[role][:frames] for role in balance_excerpt.ROLES)
            window = engine.window(mine, others, rate, start)
            if window.correlation is not None and window.correlation >= echo.STRONG_ABOVE:
                echoes.append((window.start, window.end))
            for role, samples in zip(balance_excerpt.ROLES, (mine, others), strict=True):
                for part in range(0, frames, rate):
                    piece = samples[part:part + rate]
                    level = engine.level(piece)
                    if level >= levels.SPEECH_FROM:
                        speech.append(balance_excerpt.Speech(start + part / rate, start + (part + len(piece)) / rate,
                                                            role, level))
    return speech, echoes
