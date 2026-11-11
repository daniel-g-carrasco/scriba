"""Who speaks when: diarize's way, without PyTorch.

diarize 0.1.2 (Apache 2.0) tells the voices apart with Silero's speech
filter, WeSpeaker's ResNet34-LM embedder and a grouping of the embeddings
in scikit-learn; PyTorch was there only to read the audio, to run Silero
and to compute the Kaldi filter banks. Without it (MSYS2's Python has no
PyTorch) the same chain is: the filter from vad.py, the filter banks below
in numpy, the embedder through onnxruntime's C library (onnx.py), and
diarize's grouping copied as it is (clustering.py). Measured on reference audio: on the 10-minute clip 4
voices and 58 segments, on another reference recording 3 voices and 99 segments,
both identical to diarize's, in 25.8 and 18.8 s on 4 threads (diarize
itself: 74 and 68 s). The filter banks differ from torchaudio's by 1.8e-3
at most on the log-mel, the embeddings by a cosine of 0.99999988.

The number of voices is found by the grouping (a Gaussian mixture's BIC,
then the silhouette), or fixed by the caller: on the Bluetooth headset's
track of 11/11 diarize found 19 voices, so the user may say how many
people were there.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from scriba.app.task import Progress, check, tell
from scriba.core.segments import Turn

from . import audio, vad

MIN_SEGMENT = 0.4           # stretches shorter than this get no embedding (diarize's)
WINDOW = 1.2                # the embedding windows: 1.2 s every 0.6 s (diarize's)
STEP = 0.6
EMBEDDING_SIZE = 256
MEL_BINS = 80


@dataclass(frozen=True)
class Diarization:
    turns: list[Turn]           # in order of time, with diarize's labels ("SPEAKER_00")
    speakers: int               # how many voices
    speech: list[tuple[float, float]]   # the stretches of speech, seconds
    windows: int                # embeddings computed


_mel_cache: dict = {}


def mel_banks(bins: int = MEL_BINS, nfft: int = 512, rate: float = 16000.0, low: float = 20.0, high: float = 0.0):
    """Kaldi's triangular mel filters as torchaudio computes them, in
    float32 (the differences from float64 are what moved the filter banks
    off torchaudio's before this was matched), with the Nyquist column
    at zero."""
    import numpy as np
    key = (bins, nfft, rate, low, high)
    if key in _mel_cache:
        return _mel_cache[key]
    f32 = np.float32
    if high <= 0.0:
        high += 0.5 * rate
    mel_low, mel_high = 1127.0 * np.log(1.0 + low / 700.0), 1127.0 * np.log(1.0 + high / 700.0)
    delta = (mel_high - mel_low) / (bins + 1)
    b = np.arange(bins, dtype=f32)[:, None]
    left = (mel_low + b * f32(delta)).astype(f32)
    centre = (mel_low + (b + f32(1.0)) * f32(delta)).astype(f32)
    right = (mel_low + (b + f32(2.0)) * f32(delta)).astype(f32)
    frequencies = (f32(rate / nfft) * np.arange(nfft // 2, dtype=f32)).astype(f32)
    mel = (1127.0 * np.log(1.0 + frequencies / 700.0)).astype(f32)[None, :]
    up = (mel - left) / (centre - left)
    down = (right - mel) / (right - centre)
    banks = np.pad(np.maximum(f32(0.0), np.minimum(up, down)).astype(f32), ((0, 0), (0, 1)))
    _mel_cache[key] = banks
    return banks


def fbank(wave, bins: int = MEL_BINS):
    """torchaudio.compliance.kaldi.fbank(wave, num_mel_bins=80,
    frame_length=25, frame_shift=10, dither=0, window_type="hamming",
    use_energy=False, sample_frequency=16000) for samples on the int16
    scale, in numpy: frames cut at the edges (snip_edges), the mean taken
    off each frame, pre-emphasis 0.97 with the first sample repeated, a
    Hamming window that is not periodic, an FFT of 512, the power, the mel
    filters, the log with float32's epsilon as the floor."""
    import numpy as np
    w = np.asarray(wave, dtype=np.float32)
    size, shift, nfft = 400, 160, 512
    if w.size < size:
        return np.empty((0, bins), np.float32)
    count = 1 + (w.size - size) // shift
    frames = np.lib.stride_tricks.sliding_window_view(w, size)[::shift][:count].astype(np.float32)
    frames = frames - frames.mean(axis=1, keepdims=True, dtype=np.float32)
    previous = np.concatenate([frames[:, :1], frames[:, :-1]], axis=1)
    frames = frames - np.float32(0.97) * previous
    hamming = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(size) / (size - 1))).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * hamming, n=nfft)).astype(np.float32) ** 2
    energy = spectrum @ mel_banks(bins, nfft).T
    return np.log(np.maximum(energy, np.float32(np.finfo(np.float32).eps))).astype(np.float32)


def windows(speech: list[tuple[float, float]]) -> list[tuple[int, float, float]]:
    """(index of the stretch, start, end) of every embedding window, as
    diarize cuts them: none for a stretch under 0.4 s, one for a stretch up
    to 1.8 s, else windows of 1.2 s every 0.6 s."""
    out = []
    for index, (start, end) in enumerate(speech):
        length = end - start
        if length < MIN_SEGMENT:
            continue
        if length <= WINDOW * 1.5:
            out.append((index, start, end))
            continue
        at = start
        while at + MIN_SEGMENT < end:
            out.append((index, at, min(at + WINDOW, end)))
            at += STEP
    return out


def embeddings(samples, speech: list[tuple[float, float]], session, progress: Progress | None = None,
               cancelled: threading.Event | None = None):
    """The voice embedding (256 numbers) of every window, and the windows
    kept (clustering.SubSegment). diarize wrote each window to a WAV file
    and read it back: the samples went from float64 to PCM 16 by x*32767
    rounded (libsndfile) and back by /32768 (torchaudio), and that is done
    here too, so the numbers stay diarize's."""
    import numpy as np

    from . import clustering
    rate = audio.RATE
    scaled = samples.astype(np.float64) / 32768.0
    cut = windows(speech)
    features, kept = [], []
    for n, (index, start, end) in enumerate(cut):
        check(cancelled)
        piece = np.clip(np.rint(scaled[int(start * rate): int(end * rate)] * 32767.0), -32768, 32767)
        f = fbank(piece)
        if f.shape[0] == 0:
            continue                # diarize's extraction failed on such a window and skipped it
        features.append(f - f.mean(axis=0, dtype=np.float32))
        kept.append(clustering.SubSegment(start=start, end=end, parent_idx=index))
        tell(progress, n + 1, 2 * len(cut))
    vectors = np.zeros((len(features), EMBEDDING_SIZE), np.float32)
    for i, f in enumerate(features):
        check(cancelled)
        vectors[i] = session.run(["embs"], {"feats": f[None]})[0][0]
        tell(progress, len(cut) + i + 1, 2 * len(cut))
    return vectors, kept


def diarize(samples, vad_model: str | os.PathLike, voice_model: str | os.PathLike, speakers: int | None = None,
            threads: int = 4, progress: Progress | None = None,
            cancelled: threading.Event | None = None) -> Diarization:
    """The turns of the voices in int16 samples at 16 kHz. `speakers`
    fixes how many voices there are; None lets the grouping find out."""
    # scikit-learn's joblib counts the physical cores with wmic, which
    # Windows 11 24H2 may lack and a PATH without System32\Wbem does not
    # reach (the frozen build in a clean environment, 11/11/2026: a warning
    # with a traceback on stderr); told here, as whisper's threads guess it
    os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(max(1, (os.cpu_count() or 2) // 2)))
    from . import clustering, onnx
    found = vad.detect(samples, vad_model, vad.FOR_VOICES, cancelled=cancelled)
    speech = vad.seconds(found, len(samples), vad.FOR_VOICES)
    tell(progress, 0.05, 1.0)
    with onnx.Session(voice_model, threads=threads) as session:
        vectors, kept = embeddings(samples, speech, session,
                                   (lambda done, total: tell(progress, 0.05 + 0.75 * done / max(total, 1), 1.0)),
                                   cancelled)
    check(cancelled)
    labels, _details = clustering.cluster_speakers(vectors, min_speakers=1, max_speakers=20, num_speakers=speakers)
    check(cancelled)
    segments = clustering._build_diarization_segments(
        [clustering.SpeechSegment(start=s, end=e) for s, e in speech], kept, labels, vectors)
    tell(progress, 1.0, 1.0)
    turns = [Turn(s.start, s.end, s.speaker) for s in segments]
    return Diarization(turns, len({t.label for t in turns}), speech, len(kept))
