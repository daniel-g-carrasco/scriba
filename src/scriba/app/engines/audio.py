"""The one audio format the engines are given: WAV, 16-bit PCM, mono,
16 kHz. ffmpeg makes it from whatever the user has (ffmpeg.decode); the
speech filter, the voices and whisper all read it, so the samples are the
same for every step and a time means the same everywhere.

numpy is imported inside the functions, as every heavy library of the
app, so that the command line starts at once and `--help` needs none.
"""

from __future__ import annotations

import os
import wave

from scriba.app.task import Failure
from scriba.i18n import _

RATE = 16000


def read(path: str | os.PathLike):
    """The samples as int16 (numpy). Failure when the file is not the
    format above: the engines never guess at a rate or a channel."""
    import numpy as np
    try:
        with wave.open(os.fspath(path), "rb") as w:
            shape = (w.getsampwidth(), w.getnchannels(), w.getframerate())
            if shape != (2, 1, RATE):
                raise Failure(_("The audio prepared for the engines is not 16 kHz mono."), f"{path}: {shape}")
            data = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error) as e:
        raise Failure(_("The audio prepared for the engines cannot be read."), f"{path}: {e}") from e
    return np.frombuffer(data, dtype="<i2")


def write(path: str | os.PathLike, samples) -> None:
    """int16 samples as a WAV file of the format above."""
    import numpy as np
    with wave.open(os.fspath(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.ascontiguousarray(samples, dtype="<i2").tobytes())


def seconds(samples) -> float:
    return len(samples) / RATE


LEVEL_FRAME = 512           # 32 ms, the speech filter's frame
LEVEL_PERCENTILE = 90


def level(samples, start: float, end: float) -> float | None:
    """How loud a line is, in dBFS: the 90th percentile of the level of its
    32 ms frames, so that the pauses inside the line (whisper's lines span
    the silences the filter took out) do not pull it down. None for a line
    shorter than a frame."""
    import numpy as np
    piece = samples[max(int(start * RATE), 0): max(int(end * RATE), 0)].astype(np.float64) / 32768.0
    count = piece.size // LEVEL_FRAME
    if count == 0:
        return None
    frames = piece[: count * LEVEL_FRAME].reshape(count, LEVEL_FRAME)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    return float(np.percentile(20 * np.log10(np.maximum(rms, 1e-9)), LEVEL_PERCENTILE))
