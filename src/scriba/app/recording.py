"""A recording made whole: its tracks joined in one file, the mix to listen
to, the silent outputs left out.

While a meeting is recorded every track is a FLAC file of its own
(app/recorder.py): FLAC is written frame by frame, each frame with its own
check, so a file cut short by a crash or a power cut is read up to its
last whole frame (tried: the process killed after 7 s, the file decoded to
6.3 s, ffmpeg saying nothing wrong). A Matroska file written all along
would be readable too, but one file for all the tracks means one writer
that a single failing device could stop.

When the recording stops, a worker makes these files from the tracks:

- `<name>.mka`: every track kept, copied bit for bit (no re-encoding), each
  with its title and its role (ffmpeg.ROLE_TAG: the microphone is the
  user, the outputs the others), which the chain reads to transcribe each
  track on its own (app/jobs.py);
- `<name>_insieme.m4a` (in the user's language): the mix to listen to,
  each track first brought to EBU R128's loudness on its own (two passes:
  ffmpeg's ebur128 measures, one gain per track, core/loudness.py), so
  that the user's voice near the microphone and the others' in the
  earphones come out alike, then summed and held under -1 dBTP by a
  limiter, in AAC (it plays everywhere, a phone included). Where the
  microphone heard the computer's sound (loudspeakers: core/echo.py) its
  track goes into the mix with that sound taken out (without_echo), so
  that the others are not heard twice, the second time a little late;
  the joined recording keeps the microphone as it was recorded;

The FLAC files can be removed only after checking the MKA's track count
and duration. If saving is skipped before that check, the FLAC files stay
for recovery. After the check, skipping leaves the MKA from which the
listening copy can be rebuilt. The recovery checkpoint also preserves
the original tracks when mixing fails. A silently recorded output can
be left out, and any original that had to be shortened is kept.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from scriba.app import process
from scriba.app.engines import ffmpeg
from scriba.app.task import Cancelled, Failure, check
from scriba.core import callmute, devices, levels, loudness, spans
from scriba.core.recording import clip_interval
from scriba.data import files
from scriba.data.timing import clock
from scriba.i18n import _

if TYPE_CHECKING:
    import numpy as np

DURATION_SLACK = 0.2        # seconds a track of the joined file may differ from its FLAC (a frame is 0.1 s)
CAPTURE_CLOCK_SLACK = 2.0    # allow for final buffers and the independent recorder clock
Fraction = Callable[[float], None]
MixProgress = Callable[[int, int, float, float], None]
MIX_BITRATE = "96k"         # AAC for speech, two voices and a room: plenty
# the limiter's ceiling on the mix, linear as alimiter takes it: -1.5 dBFS
# of sample peak, so that the true peak stays under -1 dBTP after AAC
# (measured on the tests' tones: -1.4 dBTP)
LIMIT = 0.841
# what happens after the stop, in order: the recorder closes its files
# (app/recorder.py, at most STOP_WAIT), then finish() joins the tracks
# (a copy, and each FLAC decoded once to check its length), looks for the
# computer's sound in the microphone and takes it out (only with both a
# microphone and an output) and makes the mix (each track measured, then
# decoded, levelled and encoded): the long one
CLOSING, JOINING, CLEANING, MIXING = "closing", "joining", "cleaning", "mixing"
WAITING, TRIMMING = "waiting", "trimming"
FINISHING = (CLOSING, JOINING, CLEANING, MIXING)


@dataclass
class Take:
    """One track as it was recorded."""
    key: str                    # the recorder's own name for it ("input-1", "output-2")
    role: str                   # "me" (the microphone) or "others" (an output): ffmpeg.ROLES
    device: str                 # the device's name, as the system shows it
    path: Path                  # its FLAC file
    loudest: float = float("-inf")  # the loudest peak heard on it, dBFS
    began: float = 0.0          # seconds into the recording when its device started (0 but for a device that came later)
    ended: float | None = None  # seconds into the recording when its device went away; None: to the end
    replaced: bool = False      # ended because the call's microphone was followed, its device still there
    # (from, to) seconds into the recording while Scriba's switch had it off
    # (digital silence on the track); to None: off until the end
    off: tuple[tuple[float, float | None], ...] = ()
    # (from, to) while the user was muted in the call (Windows' indicator):
    # the original keeps the sound; derived audio has silence here
    muted: tuple[tuple[float, float | None], ...] = ()
    named_spans: tuple[spans.Span, ...] = ()
    meeting_title: str = ""
    teams_people: int = 0


@dataclass
class Finished:
    recording: Path             # the joined file; the microphone's FLAC (or the first) when joining failed
    together: Path | None       # the mix to listen to; None when it could not be made
    kept: list[Take] = field(default_factory=list)
    dropped: list[Take] = field(default_factory=list)       # silent outputs, left out
    notes: list[str] = field(default_factory=list)          # what the user should know, in words
    pending: bool = False       # separate tracks still need to be joined


@dataclass(frozen=True)
class Meters:
    """What the page shows while nothing is recorded."""
    devices: tuple[devices.Device, ...] = ()
    levels: dict = field(default_factory=dict)      # device id -> (rms, peak) now, dBFS
    peaks: dict = field(default_factory=dict)       # device id -> the loudest peak of the last PEAKS_FOR seconds
    voices: dict = field(default_factory=dict)      # device id -> the loud part of what it heard lately
    closed: frozenset = frozenset()                 # ids not opened (core/devices.py, may_open)
    failed: dict = field(default_factory=dict)      # device id -> why its meter does not work
    error: str = ""                                 # the devices could not be listed at all
    listed: bool = False                            # the first listing is done


# what happened to the devices while recording, for the window to say
GONE, SWITCHED, ADDED, DISK_LOW, DISK_FULL, FAILED = "gone", "switched", "added", "disk-low", "disk-full", "failed"
# a call program opened another microphone: followed (automatic choice), or
# only said (a microphone chosen by hand); the event's detail is the program
FOLLOWED, CALL_ELSEWHERE = "followed", "call-elsewhere"
OFF, ON = "off", "on"       # Scriba's switch for a side; the event's detail is the role ("me", "others")


@dataclass(frozen=True)
class Event:
    at: float           # seconds into the recording
    kind: str           # one of the above
    device: str = ""    # the device's name
    detail: str = ""


@dataclass(frozen=True)
class Status:
    """The recording as the window shows it."""
    state: str                      # "recording", "paused", "stopping", "stopped", "failed"
    elapsed: float
    tracks: tuple[Take, ...]
    levels: dict                    # track key -> (rms, peak) now
    alerts: tuple[levels.Alert, ...]
    events: tuple[Event, ...]
    seconds_left: float             # what the disk can still take
    error: str = ""
    off: frozenset = frozenset()    # the roles switched off in Scriba now ("me", "others")
    call_muted: bool = False        # the user is muted in the call now (Windows' indicator, when followed)
    teams_state: str = ""
    teams_people: int = 0
    call_ended: float | None = None
    call_prompt: bool = False
    output_silent_since: float | None = None
    call_remaining: float | None = None
    teams_without_border: int = 0


def file_words() -> dict[str, str]:
    """The words of the files' names, in the user's language."""
    return {"recording": _("recording"), "me": _("microphone"), "others": _("output")}


def title(take: Take) -> str:
    return (_("Microphone: {device}") if take.role == "me" else _("Output: {device}")).format(device=take.device)


def join_arguments(ffmpeg_path: str, takes: list[Take], dest: str, cut=None, join_id=None, call_range=None) -> list[str]:
    argv = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error"]
    for take in takes:
        argv += ["-i", os.fspath(take.path)]
    for n in range(len(takes)):
        argv += ["-map", f"{n}:a:0"]
    argv += ["-c", "copy"]
    meeting_title = next((take.meeting_title for take in takes if take.meeting_title), "")
    if join_id is not None:
        argv += ["-metadata", f"{ffmpeg.JOIN_ID_TAG}={join_id}"]
    if cut is not None:
        reason, seconds = cut
        argv += ["-metadata", f"{ffmpeg.CUT_TAG}={reason}:{seconds:.3f}"]
    if call_range is not None:
        start, end = call_range
        argv += ["-metadata", f"{ffmpeg.CALL_RANGE_TAG}={start:.6f}:{end:.6f}"]
    if meeting_title:
        argv += ["-metadata", f"SCRIBA_MEETING_TITLE={meeting_title}"]
    teams_people = max((take.teams_people for take in takes), default=0)
    if teams_people:
        argv += ["-metadata", f"{ffmpeg.TEAMS_PEOPLE_TAG}={teams_people}"]
    for n, take in enumerate(takes):
        argv += [f"-metadata:s:a:{n}", f"title={title(take)}", f"-metadata:s:a:{n}", f"{ffmpeg.ROLE_TAG}={take.role}"]
        if take.muted:
            argv += [f"-metadata:s:a:{n}", f"{ffmpeg.MUTED_TAG}={callmute.write_spans(take.muted)}"]
        if take.off:
            argv += [f"-metadata:s:a:{n}", f"{ffmpeg.OFF_TAG}={callmute.write_spans(take.off)}"]
        if take.named_spans:
            argv += [f"-metadata:s:a:{n}", f"{ffmpeg.NAMED_TAG}={spans.write_spans(take.named_spans)}"]
    return argv + ["-f", "matroska", "-y", dest]


def mix_arguments(ffmpeg_path: str, takes: list[Take], measures: list[loudness.Loudness], dest: str,
                  balance: float = 0.0, streams: list[int] | None = None) -> list[str]:
    """Each track brought to the target on its own (one gain, in floating
    point, so that nothing is cut before the limiter), the tracks summed as
    they are (amix without its own division by the number of tracks: they
    rarely speak at once), the sum held under the ceiling. Without
    `streams` each take is a file of its own; with them, each take is that
    sound track of its file (the joined recording, or the microphone
    without the computer's sound beside it)."""
    argv = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error"]
    if streams is None:
        for take in takes:
            argv += ["-i", os.fspath(take.path)]
        inputs = [f"{n}:a" for n in range(len(takes))]
    else:
        paths = list(dict.fromkeys(os.fspath(take.path) for take in takes))
        for path in paths:
            argv += ["-i", path]
        inputs = [f"{paths.index(os.fspath(take.path))}:a:{index}"
                  for take, index in zip(takes, streams, strict=True)]
    chains = [f"[{source}]aresample=48000,aformat=sample_fmts=flt,"
              f"volume={loudness.mix_gain(m) + loudness.balance_gain(take.role, balance):.2f}dB[t{n}]"
              for n, (source, take, m) in enumerate(zip(inputs, takes, measures, strict=True))]
    joined = "".join(f"[t{n}]" for n in range(len(takes)))
    if len(takes) > 1:
        chains.append(f"{joined}amix=inputs={len(takes)}:normalize=0:duration=longest,"
                      f"alimiter=limit={LIMIT}:level=false[mix]")
    else:
        chains.append(f"{joined}alimiter=limit={LIMIT}:level=false[mix]")
    return argv + ["-filter_complex", ";".join(chains), "-map", "[mix]", "-ac", "1", "-c:a", "aac",
                   "-aac_coder", "fast", "-b:a", MIX_BITRATE, "-movflags", "+faststart+use_metadata_tags",
                   "-metadata", f"{ffmpeg.MUTE_APPLIED_TAG}=1", "-f", "ipod", "-y", dest]


def _run(argv: list[str], what: str, cancelled: threading.Event | None, *, on_stdout=None) -> None:
    try:
        done = process.run(argv, cancelled=cancelled, on_stdout=on_stdout)
    except OSError as e:
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    if done.returncode != 0:
        raise Failure(what, done.detail)


def _length(path: Path, cancelled=None, on_seconds=None) -> float:
    """A FLAC's length by decoding it: a file cut short has no length in
    its header (killed before the end, the header still says 0)."""
    return ffmpeg.decoded_seconds(path, cancelled=cancelled, on_seconds=on_seconds)


def join(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None, *,
         elapsed: float = 0.0, fraction: Fraction | None = None, cut=None, join_id=None, call_range=None) -> Path:
    """The tracks in `<stem>.mka` (a free name), read back and checked."""
    tmp = files.temporary_name(folder, ".mka")
    try:
        report = fraction or (lambda _value: None)
        argv = join_arguments(str(ffmpeg.need("ffmpeg")), takes, str(tmp), cut, join_id, call_range)
        argv[1:1] = ["-progress", "pipe:1"]

        def joining(line):
            reached = ffmpeg.progress_seconds(line)
            if reached is not None and elapsed > 0:
                report(.25 * min(reached / elapsed, 1))

        _run(argv, _("The tracks could not be joined in one file."), cancelled, on_stdout=joining)
        report(.25)
        media = ffmpeg.probe(tmp, cancelled)
        if media.sound_tracks != len(takes):
            raise Failure(_("The tracks could not be joined in one file."),
                          f"{media.sound_tracks} tracks in the file, {len(takes)} recorded")
        durations = []
        for n, take in enumerate(takes):
            durations.append(_length(take.path, cancelled, lambda seconds, n=n: report(
                .25 + .75 * (n + min(seconds / max(media.duration, 1), 1)) / len(takes))))
            report(.25 + .75 * (n + 1) / len(takes))
        expected = max(durations)
        if abs(media.duration - expected) > DURATION_SLACK:
            raise Failure(_("The tracks could not be joined in one file."),
                          f"the file lasts {media.duration:.2f} s, the tracks {expected:.2f} s")
        with tmp.open("r+b") as handle:
            os.fsync(handle.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return files.claim(tmp, folder, stem, ".mka")


def trim_tail(source: Path, end: float, *, revisions: str, cancelled=None) -> Path:
    return trim(source, 0, end, revisions=revisions, cancelled=cancelled)


def trim(source: Path, start: float, end: float, *, revisions: str, cancelled=None) -> Path:
    """Make a checked stream copy and archive the original under a free revision."""
    result = copy_excerpt(source, start, end, cancelled)
    try:
        archive_recording(source, revisions)
    except OSError:
        result.unlink(missing_ok=True)
        raise
    return result


def archive_recording(source: Path, revisions: str) -> None:
    files.to_revisions(source.parent, source.stem, ("",), (source.suffix,), revisions)


def _clip_spans(intervals, start, end):
    return tuple(clipped for left, right in intervals
                 if (clipped := clip_interval(left, right, start, end)) is not None)


def copy_excerpt(source: Path, start: float, end: float, cancelled=None, *, join_id=None,
                 destination: Path | None = None) -> Path:
    """Publish a checked lossless excerpt; leave its source available to the caller."""
    if source.is_symlink():
        raise Failure(_("The recording could not be trimmed."), "symbolic link")
    source = source.resolve()
    media = ffmpeg.probe(source, cancelled)
    if (source.suffix.lower() != ".mka" or not media.roles or not 0 <= start < end <= media.duration
            or (start == 0 and end == media.duration)):
        raise Failure(_("Choose an interval inside the recording."), f"{start}–{end}")
    folder = destination.parent if destination is not None else source.parent
    tmp = files.temporary_name(folder, ".mka")
    try:
        check(cancelled)
        duration = end - start
        argv = [str(ffmpeg.need("ffmpeg")), "-nostdin", "-v", "error", "-i", str(source), "-map", "0",
                "-map_metadata", "0", "-c", "copy", "-ss", f"{start:.6f}", "-t", f"{duration:.6f}",
                "-avoid_negative_ts", "disabled",
                "-metadata", f"{ffmpeg.TRIMMED_TAG}=1", "-metadata", f"{ffmpeg.CUT_TAG}=",
                "-metadata", f"{ffmpeg.TRIM_SELECTION_TAG}={start:.6f}:{end:.6f}:{media.duration:.6f}",
                "-metadata", f"{ffmpeg.TRIM_ORIGINAL_TAG}={source.name}",
                "-metadata", f"{ffmpeg.CALL_RANGE_TAG}=", "-metadata", f"{ffmpeg.JOIN_ID_TAG}={join_id or ''}"]
        expected = []
        for track in media.roles:
            muted = callmute.write_spans(_clip_spans(track.muted, start, end))
            off = callmute.write_spans(_clip_spans(track.off, start, end))
            named = tuple((name, *clipped) for name, left, right in track.named_spans
                          if (clipped := clip_interval(left, right, start, end)) is not None)
            meta = f"-metadata:s:a:{track.index}"
            argv += [meta, f"{ffmpeg.MUTED_TAG}={muted}", meta, f"{ffmpeg.NAMED_TAG}={spans.write_spans(named)}"]
            argv += [meta, f"{ffmpeg.OFF_TAG}={off}"]
            expected.append(replace(track, muted=callmute.read_spans(muted), named_spans=named,
                                    off=callmute.read_spans(off)))
        _run(argv + ["-f", "matroska", "-y", str(tmp)], _("The recording could not be trimmed."), cancelled)
        cut = ffmpeg.probe(tmp, cancelled)
        if (cut.tracks != tuple(expected) or cut.meeting_title != media.meeting_title
                or cut.teams_people != media.teams_people
                or cut.trim_selection is None or cut.trim_original != source.name
                or cut.duration <= 0 or abs(cut.duration - duration) > DURATION_SLACK):
            raise Failure(_("The recording could not be trimmed."), "stream tags or cut duration did not match")
        with tmp.open("r+b") as handle:
            os.fsync(handle.fileno())
        check(cancelled)
        stem = destination.stem if destination is not None else f"{source.stem}_{_('trimmed')}"
        return files.claim(tmp, folder, stem, ".mka")
    finally:
        tmp.unlink(missing_ok=True)


def one_of_each(takes: list[Take], work: Path,
                cancelled: threading.Event | None = None, *, streams=None) -> tuple[list[Take], list[str]]:
    """The takes to mix, the outputs with the same sound left out but one
    (core/duplicates.py: an output that passes into another, EasyEffects
    into the loudspeakers, is recorded twice), and what to tell the user.
    The recording keeps every track; the transcript leaves the copy out on
    its own (app/jobs.py). Never raises but Cancelled: when the measure
    fails every take is mixed, as before."""
    from scriba.app.engines import echo as canceller
    from scriba.core import duplicates

    outputs = [n for n, take in enumerate(takes) if take.role == "others"]
    if len(outputs) < 2:
        return list(takes), []
    try:
        check(cancelled)
        mono = [ffmpeg.decode(takes[n].path, work / f"output{n}.wav", cancelled=cancelled,
                              stream=streams[n] if streams is not None else 0) for n in outputs]
        out = duplicates.left_out(len(outputs), lambda a, b: canceller.same_sound(mono[a], mono[b], work, cancelled))
    except (Failure, OSError, EOFError, wave.Error):
        return list(takes), []
    gone = {outputs[k] for k in out}
    notes = [_("{left} carries the same sound as {kept} (one output passes into the other): it is kept in the "
               "recording, left out of the copy to listen to and of the transcript.").format(left=left, kept=kept)
             for left, kept in duplicates.names(out, [title(takes[n]) for n in outputs])]
    return [take for n, take in enumerate(takes) if n not in gone], notes


def hears_and_plays(takes: list[Take]) -> bool:
    """A microphone and a computer's output: the microphone may have heard
    the computer (core/echo.py)."""
    return {take.role for take in takes} >= {"me", "others"}


def without_echo(takes: list[Take], work: Path, streams: list[int] | None = None,
                 cancelled: threading.Event | None = None, *, elapsed: float = 0.0,
                 fraction: Fraction | None = None) -> tuple[list[Take], list[int] | None, list[str]]:
    """Prepare listening tracks with call mute and echo applied to the microphone.

    References are decoded once. If echo removal fails, use the microphone with
    its muted intervals still silenced. Return the tracks, stream indices and notes.
    """
    from scriba.app.engines import echo as canceller

    where = [(take.path, streams[n] if streams is not None else 0) for n, take in enumerate(takes)]
    outputs = [where[n] for n, take in enumerate(takes) if take.role == "others"]
    out, used, notes = list(takes), list(streams) if streams is not None else None, []
    microphones = [(n, take) for n, take in enumerate(takes) if take.role == "me" and (outputs or take.muted)]
    report = fraction or (lambda _value: None)
    references = []
    for microphone, (n, take) in enumerate(microphones):
        def phase(start, end, microphone=microphone):
            def update(done, total):
                if total > 0:
                    report((microphone + start + (end - start) * min(done / total, 1)) / len(microphones))
            return update

        mono: list[Path] = []
        try:
            check(cancelled)
            path, stream = where[n]
            mic = work / f"track{n}.wav"
            mono.append(mic)
            ffmpeg.decode(path, mic, elapsed, phase(0, .075), cancelled, stream=stream)
            masked = canceller.silence_mutes(mic, work / f"muted{n}.wav", take.muted, cancelled)
            if masked != mic:
                out[n] = replace(take, path=masked)
                if used is not None:
                    used[n] = 0
            if not outputs:
                continue
            if not references:
                for k, (path, stream) in enumerate(outputs):
                    scale = .075 / len(outputs)
                    decoding = phase(.075 + k * scale, .075 + (k + 1) * scale)
                    wav = work / f"reference{k}.wav"
                    ffmpeg.decode(path, wav, elapsed, decoding, cancelled, stream=stream)
                    references.append(wav)
                    decoding(1, 1)
            spliced, note = canceller.remove(masked, references, work / f"microphone{n}.wav", take.muted, cancelled,
                                             began=take.began, ended=take.ended, progress=phase(.15, 1))
            if spliced == masked:
                continue
        except (Failure, OSError, EOFError, wave.Error) as e:
            if take.muted and out[n] is take:
                raise Failure(_("The muted intervals could not be silenced: {reason}").format(reason=e),
                              str(e)) from e
            if len(references) != len(outputs):
                references.clear()
            notes.append(_("The computer's sound heard by the microphone could not be taken out of the copy to "
                           "listen to: {reason}").format(reason=e))
            continue
        finally:
            report((microphone + 1) / len(microphones))
            for path in mono:
                path.unlink(missing_ok=True)
        out[n] = replace(take, path=spliced)
        if used is not None:
            used[n] = 0
        notes.append(note)
    return out, used, notes


def mix(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None,
        *, progress: MixProgress | None = None, streams=None) -> Path | None:
    """The mix to listen to, `<stem>_<together>.m4a` (a free name); None
    when every track is silent."""
    measured = []
    durations = []
    total = len(takes)
    indexes = streams if streams is not None else [0] * total
    for n, (take, index) in enumerate(zip(takes, indexes, strict=True), 1):
        duration = ffmpeg.probe(take.path, cancelled).duration if progress is not None else 0.0
        durations.append(duration)
        begin = take.began
        length = max(0.0, take.ended - begin) if take.ended is not None else 0.0
        active = length or max(0.0, duration - begin)
        if progress is not None:
            progress(n, total, 0.0, active)
        measure = ffmpeg.measure_loudness(
            take.path, index, cancelled,
            on_seconds=(lambda seconds, n=n, active=active: progress(n, total, seconds, active))
            if progress is not None else None, start=begin, length=length)
        measured.append((take, measure, index))
    audible = [(t, m, i) for t, m, i in measured if not loudness.silent(m)]
    if not audible:
        audible = [entry for entry in measured if entry[0].role == "me" and entry[0].muted][:1]
    if not audible:
        return None
    check(cancelled)
    tmp = files.temporary_name(folder, ".m4a")
    try:
        argv = mix_arguments(str(ffmpeg.need("ffmpeg")), [t for t, _m, _i in audible],
                            [m for _t, m, _i in audible], str(tmp),
                            streams=[i for _t, _m, i in audible] if streams is not None else None)
        duration = max(durations, default=0.0)

        def mixing(line: str) -> None:
            reached = ffmpeg.progress_seconds(line)
            if reached is not None:
                progress(0, total, reached, duration)

        if progress is not None:
            progress(0, total, 0.0, duration)
            argv[1:1] = ["-progress", "pipe:1"]
        _run(argv, _("The mix to listen to could not be made."), cancelled,
             on_stdout=mixing if progress is not None else None)
        if ffmpeg.probe(tmp, cancelled).duration <= 0:
            raise Failure(_("The mix to listen to could not be made."), "the mix has no length")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return files.claim(tmp, folder, f"{stem}_{_('together')}", ".m4a")


def balance_available(source: str | os.PathLike | None) -> bool:
    """A readable original with both microphone and output roles.

    Missing audio is ordinary for a reopened transcript: names still work,
    but there are no independent tracks from which to make a listening copy.
    """
    return bool(source and Path(source).is_file()
                and {track.role for track in ffmpeg.probe(source).roles} == {"me", "others"})


def rebalance(source: str | os.PathLike, folder: Path, stem: str, balance: float,
              cancelled: threading.Event | None = None) -> Path:
    """A new listening mix from the original streams, with no extracted FLACs.

    The same filters as mix() preserve the automatic balance at zero. The
    joined source and every earlier mix remain untouched, also on failure.
    """
    tracks = balance_media(Path(source), cancelled).roles
    return _mix_from_source(Path(source), tracks, folder, f"{stem}_{_('balanced')}", balance, cancelled)


def listening_mix(source: Path, cancelled: threading.Event | None = None) -> Path:
    """Rebuild the automatic listening copy from a verified multitrack recording."""
    source = Path(source)
    tracks = ffmpeg.probe(source, cancelled).roles
    if not tracks:
        raise Failure(_("The recording has no separate Scriba tracks."), str(source))
    return _mix_from_source(source, tracks, source.parent, f"{source.stem}_{_('together')}", 0, cancelled)


def _mix_from_source(source, tracks, folder, stem, balance, cancelled):
    loudness.balance_gain("me", balance)
    work = Path(tempfile.mkdtemp(prefix="scriba-echo-"))
    try:
        tmp = files.temporary_name(folder, ".m4a")
        try:
            _balanced_mix([Take(str(t.index), t.role, t.title, source, muted=t.muted) for t in tracks],
                          [t.index for t in tracks],
                          work, tmp, balance, cancelled)
            if ffmpeg.probe(tmp, cancelled).duration <= 0:
                raise Failure(_("The mix to listen to could not be made."), "the mix has no length")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return files.claim(tmp, folder, stem, ".m4a")


PREVIEW_RATE = 48000        # the mix's own (aresample=48000 in mix_arguments)


def balance_preview(source: str | os.PathLike, start: float, length: float, folder: Path,
                    cancelled: threading.Event | None = None, *,
                    tracks: tuple[ffmpeg.Track, ...] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Prepare the two roles independently in a temporary folder. Measure
    loudness on this excerpt and remove its echo just as for the saved
    copy. The player applies the balance to the resulting mono PCM."""
    import numpy as np

    source = Path(source)
    if tracks is None:
        tracks = balance_media(source, cancelled).roles
    cut = [Take(str(t.index), t.role, t.title,
                ffmpeg.decode(source, folder / f"track{t.index}.wav", cancelled=cancelled, start=start,
                              length=length, stream=t.index, rate=PREVIEW_RATE),
                muted=_clip_spans(t.muted, start, start + length))
           for t in tracks]
    work = folder / "work"
    work.mkdir()
    audible = _audible_tracks(cut, None, work, cancelled)
    cut_paths = {take.path for take in cut}
    max_frames = round(length * PREVIEW_RATE)
    me, others = np.zeros((2, max_frames), dtype=np.float32)
    frames = 0
    for take, measure, _index in audible:
        check(cancelled)
        path = take.path
        if path not in cut_paths:   # the cleaned microphone is at the echo canceller's rate
            path = ffmpeg.decode(path, folder / f"cleaned-{take.key}.wav", cancelled=cancelled, rate=PREVIEW_RATE)
        with wave.open(os.fspath(path), "rb") as wav:
            pcm = np.frombuffer(wav.readframes(max_frames), dtype="<i2")
        gain = 10 ** (round(loudness.mix_gain(measure), 2) / 20) / 32768
        role = me if take.role == "me" else others
        role[:len(pcm)] += pcm.astype(np.float32) * gain
        frames = max(frames, len(pcm))
    return me[:frames], others[:frames]


def balance_media(source: Path, cancelled: threading.Event | None) -> ffmpeg.Media:
    if not source.is_file():
        raise Failure(_("The original recording is missing: a new listening copy cannot be made."), str(source))
    media = ffmpeg.probe(source, cancelled)
    if {t.role for t in media.roles} != {"me", "others"}:
        raise Failure(_("A listening balance needs separate microphone and output tracks."), "no independent roles")
    return media


def _balanced_mix(takes: list[Take], streams: list[int] | None, work: Path, dest: Path, balance: float,
                  cancelled: threading.Event | None) -> None:
    """The listening copy's road after the stop and at a new balance: the
    microphone without the computer's sound where it heard it, as in the
    first copy (finish), every audible track levelled, the balance."""
    audible = _audible_tracks(takes, streams, work, cancelled)
    _run(mix_arguments(str(ffmpeg.need("ffmpeg")), [t for t, _m, _i in audible], [m for _t, m, _i in audible],
                       str(dest), balance, [i for _t, _m, i in audible] if streams is not None else None),
         _("The mix to listen to could not be made."), cancelled)


def _audible_tracks(takes: list[Take], streams: list[int] | None, work: Path,
                    cancelled: threading.Event | None) -> list[tuple[Take, loudness.Loudness, int]]:
    takes, streams, _notes = without_echo(takes, work, streams, cancelled)
    indexes = streams if streams is not None else [0] * len(takes)
    audible = []
    muted = None
    for take, index in zip(takes, indexes, strict=True):
        check(cancelled)
        measure = ffmpeg.measure_loudness(take.path, index, cancelled)
        if not loudness.silent(measure):
            audible.append((take, measure, index))
        elif muted is None and take.role == "me" and take.muted:
            muted = (take, measure, index)
    if not audible and muted is not None:
        return [muted]
    if not audible:
        raise Failure(_("Nothing was heard on any track: a new listening copy cannot be made."), "silent tracks")
    return audible


def _interval_words(intervals: tuple) -> str:
    return ", ".join(
        (_("from {start} to the end").format(start=clock(start)) if end is None else
         _("from {start} to {end}").format(start=clock(start), end=clock(end)))
        for start, end in intervals
    )


def off_notes(takes: list[Take]) -> list[str]:
    """Where Scriba's switch had a track off, in words: there is silence
    there on purpose, and nothing to transcribe."""
    return [
        _("{track}: switched off in Scriba {intervals}. There is silence on its track during these intervals.").format(
            track=title(take), intervals=_interval_words(take.off))
        for take in takes if take.off
    ]


def muted_notes(takes: list[Take]) -> list[str]:
    """Where the user was muted in the call, in words: the sound is there,
    the transcript leaves the microphone's speech out."""
    return [
        _("{track}: muted in the call {intervals}. The original track keeps the sound.").format(
            track=title(take), intervals=_interval_words(take.muted))
        for take in takes if take.muted
    ]


def _bounded_tracks(takes: list[Take], work: Path, elapsed: float, cancelled, notes: list[str]) -> list[Take]:
    """Keep an overlong source and use a bounded, lossless copy for both outputs."""
    if elapsed <= 0:
        return takes
    bounded = []
    for n, take in enumerate(takes):
        duration = ffmpeg.probe(take.path, cancelled).duration
        if duration > elapsed + CAPTURE_CLOCK_SLACK:
            dest = work / f"bounded-{n}.mka"
            _run([str(ffmpeg.need("ffmpeg")), "-nostdin", "-loglevel", "error", "-i", str(take.path),
                  "-map", "0:a:0", "-t", f"{elapsed:.6f}", "-c", "copy", "-f", "matroska", str(dest)],
                 _("The unusually long track could not be shortened."), cancelled)
            notes.append(_("{track}: longer than the recording. The copy was shortened and the original track was kept.")
                         .format(track=title(take)))
            take = replace(take, path=dest)
        bounded.append(take)
    return bounded


def _on_joined_timeline(takes, joined, excerpt, cancelled):
    media = ffmpeg.probe(joined, cancelled)
    start, end = excerpt or (0, media.duration)
    return [replace(take, path=joined, began=max(0, min(take.began, end) - start),
                    ended=None if take.ended is None else max(0, min(take.ended, end) - start),
                    off=_clip_spans(take.off, start, end), muted=track.muted, named_spans=track.named_spans)
            for take, track in zip(takes, media.roles, strict=True)]


def finish(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None,
           progress: Callable[[str], None] | None = None, *, elapsed: float = 0.0,
           mix_progress: MixProgress | None = None, fraction: Fraction | None = None,
           journal=None, previous=None, joined: Path | None = None, choose=None) -> Finished:
    """The recording made whole (see the module's docstring). Never raises
    for a failure to join or mix: the note says why. Cancellation before
    joining keeps the separate tracks and cancellation after joining
    keeps the MKA without the listening copy. `progress` is
    told each step of FINISHING as it begins (from this thread)."""
    said = progress or (lambda _step: None)
    kept = [t for t in takes if t.role == "me" or levels.kept(t.loudest)]
    dropped = [t for t in takes if t not in kept]
    notes: list[str] = []
    if not kept:
        kept, dropped = list(takes), []
        notes.append(_("Nothing was heard on any track: the recording is kept as it is."))
    if not kept:
        raise Failure(_("Nothing was recorded."), "no track")
    first = next((t for t in kept if t.role == "me"), kept[0])
    together = None
    previous = previous or {}
    cut = previous.get("cut")
    call_range = previous.get("call_range")
    join_id = previous.get("join_id") or (uuid.uuid4().hex if journal is not None else None)
    prepared = journal.takes(previous, "mix_takes") if journal and joined else []
    reuse_mix = previous.get("mix_mute_applied") or not any(take.muted for take in kept)
    retained = {journal.local(path) for path in previous.get("retained", ())} if journal else set()
    work = Path(tempfile.mkdtemp(prefix=".scriba-work-" if journal else "scriba-echo-",
                                 dir=folder if journal else None))
    keep_work = False
    selecting = previous.get("phase") in ("finishing:waiting", "finishing:trimming")
    bounded = None
    excerpt = previous.get("excerpt") if joined is not None else None
    streams = None
    working = kept
    old_work = {take.path.parent for take in prepared if take.path.parent != folder.resolve()}
    try:
        if joined is None:
            said(JOINING)
            if journal is not None:
                journal.save("finishing:joining", takes, elapsed, cut=cut, join_id=join_id, call_range=call_range)
        if reuse_mix and prepared and all(take.path.is_file() for take in prepared):
            heard = prepared
            streams = previous.get("mix_streams")
            if excerpt:
                working = _on_joined_timeline(kept, joined, excerpt, cancelled)
        else:
            try:
                check(cancelled)
                if joined is None:
                    bounded = _bounded_tracks(kept, work, elapsed, cancelled, notes)
                    retained = {t.path for t, b in zip(kept, bounded, strict=True) if t.path != b.path}
                    joined = join(bounded, folder, stem, cancelled, elapsed=elapsed,
                                  fraction=fraction, cut=cut, join_id=join_id, call_range=call_range)
            except Failure as e:
                if joined is not None:
                    raise
                notes.append(_("{reason} The tracks are kept as separate files.").format(reason=e))
                return Finished(first.path, None, kept, dropped, notes + off_notes(kept) + muted_notes(kept),
                                pending=journal is not None)
            if journal is not None and (choose is not None or call_range or excerpt):
                selecting = True
                joined, excerpt = journal.keep_excerpt(joined, takes, elapsed, previous, choose, said, cancelled,
                                                       retained=retained, cut=cut, call_range=call_range)
                selecting = False
            if excerpt or any(not take.path.is_file() for take in kept):
                working = bounded = _on_joined_timeline(kept, joined, excerpt, cancelled)
                streams = list(range(len(bounded)))
            elif bounded is None:
                bounded = _bounded_tracks(kept, work, elapsed, cancelled, notes)
            if journal is not None:
                journal.save("finishing:excerpt" if excerpt else "finishing:cleaning", takes, elapsed, joined=joined,
                             retained=retained, cut=cut, excerpt=excerpt)
            check(cancelled)
            if hears_and_plays(bounded) or any(t.muted for t in bounded) or sum(t.role == "others" for t in bounded) > 1:
                said(CLEANING)
            heard, same_notes = one_of_each(bounded, work, cancelled, streams=streams)
            if streams is not None:
                indexes = {take.key: index for take, index in zip(bounded, streams, strict=True)}
                streams = [indexes[take.key] for take in heard]
            notes += same_notes
            if hears_and_plays(heard) or any(t.muted for t in heard):
                heard, streams, cleaned = without_echo(heard, work, streams, cancelled=cancelled,
                                                       elapsed=excerpt[1] - excerpt[0] if excerpt else elapsed,
                                                       fraction=fraction)
                notes += cleaned
        if journal is not None:
            journal.save("finishing:mixing", takes, elapsed, joined=joined, mix_takes=heard, retained=retained,
                         cut=cut, excerpt=excerpt, mix_streams=streams)
            keep_work = True
        said(MIXING)
        try:
            def mixing(n, total, reached, duration):
                if mix_progress is not None:
                    mix_progress(n, total, reached, duration)
                if fraction is not None:
                    part = min(reached / duration, 1) if duration > 0 else 0
                    fraction((n - 1 + part) / (2 * total) if n else .5 + part / 2)

            together = mix(heard, folder, joined.stem, cancelled, progress=mixing
                           if fraction is not None or mix_progress is not None else None, streams=streams)
        except Failure as e:
            notes.append(str(e))
            if journal is not None:
                return Finished(joined, None, kept, dropped, notes)
        if journal is not None:
            journal.save("done", takes, elapsed, joined=joined, together=together, cut=cut, excerpt=excerpt)
        keep_work = False
    except Cancelled:
        if joined is None:
            notes.append(_("Saving paused. The original tracks are still available."))
            return Finished(first.path, None, kept, dropped, notes, pending=True)
        if selecting:
            notes.append(_("The complete recording is saved. You can choose what to keep when Scriba opens again."))
            return Finished(joined, None, kept, dropped, notes, pending=True)
        notes.append(_("Listening copy skipped. You can create it from the recording."))
        if journal is not None:
            journal.save("done", takes, elapsed, joined=joined, cut=cut, excerpt=excerpt)
        keep_work = False
    finally:
        if not keep_work:
            for scratch in {work, *old_work}:
                if scratch == work or scratch.parent == folder.resolve() and scratch.name.startswith(".scriba-work-"):
                    shutil.rmtree(scratch, ignore_errors=True)
    for take in dropped:
        notes.append(_("{device}: silent from start to end, not kept.").format(device=take.device))
    notes += off_notes(working) + muted_notes(working)
    for take in kept + dropped:
        if take.path in retained:
            continue
        try:
            take.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            notes.append(_("{name} could not be removed ({error}): its sound is in {joined} as well.").format(
                name=take.path.name, error=e, joined=joined.name))
    return Finished(joined, together, kept, dropped, notes)


def meeting_folder(recordings: Path, when) -> Path:
    """The folder of a new recording in the folder for the recordings, its
    date and time as its name (core/recording.py, folder_name), not made
    yet: a name nothing is under (`_R01`... after it otherwise)."""
    from scriba.core import recording as rec
    return files.free_name(recordings, rec.folder_name(when), "")


def own_folders(paths) -> list[Path]:
    """The app's own folders: where it is installed (or its sources), its
    settings, state, data and cache (app/paths.py's Paths)."""
    import sys

    from scriba import PACKAGE_DIR
    program = Path(sys.executable).parent if getattr(sys, "frozen", False) else PACKAGE_DIR.parent
    return [program, paths.config, paths.state, paths.data, paths.cache_folder]


def unsuitable(folder: str | os.PathLike, own: list[Path]) -> str:
    """Why recordings may not go to `folder` ("" when they may): never
    inside the app's own folders, which an update, an uninstall or a cache
    cleaner may take away with everything in them."""
    target = Path(os.path.abspath(folder))
    for mine in own:
        mine = Path(os.path.abspath(mine))
        if target == mine or mine in target.parents:
            return _("{folder} is one of Scriba's own folders: an update could take the recordings away. "
                     "Choose a folder of yours.").format(folder=target)
    return ""
