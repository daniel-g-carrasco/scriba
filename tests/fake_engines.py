"""Made-up engines for the window: the tests of test_window.py and the
photographs of tools/screenshots.py. A Backend (app/backend.py) that
answers at once, without whisper, ffmpeg or the network, and a result
built from made-up lines, with its files really written (the Result page
opens them, and the names write new ones next to them)."""

from __future__ import annotations

import random
import threading
import time
from pathlib import Path

from scriba.app import backend, jobs, models
from scriba.app.engines import ffmpeg, whisper
from scriba.app.task import check
from scriba.core import segments as seg, speakers
from scriba.data import files, markdown, session as sessions, srt, txt
from scriba.i18n import _

CARD = whisper.Device(1, "NVIDIA RTX A2000 8GB Laptop GPU", "NVIDIA", False)
IRIS = whisper.Device(0, "Intel(R) Iris(R) Xe Graphics", "Intel", True)
SENTENCES = (
    "Buongiorno a tutti, cominciamo con il punto sui cavedi del secondo piano.",
    "Sì.",
    "Il quadro generale va spostato di due metri verso il corridoio, come da ultima revisione.",
    "Per il carroponte serve una linea dedicata, ne parliamo con Cantiere Esempio la settimana prossima.",
    "D'accordo, mando io la mail con le tavole aggiornate.",
    "Ricordo che la consegna dell'esecutivo è fissata per fine mese.",
    "Chiaro, grazie.",
    "Allora passiamo al fotovoltaico: la copertura del capannone regge il carico?",
)


class FakeBackend(backend.Backend):
    def __init__(self, folder: Path, missing=(), precise: bool = False, media=None) -> None:
        super().__init__(Path(folder) / "models", Path(folder) / "speeds.json")
        self._missing = list(missing)
        self._devices = [IRIS, CARD]
        self.precise = precise
        self.media = media or ffmpeg.Media(600.0, 1, False, 0)
        self.balanceable = False           # a made-up source with independent roles
        self.runner = None                  # run(request, progress, cancelled) -> Result
        self.tests = 0                      # how many times the cards were tested
        self.choice = jobs.Choice(CARD.name, CARD.label, 17.5, 3600 / 17.5 + 3600 / 20.0,
                                  (jobs.Tried(IRIS.name, IRIS.label, 4.6, 6.0, 0.019),
                                   jobs.Tried(CARD.name, CARD.label, 17.5, 2.5, 0.019),
                                   jobs.Tried("cpu", "cpu", 1.2, 25.0, 0.019)))
        self.test_fails: Exception | None = None
        self.hold_test: threading.Event | None = None     # set: the test of the cards waits for it
        install_recorder(self)              # and a recorder that touches no device

    def missing(self):
        return list(self._missing)

    def has(self, model):
        if model is models.WHISPER_LARGE:
            return self.precise
        return model not in self._missing

    def download(self, model, progress=None, cancelled=None):
        for part in range(1, 5):
            check(cancelled)
            if progress is not None:
                progress(model.size * part / 4, model.size)
        if model in self._missing:
            self._missing.remove(model)
        if model is models.WHISPER_LARGE:
            self.precise = True
        return self.models_dir / model.filename

    def inspect(self, source, cancelled=None):
        return self.media

    def devices(self, cancelled=None):
        return list(self._devices)

    def try_devices(self, cancelled=None, model="turbo"):
        self.tests += 1
        if self.hold_test is not None:
            while not self.hold_test.wait(0.05):
                check(cancelled)
        if self.test_fails is not None:
            raise self.test_fails
        jobs.remember_speeds(self.speeds_file, {jobs.speed_key("turbo", self.choice.label): self.choice.speed})
        jobs.save_choice(self.cards_file, self.choice)
        return self.choice

    def can_balance(self, result):
        return self.balanceable and result.source is not None and result.source.is_file()

    def run(self, request, progress, cancelled):
        return self.runner(request, progress, cancelled)


def lines(voices: int = 3, count: int = 24, doubtful: int = 2, seed: int = 7) -> list[seg.Segment]:
    """Made-up lines of a meeting: `voices` voices taking turns, `doubtful`
    of them marked, as core gives them."""
    pick = random.Random(seed)
    out, start = [], 3.0
    marked = set(pick.sample(range(count), min(doubtful, count)))
    speaker = 1
    for i in range(count):
        if i and pick.random() < 0.6:
            speaker = pick.randrange(1, voices + 1) if voices > 1 else 1
        if i < voices:
            speaker = i + 1
        text = pick.choice(SENTENCES)
        length = 1.0 + len(text) / 14
        doubts = (seg.QUIET, seg.LOW_CONFIDENCE) if i in marked else ()
        out.append(seg.Segment(start, start + length, text, seg.SPEAKER_LABEL.format(number=speaker) if voices else None,
                               level=-30.0, doubts=doubts))
        start += length + pick.uniform(0.3, 6.0)
    return out


def made_result(folder: Path, source: Path, segments, video: bool = False, notes=(),
                duration: float = 600.0, total: float = 74.0, device: str = CARD.label) -> jobs.Result:
    """A Result as jobs.run gives it, with its files written in `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    voices = len(seg.speakers_of(segments))
    header = markdown.Header(source=str(source), duration=duration, language="it", model="large-v3-turbo",
                             device=device, terms=("Cantiere Esempio", "cavedi"),
                             speakers_engine="diarize" if voices else "")
    stem = files.free_stem(folder, f"{source.stem}_{_('transcript')}", (".md", ".txt", ".srt", ".json"))
    names = speakers.display_names(seg.speakers_of(segments), {})
    outputs = [sessions.Output("md", str(markdown.write(folder, stem, header, segments, names))),
               sessions.Output("txt", str(txt.write(folder, stem, header, segments, names))),
               sessions.Output("srt", str(srt.write(folder, stem, segments, speakers=bool(voices))))]
    if video:
        copy = files.write_new(folder, f"{source.stem}_{_('subtitles')}", ".mkv", "not a video")
        outputs.append(sessions.Output("video", str(copy)))
    meeting = sessions.Session(source=str(source), duration=duration, outputs=list(outputs))
    session = files.write_new(folder, stem, ".json", sessions.dumps(meeting))
    return jobs.Result(outputs, session, list(segments), voices, duration, device,
                       {"transcribe": 33.0, "total": total}, list(notes), source, folder, stem, header)


def runner(make, step: float = 0.0, hold: threading.Event | None = None):
    """A chain that goes through the steps of the request and returns
    make(request). With `hold`, it stops in the middle of the
    transcription until `hold` is set (or the work is cancelled)."""
    def run(request, progress, cancelled):
        progress.stages = tuple(jobs.stages_for(request))
        for stage in progress.stages:
            check(cancelled)
            progress.begin(stage)
            for part in range(1, 5):
                check(cancelled)
                if step:
                    time.sleep(step)
                progress.advance(part, 4)
                if stage == "transcribe" and part == 2 and hold is not None:
                    while not hold.wait(0.02):
                        check(cancelled)
            progress.finish()
        return make(request)
    return run


# -- the recorder, made up ---------------------------------------------------------------

from scriba.app import recording as recordings  # noqa: E402
from scriba.core import devices as dev  # noqa: E402

SPEAKERS = dev.Device("out-speakers", "Altoparlanti (Realtek(R) Audio)", dev.OUTPUT, default=True)
HEADSET = dev.Device("out-headset", "Cuffie (Free BYRD)", dev.OUTPUT, communications=True, container="byrd",
                     bluetooth=True, programs=("ms-teams.exe",))
WEBCAM = dev.Device("in-webcam", "Microfono (Trust Full HD Webcam)", dev.INPUT, default=True, container="trust")
HEADSET_MIC = dev.Device("in-headset", "Cuffia auricolare (Free BYRD)", dev.INPUT, communications=True,
                         container="byrd", bluetooth=True, programs=("ms-teams.exe",))
ARRAY = dev.Device("in-array", "Gruppo microfoni (Tecnologia Intel® Smart Sound per microfoni digitali)", dev.INPUT)
DEVICES = (SPEAKERS, HEADSET, WEBCAM, HEADSET_MIC, ARRAY)
LEVELS = {"out-speakers": (-700.0, -350.0), "out-headset": (-24.0, -12.0), "in-webcam": (-48.0, -36.0),
          "in-headset": (-21.0, -9.0), "in-array": (-55.0, -44.0)}


class FakeMonitor:
    def __init__(self, devices=DEVICES, levels=None, closed=(), failed=None):
        self.devices, self.levels = tuple(devices), dict(LEVELS if levels is None else levels)
        self.closed, self.failed = frozenset(closed), dict(failed or {})
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def snapshot(self):
        return recordings.Meters(devices=self.devices, levels=dict(self.levels),
                                 peaks={k: p for k, (_r, p) in self.levels.items()},
                                 voices={k: r for k, (r, _p) in self.levels.items()},
                                 closed=self.closed, failed=self.failed, listed=True)


class FakeRecorder:
    """Records nothing: says what a recording would, as the test sets it."""

    def __init__(self, plan, folder, stem):
        self.plan, self.folder, self.stem = plan, Path(folder), stem
        self.state, self.elapsed = "new", 0.0
        self.levels, self.alerts, self.events, self.error = {}, (), (), ""
        self.seconds_left = 9 * 3600.0
        self.takes = []
        self.fail = None                    # a Failure start() raises

    def start(self):
        if self.fail is not None:
            raise self.fail
        self.folder.mkdir(parents=True, exist_ok=True)
        devices = ([("me", self.plan.input)] if self.plan.input else []) + [("others", d) for d in self.plan.outputs]
        for n, (role, device) in enumerate(devices, 1):
            path = self.folder / f"{self.stem}_{role} {n}.flac"
            path.write_bytes(b"fLaC made up")
            self.takes.append(recordings.Take(f"{role}-{n}", role, device.name, path, loudest=-12.0))
            self.levels[f"{role}-{n}"] = (-24.0, -12.0)
        self.state = "recording"

    def pause(self):
        self.state = "paused"

    def resume(self):
        self.state = "recording"

    def stop(self):
        self.state = "stopped"
        return list(self.takes)

    def snapshot(self):
        return recordings.Status(self.state, self.elapsed, tuple(self.takes), dict(self.levels), tuple(self.alerts),
                                 tuple(self.events), self.seconds_left, self.error)


def install_recorder(backend, devices=DEVICES, levels=None, closed=(), failed=None):
    """The made-up monitor and recorder on a FakeBackend: backend.monitors and
    backend.recorders keep what the window asked for."""
    backend.monitors, backend.recorders, backend.finished = [], [], []

    def monitor():
        made = FakeMonitor(devices, levels, closed, failed)
        backend.monitors.append(made)
        return made

    def recorder(plan, folder, stem):
        made = FakeRecorder(plan, folder, stem)
        made.fail = getattr(backend, "recorder_fails", None)
        backend.recorders.append(made)
        return made

    def finish(takes, folder, stem, cancelled=None):
        joined = Path(folder) / f"{stem}.mka"
        joined.write_bytes(b"made up")
        for take in takes:
            take.path.unlink(missing_ok=True)
        done = recordings.Finished(joined, None, list(takes), [], list(getattr(backend, "finish_notes", [])))
        backend.finished.append(done)
        return done

    backend.monitor, backend.recorder, backend.finish = monitor, recorder, finish
    backend.recorder_missing = lambda: list(getattr(backend, "missing_pieces", []))
    backend.free_space = lambda folder: 500 * 1024 ** 3
    return backend
