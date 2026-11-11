"""Where somebody speaks: Silero's speech filter, in ONNX.

One filter serves the two engines that need one:
the voices (diarize's own settings, below) and the transcription, which
gets only the stretches with speech. whisper-cli has a filter of its own,
but it runs on four fixed threads and took 52 to 112 s on the 10-minute
test clip, against 2.6 s for the same filter alone on one thread; this
implementation runs it as a separate step.

The model is silero_vad_16k_sequence.onnx, which gives the probabilities
of 512 frames of 32 ms in one call instead of one call a frame: the same
numbers as Silero's own JIT model to 7.6e-6, and the same 154 stretches on
the clip. The state machine that turns the probabilities into stretches is
Silero's get_speech_timestamps_from_probs (silero-vad 6.2.3, MIT,
Copyright (c) 2020-present Silero Team), brought to Python as diarize uses
it, without the splitting of stretches longer than a maximum (no maximum is
set, so that branch never runs); with faster-whisper's settings it is the
same machine faster-whisper runs.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from scriba.app.task import Progress, check, tell

from . import audio

FRAME = 512                 # samples a probability is about (32 ms)
CONTEXT = 64                # samples of the frame before that the model sees too
BLOCK = 512                 # frames per call of the sequence model


@dataclass(frozen=True)
class Settings:
    threshold: float        # a frame at or above this starts speech
    min_speech_ms: int      # stretches this short or shorter are dropped
    min_silence_ms: int     # a silence shorter than this does not end a stretch
    pad_ms: int             # kept on both sides of a stretch
    decimals: int | None    # the times rounded to this many decimals; None: exact


# diarize 0.1.2's own (diarize/vad.py): the voices' engine was measured with
# these, and with them the port gives its 58 segments on the test clip
FOR_VOICES = Settings(threshold=0.45, min_speech_ms=200, min_silence_ms=50, pad_ms=20, decimals=1)
# faster-whisper 1.2.1's VadOptions, which trascrivi.py used (vad_filter=True):
# a silence must last 2 s to cut, and 0.4 s are kept on each side, so the
# filter does not eat the start and the end of words (whisper-cli's own
# filter, at 0.25 s and 0.03 s, cut about 80 words of the clip)
FOR_SPEECH = Settings(threshold=0.5, min_speech_ms=0, min_silence_ms=2000, pad_ms=400, decimals=None)


def probabilities(samples, session, progress: Progress | None = None,
                  cancelled: threading.Event | None = None):
    """The probability of speech of every frame of 32 ms (numpy float32).
    The samples are converted to float a block at a time, so an hour of
    audio never sits in memory twice."""
    import numpy as np
    n_frames = (len(samples) + FRAME - 1) // FRAME
    h = np.zeros((1, 1, 128), np.float32)
    c = np.zeros((1, 1, 128), np.float32)
    context = np.zeros(CONTEXT, np.float32)
    out = []
    for first in range(0, n_frames, BLOCK):
        check(cancelled)
        count = min(BLOCK, n_frames - first)
        piece = samples[first * FRAME: min(len(samples), (first + count) * FRAME)].astype(np.float32) / 32768.0
        frames = np.zeros((count, FRAME), np.float32)
        frames.reshape(-1)[:piece.size] = piece
        contexts = np.empty((count, CONTEXT), np.float32)
        contexts[0] = context
        if count > 1:
            contexts[1:] = frames[:-1, -CONTEXT:]
        context = frames[-1, -CONTEXT:].copy()
        p, h, c = session.run(["speech_probs", "hn", "cn"],
                              {"input": np.concatenate((contexts, frames), axis=1), "h": h, "c": c})
        out.append(np.asarray(p, np.float32).reshape(-1))
        tell(progress, first + count, n_frames)
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def stretches(probs, n_samples: int, settings: Settings, rate: int = audio.RATE) -> list[tuple[int, int]]:
    """The stretches of speech, (first sample, sample after the last)."""
    min_speech = rate * settings.min_speech_ms / 1000
    pad = rate * settings.pad_ms / 1000
    min_silence = rate * settings.min_silence_ms / 1000
    low = max(settings.threshold - 0.15, 0.01)          # below this a frame is silence for sure
    triggered = False
    speeches: list[dict] = []
    current: dict = {}
    silence_from = 0
    for i, p in enumerate(probs):
        p = float(p)
        sample = FRAME * i
        if p >= settings.threshold and silence_from:
            silence_from = 0
        if p >= settings.threshold and not triggered:
            triggered = True
            current["start"] = sample
            continue
        if p < low and triggered:
            if not silence_from:
                silence_from = sample
            if sample - silence_from < min_silence:
                continue
            current["end"] = silence_from
            if current["end"] - current["start"] > min_speech:
                speeches.append(current)
            current = {}
            silence_from = 0
            triggered = False
    if current and n_samples - current["start"] > min_speech:
        current["end"] = n_samples
        speeches.append(current)
    for i, speech in enumerate(speeches):
        if i == 0:
            speech["start"] = int(max(0, speech["start"] - pad))
        if i != len(speeches) - 1:
            gap = speeches[i + 1]["start"] - speech["end"]
            if gap < 2 * pad:           # two stretches nearer than both pads: they meet halfway
                speech["end"] += int(gap // 2)
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - gap // 2))
            else:
                speech["end"] = int(min(n_samples, speech["end"] + pad))
                speeches[i + 1]["start"] = int(max(0, speeches[i + 1]["start"] - pad))
        else:
            speech["end"] = int(min(n_samples, speech["end"] + pad))
    return [(int(s["start"]), int(s["end"])) for s in speeches]


def seconds(spans: list[tuple[int, int]], n_samples: int, settings: Settings,
            rate: int = audio.RATE) -> list[tuple[float, float]]:
    """The stretches in seconds, rounded as `settings` says (diarize rounds
    to a tenth: the windows of the voices start there)."""
    length = n_samples / rate
    if settings.decimals is None:
        return [(s / rate, e / rate) for s, e in spans]
    return [(max(round(s / rate, settings.decimals), 0), min(round(e / rate, settings.decimals), length))
            for s, e in spans]


def detect(samples, model: str | os.PathLike, settings: Settings, progress: Progress | None = None,
           cancelled: threading.Event | None = None) -> list[tuple[int, int]]:
    """The stretches of speech of int16 samples at 16 kHz, in samples."""
    from . import onnx
    with onnx.Session(model, threads=1) as session:
        probs = probabilities(samples, session, progress, cancelled)
    return stretches(probs, len(samples), settings)
