"""Loudness: how loud a track is (EBU R128), and the gain that brings it to
the target without its peaks going over the ceiling.

A recording is kept as it came in (no gain while recording: a gain set
live cannot be undone, and one that is too high clips); the volume is put
right afterwards, on the file, one track at a time, so that whoever spoke
softly and whoever spoke loudly reach the same loudness before the tracks
are joined. ffmpeg measures (its ebur128 filter: integrated loudness,
range, gate, true peak; four times quicker than loudnorm's first pass,
measured 11/11/2026: 1.2 s against 5.3 s for 8.7 minutes); this module
works out what to do with the measure.

The gain is linear (the same for the whole track): it changes the level,
never the dynamics, so that what a listener hears is what was said, only
louder, and the level of each line can still be told from the gain
(core/segments.py's QUIET measures the line as recorded). Two passes, a
measure and a gain, as loudnorm's linear mode does; where a lone peak
would cross the ceiling the mix's limiter takes it (mix_gain), the
engines' copy is held under it instead (linear_gain).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

TARGET = -23.0          # LUFS, EBU R128
CEILING = -1.0          # dBTP: the true peak left below 0 dBFS, for the encoders that follow
MOST = 40.0             # dB: past this the track is noise, not quiet speech
SILENT_BELOW = -70.0    # LUFS: R128's absolute gate; a track under it has nothing to raise


@dataclass(frozen=True)
class Loudness:
    integrated: float       # LUFS
    true_peak: float        # dBTP
    range: float            # LU
    threshold: float        # LUFS, the relative gate


def _number(text: str) -> float:
    text = text.strip().lower()
    return -math.inf if text == "-inf" else math.inf if text == "inf" else float(text)


def parse_ebur128(text: str) -> Loudness:
    """The measure out of the summary ffmpeg's ebur128 filter prints at the
    end (with peak=true). ValueError when there is none. A silent track
    is -70 LUFS and a peak of "-inf"."""
    start = text.rfind("Integrated loudness:")
    if start < 0:
        raise ValueError("no ebur128 summary in the text")
    summary = text[start:]
    number = r"(-?inf|-?\d+(?:\.\d+)?)"
    found = [re.search(pattern, summary) for pattern in (rf"I:\s*{number}\s*LUFS", rf"Threshold:\s*{number}\s*LUFS",
                                                         rf"LRA:\s*{number}\s*LU", rf"Peak:\s*{number}\s*dBFS")]
    if not all(found):
        raise ValueError("an incomplete ebur128 summary")
    integrated, threshold, range_, peak = (_number(m.group(1)) for m in found)
    return Loudness(integrated, peak, range_, threshold)


def silent(measure: Loudness) -> bool:
    return not math.isfinite(measure.integrated) or measure.integrated <= SILENT_BELOW


def linear_gain(measure: Loudness, target: float = TARGET, ceiling: float = CEILING, most: float = MOST) -> float:
    """The gain in dB that brings the track to `target` LUFS, lowered so
    that its true peak stays under `ceiling` dBTP and never above `most`;
    0 for a silent track. Negative when the track is louder than the
    target (it is turned down)."""
    if silent(measure):
        return 0.0
    gain = target - measure.integrated
    if math.isfinite(measure.true_peak):
        gain = min(gain, ceiling - measure.true_peak)
    return min(gain, most)


def mix_gain(measure: Loudness, target: float = TARGET, most: float = MOST) -> float:
    """The gain in dB that brings a track of the mix to `target` LUFS, its
    peaks left to the limiter that follows the sum: a microphone that once
    touched 0 dBFS (the live call's did) would otherwise never be raised.
    0 for a silent track."""
    if silent(measure):
        return 0.0
    return max(min(target - measure.integrated, most), -most)


def balance_gain(role: str, position: float) -> float:
    """Attenuate the less favoured role by up to 12 dB.

    Zero preserves the automatic mix. The preferred side is never raised:
    this changes the ratio without asking the limiter for extra headroom.
    All tracks with one role receive the same change.
    """
    if not math.isfinite(position) or not -1 <= position <= 1:
        raise ValueError("balance must be between -1 and 1")
    if role not in ("me", "others"):
        raise ValueError("a balance needs known track roles")
    return 12.0 * min(position, 0) if role == "me" else -12.0 * max(position, 0)
