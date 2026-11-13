"""What the window asks of the engines, in one place.

The window shows and triggers, and works nothing out: which models are
missing, what a recording holds, where whisper will work, how long the
work will take, the work itself and the copies with the names are all
asked of this object, from a thread (gui/ui.py, run_in_thread) for
whatever touches the disk or starts a program. The tests and the
screenshots give the window a made-up one (a subclass that answers at
once), so that the window is seen and tried without engines and without
the network.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from scriba.core import estimate
from scriba.core.terms import terms_of

from . import jobs, models
from .config import Config


def needed_models() -> list[models.Model]:
    """What the first start downloads: the fast transcription model, the
    speech filter, the voices. The precise model is offered apart (the
    preferences): it weighs twice the rest."""
    return [models.WHISPER_TURBO, models.SILERO_VAD, models.WESPEAKER]


def request_for(cfg: Config, source: Path, out: Path | None, video: bool) -> jobs.Request:
    """The work as the settings describe it."""
    device = cfg.device or cfg.measured_device
    return jobs.Request(source=Path(source), out=out, language=cfg.speech_language or "it",
                        model=cfg.quality if cfg.quality in models.WHISPER else "turbo",
                        terms=terms_of(cfg.terms), speakers=cfg.speakers,
                        speaker_count=cfg.speaker_count or None, video=video and cfg.subtitled_video,
                        cpu=device == "cpu", device="" if device == "cpu" else device,
                        my_name=" ".join(cfg.my_name.split()) or "Io")


class Backend:
    def __init__(self, models_dir: str | os.PathLike | None, speeds_file: Path | None) -> None:
        self.models_dir = Path(models_dir) if models_dir is not None else models.folder()
        self.speeds_file = speeds_file
        self._devices = None          # whisper's cards, once listed
        self._lock = threading.Lock()

    # -- the models ----------------------------------------------------------------

    def missing(self) -> list[models.Model]:
        return models.missing(needed_models(), self.models_dir)

    def has(self, model: models.Model) -> bool:
        return models.present(model, self.models_dir)

    def download(self, model: models.Model, progress=None, cancelled: threading.Event | None = None) -> Path:
        return models.download(model, self.models_dir, progress, cancelled)

    def request(self, cfg: Config, source: Path, out: Path | None, video: bool) -> jobs.Request:
        """The work as the settings describe it, with the fast model when the
        precise one is chosen and not downloaded."""
        request = request_for(cfg, source, out, video)
        if not self.has(models.WHISPER[request.model]):
            from dataclasses import replace
            request = replace(request, model="turbo")
        return request

    # -- the recording -------------------------------------------------------------

    def inspect(self, source: str | os.PathLike, cancelled: threading.Event | None = None):
        """What the recording holds (ffmpeg.Media: duration, video or not).
        A recording cut short by a crash has no length in its header (a
        FLAC track of the recorder's, killed): it is decoded to know it."""
        from dataclasses import replace

        from scriba.app.engines import ffmpeg
        media = ffmpeg.probe(source, cancelled)
        if media.duration <= 0 and media.sound_tracks:
            media = replace(media, duration=ffmpeg.decoded_seconds(source, 0, cancelled))
        return media

    # -- where whisper works -------------------------------------------------------

    def devices(self, cancelled: threading.Event | None = None) -> list:
        """The graphics cards whisper sees (listed once, about a second)."""
        with self._lock:
            if self._devices is not None:
                return list(self._devices)
        from scriba.app.engines import whisper
        found = whisper.list_devices(cancelled=cancelled)
        with self._lock:
            self._devices = found
        return list(found)

    @property
    def cards_file(self) -> Path | None:
        """Where the test of the cards keeps its outcome: next to the speeds."""
        return self.speeds_file.with_name(jobs.CARDS_FILE) if self.speeds_file is not None else None

    def try_devices(self, cancelled: threading.Event | None = None, model: str = "turbo") -> jobs.Choice:
        """The test of the cards, on the app's sample of speech (about half a
        minute: the processor's run is the long one)."""
        return jobs.try_devices(None, model, self.models_dir, self.speeds_file, cancelled, self.cards_file)

    def test_notes(self) -> list[str]:
        """What the last test of the cards has to say past the card chosen
        (a faster card left out for its wrong words); [] when there was none."""
        choice = jobs.load_choice(self.cards_file)
        return jobs.choice_notes(choice) if choice is not None else []

    def device_label(self, request: jobs.Request) -> str:
        """Where the work of `request` would run, as the speeds name it.
        Before the cards are listed, the name asked for stands for its card."""
        if request.cpu:
            return "cpu"
        with self._lock:
            listed = self._devices
        if listed is None:
            return f"Vulkan: {request.device}" if request.device else "cpu"
        from scriba.app.engines import whisper
        chosen = whisper.choose(listed, request.device)
        return chosen.label if chosen is not None else "cpu"

    def estimate(self, duration: float, request: jobs.Request) -> float:
        """Seconds of work for `duration` seconds of audio."""
        return jobs.estimated_seconds(duration, request, self.device_label(request),
                                      jobs.load_speeds(self.speeds_file))

    def first_estimate(self, request: jobs.Request) -> bool:
        """The estimate is not yet measured for this work on this device."""
        return jobs.first_estimate(request, self.device_label(request), jobs.load_speeds(self.speeds_file))

    def hour(self, request: jobs.Request) -> float:
        return self.estimate(3600, request)

    # -- the work ------------------------------------------------------------------

    def run(self, request: jobs.Request, progress: jobs.Progress, cancelled: threading.Event) -> jobs.Result:
        return jobs.run(request, progress, cancelled, self.models_dir, self.speeds_file)

    def open_session(self, path):
        return jobs.open_session(path)

    def save_names(self, result: jobs.Result, names: dict[str, str]) -> jobs.Named:
        return jobs.save_names(result, names)

    def preview(self, source, start: float, end: float, cancelled: threading.Event):
        from . import preview
        return preview.prepare(source, start, end, cancelled)

    def can_balance(self, result: jobs.Result) -> bool:
        from . import recording
        return recording.balance_available(result.source)

    def save_balance(self, result: jobs.Result, balance: float) -> jobs.Named:
        return jobs.save_balance(result, balance)

    def balance_preview(self, result: jobs.Result, balance: float, cancelled: threading.Event):
        """A few seconds of the listening copy with this balance, where the
        user and the others both speak (speakers.both_heard)."""
        from scriba.core import speakers
        from scriba.i18n import _

        from . import preview
        from .task import Failure
        if result.source is None:
            raise Failure(_("The original recording is missing: a new listening copy cannot be made."), "no source")
        start, end = speakers.both_heard(result.segments, result.me, duration=result.duration)
        return preview.balanced(result.source, start, end, balance, cancelled)

    @staticmethod
    def about(seconds: float) -> str:
        return estimate.about(seconds)

    # -- recording ---------------------------------------------------------------------

    def recorder_missing(self) -> list[str]:
        """The GStreamer pieces the recorder lacks on this computer: [] when
        it can record. Never raises: no GStreamer at all is said too."""
        try:
            from scriba.app import recorder
        except (ImportError, ValueError) as e:
            return [f"GStreamer ({e})"]
        return recorder.missing()

    def monitor(self):
        """The devices and their meters while nothing is recorded (start,
        snapshot, stop)."""
        from scriba.app import recorder
        return recorder.Monitor()

    def recorder(self, plan, folder: Path, stem: str):
        """A recording to start (app/recorder.py, Recorder)."""
        from scriba.app import recorder
        return recorder.Recorder(plan, folder, stem)

    def finish(self, takes, folder: Path, stem: str, cancelled: threading.Event | None = None, progress=None):
        """The tracks made one file and a mix (app/recording.py, finish)."""
        from scriba.app import recording
        return recording.finish(takes, folder, stem, cancelled, progress)

    def free_space(self, folder: Path) -> int:
        import shutil
        return shutil.disk_usage(folder).free
