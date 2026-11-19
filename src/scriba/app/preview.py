"""A disposable voice excerpt: GTK only receives PCM WAV, never tagged media.

Some tagged MP3s abort GTK's GStreamer backend in native code, outside
Python's error handling. ffmpeg already reads the source formats safely;
extracting at most fifteen seconds also bounds temporary space and avoids
seeking a whole recording in the player. The balance preview prepares two
PCM excerpts for live mixing instead. Originals are only read.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scriba.app import recording
from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.core import loudness
from scriba.core.speakers import LISTEN_MAX, PREVIEW_SECONDS
from scriba.i18n import _

if TYPE_CHECKING:
    import numpy as np


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


def prepare(source: str | Path, start: float, end: float, cancelled: threading.Event | None = None) -> Clip:
    check(cancelled)
    start = max(start, 0.0)
    length = min(end - start, LISTEN_MAX)
    if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty preview interval")
    folder = Path(tempfile.mkdtemp(prefix="scriba-preview-"))
    path = folder / "voice.wav"
    try:
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


def balanced(source: str | Path, start: float, end: float,
             cancelled: threading.Event | None = None) -> BalanceClip:
    """Read a bounded excerpt of the original, retaining the roles in memory."""
    check(cancelled)
    length = end - max(start, 0.0)
    if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty preview interval")
    with tempfile.TemporaryDirectory(prefix="scriba-preview-") as work:
        me, others = recording.balance_preview(source, max(start, 0.0), min(length, PREVIEW_SECONDS),
                                              Path(work), cancelled)
    check(cancelled)
    if len(me) == 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty balance excerpt")
    return BalanceClip(me, others)
