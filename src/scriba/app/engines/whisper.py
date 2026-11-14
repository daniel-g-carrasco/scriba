"""whisper.cpp's whisper-cli, driven: speech to text, on the graphics card
when there is one that works, on the processor when there is not.

Measured with MSYS2's whisper.cpp 1.9.4 on an i7-12700H with an RTX
A2000 and an Iris Xe: large-v3-turbo q5_0 on
the RTX through Vulkan does the 10-minute test clip in 35.8 s, on the
processor alone in 707 s. Four traps came out of that, handled here:

1. Vulkan picks the integrated card by itself (the Iris Xe is device 0:
   204 s, and a different text). The cards are listed (whisper-bench
   prints them before anything else, in under a second) and the
   dedicated one is chosen, through GGML_VK_VISIBLE_DEVICES; the user may
   name another.
2. whisper-cli's own speech filter runs on four fixed threads and cost 52
   to 112 s of the 117. The filter is a step of its own (vad.py), and
   whisper is given the speech only (core/timeline.py brings the times
   back).
3. large-v3 with its default context falls into loops (64 % of the words
   wrong); `-mc 0 --carry-initial-prompt` brings it back to 14.9 %.
   Turbo does it too, on a long recording: in a meeting of 1 h 48 min its
   microphone track fell into one sentence at minute 53 and wrote it
   2275 times, to the end, each with a high confidence; on 20 minutes
   across that point the default context gave one line 118 times out of
   540, `-mc 0` none out of 246, in less than half the time (13/11/2026).
   The text a loop writes is fed back as context and keeps it going, so
   those two options go with every model.
4. The first window on a card compiles Vulkan's shaders (4 to 5 s the
   first time; the driver keeps them after that: 2.8 s for 30 s of audio
   the second time). The benchmark below runs its sample twice in one
   process, so the speed it reports is the steady one, and the first
   copy's time says what the start costs.

The options are read from the installed program's own --help: a version
that lacks one the app needs is said, not run with the wrong arguments.
whisper-cli 1.9.4 writes, per segment, the times and every token with its
probability and its times (-ojf), from which the confidence of a line and
the times of its words are worked out; it
does not write the probability of "no speech" (the library has it,
whisper_full_get_segment_no_speech_prob, the program does not print it),
so that measure stays None and the speech filter does that work.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scriba import ENV_PREFIX
from scriba.app.task import Failure, Progress, check, tell
from scriba.core.segments import Segment
from scriba.core.terms import PROMPT_MAX_CHARACTERS, SEPARATOR
from scriba.i18n import _

from . import process

CLI_VARIABLE = f"{ENV_PREFIX}_WHISPER_CLI"     # another whisper-cli (whisper-bench is looked for next to it)
DEVICES_VARIABLE = "GGML_VK_VISIBLE_DEVICES"
# what the app cannot do without; the rest is used when the program has it
NEEDED = ("-m", "-f", "-l", "-t", "-of", "-ojf")
_DEVICE_LINE = re.compile(r"ggml_vulkan: (\d+) = (.+?) \(([^()]*)\) \| uma: (\d)")
_PROGRESS = re.compile(r"progress =\s*(\d+)%")
_LANGUAGE = re.compile(r"auto-detected language: (\w+) \(p = ([0-9.]+)\)")
_FLAG = re.compile(r"(?<![\w-])(--?[a-z][a-z0-9-]*)")
_SPECIAL = re.compile(r"^\[_[A-Z]+_?\d*\]$|^<\|.*\|>$")       # [_BEG_], [_TT_150], <|endoftext|>


@dataclass(frozen=True)
class Device:
    index: int              # Vulkan's number for it, among all the cards
    name: str
    vendor: str
    integrated: bool        # shares the computer's memory (uma: 1): slower than a card of its own

    @property
    def label(self) -> str:
        return f"Vulkan: {self.name}"


@dataclass(frozen=True)
class Options:
    model: Path
    language: str = "it"                # "it", "en", ... or "auto"
    terms: tuple[str, ...] = ()
    threads: int = 4
    device: Device | None = None        # None: the processor
    precise: bool = False               # large-v3: trap 3 above


@dataclass
class Transcript:
    segments: list[Segment]
    language: str
    language_probability: float | None
    device: Device | None               # where it ran in the end
    fallback: str = ""                  # why the card was given up, when it was
    seconds: float = 0.0                # of work
    notes: list[str] = field(default_factory=list)


def find() -> Path | None:
    """whisper-cli: the frozen build's own, else the variable's or the
    PATH's (bundle.py); whisper-bench is looked for next to it."""
    from . import bundle
    return bundle.program("whisper-cli", CLI_VARIABLE)


def need() -> Path:
    cli = find()
    if cli is None:
        raise Failure(_("whisper-cli is missing: Scriba needs it to turn speech into text."), "no whisper-cli")
    return cli


def parse_devices(text: str) -> list[Device]:
    """The Vulkan cards in what a ggml program prints when it starts
    ("ggml_vulkan: 1 = NVIDIA RTX A2000 8GB Laptop GPU (NVIDIA) | uma: 0 |
    ...")."""
    return [Device(int(m.group(1)), m.group(2).strip(), m.group(3).strip(), m.group(4) == "1")
            for m in _DEVICE_LINE.finditer(text)]


def list_devices(cli: Path | None = None, cancelled: threading.Event | None = None) -> list[Device]:
    """The cards Vulkan sees, all of them (whatever GGML_VK_VISIBLE_DEVICES
    says in the user's environment). whisper-bench lists them before it
    reads its arguments, so `--help` is enough: 0.9 s, no model loaded."""
    cli = cli or find()
    if cli is None:
        return []
    bench = cli.with_name("whisper-bench" + cli.suffix)
    if not bench.is_file():
        return []
    lines: list[str] = []
    environ = {k: v for k, v in os.environ.items() if k != DEVICES_VARIABLE}
    try:
        process.run([str(bench), "--help"], env=environ, on_stdout=lines.append, on_stderr=lines.append,
                    cancelled=cancelled)
    except OSError:
        return []
    return parse_devices("\n".join(lines))


def choose(devices: Sequence[Device], wanted: str = "") -> Device | None:
    """The card to use: the one named `wanted` ("cpu": none), else the
    first that has memory of its own, else the integrated one."""
    if wanted.lower() == "cpu":
        return None
    if wanted:
        for device in devices:
            if device.name == wanted or device.label == wanted:
                return device
    dedicated = [d for d in devices if not d.integrated]
    return (dedicated or list(devices) or [None])[0]


def processor_threads() -> int:
    """Threads for the processor alone: the physical cores, guessed as half
    the logical ones (the test machine has 20 logical, 14 physical; 14
    threads gave the 707 s measured), less nothing for the window, which
    waits on the main loop."""
    return max(1, (os.cpu_count() or 2) // 2)


_flags_cache: dict[str, frozenset[str]] = {}


def parse_flags(help_text: str) -> frozenset[str]:
    return frozenset(_FLAG.findall(help_text))


def flags(cli: Path, cancelled: threading.Event | None = None) -> frozenset[str]:
    """The options the installed whisper-cli knows, from its own --help."""
    key = os.fspath(cli)
    if key not in _flags_cache:
        lines: list[str] = []
        try:
            process.run([key, "--help"], on_stdout=lines.append, on_stderr=lines.append, cancelled=cancelled)
        except OSError as e:
            raise Failure(_("whisper-cli could not be started."), str(e)) from e
        _flags_cache[key] = parse_flags("\n".join(lines))
    return _flags_cache[key]


def prompt(terms: Sequence[str]) -> str:
    """The terms as whisper's initial prompt, as the engine test gave them
    ("Officina Esempio, Cantiere Esempio, Gruppo Esempio, ..."): whisper writes the names as it saw them
    there. Cut at the last whole term that fits (core/terms.py, joined,
    has already cut them where they are joined, and says so)."""
    out = ""
    for term in dict.fromkeys(t.strip() for t in terms if t.strip()):
        candidate = f"{out}{SEPARATOR}{term}" if out else term
        if len(candidate) > PROMPT_MAX_CHARACTERS:
            break
        out = candidate
    return out


def options_arguments(cli: Path, options: Options, known: frozenset[str]) -> list[str]:
    """whisper-cli and the options of a run, without the files. Failure
    when the program lacks an option the app cannot do without."""
    lacking = [f for f in NEEDED if f not in known]
    if lacking:
        raise Failure(_("The installed whisper-cli is too old for Scriba (it lacks {options}).").format(
            options=", ".join(lacking)), "missing " + " ".join(lacking))
    argv = [os.fspath(cli), "-m", os.fspath(options.model), "-l", options.language or "auto",
            "-t", str(max(1, options.threads)), "-ojf"]
    if "-pp" in known:
        argv.append("-pp")
    text = prompt(options.terms)
    if text and "--prompt" in known:
        argv += ["--prompt", text]
    argv += ["-mc", "0"] if "-mc" in known else []      # no text carried from one window to the next: trap 3
    if text and "--carry-initial-prompt" in known:
        argv.append("--carry-initial-prompt")
    if options.device is None:
        argv.append("-ng")
    return argv


def arguments(cli: Path, wav: Path, out_base: Path, options: Options, known: frozenset[str]) -> list[str]:
    """whisper-cli's arguments for one file, its JSON written to
    `out_base`.json."""
    return [*options_arguments(cli, options, known), "-f", os.fspath(wav), "-of", os.fspath(out_base)]


def environment(options: Options, base: dict | None = None) -> dict:
    environ = dict(os.environ if base is None else base)
    if options.device is not None:
        environ[DEVICES_VARIABLE] = str(options.device.index)
    else:
        environ.pop(DEVICES_VARIABLE, None)
    return environ


def confidence(tokens: Sequence[dict]) -> float | None:
    """The mean log probability of a segment's words, as whisper's
    avg_logprob: from the probability of every token that is text (the
    markers of time and of the segment's start are left out)."""
    logs = [math.log(max(float(t.get("p", 0.0)), 1e-10)) for t in tokens
            if not _SPECIAL.match(str(t.get("text", "")).strip())]
    return sum(logs) / len(logs) if logs else None


def word_times(text: str, tokens: Sequence[dict]) -> tuple[tuple[float, float], ...]:
    """(start, end), in seconds, of each word of `text` (text.split()): a
    word starts with its first token and ends with its last. -ojf turns on
    whisper's timing of the tokens (no option more, no time more). Measured
    on 13/11/2026 (5 minutes, turbo, -mc 0, 868 tokens of text): in order,
    never outside their line, 42 of no length; against the DTW alignment
    (-dtw, which needs -nfa, and that changed the text of 21 lines of 86)
    a word's start is off by 0.2 s at the median, 0.6 s at the 90th
    centile, where sharing the line's time by length gives 0.2 and 0.9 s.
    () when the tokens do not spell the text (a character cut between two
    tokens)."""
    spans: list[tuple[float, float]] = []
    spelled = ""
    for token in tokens:
        piece = str(token.get("text", ""))
        if _SPECIAL.match(piece.strip()):
            continue
        offsets = token.get("offsets") or {}
        span = (float(offsets.get("from", 0)) / 1000, float(offsets.get("to", 0)) / 1000)
        spelled += piece
        spans += [span] * len(piece)
    if spelled.split() != text.split():
        return ()
    return tuple((spans[m.start()][0], max(spans[m.end() - 1][1], spans[m.start()][0]))
                 for m in re.finditer(r"\S+", spelled))


def parse_output(data: dict) -> tuple[list[Segment], str]:
    """The segments of whisper-cli's full JSON (-ojf), times in seconds,
    and the language it says."""
    segments = []
    for item in data.get("transcription") or []:
        offsets = item.get("offsets") or {}
        start, end = float(offsets.get("from", 0)) / 1000, float(offsets.get("to", 0)) / 1000
        text, tokens = str(item.get("text", "")), item.get("tokens") or []
        segments.append(Segment(start, max(end, start), text, avg_logprob=confidence(tokens),
                                words=word_times(text, tokens)))
    language = str(((data.get("result") or {}).get("language")) or (data.get("params") or {}).get("language") or "")
    return segments, language


def _run(cli: Path, wav: Path, workdir: Path, options: Options, progress: Progress | None,
         cancelled: threading.Event | None, on_state: Callable[[str], None] | None) -> tuple[Transcript, str]:
    """One run of whisper-cli: the transcript, or None and why it failed."""
    out_base = workdir / "whisper"
    result = out_base.with_suffix(".json")
    result.unlink(missing_ok=True)
    argv = arguments(cli, wav, out_base, options, flags(cli, cancelled))
    said: dict = {"gpu": None, "language": None}
    started = [False]

    def heard(line: str) -> None:
        found = _PROGRESS.search(line)
        if found:
            if not started[0] and on_state is not None:
                on_state("transcribing")
            started[0] = True
            tell(progress, float(found.group(1)), 100.0)
        elif "using Vulkan" in line or "found GPU device" in line:
            said["gpu"] = True
        elif "no GPU found" in line:
            said["gpu"] = False
        else:
            language = _LANGUAGE.search(line)
            if language:
                said["language"] = (language.group(1), float(language.group(2)))

    if on_state is not None:
        on_state("loading")
    began = time.perf_counter()
    try:
        done = process.run(argv, env=environment(options), on_stderr=heard, cancelled=cancelled)
    except OSError as e:
        raise Failure(_("whisper-cli could not be started."), str(e)) from e
    seconds = time.perf_counter() - began
    if done.returncode != 0 or not result.is_file():
        return None, f"exit {done.returncode}: {done.detail}"
    try:
        data = json.loads(result.read_bytes().decode("utf-8", "replace"))
    except ValueError as e:
        return None, f"output not JSON: {e}"
    segments, language = parse_output(data)
    detected = said["language"]
    device = options.device if said["gpu"] is not False else None
    transcript = Transcript(segments, detected[0] if detected else language, detected[1] if detected else None,
                            device, seconds=seconds)
    if options.device is not None and device is None:
        transcript.fallback = "whisper-cli found no usable graphics card"
    return transcript, ""


def transcribe(wav: str | os.PathLike, options: Options, workdir: str | os.PathLike,
               progress: Progress | None = None, cancelled: threading.Event | None = None,
               on_state: Callable[[str], None] | None = None) -> Transcript:
    """The text of `wav` (16 kHz mono), with whisper's times. `workdir` is
    a temporary folder of the caller's: whisper writes its JSON there, and
    a cancelled run leaves only that folder to throw away. When the card
    fails, the work is done again on the processor and Transcript.fallback
    says why (on_state is told "fallback")."""
    cli = need()
    wav, workdir = Path(wav), Path(workdir)
    transcript, failure = _run(cli, wav, workdir, options, progress, cancelled, on_state)
    if transcript is None and options.device is not None:
        check(cancelled)
        if on_state is not None:
            on_state("fallback")
        tell(progress, 0.0, 100.0)
        cpu = Options(options.model, options.language, options.terms, processor_threads(), None, options.precise)
        transcript, second = _run(cli, wav, workdir, cpu, progress, cancelled, on_state)
        if transcript is not None:
            transcript.fallback = failure
        else:
            failure = f"{failure}\non the processor: {second}"
    if transcript is None:
        raise Failure(_("whisper-cli could not transcribe the audio."), failure)
    return transcript


SAMPLE_SECONDS = 30.0         # what the test takes of a recording of the user's


@dataclass(frozen=True)
class Trial:
    device: Device | None       # None: the processor
    seconds: float              # for the sample once the engine is going (the speed)
    first: float                # for the first copy, the start of the engine included
    text: str                   # what it heard, to compare the devices
    error: str = ""
    audio: float = SAMPLE_SECONDS   # the sample's length, in seconds

    @property
    def speed(self) -> float:
        """Seconds of audio per second of work, as core/estimate.py takes it."""
        return 0.0 if self.error or self.seconds <= 0 else self.audio / self.seconds


def benchmark(sample: str | os.PathLike, model: str | os.PathLike, devices: Sequence[Device],
              workdir: str | os.PathLike, language: str = "it", precise: bool = False,
              cancelled: threading.Event | None = None) -> list[Trial]:
    """How fast each card and the processor transcribe `sample` (half a
    minute of 16 kHz mono: the app's own sample of speech, or a piece of a
    recording cut by the caller), and what each wrote: the first-start
    test the window runs to choose where to work and to say how long an
    hour will take. On a card the sample is given twice in one run (trap
    4: the first copy pays for loading the model and compiling the
    shaders), and the speed is the second copy's; on the processor once."""
    from . import audio
    cli = need()
    known = flags(cli, cancelled)
    workdir = Path(workdir)
    length = audio.seconds(audio.read(sample))
    trials = []
    for device in [*devices, None]:
        check(cancelled)
        # copies of the sample in the work folder: without -of, whisper
        # writes <file>.json next to each file
        copies = [workdir / "sample-1.wav"] + ([workdir / "sample-2.wav"] if device is not None else [])
        for copy in copies:
            shutil.copyfile(sample, copy)
        options = Options(Path(model), language, (), 4 if device else processor_threads(), device, precise)
        saved: list[float] = []

        def heard(line: str, saved=saved) -> None:
            if "saving output" in line:
                saved.append(time.perf_counter())

        began = time.perf_counter()
        try:
            done = process.run([*options_arguments(cli, options, known), *map(str, copies)],
                               env=environment(options), cwd=workdir, on_stderr=heard, cancelled=cancelled)
        except OSError as e:
            trials.append(Trial(device, 0.0, 0.0, "", str(e), length))
            continue
        if done.returncode != 0 or len(saved) != len(copies):
            trials.append(Trial(device, 0.0, 0.0, "", f"exit {done.returncode}: {done.detail}", length))
            continue
        first = saved[0] - began
        steady = saved[-1] - saved[-2] if len(saved) > 1 else first
        try:
            data = json.loads(Path(f"{copies[-1]}.json").read_bytes().decode("utf-8", "replace"))
            text = " ".join(s.text.strip() for s in parse_output(data)[0])
        except (OSError, ValueError) as e:
            text = f"({e})"
        trials.append(Trial(device, steady, first, text, audio=length))
    return trials
