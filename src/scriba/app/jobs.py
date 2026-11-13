"""The chain that makes a transcript out of a recording, without a window.

decode (ffmpeg: whatever the user has, to WAV 16 kHz mono in a temporary
folder) -> speech (Silero: where somebody speaks) -> transcribe (whisper,
given the speech only, its times brought back to the recording's) ->
speakers (diarize's way, when asked) -> write (the transcript in Markdown
and plain text, the subtitles, the meeting's own file) -> video (a copy
of a video with the subtitles inside, when asked and when the file is a
video). The lines that may not be what was said are marked
(core/segments.py, flag_doubtful), never dropped.

It is trascrivi.py's chain, and its outputs (the same names, the same
lines), with three differences the tests of 11/11/2026 asked for: the
speech filter is a step of its own (whisper-cli's costs a minute and a
half on the reference clip), the voices are found without PyTorch,
and the doubtful lines are said.

A recording of Scriba's own has its tracks apart (the microphone, which
is the user, and the computer's output, which is the others: Request.
tracks). Each track is then decoded, filtered and transcribed on its
own, and the lines are merged in the order of time (core/segments.py,
merge): the microphone's lines are one voice, the user's (Result.me),
never told apart from the others by ear; the output's voices are told
apart among themselves when asked, each track's labels kept apart from
the other tracks' (prefix_speakers) before the voices are numbered. A
microphone that heard the loudspeakers has the computer's sound taken out
first, where it heard it (_without_echo), so that the others' words are
not written twice, the second time as the user's.

The work runs in the caller's thread (the window gives it one): Progress
says which step is going and how far, a threading.Event stops it between
two pieces of work (the engines look at it: a child process is terminated
at once), and what was made in between goes with the temporary folder.
What reaches the user's folder appears whole or not at all, and never in
the place of a file that is there (data/files.py). No toolkit here.
"""

from __future__ import annotations

import datetime
import json
import math
import os
import shutil
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from scriba import PACKAGE_DIR, WINDOWS, __version__
from scriba.core import callmute, estimate, loudness, segments as seg, speakers, timeline, words
from scriba.core.paragraphs import paragraphs
from scriba.core.subtitles import subtitles as cut_subtitles
from scriba.data import files, markdown, session as sessions, srt, txt
from scriba.data.timing import clock
from scriba.i18n import _

from . import models
from scriba.app.engines import vad
from scriba.app.engines.ffmpeg import Track
from .task import Failure, check

STAGES = ("decode", "speech", "transcribe", "speakers", "write", "video")


def stage_names() -> dict[str, str]:
    """Each step in the user's words, for the window and the command line."""
    return {
        "decode": _("Reading the audio"),
        "speech": _("Finding the speech"),
        "transcribe": _("Transcribing"),
        "speakers": _("Telling the voices apart"),
        "write": _("Writing the files"),
        "video": _("Making the video with subtitles"),
    }
# how much of the work each step is, for the overall bar, before any
# measure: on the test clip (11/11/2026) whisper on the card took 36 s,
# the voices 26 s, decoding and the filter a second or two each
WEIGHTS = {"decode": 2.0, "speech": 2.0, "transcribe": 40.0, "speakers": 30.0, "write": 0.5, "video": 3.0}
SPEEDS_FILE = "speeds.json"
# Precise (large-v3) before it ever ran on a device: as slow as Fast
# (large-v3-turbo, timed by the test of the cards) by this factor. They
# share the encoder; Precise has 32 decoder layers to Fast's 4 and searches
# wider. On the RTX A2000 of the PC it was written on (speeds.json,
# 13/11/2026) Fast ran at 7.3 times real time and Precise at 8.4: the
# encoder is most of the work on a card. On a processor the decoder weighs
# more [I]. Twice as slow keeps the first estimate on the safe side without
# the old default (as long as the audio), which said about 40 minutes for a
# work done in under 10.
PRECISE_SLOWER = 2.0
# the silence between two stretches of speech given to whisper, measured on
# the test clip (11/11/2026, turbo on the card): glued with none, whisper
# lost the first sentence and wrote "cavelli" for "cavedi" seven times (the
# whole clip: 11 "cavedi"); with 0.2 s, 11 "cavedi", the first sentence, 1143
# words against 1170 on the whole clip, in 31.5 s against 33.0
GAP = 0.2
# The loudness of what the engines hear brought to EBU R128 first (one gain
# per track, core/loudness.py)? Tests on quiet speech showed worse text. The faint first minute and a half
# (-46 LUFS, raised by 22 dB) came out as loops whisper did not write
# without it ("per esempio il docente" over and over), and the whole file
# gave 145 lines of which 30 doubtful, against 113 and 17 as recorded. The
# step stays, off, for the next measures (scriba transcribe --normalise)
NORMALISE = False
# the label the microphone's lines take before the voices are numbered
ME = "me"


@dataclass(frozen=True)
class Request:
    source: Path
    out: Path | None = None             # None: next to the recording (see output_folder)
    language: str = "it"                # "it", "en", ... or "auto"
    model: str = "turbo"                # a key of models.WHISPER
    terms: tuple[str, ...] = ()         # names and acronyms whisper should write right
    speakers: bool = False
    speaker_count: int | None = None    # None: found by the engine
    video: bool = False                 # also a copy of the video with the subtitles inside
    cpu: bool = False                   # never the graphics card
    device: str = ""                    # the card to use, by name; "": the best one found
    speech_filter: vad.Settings = vad.FOR_SPEECH
    tracks: tuple[Track, ...] = ()      # each track on its own (see the docstring); (): as the file says
    my_name: str = "Io"                 # the microphone's name, written in the first files
    normalise: bool = NORMALISE         # the engines hear each track at the same loudness
    # Scriba's folder for the recordings, for a file kept elsewhere (an OBS
    # video): with `out` None the files go in a folder of its own there,
    # with a link to the file (home_folder, link_to); None: output_folder
    home: Path | None = None


class Progress:
    """Where the work is: the step, how far into it (0 to 1), and what the
    engine is doing at the moment ("loading" while the model goes into the
    card's memory). Written by the worker's thread, read by any other: the
    window reads it from an idle callback, the command line prints it.
    `on_change` is called in the worker's thread after every change."""

    def __init__(self, stages=STAGES, on_change: Callable[[Progress], None] | None = None) -> None:
        self._lock = threading.Lock()
        self.stages = tuple(stages)
        self.on_change = on_change
        self.stage: str | None = None
        self.fraction = 0.0
        self.state = ""
        self.finished: dict[str, float] = {}        # step -> seconds it took
        self.skipped: set[str] = set()
        self._began = 0.0

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    def begin(self, stage: str) -> None:
        with self._lock:
            self.stage, self.fraction, self.state, self._began = stage, 0.0, "", time.perf_counter()
        self._changed()

    def advance(self, done: float, total: float) -> None:
        with self._lock:
            self.fraction = min(max(done / total, 0.0), 1.0) if total else 0.0
        self._changed()

    def say(self, state: str) -> None:
        with self._lock:
            self.state = state
        self._changed()

    def finish(self) -> float:
        with self._lock:
            seconds = time.perf_counter() - self._began
            if self.stage is not None:
                self.finished[self.stage] = seconds
            self.fraction = 1.0
        self._changed()
        return seconds

    def skip(self, stage: str) -> None:
        with self._lock:
            self.skipped.add(stage)
        self._changed()

    def snapshot(self) -> dict:
        """Everything at one moment, copied under the lock: what the window
        shows while the worker goes on changing it."""
        with self._lock:
            return {"stages": self.stages, "stage": self.stage, "fraction": self.fraction, "state": self.state,
                    "finished": dict(self.finished), "skipped": set(self.skipped)}

    def overall(self) -> float:
        """The whole work done, 0 to 1, each step weighed by WEIGHTS."""
        with self._lock:
            counted = [s for s in self.stages if s not in self.skipped]
            total = sum(WEIGHTS.get(s, 1.0) for s in counted) or 1.0
            done = sum(WEIGHTS.get(s, 1.0) for s in counted if s in self.finished)
            if self.stage in counted and self.stage not in self.finished:
                done += WEIGHTS.get(self.stage, 1.0) * self.fraction
            return done / total


@dataclass
class Result:
    outputs: list[sessions.Output]
    session: Path
    segments: list[seg.Segment]
    speakers: int
    duration: float
    device: str                         # where whisper ran: "Vulkan: <card>" or "cpu"
    timings: dict[str, float]
    notes: list[str] = field(default_factory=list)      # sentences for the user: a fallback, a step skipped
    source: Path | None = None          # the recording
    folder: Path | None = None          # where the files went
    stem: str = ""                      # the name they share (call_transcript, call_transcript_R01...)
    header: markdown.Header | None = None   # what the transcripts' header says: the named copies repeat it
    me: str = ""                        # the user's voice (the microphone's track), once numbered; "": not known
    listen: Path | None = None          # what to play to know a voice: the recording's mix, or the recording
    names: dict[str, str] = field(default_factory=dict)


def full_path(path: str | os.PathLike) -> str:
    """A path whole and as the system writes it, with backslashes on
    Windows: the header of a transcript names the recording so, to be
    pasted into Explorer. MSYS2's Python writes "/" there (its os.sep is
    "/", with MSYSTEM set or not: measured 11/11/2026), so the separators
    are put right by hand."""
    full = os.path.normpath(os.path.abspath(path))
    return full.replace("/", "\\") if os.name == "nt" else full


def stages_for(request: Request) -> list[str]:
    """The steps the work of `request` goes through, in order."""
    return [s for s in STAGES if (s != "speakers" or request.speakers) and (s != "video" or request.video)]


def output_folder(source: Path, out: Path | None) -> Path:
    """Where the files go: the folder asked for, else next to the
    recording, unless the recording is on another computer (a server's
    share: everybody's), where the current folder is used instead."""
    if out is not None:
        return Path(out)
    if WINDOWS:
        from . import win32
        if win32.on_network(str(source)):
            return Path.cwd()
    return Path(source).resolve().parent


def elsewhere(source: str | os.PathLike, recordings: str | os.PathLike) -> bool:
    """The file is outside Scriba's folder for the recordings (an OBS video,
    a phone's recording): its transcripts may go in a folder of their own
    there (home_folder) instead of next to it."""
    try:
        Path(source).resolve().relative_to(Path(recordings).resolve())
    except ValueError:
        return True
    return False


def home_folder(source: str | os.PathLike, recordings: str | os.PathLike) -> Path:
    """The folder in `recordings` for the transcripts of a file kept
    elsewhere: named as the file, the one whose meeting files are of this
    very file when there is one (a new transcription goes with the ones
    before), else the first free name (`name_R01`...)."""
    source, recordings = Path(source), Path(recordings)
    wanted = full_path(source)
    for n in range(files.MAX_REVISIONS + 1):
        folder = recordings / (source.stem if n == 0 else f"{source.stem}_R{n:02d}")
        if not os.path.lexists(folder):
            return folder
        if folder.is_dir() and any(_meeting_of(path) == wanted for path in folder.glob("*.json")):
            return folder
    raise FileExistsError(f"{recordings / source.stem}: {files.MAX_REVISIONS} folders already there")


def _meeting_of(path: Path) -> str:
    """The recording a meeting file is of; "" when it is not one or cannot be read."""
    try:
        return sessions.load(path).source
    except sessions.SessionError:
        return ""


def link_to(source: Path, folder: Path) -> list[str]:
    """A link to the file in the folder of its transcripts, under its own
    name, unless there is one already; what to tell the user when it cannot
    be made. On Windows a shortcut (.lnk, made by the shell: it follows the
    file's name in any language, and Explorer opens the file with it), a
    symbolic link elsewhere. Never a copy: the original stays where it is,
    and the meeting file names it too."""
    try:
        if WINDOWS:
            link = folder / f"{source.name}.lnk"
            if not os.path.lexists(link):
                from . import win32
                win32.shortcut(full_path(source), full_path(link))
        else:
            link = folder / source.name
            if not os.path.lexists(link):
                os.symlink(os.path.abspath(source), link)
    except OSError as e:
        return [_("The link to the original file could not be made ({reason}): it is at {path}.").format(
            reason=e, path=full_path(source))]
    return []


def transcript_stem(source: str | os.PathLike) -> str:
    """The name the files of a transcript share: "<recording>_trascrizione"."""
    return f"{Path(source).stem}_{_('transcript')}"


def revisions_folder() -> str:
    return _("Previous revisions")


def _previous_to_revisions(folder: Path, source: Path) -> list[str]:
    """A new transcript of the same audio takes the place of the one before
    in view; the one before (its automatic files, those with the names, its
    meeting files) goes to the folder of the previous revisions with
    `_R01`, `_R02`... Nothing is deleted or replaced. When a file cannot be
    moved (open in another program) the set stays where it is, the new one
    takes the next free name beside it, and that is said."""
    try:
        files.to_revisions(folder, transcript_stem(source), ("", f"_{_('names')}", f"_{_('balanced')}"),
                           (".md", ".txt", ".srt", ".json"), revisions_folder())
    except OSError as e:
        return [_("The previous transcript could not be moved to “{folder}” ({reason}): the new one is next to "
                  "it, under another name.").format(folder=revisions_folder(), reason=e)]
    return []


def needed_models(request: Request) -> list[models.Model]:
    out = [models.WHISPER[request.model], models.SILERO_VAD]
    if request.speakers:
        out.append(models.WESPEAKER)
    return out


def speed_key(model: str, device: str) -> str:
    return f"{model}|{device}"


def load_speeds(path: Path | None) -> dict[str, float]:
    """The speeds measured on this computer (step and engine -> seconds of
    audio per second of work); {} when there are none or the file is not
    readable (a measure lost costs one estimate, never the work)."""
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, (int, float)) and v > 0} \
        if isinstance(data, dict) else {}


def remember_speeds(path: Path | None, measured: dict[str, float]) -> None:
    if path is None or not measured:
        return
    speeds = load_speeds(path)
    speeds.update(measured)
    path.parent.mkdir(parents=True, exist_ok=True)
    files.replace(path, json.dumps(speeds, indent=2, sort_keys=True) + "\n")


def model_speed(model: str, device: str, speeds: dict[str, float]) -> tuple[float | None, bool]:
    """The speed of whisper's `model` on `device`, and whether it was
    measured there. A model never run on a device takes the other model's
    speed there (the test of the cards times Fast only), Precise at
    1 / PRECISE_SLOWER of it; None when neither was measured."""
    own = speeds.get(speed_key(model, device))
    if own:
        return own, True
    other = {"large": "turbo", "turbo": "large"}.get(model)
    known = speeds.get(speed_key(other, device)) if other else None
    if not known:
        return None, False
    return (known / PRECISE_SLOWER if model == "large" else known), False


def estimated_seconds(duration: float, request: Request, device: str, speeds: dict[str, float]) -> float:
    """How long the work will take for `duration` seconds of audio, from
    the speeds measured last time on this computer (core/estimate.py)."""
    total = estimate.work_time(duration, model_speed(request.model, device, speeds)[0])
    if request.speakers:
        total += estimate.work_time(duration, speeds.get(speed_key("speakers", "cpu")), default=20.0)
    return total


def first_estimate(request: Request, device: str, speeds: dict[str, float]) -> bool:
    """The estimate rests on no measure of this very work on this computer:
    the model never ran on the device, or the voices were never told apart."""
    return not model_speed(request.model, device, speeds)[1] or (
        request.speakers and not speeds.get(speed_key("speakers", "cpu")))


def _pick_device(request: Request, cancelled: threading.Event | None):
    from scriba.app.engines import whisper
    if request.cpu:
        return None
    return whisper.choose(whisper.list_devices(cancelled=cancelled), request.device)


def run(request: Request, progress: Progress | None = None, cancelled: threading.Event | None = None,
        models_dir: str | os.PathLike | None = None, speeds_file: Path | None = None) -> Result:
    """The transcript of `request.source`, written where output_folder says.
    Failure (a sentence to show) when a model or a program is missing or an
    engine fails; Cancelled when `cancelled` is set. Either way the
    temporary folder is gone, and no file of the user's was touched."""
    from scriba.app.engines import ffmpeg, whisper

    progress = progress or Progress()
    source = Path(request.source)
    if not source.is_file():
        raise Failure(_("{name} is not there.").format(name=source.name), str(source))
    if request.model not in models.WHISPER:
        raise Failure(_("Unknown model {model}: turbo or large.").format(model=request.model), request.model)
    lacking = models.missing(needed_models(request), models_dir)
    if lacking:
        raise Failure(_("Models to download first: {names}.").format(names=", ".join(m.filename for m in lacking)),
                      str(models.folder() if models_dir is None else models_dir))
    notes: list[str] = []
    if request.home is not None and request.out is None:
        folder = home_folder(source, request.home)
        folder.mkdir(parents=True, exist_ok=True)
        notes += link_to(source, folder)
    else:
        folder = output_folder(source, request.out)
        folder.mkdir(parents=True, exist_ok=True)
    stages = stages_for(request)
    progress.stages = tuple(stages)
    timings: dict[str, float] = {}
    work = Path(tempfile.mkdtemp(prefix="scriba-job-"))
    began = time.perf_counter()
    try:
        progress.begin("decode")
        media = ffmpeg.probe(source, cancelled)
        if media.sound_tracks == 0:
            raise Failure(_("{name} has no sound track.").format(name=source.name), str(source))
        # the tracks asked for, or those the recording says are whose (Scriba's own)
        tracks = [t for t in (request.tracks or media.roles) if t.index < media.sound_tracks]
        parts = [_Part(t) for t in tracks] if tracks else [_Part(Track(0))]
        request = replace(request, tracks=tuple(tracks))
        echo_check = _hears_and_plays(parts)
        slots = len(parts) + echo_check
        wavs = [ffmpeg.decode(source, work / f"audio{n}.wav", media.duration, _within(progress, n, slots),
                              cancelled, stream=part.track.index) for n, part in enumerate(parts)]
        if echo_check:
            wavs, said = _without_echo(parts, wavs, work, _within(progress, len(parts), slots), cancelled)
            notes += said
        for part, wav in zip(parts, wavs, strict=True):
            _prepare(part, wav, request.normalise, cancelled)
        duration = max(part.duration for part in parts)
        timings["decode"] = progress.finish()

        progress.begin("speech")
        for n, part in enumerate(parts):
            _find_speech(part, work / f"speech{n}.wav", request.speech_filter, models_dir,
                         _within(progress, n, len(parts)), cancelled)
            if part.left_out > 0:
                notes.append(_("{time} of speech on your microphone fell where you were muted in the call: it was "
                               "not transcribed, and its sound stays in the recording.").format(
                    time=clock(part.left_out)))
        timings["speech"] = progress.finish()

        progress.begin("transcribe")
        device = _pick_device(request, cancelled)
        language, language_probability, used = request.language, None, "cpu"
        model = models.WHISPER[request.model]
        for n, part in enumerate(parts):
            if not part.pieces:
                continue
            options = whisper.Options(models.path_of(model, models_dir), request.language, tuple(request.terms),
                                      4 if device is not None else whisper.processor_threads(), device,
                                      precise=model is models.WHISPER_LARGE)
            transcript = whisper.transcribe(work / f"speech{n}.wav", options, work, _within(progress, n, len(parts)),
                                            cancelled, progress.say)
            if transcript.fallback:
                note = _("The graphics card did not work: the text was made on the processor (slower).")
                if note not in notes:
                    notes.append(note)
                device = None           # the next track straight on the processor
            used = transcript.device.label if transcript.device is not None else "cpu"
            if language_probability is None:
                language = transcript.language or request.language
                language_probability = transcript.language_probability
            part.lines = _brought_back(part, transcript.segments)
        if not any(part.pieces for part in parts):
            notes.append(_("No speech was found in the recording."))
        timings["transcribe"] = progress.finish()

        if request.speakers:
            progress.begin("speakers")
        lines, me = _voices(parts, request, models_dir, progress, cancelled)
        voices = len(seg.speakers_of(lines)) if request.speakers or len(parts) > 1 else 0
        if request.speakers:
            timings["speakers"] = progress.finish()
        lines = seg.flag_doubtful(lines)

        check(cancelled)
        progress.begin("write")
        notes += _previous_to_revisions(folder, source)
        names = {me: " ".join(request.my_name.split()) or "Io"} if me else {}
        outputs, stem, header = _write(request, source, folder, duration, language, language_probability, used,
                                       lines, voices, names)
        timings["write"] = progress.finish()

        if request.video:
            progress.begin("video")
            if media.video:
                subtitles = next(o.path for o in outputs if o.kind == "srt")
                copy = ffmpeg.subtitled_copy(source, subtitles, folder, f"{source.stem}_{_('subtitles')}", language,
                                             media, cancelled)
                outputs.append(sessions.Output("video", full_path(copy)))
            else:
                progress.skip("video")
                notes.append(_("The file has no video track: no video with subtitles was made."))
            timings["video"] = progress.finish()

        timings["total"] = time.perf_counter() - began
        result = Result(outputs, folder / f"{stem}.json", lines, voices, duration, used, timings, notes,
                        source, folder, stem, header, me, listening_copy(source), names)
        result.session = _save_session(result, request)
        measured = {}
        if any(part.pieces for part in parts) and estimate.speed(duration, timings["transcribe"]):
            measured[speed_key(request.model, used)] = estimate.speed(duration, timings["transcribe"])
        if request.speakers and estimate.speed(duration, timings["speakers"]):
            measured[speed_key("speakers", "cpu")] = estimate.speed(duration, timings["speakers"])
        remember_speeds(speeds_file, measured)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


class _Part:
    """One track of the recording on its way through the chain."""

    def __init__(self, track: Track) -> None:
        self.track = track
        self.samples = None         # as recorded, 16 kHz: the lines' levels are measured on these
        self.heard = None           # what the engines hear: the same, at the target loudness when asked
        self.gain = 0.0             # dB given to `heard`
        self.pieces: list = []      # the speech found, as timeline.Piece
        self.left_out = 0.0         # seconds of the microphone's speech left out, muted in the call
        self.lines: list[seg.Segment] = []

    @property
    def duration(self) -> float:
        from scriba.app.engines import audio
        return audio.seconds(self.samples) if self.samples is not None else 0.0


def _within(progress: Progress, n: int, count: int):
    """A step's progress for the n-th of `count` tracks: the bar goes once
    across the step, whatever the number of tracks."""
    def advance(done: float, total: float) -> None:
        progress.advance(n + (done / total if total else 0.0), count)
    return advance


def _hears_and_plays(parts: list[_Part]) -> bool:
    """A microphone's track and a computer's: the microphone may have heard
    the computer (core/echo.py)."""
    return {part.track.role for part in parts} >= {"me", "others"}


def _without_echo(parts: list[_Part], wavs: list[Path], work: Path, advance,
                  cancelled: threading.Event | None) -> tuple[list[Path], list[str]]:
    """Each microphone's track with the computer's sound taken out where it
    heard it (core/echo.py says where, app/engines/echo.py takes it out),
    the raw track elsewhere; the decoded files to use, and what to tell the
    user. The others' lines then come from the computer's track only, and
    the microphone's stay the user's. Without the canceller, or when it
    fails, the raw track is used and that is said."""
    from scriba.app.engines import audio, echo as canceller
    from scriba.core import echo

    mics = [n for n, part in enumerate(parts) if part.track.role == "me"]
    computer = [audio.read(wav) for part, wav in zip(parts, wavs, strict=True) if part.track.role == "others"]
    out, notes = list(wavs), []
    for k, n in enumerate(mics):
        def step(done, total, k=k):
            advance(k + (done / total if total else 0.0), len(mics))
        try:
            pair = canceller.write_pair(work / f"echo{n}-pair.wav", audio.read(wavs[n]), computer, audio.RATE)
            windows, duration = canceller.measure(pair, lambda done, total: step(done, 2 * total), cancelled)
            stretches = echo.stretches(windows, duration)
            heard = echo.echo_seconds(stretches)
            if heard <= 0:
                continue
            cleaned = canceller.cancel(pair, work / f"echo{n}-cleaned.wav",
                                       lambda done, total: step(total + done, 2 * total), cancelled)
            out[n] = canceller.splice(pair, cleaned, stretches, work / f"audio{n}-without-echo.wav", cancelled)
        except (Failure, OSError, EOFError, wave.Error) as e:
            notes.append(_("The computer's sound heard by the microphone could not be taken out: {reason} Your "
                           "track was transcribed as recorded, and the others' words may be written as yours.").format(
                reason=e))
            continue
        notes.append(_("The microphone heard the computer's sound (loudspeakers) in {share} of the recording "
                       "({time}): there it was taken out of your track before transcribing, so the others' words "
                       "come from the computer's track only.").format(share=percent(echo.share(stretches)),
                                                                       time=clock(heard)))
    return out, notes


def _prepare(part: _Part, wav: Path, normalise: bool, cancelled: threading.Event | None) -> None:
    """The track's samples, and the copy the engines hear: brought to EBU
    R128's loudness by one gain (measured by ffmpeg on the decoded track),
    under the ceiling for its peaks."""
    from scriba.app.engines import audio, ffmpeg
    part.samples = audio.read(wav)
    part.heard = part.samples
    if not normalise or len(part.samples) == 0:
        return
    part.gain = loudness.linear_gain(ffmpeg.measure_loudness(wav, 0, cancelled))
    if abs(part.gain) >= 0.1:
        import numpy as np
        louder = part.samples.astype(np.float32) * np.float32(10 ** (part.gain / 20))
        part.heard = np.clip(np.round(louder), -32768, 32767).astype(np.int16)


def _find_speech(part: _Part, dest: Path, settings: vad.Settings, models_dir, advance,
                 cancelled: threading.Event | None) -> None:
    """Where somebody speaks in the track, and that speech alone in `dest`
    for whisper, the pieces apart by GAP seconds of silence. On the
    microphone's track the stretches muted in the call are left out (the
    sound stays in the recording): what it heard there was not said in the
    call. A stretch switched off in Scriba needs nothing: it is digital
    silence, where no speech is found."""
    from scriba.app.engines import audio
    found = vad.detect(part.heard, models.path_of(models.SILERO_VAD, models_dir), settings, advance, cancelled)
    speech = vad.seconds(found, len(part.heard), settings)
    if part.track.role == "me" and part.track.muted:
        part.left_out = callmute.taken(speech, part.track.muted)
        speech = callmute.outside(speech, part.track.muted)
    part.pieces = timeline.pack(speech, GAP)
    if part.pieces:
        import numpy as np
        rate = audio.RATE
        silence = np.zeros(round(GAP * rate), np.int16)
        joined = []
        for p in part.pieces:
            joined += [part.heard[round(p.start * rate):round(p.end * rate)], silence]
        audio.write(dest, np.concatenate(joined[:-1]))


def _brought_back(part: _Part, segments) -> list[seg.Segment]:
    """whisper's lines with the recording's times, and the level each had
    as recorded (before any gain: QUIET is about the recording)."""
    from scriba.app.engines import audio
    lines = []
    for s, (start, end) in zip(segments, timeline.spans(part.pieces, [(s.start, s.end) for s in segments]),
                               strict=True):
        lines.append(replace(s, start=start, end=end, level=audio.level(part.samples, start, end),
                             words=timeline.words(part.pieces, s.words, start, end)))
    return seg.spoken(lines)


def _voices(parts: list[_Part], request: Request, models_dir, progress: Progress,
            cancelled: threading.Event | None) -> tuple[list[seg.Segment], str]:
    """All the lines, in the order of time, with their voices numbered, and
    the label the user's voice got ("" when no track is the user's).

    One track: the voices told apart by ear when asked (as trascrivi.py).
    Several: the microphone's track is the user, one voice, whatever the
    voice engine would say of it (a headset hears its wearer; a voice of
    the room heard by the microphone is the one case this gets wrong);
    each output track has its voices told apart when asked (the people in
    the call, as many as asked less the user), or is one voice when not."""
    if len(parts) == 1:
        if parts[0].track.role == "me":
            return [replace(s, speaker="Speaker 1") for s in parts[0].lines], "Speaker 1"
        lines = parts[0].lines
        if request.speakers:
            from scriba.app.engines import diarize
            result = diarize.diarize(parts[0].samples, models.path_of(models.SILERO_VAD, models_dir),
                                     models.path_of(models.WESPEAKER, models_dir), request.speaker_count,
                                     progress=progress.advance, cancelled=cancelled)
            lines = seg.number_speakers(seg.assign_speakers(lines, result.turns))
        return lines, ""
    mine = any(part.track.role == "me" for part in parts)
    others = [part for part in parts if part.track.role != "me"]
    count = request.speaker_count
    if count and mine:
        count = max(count - 1, 1)
    merged: list[seg.Segment] = []
    for n, part in enumerate(parts):
        check(cancelled)
        if part.track.role == "me":
            lines = [replace(s, speaker=ME) for s in part.lines]
        elif request.speakers and part.lines and count != 1:
            from scriba.app.engines import diarize
            k = others.index(part)
            result = diarize.diarize(part.samples, models.path_of(models.SILERO_VAD, models_dir),
                                     models.path_of(models.WESPEAKER, models_dir),
                                     count if len(others) == 1 else None,
                                     progress=_within(progress, k, len(others)), cancelled=cancelled)
            lines = seg.prefix_speakers(seg.assign_speakers(part.lines, result.turns), f"{n}:")
        else:
            lines = [replace(s, speaker=f"{n}:") for s in part.lines]
        merged = seg.merge(merged, lines)
    numbered = seg.number_speakers(merged)
    me = next((after.speaker for before, after in zip(merged, numbered, strict=True) if before.speaker == ME), "")
    return numbered, me


def listening_copy(source: str | os.PathLike) -> Path:
    """What to play of a recording: the mix of its tracks that the recorder
    wrote next to it (app/recorder.py, "<name>_together.m4a" in the user's
    language), or the recording itself."""
    source = Path(source)
    mix = source.with_name(f"{source.stem}_{_('together')}.m4a")
    return mix if mix.is_file() else source


def _write(request: Request, source: Path, folder: Path, duration: float, language: str,
           language_probability: float | None, device: str, lines: list[seg.Segment],
           voices: int, names: dict[str, str] | None = None) -> tuple[list[sessions.Output], str, markdown.Header]:
    """The md, txt and srt under one free name (the meeting's own file
    takes it too, at the end); the outputs, the name and the header."""
    stem = files.free_stem(folder, transcript_stem(source), (".md", ".txt", ".srt", ".json"))
    model = models.WHISPER[request.model]
    header = markdown.Header(source=full_path(source), duration=duration, language=language,
                             language_probability=language_probability,
                             model=model.key.removeprefix("whisper-"), device=device,
                             when=datetime.datetime.now(), terms=tuple(request.terms),
                             speakers_engine="diarize" if request.speakers else "")
    # "Parlante 2" in an Italian file, as trascrivi.py wrote it; the
    # subtitles keep their short tags (P2)
    shown = speakers.display_names(seg.speakers_of(lines), names or {})
    # the files in paragraphs, the subtitles cut to the conventions' size
    outputs = [sessions.Output("md", full_path(markdown.write(folder, stem, header, lines, shown, paragraphs))),
               sessions.Output("txt", full_path(txt.write(folder, stem, header, lines, shown, paragraphs))),
               sessions.Output("srt", full_path(srt.write(folder, stem, speakers.rename(lines, names or {}),
                                                          speakers=bool(voices), cut=cut_subtitles)))]
    return outputs, stem, header


def _snapshot(result: Result) -> dict:
    """Original identities and full confidence details, independent of the audio and engines."""
    header = asdict(result.header)
    header["when"] = result.header.when.isoformat() if result.header.when else None
    return {"segments": [asdict(s) for s in result.segments], "header": header, "me": result.me,
            "listen": full_path(result.listen) if result.listen else "", "stem": result.stem,
            "notes": list(result.notes)}


def _save_session(result: Result, request: Request) -> Path:
    options = {"language": request.language, "model": request.model, "terms": list(request.terms),
               "speakers": request.speakers, "speaker_count": request.speaker_count, "video": request.video,
               "device": result.device, "normalise": request.normalise, "scriba": __version__,
               "my_name": request.my_name}
    if request.tracks:
        options["tracks"] = [{"index": t.index, "role": t.role, "title": t.title} for t in request.tracks]
    meeting = sessions.Session(source=full_path(result.source), duration=result.duration,
                               created=datetime.datetime.now().isoformat(timespec="seconds"), options=options,
                               outputs=list(result.outputs), names=dict(result.names),
                               timings={k: round(v, 3) for k, v in result.timings.items()},
                               extra={"transcript": _snapshot(result)})
    return files.write_new(result.folder, result.stem, ".json", sessions.dumps(meeting))


def _saved_words(item: dict) -> tuple[tuple[float, float], ...]:
    """A saved line's word times, or () when there are none (a meeting
    saved before 13/11/2026) or they do not fit its text: its subtitles
    then share the line's time, as for any line without them."""
    try:
        times = tuple((float(a), float(b)) for a, b in item.get("words") or ())
    except (TypeError, ValueError):
        return ()
    text = item.get("text")
    if (not isinstance(text, str) or len(times) != len(text.split())
            or not all(math.isfinite(a) and math.isfinite(b) for a, b in times)):
        return ()
    return times


def open_session(path: str | os.PathLike) -> tuple[Result, Request]:
    """Read a saved result without loading models or reading its recording.

    Sessions made before snapshots use their automatic subtitles. Names
    are never inverted: two engine voices may have the same human name.
    """
    path = Path(path).resolve()
    meeting = sessions.load(path)
    options = meeting.options
    source = Path(meeting.source)
    request = Request(source, language=options.get("language", "it"), model=options.get("model", "turbo"),
                      speakers=options.get("speakers", True), my_name=options.get("my_name", "Io"))
    try:
        snapshot = meeting.extra.get("transcript")
        if snapshot is not None:
            lines = []
            for item in snapshot["segments"]:
                item = {**item, "doubts": tuple(item.get("doubts", ())), "words": _saved_words(item)}
                line = seg.Segment(**item)
                if (not isinstance(line.text, str) or not isinstance(line.doubts, tuple)
                        or not all(isinstance(d, str) for d in line.doubts)
                        or (line.speaker is not None and not isinstance(line.speaker, str))
                        or line.start.__class__ not in (int, float) or not math.isfinite(line.start)
                        or line.end.__class__ not in (int, float) or not math.isfinite(line.end)
                        or line.start < 0 or line.end < line.start):
                    raise ValueError("invalid saved segment")
                lines.append(line)
            head = dict(snapshot["header"])
            head["when"] = datetime.datetime.fromisoformat(head["when"]) if head.get("when") else None
            head["terms"] = tuple(head.get("terms", ()))
            header = markdown.Header(**head)
            me, listen, stem = snapshot.get("me", ""), snapshot.get("listen", ""), snapshot["stem"]
            if not stem or stem in (".", "..") or any(c in stem for c in '/\\:\0'):
                raise ValueError("invalid saved stem")
            notes = list(snapshot.get("notes", []))
            if not all(isinstance(v, str) for v in (me, listen, stem, *notes)):
                raise ValueError("invalid saved result")
        else:
            automatic = next((o for o in meeting.outputs if o.kind == "srt"), None)
            if automatic is None:
                raise ValueError("no automatic subtitles")
            subtitle_path = Path(automatic.path)
            if not subtitle_path.is_absolute():
                subtitle_path = path.parent / subtitle_path
            lines = [seg.Segment(c.start, c.end, c.text, c.speaker, doubts=c.doubts)
                     for c in srt.read(subtitle_path)]
            header = markdown.Header(meeting.source, meeting.duration, language=request.language,
                                     model=request.model, device=options.get("device", ""),
                                     terms=tuple(options.get("terms", ())))
            me, listen, stem = "", str(listening_copy(source)), path.stem
            notes = [_("This older meeting was reopened from its automatic subtitles; detailed confidence "
                       "and the original header were not saved.")]
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as e:
        raise Failure(_("This meeting cannot be reopened: its saved transcript or automatic subtitles are "
                        "missing or damaged."), str(e)) from e
    result = Result(list(meeting.outputs), path, lines, len(seg.speakers_of(lines)), meeting.duration,
                    options.get("device", "cpu"), dict(meeting.timings), notes, source, path.parent,
                    stem, header, me, Path(listen) if listen else None, dict(meeting.names))
    return result, request


def default_folder(source: str | os.PathLike, documents: Path | None) -> Path:
    """Where the files go when the user chose no folder: next to the
    recording; for a recording on another computer (a server's share, which
    is everybody's), the user's documents folder, which the window gives."""
    if WINDOWS and documents is not None:
        from . import win32
        if win32.on_network(str(source)):
            return Path(documents)
    return Path(source).resolve().parent


@dataclass
class Named:
    outputs: list[sessions.Output]      # the new files
    notes: list[str] = field(default_factory=list)


NAMED = ("md-named", "txt-named", "srt-named")


def save_names(result: Result, names: dict[str, str]) -> Named:
    """The files with the names the user gave the voices (Markdown, plain
    text, subtitles), `<transcript>_nomi`, next to the automatic ones, which
    are never touched. Names are no new revision (13/11/2026): saved
    again, the files with the names are rewritten whole (each atomically)
    and the meeting file keeps the names in place. When the meeting file
    cannot be written the files are there all the same, and that is said.
    Failure when no voice was given a name."""
    names = speakers.clean(names)
    if not names and not result.names:
        raise Failure(_("Give a name to at least one voice first."), "no names")
    if result.folder is None or result.header is None:
        raise Failure(_("This transcript cannot be saved again."), "no folder or header in the result")
    folder = Path(result.folder)
    stem = f"{result.stem}_{_('names')}"
    header = replace(result.header, named=True)
    shown = speakers.display_names(seg.speakers_of(result.segments), names)
    renamed = speakers.rename(result.segments, names)
    texts = (("md-named", ".md", markdown.render(header, result.segments, shown, paragraphs)),
             ("txt-named", ".txt", txt.render(header, result.segments, shown, paragraphs)),
             ("srt-named", ".srt", srt.render(renamed, speakers=True, cut=cut_subtitles)))
    outputs = []
    for kind, ext, text in texts:
        files.replace(folder / f"{stem}{ext}", text)
        outputs.append(sessions.Output(kind, full_path(folder / f"{stem}{ext}")))
    notes = []
    try:
        meeting = sessions.load(result.session)
        meeting.names = dict(names)
        meeting.outputs = [*(o for o in meeting.outputs if o.kind not in NAMED), *outputs]
        meeting.extra["transcript"] = _snapshot(result)
        files.replace(result.session, sessions.dumps(meeting))
        result.names = dict(names)
    except (sessions.SessionError, OSError) as e:
        notes.append(_("The files are written, but the meeting file could not keep the names: {reason}").format(
            reason=e))
    result.outputs = [*(o for o in result.outputs if o.kind not in NAMED), *outputs]
    return Named(outputs, notes)


def listening_balance(result: Result) -> float:
    """The balance of the current listening file, or the automatic centre."""
    for output in reversed(result.outputs):
        if output.kind == "audio-balanced" and Path(output.path) == result.listen:
            value = output.extra.get("balance", 0.0)
            if value.__class__ in (float, int) and math.isfinite(value) and -1 <= value <= 1:
                return float(value)
    return 0.0


def save_balance(result: Result, balance: float) -> Named:
    """A new listening file and meeting revision; the transcript is unchanged."""
    from . import recording

    if result.source is None or result.folder is None:
        raise Failure(_("The original recording is missing: a new listening copy cannot be made."), "no source")
    path = recording.rebalance(result.source, result.folder, result.source.stem, balance)
    output = sessions.Output("audio-balanced", full_path(path), {"balance": balance})
    result.listen = path
    notes = []
    try:
        meeting = sessions.load(result.session)
        meeting.outputs = [*meeting.outputs, output]
        meeting.extra["transcript"] = _snapshot(result)
        result.session = files.write_new(result.folder, f"{result.stem}_{_('balanced')}", ".json", sessions.dumps(meeting))
    except (sessions.SessionError, OSError) as e:
        notes.append(_("The listening copy is written, but the meeting file could not keep it: {reason}").format(reason=e))
    result.outputs = [*result.outputs, output]
    return Named([output], notes)


# The speech the app carries for the test of the cards (samples/SOURCES.md:
# 27.6 s of Collodi's Pinocchio read for LibriVox, in the public domain),
# and the text it reads: the test runs on it as soon as the models are
# there, without waiting for a recording of the user's
SAMPLE = PACKAGE_DIR / "samples" / "sample-it.flac"
SAMPLE_TEXT = SAMPLE.with_suffix(".txt")
SAMPLE_LANGUAGE = "it"
# How far from the sample's words a card may write before it counts as
# wrong, and a slower card (or the processor) that writes them is chosen
# instead. On 11/11/2026 the RTX A2000, the Iris Xe and the processor
# wrote the same words on the sample, one of 54 away from Collodi's text
# (1.9 %: "di catasta" for "da catasta"); the same model on the same audio
# differs from one way of computing to another by a word or two at most
# (rounding on the card), while a card that goes wrong (loops, a sentence
# made up, words dropped: whisper's failures) changes a sentence or more.
# 10 % is five words of the sample: five times what was measured, less
# than one sentence of what a broken card does.
WRONG = 0.10
CARDS_FILE = "cards.json"


@dataclass(frozen=True)
class Tried:
    """A card (or the processor) as the test found it: how fast, and how
    far its words were from the sample's."""
    device: str                 # a card's name, or "cpu"
    label: str                  # as the speeds are kept: "Vulkan: <name>" or "cpu"
    speed: float                # seconds of audio per second of work; 0 when it failed
    first: float = 0.0          # seconds for the first copy, the start of the engine included
    difference: float | None = None     # word error rate against the sample's text; None: not known
    error: str = ""
    text: str = ""

    @property
    def wrong(self) -> bool:
        return self.difference is not None and self.difference > WRONG


@dataclass(frozen=True)
class Choice:
    """Where whisper works best on this computer, measured (see try_devices)."""
    device: str             # as the settings and Request.device take it: a card's name, or "cpu"
    label: str              # as the speeds are kept: "Vulkan: <name>" or "cpu"
    speed: float            # seconds of audio per second of work
    hour: float             # seconds of work an hour of audio will take, voices included
    tried: tuple[Tried, ...] = ()   # one per card and the processor, in the order they were tried

    @property
    def passed_over(self) -> list[Tried]:
        """The cards faster than the one chosen that wrote the sample wrong."""
        return [t for t in self.tried if t.wrong and not t.error and t.speed > self.speed]


def tried_of(trial, expected: str | None) -> Tried:
    """whisper's Trial as the test keeps it, with its words measured
    against `expected` (None: a recording of the user's, whose words
    nobody knows)."""
    device = trial.device.name if trial.device is not None else "cpu"
    label = trial.device.label if trial.device is not None else "cpu"
    difference = None
    if expected is not None and not trial.error:
        difference = words.difference(expected, trial.text)
    return Tried(device, label, trial.speed, trial.first, difference, trial.error, trial.text)


def best(tried) -> Tried | None:
    """The fastest card (or the processor) that worked and wrote the
    sample's words (WRONG); when none did, the one closest to them (the
    faster of two as close). Without the text (a recording of the user's),
    the fastest that heard something: a card that runs and writes nothing
    where another writes words is not working."""
    working = [t for t in tried if not t.error and t.speed > 0]
    known = [t for t in working if t.difference is not None]
    if known:
        right = [t for t in known if not t.wrong]
        if right:
            return max(right, key=lambda t: t.speed)
        return min(known, key=lambda t: (t.difference, -t.speed))
    heard = [t for t in working if t.text.strip()]
    pool = heard or working
    return max(pool, key=lambda t: t.speed) if pool else None


def percent(fraction: float) -> str:
    return f"{fraction * 100:.0f} %"


def choice_notes(choice: Choice) -> list[str]:
    """What the test of the cards has to say beyond the card chosen: the
    faster cards left out because their words were wrong, and the chosen
    one's own, when no card wrote the sample right."""
    notes = [_("{card} was faster, but wrote {percent} of the sample's words wrong: it is not used.").format(
        card=t.device, percent=percent(t.difference)) for t in choice.passed_over]
    chosen = next((t for t in choice.tried if t.device == choice.device), None)
    if chosen is not None and chosen.wrong:
        card = _("the processor") if chosen.device == "cpu" else chosen.device
        notes.append(_("No card wrote the sample's words right: {card} came closest ({percent} wrong).").format(
            card=card, percent=percent(chosen.difference)))
    return notes


def save_choice(path: Path | None, choice: Choice) -> None:
    """The test's outcome kept for the next start (the window says why a
    faster card was left out): JSON, written whole or not at all."""
    if path is None:
        return
    data = {"device": choice.device, "label": choice.label, "speed": choice.speed, "hour": choice.hour,
            "tested": datetime.datetime.now().isoformat(timespec="seconds"),
            "tried": [{"device": t.device, "label": t.label, "speed": t.speed, "first": t.first,
                       "difference": t.difference, "error": t.error, "text": t.text} for t in choice.tried]}
    path.parent.mkdir(parents=True, exist_ok=True)
    files.replace(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_choice(path: Path | None) -> Choice | None:
    """The test's outcome kept last time; None when there is none or the
    file is not readable (the test can always be run again)."""
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tried = tuple(Tried(str(t["device"]), str(t["label"]), float(t["speed"]), float(t.get("first", 0.0)),
                            None if t.get("difference") is None else float(t["difference"]),
                            str(t.get("error", "")), str(t.get("text", ""))) for t in data.get("tried", []))
        return Choice(str(data["device"]), str(data["label"]), float(data["speed"]), float(data["hour"]), tried)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def sample_text() -> str:
    return SAMPLE_TEXT.read_text(encoding="utf-8")


def try_devices(source: str | os.PathLike | None = None, model: str = "turbo",
                models_dir: str | os.PathLike | None = None, speeds_file: Path | None = None,
                cancelled: threading.Event | None = None, cards_file: Path | None = None) -> Choice:
    """The first-start test: the app's sample of
    speech (SAMPLE), or 30 seconds of `source` from a quarter of the way in
    (the start of a call is often greetings and silence), transcribed on
    every card and on the processor. On the sample each text is measured
    against the words read (core/words.py), and the fastest that writes
    them is chosen (best). Every speed is kept for the estimates, and the
    outcome in `cards_file`. Failure when nothing works (the reason of the
    first that failed)."""
    from scriba.app.engines import audio, ffmpeg, whisper
    wanted = models.WHISPER[model]
    if not models.present(wanted, models_dir):
        raise Failure(_("Models to download first: {names}.").format(names=wanted.filename), wanted.filename)
    expected = sample_text() if source is None else None
    source = SAMPLE if source is None else Path(source)
    work = Path(tempfile.mkdtemp(prefix="scriba-bench-"))
    try:
        if expected is not None:
            wav = ffmpeg.decode(source, work / "sample.wav", cancelled=cancelled)
        else:
            media = ffmpeg.probe(source, cancelled)
            start = max(0.0, min(media.duration / 4, media.duration - whisper.SAMPLE_SECONDS))
            wav = ffmpeg.decode(source, work / "sample.wav", cancelled=cancelled, start=start,
                                length=whisper.SAMPLE_SECONDS)
        if audio.seconds(audio.read(wav)) < 1:
            raise Failure(_("The recording is too short to test the graphics cards on."), str(source))
        devices = whisper.list_devices(cancelled=cancelled)
        trials = whisper.benchmark(wav, models.path_of(wanted, models_dir), devices, work, language=SAMPLE_LANGUAGE,
                                   precise=wanted is models.WHISPER_LARGE, cancelled=cancelled)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    tried = tuple(tried_of(t, expected) for t in trials)
    chosen = best(tried)
    if chosen is None:
        failed = next((t for t in tried if t.error), None)
        raise Failure(_("whisper did not work on any graphics card nor on the processor."),
                      failed.error if failed else "no trial")
    measured = {speed_key(model, t.label): t.speed for t in tried if t.speed > 0}
    remember_speeds(speeds_file, measured)
    hour = estimated_seconds(3600, Request(source, model=model, speakers=True), chosen.label,
                             {**load_speeds(speeds_file), **measured})
    choice = Choice(chosen.device, chosen.label, chosen.speed, hour, tried)
    save_choice(cards_file, choice)
    return choice
