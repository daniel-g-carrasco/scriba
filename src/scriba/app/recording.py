"""A recording made whole: its tracks joined in one file, the mix to listen
to, the silent outputs left out.

While a meeting is recorded every track is a FLAC file of its own
(app/recorder.py): FLAC is written frame by frame, each frame with its own
check, so a file cut short by a crash or a power cut is read up to its
last whole frame (tried: the process killed after 7 s, the file decoded to
6.3 s, ffmpeg saying nothing wrong). A Matroska file written all along
would be readable too, but one file for all the tracks means one writer
that a single failing device could stop.

When the recording stops, this module (in a thread: it takes seconds for
an hour) makes of the tracks:

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

and only when both are written and read back (the tracks counted, every
duration within DURATION_SLACK of its FLAC's) are the FLAC files removed:
their bits are in the .mka. An output that stayed at digital silence all
along (an output nobody played on: the automatic mode records them all)
is left out and its file removed, and that is said. If anything fails the
FLAC files stay where they are, all of them, and the reason is said.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from scriba.app.engines import ffmpeg, process
from scriba.app.task import Failure, check
from scriba.core import callmute, devices, levels, loudness
from scriba.data import files
from scriba.data.timing import clock
from scriba.i18n import _

DURATION_SLACK = 0.2        # seconds a track of the joined file may differ from its FLAC (a frame is 0.1 s)
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
    # the sound is kept, the transcript leaves the microphone's speech out
    muted: tuple[tuple[float, float | None], ...] = ()


@dataclass
class Finished:
    recording: Path             # the joined file; the microphone's FLAC (or the first) when joining failed
    together: Path | None       # the mix to listen to; None when it could not be made
    kept: list[Take] = field(default_factory=list)
    dropped: list[Take] = field(default_factory=list)       # silent outputs, left out
    notes: list[str] = field(default_factory=list)          # what the user should know, in words


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


def file_words() -> dict[str, str]:
    """The words of the files' names, in the user's language."""
    return {"recording": _("recording"), "me": _("microphone"), "others": _("output")}


def title(take: Take) -> str:
    return (_("Microphone: {device}") if take.role == "me" else _("Output: {device}")).format(device=take.device)


def join_arguments(ffmpeg_path: str, takes: list[Take], dest: str) -> list[str]:
    argv = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error"]
    for take in takes:
        argv += ["-i", os.fspath(take.path)]
    for n in range(len(takes)):
        argv += ["-map", f"{n}:a:0"]
    argv += ["-c", "copy"]
    for n, take in enumerate(takes):
        argv += [f"-metadata:s:a:{n}", f"title={title(take)}", f"-metadata:s:a:{n}", f"{ffmpeg.ROLE_TAG}={take.role}"]
        if take.muted:
            argv += [f"-metadata:s:a:{n}", f"{ffmpeg.MUTED_TAG}={callmute.write_spans(take.muted)}"]
    return argv + ["-f", "matroska", "-y", dest]


def mix_arguments(ffmpeg_path: str, takes: list[Take], measures: list[loudness.Loudness], dest: str,
                  balance: float = 0.0, streams: list[int] | None = None, wav: bool = False) -> list[str]:
    """Each track brought to the target on its own (one gain, in floating
    point, so that nothing is cut before the limiter), the tracks summed as
    they are (amix without its own division by the number of tracks: they
    rarely speak at once), the sum held under the ceiling. Without
    `streams` each take is a file of its own; with them, each take is that
    sound track of its file (the joined recording, or the microphone
    without the computer's sound beside it). `wav`: a PCM WAV at 48 kHz
    instead of AAC, for GTK's player (the balance's preview)."""
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
    if wav:
        return argv + ["-filter_complex", ";".join(chains), "-map", "[mix]", "-ac", "1", "-c:a", "pcm_s16le",
                       "-f", "wav", "-y", dest]
    return argv + ["-filter_complex", ";".join(chains), "-map", "[mix]", "-ac", "1", "-c:a", "aac",
                   "-b:a", MIX_BITRATE, "-movflags", "+faststart", "-f", "ipod", "-y", dest]


def _run(argv: list[str], what: str, cancelled: threading.Event | None) -> None:
    try:
        done = process.run(argv, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("ffmpeg could not be started."), str(e)) from e
    if done.returncode != 0:
        raise Failure(what, done.detail)


def _length(path: Path) -> float:
    """A FLAC's length by decoding it: a file cut short has no length in
    its header (killed before the end, the header still says 0)."""
    return ffmpeg.decoded_seconds(path)


def join(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None) -> Path:
    """The tracks in `<stem>.mka` (a free name), read back and checked."""
    tmp = files.temporary_name(folder, ".mka")
    try:
        _run(join_arguments(str(ffmpeg.need("ffmpeg")), takes, str(tmp)),
             _("The tracks could not be joined in one file."), cancelled)
        media = ffmpeg.probe(tmp, cancelled)
        if media.sound_tracks != len(takes):
            raise Failure(_("The tracks could not be joined in one file."),
                          f"{media.sound_tracks} tracks in the file, {len(takes)} recorded")
        expected = max(_length(t.path) for t in takes)
        if abs(media.duration - expected) > DURATION_SLACK:
            raise Failure(_("The tracks could not be joined in one file."),
                          f"the file lasts {media.duration:.2f} s, the tracks {expected:.2f} s")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return files.claim(tmp, folder, stem, ".mka")


def hears_and_plays(takes: list[Take]) -> bool:
    """A microphone and a computer's output: the microphone may have heard
    the computer (core/echo.py)."""
    return {take.role for take in takes} >= {"me", "others"}


def without_echo(takes: list[Take], work: Path, streams: list[int] | None = None,
                 cancelled: threading.Event | None = None) -> tuple[list[Take], list[int] | None, list[str]]:
    """The takes to mix, each microphone's with the computer's sound taken
    out where it heard it (core/echo.py, app/engines/echo.py) and the raw
    track elsewhere, as a WAV in `work`; the streams to go with them (as
    mix_arguments takes them); what to tell the user.

    At 16 kHz, as the transcription has it, not at the recording's 48 kHz.
    Both were measured on 13/11/2026 (5 minutes of the real meeting, the
    48 kHz one brought up from 16 kHz): the canceller works at both (42 and
    47 dB taken out, 35 dB above 8 kHz on a made-up echo of the whole band),
    but at 48 kHz it took 1.8 s per minute of audio against 0.6, about
    two minutes more after the stop of a meeting of an hour and three
    quarters, and the file of two channels is three times as heavy. What
    is lost is the user's voice above 8 kHz in the copy to listen to (the
    others' voices, through the call, rarely have it); the transcript never
    had it. Never raises but Cancelled: when something fails the raw track
    is mixed and the note says why."""
    from scriba.app.engines import echo as canceller
    from scriba.core import echo

    where = [(take.path, streams[n] if streams is not None else 0) for n, take in enumerate(takes)]
    outputs = [where[n] for n, take in enumerate(takes) if take.role == "others"]
    out, used, notes = list(takes), list(streams) if streams is not None else None, []
    for n, take in enumerate(takes):
        if take.role != "me" or not outputs:
            continue
        mono: list[Path] = []
        try:
            check(cancelled)
            mono = [ffmpeg.decode(path, work / f"track{n}-{k}.wav", cancelled=cancelled, stream=stream)
                    for k, (path, stream) in enumerate([where[n], *outputs])]
            pair = canceller.pair_files(mono[0], mono[1:], work / f"pair{n}.wav", cancelled)
            windows, duration = canceller.measure(pair, cancelled=cancelled)
            parts = echo.stretches(windows, duration)
            if echo.echo_seconds(parts) <= 0:
                continue
            cleaned = canceller.cancel(pair, work / f"cleaned{n}.wav", cancelled=cancelled)
            spliced = canceller.splice(pair, cleaned, parts, work / f"microphone{n}.wav", cancelled)
        except (Failure, OSError, EOFError, wave.Error) as e:
            notes.append(_("The computer's sound heard by the microphone could not be taken out of the copy to "
                           "listen to: {reason}").format(reason=e))
            continue
        finally:
            for path in mono:
                path.unlink(missing_ok=True)
        out[n] = replace(take, path=spliced)
        if used is not None:
            used[n] = 0
        notes.append(_("The microphone heard the computer's sound (loudspeakers) in {share} of the recording: "
                       "in the copy to listen to it was taken out there, so the others are not heard twice.").format(
            share=f"{echo.share(parts) * 100:.0f} %"))
    return out, used, notes


def mix(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None) -> Path | None:
    """The mix to listen to, `<stem>_<together>.m4a` (a free name); None
    when every track is silent."""
    measured = [(t, ffmpeg.measure_loudness(t.path, 0, cancelled)) for t in takes]
    audible = [(t, m) for t, m in measured if not loudness.silent(m)]
    if not audible:
        return None
    check(cancelled)
    tmp = files.temporary_name(folder, ".m4a")
    try:
        _run(mix_arguments(str(ffmpeg.need("ffmpeg")), [t for t, _m in audible], [m for _t, m in audible], str(tmp)),
             _("The mix to listen to could not be made."), cancelled)
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
    loudness.balance_gain("me", balance)
    source = Path(source)
    tracks = _balance_tracks(source, cancelled)
    work = Path(tempfile.mkdtemp(prefix="scriba-echo-"))
    try:
        tmp = files.temporary_name(folder, ".m4a")
        try:
            _balanced_mix([Take(str(t.index), t.role, t.title, source) for t in tracks], [t.index for t in tracks],
                          work, tmp, balance, cancelled)
            if ffmpeg.probe(tmp, cancelled).duration <= 0:
                raise Failure(_("The mix to listen to could not be made."), "the mix has no length")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return files.claim(tmp, folder, f"{stem}_{_('balanced')}", ".m4a")


PREVIEW_RATE = 48000        # the mix's own (aresample=48000 in mix_arguments)


def balance_preview(source: str | os.PathLike, start: float, length: float, balance: float, folder: Path,
                    cancelled: threading.Event | None = None) -> Path:
    """A few seconds of the listening copy that rebalance() would make with
    this balance, as a WAV in `folder` (a temporary folder of the caller's;
    the recording is only read): each track's stretch cut at the mix's
    rate, then the same road (the microphone without the computer's sound
    where it heard it, each track levelled, the balance, the limiter).

    One difference, chosen: the tracks are levelled on the stretch, not on
    the whole recording. Measuring the whole of a meeting of almost two
    hours, the microphone cleaned first, takes minutes (the stop's mix:
    171 s for 108 minutes), too long for a preview asked for again at every
    move of the slider; on a stretch where both sides speak the measure of
    speech comes out close to the whole one's (EBU R128 leaves silence
    out of its measure)."""
    loudness.balance_gain("me", balance)
    source = Path(source)
    tracks = _balance_tracks(source, cancelled)
    cut = [Take(str(t.index), t.role, t.title,
                ffmpeg.decode(source, folder / f"track{t.index}.wav", cancelled=cancelled, start=start,
                              length=length, stream=t.index, rate=PREVIEW_RATE))
           for t in tracks]
    work = folder / "work"
    work.mkdir(exist_ok=True)
    dest = folder / "preview.wav"
    try:
        _balanced_mix(cut, None, work, dest, balance, cancelled, wav=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for take in cut:
            take.path.unlink(missing_ok=True)
    return dest


def _balance_tracks(source: Path, cancelled: threading.Event | None) -> tuple[ffmpeg.Track, ...]:
    if not source.is_file():
        raise Failure(_("The original recording is missing: a new listening copy cannot be made."), str(source))
    tracks = ffmpeg.probe(source, cancelled).roles
    if {t.role for t in tracks} != {"me", "others"}:
        raise Failure(_("A listening balance needs separate microphone and output tracks."), "no independent roles")
    return tracks


def _balanced_mix(takes: list[Take], streams: list[int] | None, work: Path, dest: Path, balance: float,
                  cancelled: threading.Event | None, wav: bool = False) -> None:
    """The listening copy's road after the stop and at a new balance: the
    microphone without the computer's sound where it heard it, as in the
    first copy (finish), every audible track levelled, the balance."""
    takes, streams, _notes = without_echo(takes, work, streams, cancelled)
    indexes = streams if streams is not None else [0] * len(takes)
    audible = []
    for take, index in zip(takes, indexes, strict=True):
        check(cancelled)
        measure = ffmpeg.measure_loudness(take.path, index, cancelled)
        if not loudness.silent(measure):
            audible.append((take, measure, index))
    if not audible:
        raise Failure(_("Nothing was heard on any track: a new listening copy cannot be made."), "silent tracks")
    _run(mix_arguments(str(ffmpeg.need("ffmpeg")), [t for t, _m, _i in audible], [m for _t, m, _i in audible],
                       str(dest), balance, [i for _t, _m, i in audible] if streams is not None else None, wav=wav),
         _("The mix to listen to could not be made."), cancelled)


def off_notes(takes: list[Take]) -> list[str]:
    """Where Scriba's switch had a track off, in words: there is silence
    there on purpose, and nothing to transcribe."""
    out = []
    for take in takes:
        for start, end in take.off:
            text = _("{track}: switched off in Scriba from {start} to {end}, silence on its track.") \
                if end is not None else _("{track}: switched off in Scriba from {start} to the end, silence on its "
                                          "track.")
            out.append(text.format(track=title(take), start=clock(start), end=clock(end or 0.0)))
    return out


def muted_notes(takes: list[Take]) -> list[str]:
    """Where the user was muted in the call, in words: the sound is there,
    the transcript leaves the microphone's speech out."""
    out = []
    for take in takes:
        for start, end in take.muted:
            text = _("{track}: muted in the call from {start} to {end}; what the microphone heard there stays in the "
                     "recording but not in the transcript.") if end is not None else \
                _("{track}: muted in the call from {start} to the end; what the microphone heard there stays in the "
                  "recording but not in the transcript.")
            out.append(text.format(track=title(take), start=clock(start), end=clock(end or 0.0)))
    return out


def finish(takes: list[Take], folder: Path, stem: str, cancelled: threading.Event | None = None,
           progress: Callable[[str], None] | None = None) -> Finished:
    """The recording made whole (see the module's docstring). Never raises
    for a failure of ffmpeg's: the FLAC files then stay, and the note says
    why; Cancelled stops it with the FLAC files untouched. `progress` is
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
    said(JOINING)
    try:
        joined = join(kept, folder, stem, cancelled)
    except Failure as e:
        notes.append(_("{reason} The tracks are kept as separate files.").format(reason=e))
        return Finished(first.path, None, kept, dropped, notes + off_notes(kept) + muted_notes(kept))
    together = None
    work = Path(tempfile.mkdtemp(prefix="scriba-echo-"))
    try:
        heard = kept
        if hears_and_plays(kept):
            said(CLEANING)
            heard, _streams, cleaned = without_echo(kept, work, cancelled=cancelled)
            notes += cleaned
        said(MIXING)
        try:
            together = mix(heard, folder, stem, cancelled)
        except Failure as e:
            notes.append(str(e))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for take in dropped:
        notes.append(_("{device}: silent from start to end, not kept.").format(device=take.device))
    notes += off_notes(kept) + muted_notes(kept)
    for take in kept + dropped:
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
