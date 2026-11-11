"""How long a transcription will take, said before it starts (the plan: a
meeting of an hour must finish in a time declared beforehand).

The measure is the speed of the last run: seconds of audio done per second
of work, the "times real time" that trascrivi.py printed (4.0 means an hour
of audio in a quarter of an hour). The machine, the model and the GPU
change it, so the app keeps one per model and device and passes it in;
this module only does the sums, and says the times in words for the
window. A run too short to say anything (the model loading weighs more
than the audio) is not taken as a measure.
"""

from __future__ import annotations

import math

from scriba.i18n import _, ngettext

# Before any measure: as long as the audio. A guess on the safe side for
# whisper large-v3-turbo on a laptop's CPU [I: to be replaced by the
# measures of package 4]; the first run measures the machine and the next
# estimate uses that.
DEFAULT_SPEED = 1.0
# Under a minute of audio the start (loading a model of 1.5 GB) is most
# of the time: the speed measured would say little about the next hour.
MIN_MEASURED_AUDIO = 60.0


def _good(value: float | None) -> bool:
    return value is not None and value.__class__ in (int, float) and math.isfinite(value) and value > 0


def speed(audio_seconds: float, work_seconds: float) -> float | None:
    """The speed of a finished run, or None when the run says nothing
    trustworthy: too little audio, or times that are not times."""
    if not (_good(audio_seconds) and _good(work_seconds)) or audio_seconds < MIN_MEASURED_AUDIO:
        return None
    return audio_seconds / work_seconds


def work_time(audio_seconds: float, measured: float | None = None, default: float = DEFAULT_SPEED) -> float:
    """The seconds of work for `audio_seconds` of audio at the speed
    `measured` last time, or at `default` when there is no measure (or a
    measure that is not a speed). No audio, no work."""
    if not _good(audio_seconds):
        return 0.0
    rate = measured if _good(measured) else default
    if not _good(rate):
        raise ValueError(f"default speed {default!r}")
    return audio_seconds / rate


# Past this share of the work done, the pace of this very run says more
# than the estimate made before it (which may be a guess without measure)
TRUST_PACE_AFTER = 0.15


def remaining(estimated: float, elapsed: float, fraction: float) -> float:
    """The seconds of work still to go: the estimate less the time gone at
    first, then the pace of the run itself (the time gone over the share
    done)."""
    if not (_good(elapsed) and fraction.__class__ in (int, float) and math.isfinite(fraction)):
        gone = elapsed if _good(elapsed) else 0.0
        return max(estimated - gone, 0.0) if _good(estimated) else 0.0
    if fraction >= TRUST_PACE_AFTER:
        return max(elapsed * (1.0 - min(fraction, 1.0)) / fraction, 0.0)
    return max(estimated - elapsed, 0.0) if _good(estimated) else 0.0


def about(seconds: float) -> str:
    """A time to come, in round words: "less than a minute", "about 4
    minutes", "about 1 h 10 min". Rounded up past the minute: a promise of
    four minutes kept in five reads worse than one of five kept in four."""
    if not _good(seconds) or seconds < 60:
        return _("less than a minute")
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return ngettext("about {n} minute", "about {n} minutes", minutes).format(n=minutes)
    hours, minutes = divmod(minutes, 60)
    return _("about {hours} h {minutes:02d} min").format(hours=hours, minutes=minutes)


def span(seconds: float) -> str:
    """A time gone, exactly as far as it matters: "45 s", "7 min 40 s",
    "1 h 02 min"."""
    seconds = round(seconds) if _good(seconds) else 0
    if seconds < 60:
        return _("{s} s").format(s=seconds)
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return _("{m} min {s:02d} s").format(m=minutes, s=s)
    hours, minutes = divmod(minutes, 60)
    return _("{h} h {m:02d} min").format(h=hours, m=minutes)
