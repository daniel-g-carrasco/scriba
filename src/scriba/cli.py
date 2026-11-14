"""The command line: Scriba without a window, for scripts and for the
colleagues who want trascrivi.py's way of working.

    scriba transcribe <file> [--speakers [N]] [--video] [--language it|en|auto]
                             [--model turbo|large] [--terms "..."] [--terms-file FILE]...
                             [--out DIR] [--cpu]
    scriba models [--download]
    scriba benchmark [<file>] [--model turbo|large]
    scriba record <seconds> --out DIR [--no-microphone] [--no-sound]
    scriba check [<file>...]

It is a front end as the window is: it uses app/ (jobs, models, the
engines) and never the toolkit, so it starts without GTK. Exit status: 0
done, 1 the work failed (the reason is said), 2 the command was wrong
(argparse's own), 3 cancelled (Ctrl+C: the child processes are stopped and
the temporary folder is gone; nothing half written stays in the user's
folder).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from scriba import SLUG, __version__
from scriba.core.terms import terms_of  # noqa: F401 (the command line's own, and the tests')
from scriba.i18n import _, ngettext

COMMANDS = ("transcribe", "models", "benchmark", "record", "check")
OK, FAILED, USAGE, CANCELLED = 0, 1, 2, 3


def stage_names() -> dict[str, str]:
    from scriba.app.jobs import stage_names as names      # app only when a command runs: --help stays quick
    return names()


def _speaker_count(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(_("the number of speakers cannot be negative"))
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SLUG, description=_("Transcribe meetings on this computer."))
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar=_("COMMAND"))
    transcribe = commands.add_parser("transcribe", help=_("transcribe a recording"),
                                     description=_("Transcribe a recording: Markdown, plain text and subtitles "
                                                   "next to it (or in --out), never over a file that is there."))
    transcribe.add_argument("file", type=Path, help=_("the recording (audio or video)"))
    transcribe.add_argument("--speakers", nargs="?", const=0, default=None, type=_speaker_count, metavar="N",
                            help=_("tell the voices apart; N fixes how many there are"))
    transcribe.add_argument("--video", action="store_true", help=_("also a copy of the video with the subtitles inside"))
    transcribe.add_argument("--language", default="it", choices=("it", "en", "auto"), help=_("the language spoken"))
    transcribe.add_argument("--model", default="turbo", choices=("turbo", "large"),
                            help=_("turbo is fast, large is more precise and slower"))
    transcribe.add_argument("--terms", default="", help=_("names and acronyms to write right, separated by commas"))
    transcribe.add_argument("--terms-file", action="append", default=[], type=Path, metavar="FILE",
                            help=_("a file of terms, one a line (lines starting with # left out), read after "
                                   "--terms; can be given more than once"))
    transcribe.add_argument("--out", type=Path, default=None, help=_("the folder for the files"))
    transcribe.add_argument("--cpu", action="store_true", help=_("work on the processor only"))
    transcribe.add_argument("--normalise", action="store_true",
                            help=_("bring each track to the same loudness (EBU R128) before the engines hear it "
                                   "(off: on faint recordings whisper invented more)"))
    listing = commands.add_parser("models", help=_("the models: which are there, and their download"))
    listing.add_argument("--download", action="store_true", help=_("download the models that are missing"))
    bench = commands.add_parser("benchmark", help=_("how fast each graphics card and the processor transcribe"))
    bench.add_argument("file", type=Path, nargs="?", default=None,
                       help=_("a recording, 30 seconds of which are used (by default the app's sample of speech, "
                              "whose words are known)"))
    bench.add_argument("--model", default="turbo", choices=("turbo", "large"))
    record = commands.add_parser("record", help=_("record the microphone and the computer's sound"),
                                 description=_("Record for the seconds given (Ctrl+C stops sooner), with the "
                                               "devices the window would choose by itself, then make the "
                                               "recording whole: one .mka with a track each, and a mix to "
                                               "listen to."))
    record.add_argument("seconds", type=float, help=_("how long to record"))
    record.add_argument("--out", type=Path, required=True, help=_("the folder for the recording"))
    record.add_argument("--no-microphone", action="store_true", help=_("leave the microphone out"))
    record.add_argument("--no-sound", action="store_true", help=_("leave the computer's sound out"))
    check = commands.add_parser("check", help=_("what Scriba finds on this computer: engines, models, recorder"),
                                description=_("Say where each engine is, which models are there and whether "
                                              "the recorder has what it needs; each file given is opened, "
                                              "muted, by the player of Listen."))
    check.add_argument("files", type=Path, nargs="*", help=_("recordings to open with the player"))
    return parser


class Printer:
    """The progress on stderr: on a terminal one line rewritten, elsewhere
    (a log, a pipe) one line per step."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr
        self.live = hasattr(self.stream, "isatty") and self.stream.isatty()
        self.stage = None
        self.shown = -1
        self.lock = threading.Lock()

    def __call__(self, progress) -> None:
        with self.lock:
            names = stage_names()
            if progress.stage != self.stage:
                if self.stage in progress.finished and self.stage not in progress.skipped:
                    self._line(f"{names.get(self.stage, self.stage)}: {progress.finished[self.stage]:.1f} s", end=True)
                self.stage, self.shown = progress.stage, -1
                if not self.live:
                    print(f"{names.get(self.stage, self.stage)}...", file=self.stream, flush=True)
            percent = int(progress.overall() * 100)
            if self.live and percent != self.shown:
                self.shown = percent
                state = f" ({_('loading the model')})" if progress.state == "loading" else ""
                self._line(f"{percent:3d}%  {names.get(self.stage, self.stage)}{state}")

    def _line(self, text: str, end: bool = False) -> None:
        if self.live:
            self.stream.write("\r" + text.ljust(70) + ("\n" if end else ""))
            self.stream.flush()
        elif end:
            print("  " + text, file=self.stream, flush=True)

    def close(self, progress) -> None:
        with self.lock:
            if self.stage in progress.finished:
                self._line(f"{stage_names().get(self.stage, self.stage)}: {progress.finished[self.stage]:.1f} s",
                           end=True)


def _in_thread(work):
    """`work(cancelled)` in a thread of its own, so that Ctrl+C reaches this
    one: it sets the event, and the work stops at its next look."""
    cancelled = threading.Event()
    box: dict = {}

    def body():
        try:
            box["result"] = work(cancelled)
        except BaseException as e:      # the thread's border: whatever happened is handed back and raised there
            box["error"] = e

    worker = threading.Thread(target=body, daemon=True)
    worker.start()
    while worker.is_alive():
        try:
            worker.join(0.2)
        except KeyboardInterrupt:
            cancelled.set()
            print("\n" + _("Stopping..."), file=sys.stderr, flush=True)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _say_failure(error) -> int:
    print(_("Error: {reason}").format(reason=error), file=sys.stderr)
    if getattr(error, "detail", ""):
        print(error.detail, file=sys.stderr)
    return FAILED


def transcribe(args) -> int:
    from scriba.app import jobs, services
    from scriba.app.task import Cancelled, Failure
    request = jobs.Request(source=args.file, out=args.out, language=args.language, model=args.model,
                           terms=terms_of(args.terms), term_files=tuple(str(f) for f in args.terms_file),
                           speakers=args.speakers is not None,
                           speaker_count=args.speakers or None, video=args.video, cpu=args.cpu,
                           normalise=args.normalise)
    speeds = services.app_paths().state / jobs.SPEEDS_FILE
    printer = Printer()
    progress = jobs.Progress(on_change=printer)
    began = time.perf_counter()
    try:
        result = _in_thread(lambda cancelled: jobs.run(request, progress, cancelled, speeds_file=speeds))
    except Cancelled:
        print(_("Cancelled: no file is left half written."), file=sys.stderr)
        return CANCELLED
    except Failure as e:
        printer.close(progress)
        return _say_failure(e)
    printer.close(progress)
    for output in result.outputs:
        print(f"OK    {output.path}")
    print(f"OK    {jobs.full_path(result.session)}")
    total = time.perf_counter() - began
    summary = _("{duration:.0f} s of audio in {total:.0f} s ({speed:.1f} times real time), on {device}").format(
        duration=result.duration, total=total, speed=result.duration / max(total, 1e-6), device=result.device)
    if request.speakers:
        summary += "; " + ngettext("{n} voice", "{n} voices", result.speakers).format(n=result.speakers)
    print(summary)
    for note in result.notes:
        print(note)
    return OK


def models_command(args) -> int:
    from scriba.app import models
    from scriba.app.task import Cancelled, Failure
    where = models.folder()
    print(_("Models folder: {path}").format(path=where))
    for model in models.CATALOGUE:
        mark = "OK   " if models.present(model, where) else "--   "
        print(f"{mark} {model.filename}  {models.megabytes(model.size)}  {model.licence}  {models.purpose(model)}")
    if not args.download:
        return OK
    for model in models.missing(models.CATALOGUE, where):
        print(_("Downloading {name}...").format(name=model.filename), file=sys.stderr)
        shown = [-1]

        def progress(done, total, shown=shown):
            percent = int(100 * done / total) if total else 0
            if percent >= shown[0] + 5:
                shown[0] = percent
                print(f"  {percent:3d}%", file=sys.stderr, flush=True)

        try:
            _in_thread(lambda cancelled, model=model, progress=progress:
                       models.download(model, where, progress, cancelled))
        except Cancelled:
            print(_("Cancelled: the part downloaded is kept and the next download goes on from there."),
                  file=sys.stderr)
            return CANCELLED
        except Failure as e:
            return _say_failure(e)
        print(f"OK    {models.path_of(model, where)}")
    return OK


def benchmark_command(args) -> int:
    """The test of the cards the window runs once the models are there: on
    the app's sample of speech (its words known, so each card's text is
    measured against them), or on 30 seconds of a recording given. Nothing
    is kept: the window's own choice stays as it is."""
    from scriba.app import jobs
    from scriba.app.task import Cancelled, Failure
    from scriba.core import estimate
    try:
        choice = _in_thread(lambda cancelled: jobs.try_devices(args.file, args.model, cancelled=cancelled))
    except Cancelled:
        return CANCELLED
    except Failure as e:
        return _say_failure(e)
    for tried in choice.tried:
        where = _("processor") if tried.device == "cpu" else tried.label
        if tried.error:
            print(f"ERR   {where}: {tried.error.splitlines()[0]}")
            continue
        line = _("{where}: {speed:.1f} times real time (first copy {first:.1f} s)").format(
            where=where, speed=tried.speed, first=tried.first)
        if tried.difference is not None:
            line += "; " + _("{percent} of the words differ from the sample's").format(
                percent=jobs.percent(tried.difference))
        print(line)
    chosen = _("the processor") if choice.device == "cpu" else choice.device
    print(_("Chosen: {card}; an hour of audio with the voices in {about}.").format(
        card=chosen, about=estimate.about(choice.hour)))
    for note in jobs.choice_notes(choice):
        print(note)
    return OK


def record_command(args) -> int:
    """A recording without a window: the automatic choice of the devices
    (core/devices.py, as the window makes it before the meters have heard
    anything), the seconds asked, then the recording made whole."""
    import datetime

    from scriba.app import recording, services
    from scriba.app.task import Failure
    from scriba.core import devices as dev, recording as rec
    if args.no_microphone and args.no_sound:
        print(_("Nothing to record: both the microphone and the sound are left out."), file=sys.stderr)
        return USAGE
    try:
        from scriba.app import recorder
    except (ImportError, ValueError) as e:
        return _say_failure(Failure(_("GStreamer is missing: Scriba needs it to record."), str(e)))
    lacking = recorder.missing()
    if lacking:
        return _say_failure(Failure(_("The recorder lacks GStreamer's {names}.").format(names=", ".join(lacking))))
    folder = Path(args.out)
    refused = recording.unsuitable(folder, recording.own_folders(services.app_paths()))
    if refused:
        return _say_failure(Failure(refused))
    try:
        plan = dev.plan(recorder.list_devices(), input_on=not args.no_microphone, output_on=not args.no_sound)
    except Failure as e:
        return _say_failure(e)
    if plan.input is None and not plan.outputs:
        return _say_failure(Failure(_("No sound device to record from.")))
    for device in ([plan.input] if plan.input else []) + list(plan.outputs):
        print(_("Recording {device}").format(device=device.name), file=sys.stderr)
    stem = rec.stem(datetime.datetime.now(), recording.file_words()["recording"])
    take = recorder.Recorder(plan, folder, stem)
    try:
        take.start()
    except Failure as e:
        return _say_failure(e)
    end = time.monotonic() + max(args.seconds, 0.5)
    try:
        while time.monotonic() < end and take.snapshot().state in ("recording", "paused"):
            time.sleep(0.1)
    except KeyboardInterrupt:
        print(_("Stopping..."), file=sys.stderr)
    status = take.snapshot()
    takes = take.stop()
    if status.error:
        print(status.error, file=sys.stderr)
    try:
        finished = recording.finish(takes, folder, stem)
    except Failure as e:
        return _say_failure(e)
    for path in (finished.recording, finished.together):
        if path is not None:
            print(f"OK    {path}")
    for note in finished.notes:
        print(note)
    return OK


def check_command(args) -> int:
    """Where the engines are, the models, the recorder's pieces, and the
    player of Listen on each file given (gui/playcheck.py, muted). 1 when
    something the app needs is missing or a file does not play."""
    from scriba.app import jobs, models
    from scriba.app.engines import ffmpeg, onnx, whisper
    missing = 0
    found = {"whisper-cli": whisper.find(), "ffmpeg": ffmpeg.find(), "ffprobe": ffmpeg.find("ffprobe"),
             "onnxruntime": onnx.find_library()}
    cli = found["whisper-cli"]
    found["whisper-bench"] = cli.with_name("whisper-bench" + cli.suffix) if cli is not None else None
    for name, path in found.items():
        there = path is not None and Path(path).is_file()
        missing += not there
        print(f"{'OK  ' if there else '--  '}  {name}: {path if there else _('not found')}")
    version = _first_line([str(found["ffmpeg"]), "-hide_banner", "-version"]) if found["ffmpeg"] else ""
    if version:
        print(f"      {version}")
    for device in whisper.list_devices():
        print(f"OK    {device.label}" + (f" ({_('integrated')})" if device.integrated else ""))
    print(f"{'OK  ' if jobs.SAMPLE.is_file() else '--  '}  {_('sample of speech')}: {jobs.SAMPLE}")
    missing += not jobs.SAMPLE.is_file()
    where = models.folder()
    for model in models.CATALOGUE:
        print(f"{'OK  ' if models.present(model, where) else '--  '}  {model.filename}")
    try:
        from scriba.app import recorder
        lacking = recorder.missing()
        needed = recorder.NEEDED
    except (ImportError, ValueError) as e:
        lacking, needed = [f"GStreamer ({e})"], ()
    missing += bool(lacking)
    print(f"{'OK  ' if not lacking else '--  '}  {_('recorder')}: {', '.join(lacking or needed)}")
    if args.files:
        from scriba.gui.playcheck import try_playing
        for path in args.files:
            played = try_playing(str(path))
            good = played.prepared and not played.error
            missing += not good
            line = _("ready {ready}, seekable {seekable}, {duration:.1f} s long, from {start:.1f} s to "
                     "{reached:.1f} s").format(ready=played.prepared, seekable=played.seekable,
                                                duration=played.duration, start=played.started,
                                                reached=played.reached)
            print(f"{'OK  ' if good else 'ERR '}  {path.name}: {line}" + (f" ({played.error})" if played.error else ""))
    return OK if not missing else FAILED


def _first_line(argv: list[str]) -> str:
    import subprocess
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    said = (done.stdout or done.stderr).strip()
    return said.splitlines()[0] if said else ""


def main(argv: list[str]) -> int:
    from scriba.app import log
    log.setup(SLUG)
    args = build_parser().parse_args(argv)
    if args.command == "transcribe":
        return transcribe(args)
    if args.command == "models":
        return models_command(args)
    if args.command == "record":
        return record_command(args)
    if args.command == "check":
        return check_command(args)
    return benchmark_command(args)
