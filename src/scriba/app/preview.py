"""A disposable voice excerpt: GTK only receives PCM WAV, never tagged media.

Some tagged MP3s abort GTK's GStreamer backend in native code, outside
Python's error handling. ffmpeg already reads the source formats safely;
extracting at most fifteen seconds (twenty for the balance's preview) also bounds temporary space and avoids
seeking a whole recording in the player. Originals are only read.
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from scriba.app.engines import ffmpeg
from scriba.app.task import Failure, check
from scriba.core.speakers import LISTEN_MAX
from scriba.i18n import _


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


def balanced(source: str | Path, start: float, end: float, balance: float,
             cancelled: threading.Event | None = None) -> Clip:
    """The balance's preview: that stretch of the listening copy with this
    balance (recording.balance_preview), in a temporary folder of its own
    that Clip.close removes. The user's files are only read."""
    from scriba.app import recording

    check(cancelled)
    length = end - max(start, 0.0)
    if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
        raise Failure(_("No audio was found at this point in the recording."), "empty preview interval")
    folder = Path(tempfile.mkdtemp(prefix="scriba-preview-"))
    try:
        path = recording.balance_preview(source, max(start, 0.0), length, balance, folder, cancelled)
        check(cancelled)
        with wave.open(str(path), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        if duration <= 0:
            raise Failure(_("No audio was found at this point in the recording."), "empty preview WAV")
        return Clip(path, duration)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
