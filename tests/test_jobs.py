#!/usr/bin/env python3
"""The chain, from a recording to the files: first with the engines
replaced by stand-ins that answer as the real ones do (the chain's own
logic: times brought back, voices numbered, doubtful lines marked, one
name for the set, nothing over a file that is there), then with the real
engines on the test clip (marked `real`, see test_engines.py)."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup
from test_engines import AUDIO, MODELS, real

from scriba.app import jobs, models
from scriba.app.engines import audio, diarize, ffmpeg, vad, whisper
from scriba.app.task import Cancelled, Failure
from scriba.core import loudness
from scriba.core.segments import Segment, Turn

RATE = audio.RATE


class StandIns:
    """The engines as they answer, without the programs: 20 s of audio
    with speech from 2 to 5 s and from 10 to 12 s."""

    def __init__(self, test, video=False, tracks=()):
        self.calls = {}
        media = ffmpeg.Media(20.0, len(tracks) or 1, video, 0, tuple(tracks))

        def decode(source, dest, duration=0.0, progress=None, cancelled=None, stream=0):
            self.calls.setdefault("streams", []).append(stream)
            import numpy as np
            tone = 0.1 * 32768 * np.sin(2 * np.pi * 440 * np.arange(20 * RATE) / RATE)       # -23 dBFS
            audio.write(dest, tone.astype(np.int16))
            return Path(dest)

        def transcribe(wav, options, workdir, progress=None, cancelled=None, on_state=None):
            self.calls["options"] = options
            self.calls["speech"] = len(audio.read(wav)) / RATE
            # times of the packed speech: 3 s, 0.2 s of silence, 2 s
            return whisper.Transcript([Segment(0.5, 2.5, " Buongiorno a tutti."),
                                       Segment(3.3, 4.0, " Grazie.", avg_logprob=-1.6)], "it", None, None)

        def voices(samples, vad_model, voice_model, speakers=None, threads=4, progress=None, cancelled=None):
            self.calls["speakers"] = speakers
            return diarize.Diarization([Turn(0.0, 6.0, "SPEAKER_03"), Turn(9.0, 13.0, "SPEAKER_00")], 2, [], 0)

        def measure(source, stream=0, cancelled=None):
            self.calls.setdefault("measured", []).append(Path(source).name)
            return loudness.Loudness(-23.0, -10.0, 3.0, -33.0)          # the tone is at the target already

        for target, value in ((ffmpeg, {"probe": lambda source, cancelled=None: media, "decode": decode,
                                        "measure_loudness": measure}),
                              (vad, {"detect": lambda *a, **k: [(2 * RATE, 5 * RATE), (10 * RATE, 12 * RATE)]}),
                              (whisper, {"transcribe": transcribe, "list_devices": lambda cancelled=None: []}),
                              (diarize, {"diarize": voices}),
                              (models, {"missing": lambda wanted, where=None: []})):
            for name, function in value.items():
                patcher = mock.patch.object(target, name, function)
                patcher.start()
                test.addCleanup(patcher.stop)


class ChainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.source = self.folder / "call.m4a"
        self.source.write_bytes(b"the user's recording")
        self.out = self.folder / "out"

    def test_the_lines_come_back_to_the_recording_s_times_with_their_voices_and_doubts(self):
        engines = StandIns(self)
        result = jobs.run(jobs.Request(self.source, self.out, speakers=True, terms=("Officina Esempio",)))
        self.assertAlmostEqual(engines.calls["speech"], 5.2)                # 3 s + 0.2 s of silence + 2 s
        self.assertEqual(engines.calls["options"].terms, ("Officina Esempio",))
        self.assertIsNone(engines.calls["speakers"])
        lines = [(round(s.start, 3), round(s.end, 3), s.text, s.speaker, s.doubts) for s in result.segments]
        self.assertEqual(lines, [(2.5, 4.5, "Buongiorno a tutti.", "Speaker 1", ()),
                                 (10.1, 10.8, "Grazie.", "Speaker 2", ("low_confidence",))])
        self.assertEqual(result.speakers, 2)
        self.assertAlmostEqual(result.segments[0].level, -23.0, delta=0.2)     # measured on the recording
        md = Path(next(o.path for o in result.outputs if o.kind == "md")).read_text(encoding="utf-8")
        self.assertIn("**Speaker 1** [00:02]\n[00:02] Buongiorno a tutti.", md)
        self.assertIn("[00:10] (?) Grazie. _(words uncertain)_", md)
        srt = Path(next(o.path for o in result.outputs if o.kind == "srt")).read_text(encoding="utf-8")
        self.assertIn("00:00:02,500 --> 00:00:04,500\nP1: Buongiorno a tutti.", srt)

    def test_a_recording_with_its_tracks_has_the_microphone_as_the_user_and_the_others_told_apart(self):
        tracks = (ffmpeg.Track(0, "Microphone", "me"), ffmpeg.Track(1, "Output", "others"))
        engines = StandIns(self, tracks=tracks)
        mix = self.source.with_name("call_together.m4a")
        mix.write_bytes(b"the mix")
        result = jobs.run(jobs.Request(self.source, self.out, speakers=True, speaker_count=3))
        self.assertEqual(engines.calls["streams"], [0, 1])          # each track decoded on its own
        self.assertEqual(engines.calls["speakers"], 2)              # the people less the user, on the output only
        lines = [(round(s.start, 1), s.speaker) for s in result.segments]
        self.assertEqual(lines, [(2.5, "Speaker 1"), (2.5, "Speaker 2"), (10.1, "Speaker 1"), (10.1, "Speaker 3")])
        self.assertEqual(result.me, "Speaker 1")
        self.assertEqual(result.speakers, 3)
        self.assertEqual(result.listen, mix)
        meeting = json.loads(result.session.read_text(encoding="utf-8"))
        self.assertEqual([t["role"] for t in meeting["options"]["tracks"]], ["me", "others"])

    def test_microphone_names_are_written_automatically_but_original_labels_stay_in_the_session(self):
        tracks = (ffmpeg.Track(0, "Microphone", "me"), ffmpeg.Track(1, "Output", "others"))
        StandIns(self, tracks=tracks)
        result = jobs.run(jobs.Request(self.source, self.out, my_name="  Daniel  "))
        for kind in ("md", "txt", "srt"):
            text = Path(next(o.path for o in result.outputs if o.kind == kind)).read_text(encoding="utf-8")
            self.assertIn("Daniel", text)
        self.assertEqual(result.names, {"Speaker 1": "Daniel"})
        opened, request = jobs.open_session(result.session)
        self.assertEqual(opened.names, {"Speaker 1": "Daniel"})
        self.assertEqual(opened.segments, result.segments)
        self.assertEqual(opened.me, "Speaker 1")
        self.assertEqual(request.source, self.source)

    def test_a_single_microphone_track_is_the_user_without_diarisation(self):
        engines = StandIns(self, tracks=(ffmpeg.Track(0, "Microphone", "me"),))
        result = jobs.run(jobs.Request(self.source, self.out, speakers=True))
        self.assertNotIn("speakers", engines.calls)
        self.assertEqual(result.me, "Speaker 1")
        self.assertEqual(result.names, {"Speaker 1": "Io"})
        self.assertEqual({s.speaker for s in result.segments}, {"Speaker 1"})

    def test_two_tracks_without_telling_the_voices_apart_are_one_voice_each(self):
        tracks = (ffmpeg.Track(0, "Microphone", "me"), ffmpeg.Track(1, "Output", "others"))
        engines = StandIns(self, tracks=tracks)
        result = jobs.run(jobs.Request(self.source, self.out))
        self.assertNotIn("speakers", engines.calls)
        self.assertEqual([s.speaker for s in result.segments], ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"])
        self.assertEqual((result.me, result.speakers, result.listen), ("Speaker 1", 2, self.source))

    def test_the_loudness_is_left_as_recorded_unless_asked(self):
        engines = StandIns(self)
        jobs.run(jobs.Request(self.source, self.out))
        self.assertNotIn("measured", engines.calls)
        jobs.run(jobs.Request(self.source, self.out, normalise=True))
        self.assertEqual(engines.calls["measured"], ["audio0.wav"])

    def test_the_files_share_one_name_never_over_one_that_is_there_and_the_source_is_untouched(self):
        StandIns(self)
        self.out.mkdir()
        (self.out / "call_transcript.srt").write_text("another day's", encoding="utf-8")
        result = jobs.run(jobs.Request(self.source, self.out))
        names = sorted(Path(o.path).name for o in result.outputs) + [result.session.name]
        self.assertEqual(names, ["call_transcript_R01.md", "call_transcript_R01.srt", "call_transcript_R01.txt",
                                 "call_transcript_R01.json"])
        self.assertEqual((self.out / "call_transcript.srt").read_text(encoding="utf-8"), "another day's")
        self.assertEqual(self.source.read_bytes(), b"the user's recording")
        meeting = json.loads(result.session.read_text(encoding="utf-8"))
        self.assertEqual(meeting["kind"], "meeting-session")
        self.assertEqual({o["kind"] for o in meeting["outputs"]}, {"md", "txt", "srt"})
        self.assertIn("transcribe", meeting["timings"])

    def test_a_fixed_number_of_voices_reaches_the_engine(self):
        engines = StandIns(self)
        jobs.run(jobs.Request(self.source, self.out, speakers=True, speaker_count=3))
        self.assertEqual(engines.calls["speakers"], 3)

    def test_the_video_is_skipped_and_said_for_a_file_without_one(self):
        StandIns(self)
        progress = jobs.Progress()
        result = jobs.run(jobs.Request(self.source, self.out, video=True), progress)
        self.assertNotIn("video", {o.kind for o in result.outputs})
        self.assertTrue(any("no video track" in note for note in result.notes))
        self.assertIn("video", progress.skipped)
        self.assertEqual(progress.overall(), 1.0)

    def test_the_large_model_gets_its_cure_and_the_processor_when_asked(self):
        engines = StandIns(self)
        jobs.run(jobs.Request(self.source, self.out, model="large", cpu=True))
        self.assertTrue(engines.calls["options"].precise)
        self.assertIsNone(engines.calls["options"].device)

    def test_cancelled_leaves_nothing_in_the_folder_nor_in_the_temporary_one(self):
        StandIns(self)
        stop = threading.Event()
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(**kwargs):
            made.append(real_mkdtemp(**kwargs))
            return made[-1]

        progress = jobs.Progress(on_change=lambda p: stop.set() if p.stage == "transcribe" else None)
        with mock.patch.object(jobs.tempfile, "mkdtemp", mkdtemp), self.assertRaises(Cancelled):
            jobs.run(jobs.Request(self.source, self.out), progress, stop)
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertFalse(os.path.exists(made[0]))

    def test_what_cannot_start_is_said_and_nothing_is_written(self):
        with self.assertRaises(Failure) as caught:
            jobs.run(jobs.Request(self.folder / "gone.mp3", self.out))
        self.assertIn("gone.mp3", str(caught.exception))
        with self.assertRaises(Failure) as caught:
            jobs.run(jobs.Request(self.source, self.out, model="huge"))
        with self.assertRaises(Failure) as caught:
            jobs.run(jobs.Request(self.source, self.out), models_dir=self.folder / "no-models")
        self.assertIn(models.WHISPER_TURBO.filename, str(caught.exception))
        self.assertFalse(self.out.exists())


class NamesTest(unittest.TestCase):
    """The names given to the voices: new files, the automatic ones untouched."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.source = self.folder / "call.m4a"
        self.source.write_bytes(b"the user's recording")
        StandIns(self)
        self.result = jobs.run(jobs.Request(self.source, self.folder, speakers=True))
        self.before = {p.name: p.read_bytes() for p in self.folder.iterdir()}

    def test_the_names_make_new_files_and_the_automatic_ones_stay_as_they_were(self):
        named = jobs.save_names(self.result, {"Speaker 1": "  Anna  Rossi ", "Speaker 2": ""})
        self.assertEqual(sorted(Path(o.path).name for o in named.outputs),
                         ["call_transcript_names.md", "call_transcript_names.srt", "call_transcript_names.txt"])
        self.assertEqual({o.kind for o in named.outputs}, {"md-named", "txt-named", "srt-named"})
        for name, content in self.before.items():
            self.assertEqual((self.folder / name).read_bytes(), content, name)
        md = (self.folder / "call_transcript_names.md").read_text(encoding="utf-8")
        self.assertIn("**Anna Rossi** [00:02]", md)
        self.assertIn("**Speaker 2** [00:10]", md)
        self.assertIn("with the names given by hand", md)
        srt = (self.folder / "call_transcript_names.srt").read_text(encoding="utf-8")
        self.assertIn("Anna Rossi: Buongiorno a tutti.", srt)
        self.assertIn("P2: ", srt)
        meeting = json.loads(self.result.session.read_text(encoding="utf-8"))
        self.assertEqual(meeting["names"], {"Speaker 1": "Anna Rossi"})
        self.assertIn("md-named", {o["kind"] for o in meeting["outputs"]})
        self.assertEqual(named.notes, [])
        self.assertIn("md-named", {o.kind for o in self.result.outputs})

    def test_saved_twice_the_second_copy_takes_a_free_name(self):
        jobs.save_names(self.result, {"Speaker 1": "Anna"})
        again = jobs.save_names(self.result, {"Speaker 1": "Anna Maria"})
        self.assertEqual(sorted(Path(o.path).name for o in again.outputs),
                         ["call_transcript_names_R01.md", "call_transcript_names_R01.srt",
                          "call_transcript_names_R01.txt"])
        self.assertIn("Anna:", (self.folder / "call_transcript_names.srt").read_text(encoding="utf-8"))

    def test_no_name_given_is_said_and_nothing_is_written(self):
        with self.assertRaises(Failure):
            jobs.save_names(self.result, {"Speaker 1": "  "})
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), sorted(self.before))

    def test_a_meeting_file_that_cannot_be_updated_is_said_and_the_files_are_there(self):
        self.result.session.write_text("damaged", encoding="utf-8")
        named = jobs.save_names(self.result, {"Speaker 2": "Bob"})
        self.assertEqual(len(named.notes), 1)
        self.assertTrue(all(Path(o.path).is_file() for o in named.outputs))
    def test_reopening_needs_neither_models_nor_audio_and_keeps_original_voices_and_doubts(self):
        original = self.result.session
        jobs.save_names(self.result, {"Speaker 1": "Anna", "Speaker 2": "Anna"})
        self.assertNotEqual(self.result.session, original)
        self.source.unlink()
        for output in self.result.outputs:
            Path(output.path).unlink()
        opened, request = jobs.open_session(self.result.session)
        self.assertEqual(opened.segments, self.result.segments)
        self.assertEqual(opened.header, self.result.header)
        self.assertEqual(opened.names, {"Speaker 1": "Anna", "Speaker 2": "Anna"})
        jobs.save_names(opened, {"Speaker 2": "Bob"})
        text = Path(opened.outputs[-1].path).read_text(encoding="utf-8")
        self.assertIn("P1:", text)
        self.assertIn("Bob:", text)

    def test_old_sessions_use_the_automatic_subtitles_and_refuse_missing_ones(self):
        from scriba.data import session
        old = session.Session(source=str(self.source), duration=20, outputs=self.result.outputs,
                              names={"Speaker 1": "Anna", "Speaker 2": "Anna"})
        path = self.folder / "old.json"
        session.save(old, path)
        opened, request = jobs.open_session(path)
        self.assertEqual([s.speaker for s in opened.segments], ["Speaker 1", "Speaker 2"])
        self.assertTrue(opened.segments[1].doubts)
        Path(next(o.path for o in old.outputs if o.kind == "srt")).unlink()
        with self.assertRaises(Failure):
            jobs.open_session(path)

    def test_all_names_can_be_cleared_in_a_new_revision(self):
        jobs.save_names(self.result, {"Speaker 1": "Anna"})
        previous = self.result.session
        before = previous.read_bytes()
        jobs.save_names(self.result, {})
        self.assertNotEqual(self.result.session, previous)
        self.assertEqual(previous.read_bytes(), before)
        self.assertEqual(json.loads(self.result.session.read_text(encoding="utf-8"))["names"], {})

    def test_a_saved_stem_cannot_write_outside_the_meeting_folder(self):
        raw = json.loads(self.result.session.read_text(encoding="utf-8"))
        for stem in ("../elsewhere", "..", "C:\\other", "a/b", "a\\b", ""):
            with self.subTest(stem=stem):
                raw["transcript"]["stem"] = stem
                self.result.session.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(Failure):
                    jobs.open_session(self.result.session)



class DevicesTest(unittest.TestCase):
    """The test of the cards at the first start: on the app's sample, the
    fastest that writes its words."""

    @staticmethod
    def trial(name, seconds, text="Buongiorno", error="", integrated=False, length=30.0):
        device = whisper.Device(1, name, "NVIDIA", integrated) if name else None
        return whisper.Trial(device, seconds, seconds * 2, text, error, length)

    @staticmethod
    def tried(name, speed, difference=None, error="", text="Buongiorno"):
        return jobs.Tried(name or "cpu", f"Vulkan: {name}" if name else "cpu", speed, 1.0, difference, error, text)

    def test_the_fastest_that_writes_the_sample_right_is_chosen(self):
        iris, rtx, cpu = self.tried("Iris Xe", 30.0, 0.31), self.tried("RTX A2000", 17.5, 0.02), self.tried("", 1.2, 0.0)
        self.assertIs(jobs.best([iris, rtx, cpu]), rtx)             # faster, but a third of its words wrong
        self.assertIs(jobs.best([iris, self.tried("RTX A2000", 17.5, 0.10), cpu]).device, "RTX A2000")  # the limit is in
        choice = jobs.Choice(rtx.device, rtx.label, rtx.speed, 300.0, (iris, rtx, cpu))
        self.assertEqual(choice.passed_over, [iris])
        notes = jobs.choice_notes(choice)
        self.assertEqual(len(notes), 1)
        self.assertIn("Iris Xe", notes[0])
        self.assertIn("31 %", notes[0])

    def test_when_no_card_writes_it_right_the_closest_is_chosen_and_that_is_said(self):
        iris, rtx = self.tried("Iris Xe", 30.0, 0.5), self.tried("RTX A2000", 17.5, 0.2)
        same = self.tried("Other", 20.0, 0.2)
        self.assertIs(jobs.best([iris, rtx, same]), same)             # as close as the RTX, and faster
        choice = jobs.Choice(rtx.device, rtx.label, rtx.speed, 300.0, (iris, rtx))
        self.assertIn("came closest (20 % wrong)", jobs.choice_notes(choice)[-1])

    def test_on_a_recording_the_fastest_that_heard_something_is_chosen(self):
        tried = [jobs.tried_of(t, None) for t in (
            self.trial("Intel Iris Xe", 6.7, integrated=True), self.trial("RTX A2000", 1.7, text=""),
            self.trial("", 25.6), self.trial("Broken", 0.0, error="exit 1"))]
        self.assertTrue(all(t.difference is None for t in tried))
        self.assertEqual(jobs.best(tried).device, "Intel Iris Xe")       # the RTX heard nothing
        self.assertIsNone(jobs.best([jobs.tried_of(self.trial("Broken", 0.0, error="exit 1"), None)]))

    def test_a_card_that_heard_nothing_of_the_sample_is_all_wrong(self):
        tried = jobs.tried_of(self.trial("RTX A2000", 1.0, text=""), jobs.sample_text())
        self.assertEqual(tried.difference, 1.0)
        self.assertTrue(tried.wrong)
        self.assertIsNone(jobs.tried_of(self.trial("Broken", 0.0, error="exit 1"), "text").difference)

    def test_the_test_runs_on_the_whole_sample_and_keeps_every_speed_and_its_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            asked = {}

            def decode(source, dest, duration=0.0, progress=None, cancelled=None, start=0.0, length=0.0):
                import numpy as np
                asked.update(source=Path(source), start=start, length=length)
                audio.write(dest, np.zeros(int(27.6 * RATE), np.int16))
                return Path(dest)

            said = jobs.sample_text()
            trials = [self.trial("Iris Xe", 4.0, text=said.replace("pezzo di legno", "pezzo di lego grande"),
                                 length=27.6),
                      self.trial("RTX A2000", 1.6, text=said, length=27.6), self.trial("", 18.0, text=said, length=27.6)]
            speeds, cards = folder / "speeds.json", folder / "cards.json"
            with mock.patch.object(ffmpeg, "decode", decode), \
                    mock.patch.object(models, "present", lambda m, w=None: True), \
                    mock.patch.object(whisper, "list_devices", lambda cancelled=None: [t.device for t in trials[:2]]), \
                    mock.patch.object(whisper, "benchmark", lambda *a, **k: trials):
                choice = jobs.try_devices(None, "turbo", folder, speeds, cards_file=cards)
            self.assertEqual(asked, {"source": jobs.SAMPLE, "start": 0.0, "length": 0.0})
            self.assertEqual((choice.device, choice.label), ("RTX A2000", "Vulkan: RTX A2000"))
            self.assertAlmostEqual(choice.speed, 27.6 / 1.6)
            iris = choice.tried[0]
            self.assertAlmostEqual(iris.difference, 2 / 54)            # a word changed, one put in
            self.assertFalse(iris.wrong)                                # within the limit, and slower
            self.assertEqual(sorted(jobs.load_speeds(speeds)),
                             ["turbo|Vulkan: Iris Xe", "turbo|Vulkan: RTX A2000", "turbo|cpu"])
            self.assertEqual(jobs.load_choice(cards), choice)          # kept for the next start

    def test_a_faster_card_with_the_words_wrong_loses_to_a_slower_one_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            said = jobs.sample_text()
            trials = [self.trial("Iris Xe", 0.5, text="C'era una volta un re. Un re. Un re. Un re.", length=27.6),
                      self.trial("", 18.0, text=said, length=27.6)]
            with mock.patch.object(ffmpeg, "decode", lambda s, d, **k: Path(d)), \
                    mock.patch.object(audio, "read", lambda p: __import__("numpy").zeros(27 * RATE, "int16")), \
                    mock.patch.object(models, "present", lambda m, w=None: True), \
                    mock.patch.object(whisper, "list_devices", lambda cancelled=None: [trials[0].device]), \
                    mock.patch.object(whisper, "benchmark", lambda *a, **k: trials):
                choice = jobs.try_devices(None, "turbo", folder, None)
            self.assertEqual(choice.device, "cpu")
            self.assertEqual([t.device for t in choice.passed_over], ["Iris Xe"])
            self.assertIn("Iris Xe was faster", jobs.choice_notes(choice)[0])

    def test_on_a_recording_the_test_takes_30_seconds_from_a_quarter(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = folder / "call.mkv"
            source.write_bytes(b"x")
            asked = {}

            def decode(source, dest, duration=0.0, progress=None, cancelled=None, start=0.0, length=0.0):
                import numpy as np
                asked.update(start=start, length=length)
                audio.write(dest, np.zeros(int(length * RATE), np.int16))
                return Path(dest)

            trials = [self.trial("RTX A2000", 1.7), self.trial("", 25.6)]
            speeds = folder / "speeds.json"
            with mock.patch.object(ffmpeg, "probe", lambda s, c=None: ffmpeg.Media(3600.0, 1, True, 0)), \
                    mock.patch.object(ffmpeg, "decode", decode), \
                    mock.patch.object(models, "present", lambda m, w=None: True), \
                    mock.patch.object(whisper, "list_devices", lambda cancelled=None: [trials[0].device]), \
                    mock.patch.object(whisper, "benchmark", lambda *a, **k: trials):
                choice = jobs.try_devices(source, "turbo", folder, speeds)
            self.assertEqual(asked, {"start": 900.0, "length": whisper.SAMPLE_SECONDS})
            self.assertEqual((choice.device, choice.label), ("RTX A2000", "Vulkan: RTX A2000"))
            self.assertAlmostEqual(choice.speed, 30 / 1.7)
            self.assertIsNone(choice.tried[0].difference)
            self.assertEqual(sorted(jobs.load_speeds(speeds)), ["turbo|Vulkan: RTX A2000", "turbo|cpu"])
            # an hour: whisper at the speed measured, the voices at the default guess
            self.assertAlmostEqual(choice.hour, 3600 / (30 / 1.7) + 3600 / 20.0)

    def test_nothing_working_is_a_failure_with_the_reason(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "call.wav"
            audio.write(source, np.zeros(40 * RATE, np.int16))
            with mock.patch.object(ffmpeg, "probe", lambda s, c=None: ffmpeg.Media(40.0, 1, False, 0)), \
                    mock.patch.object(ffmpeg, "decode", lambda s, d, *a, **k: Path(source)), \
                    mock.patch.object(models, "present", lambda m, w=None: True), \
                    mock.patch.object(whisper, "list_devices", lambda cancelled=None: []), \
                    mock.patch.object(whisper, "benchmark", lambda *a, **k: [self.trial("", 0.0, error="exit 3")]):
                with self.assertRaises(Failure) as caught:
                    jobs.try_devices(source, "turbo", tmp, None)
            self.assertEqual(caught.exception.detail, "exit 3")

    def test_an_outcome_that_cannot_be_read_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.json"
            self.assertIsNone(jobs.load_choice(path))
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(jobs.load_choice(path))
            path.write_text('{"device": "x"}', encoding="utf-8")
            self.assertIsNone(jobs.load_choice(path))
            self.assertIsNone(jobs.load_choice(None))


class SampleTest(unittest.TestCase):
    """The sample of speech the app carries, and what goes with it."""

    def test_the_sample_is_there_whole_with_its_text_and_its_origin(self):
        import hashlib
        self.assertEqual(hashlib.sha256(jobs.SAMPLE.read_bytes()).hexdigest(),
                         "05731434bfa59a31d671de36883cf194f21676dd7585ed0a2589c6b70203d8bc")
        self.assertEqual(len(jobs.sample_text().split()), 54)
        sources = (jobs.SAMPLE.parent / "SOURCES.md").read_text(encoding="utf-8")
        for fact in ("LibriVox", "Simona Pagliari", "public domain", "archive.org"):
            self.assertIn(fact, sources)

    def test_the_reader_and_the_source_are_in_the_credits(self):
        from scriba.app import credits
        credit = next(c for c in credits.THIRD_PARTY if "LibriVox" in c.name)
        self.assertIn("Simona Pagliari", credit.name)
        self.assertEqual(credit.licence, "CC-PDDC")

    def test_the_builds_carry_the_sample(self):
        spec = (_setup.ROOT / "build-aux" / "windows" / "scriba.spec").read_text(encoding="utf-8")
        self.assertIn("samples", spec)
        manifest = (_setup.ROOT / "build-aux" / "flatpak" / "io.github.daniel_g_carrasco.scriba.yaml").read_text(
            encoding="utf-8")
        self.assertIn("samples/sample-it.flac", manifest)

    @real("ffmpeg", "whisper-cli", needs=(models.WHISPER_TURBO,))
    def test_every_card_and_the_processor_write_the_sample_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            choice = jobs.try_devices(None, "turbo", MODELS, Path(tmp) / "speeds.json",
                                      cards_file=Path(tmp) / "cards.json")
        for tried in choice.tried:
            self.assertEqual(tried.error, "", tried.label)
            self.assertLessEqual(tried.difference, jobs.WRONG, f"{tried.label}: {tried.text}")
        self.assertEqual(choice.tried[-1].device, "cpu")
        if len(choice.tried) > 1:
            self.assertNotEqual(choice.device, "cpu")               # a card is faster than the processor here


class ProgressTest(unittest.TestCase):
    def test_the_whole_is_the_steps_weighed_and_the_skipped_ones_do_not_count(self):
        seen = []
        progress = jobs.Progress(("decode", "transcribe", "video"), on_change=seen.append)
        progress.begin("decode")
        progress.finish()
        progress.begin("transcribe")
        progress.advance(50, 100)
        weights = jobs.WEIGHTS
        total = weights["decode"] + weights["transcribe"] + weights["video"]
        self.assertAlmostEqual(progress.overall(), (weights["decode"] + weights["transcribe"] / 2) / total)
        progress.skip("video")
        self.assertAlmostEqual(progress.overall(), (weights["decode"] + weights["transcribe"] / 2)
                               / (weights["decode"] + weights["transcribe"]))
        self.assertIn("decode", progress.finished)
        self.assertTrue(seen)


class SpeedsTest(unittest.TestCase):
    def test_the_speeds_are_kept_and_a_damaged_file_costs_only_the_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / jobs.SPEEDS_FILE
            self.assertEqual(jobs.load_speeds(path), {})
            jobs.remember_speeds(path, {"turbo|cpu": 0.8})
            jobs.remember_speeds(path, {"speakers|cpu": 20.0})
            self.assertEqual(jobs.load_speeds(path), {"turbo|cpu": 0.8, "speakers|cpu": 20.0})
            request = jobs.Request(Path("x.wav"), speakers=True)
            self.assertAlmostEqual(jobs.estimated_seconds(600, request, "cpu", jobs.load_speeds(path)), 750 + 30)
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(jobs.load_speeds(path), {})

    def test_the_files_go_next_to_the_recording_unless_asked_elsewhere(self):
        source = Path(tempfile.gettempdir()) / "call.mp4"
        self.assertEqual(jobs.output_folder(source, Path("elsewhere")), Path("elsewhere"))
        self.assertEqual(jobs.output_folder(source, None), source.resolve().parent)

    @unittest.skipUnless(_setup.WINDOWS, "Windows only")
    def test_for_a_recording_on_a_server_the_files_go_to_the_current_folder(self):
        from scriba.app import win32
        self.assertTrue(win32.on_network(r"\\server\share\call.mp4"))
        self.assertFalse(win32.on_network(os.path.join(tempfile.gettempdir(), "call.mp4")))
        with mock.patch.object(win32, "on_network", return_value=True):
            self.assertEqual(jobs.output_folder(Path(r"I:\x\call.mp4"), None), Path.cwd())

    def test_the_window_s_default_folder_is_next_to_the_recording_or_the_documents_for_a_server(self):
        source = Path(tempfile.gettempdir()) / "call.mp4"
        documents = Path(tempfile.gettempdir()) / "Documents"
        self.assertEqual(jobs.default_folder(source, documents), source.resolve().parent)
        if _setup.WINDOWS:
            from scriba.app import win32
            with mock.patch.object(win32, "on_network", return_value=True):
                self.assertEqual(jobs.default_folder(Path(r"\\server\share\call.mp4"), documents), documents)


class RealChainTest(unittest.TestCase):
    """The real engines on 90 s of the test clip."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        samples = audio.read(AUDIO)
        self.source = self.folder / "call.wav"
        audio.write(self.source, samples[: 90 * RATE])

    @real("ffmpeg", "ffprobe", "whisper-cli", needs=(models.WHISPER_TURBO, models.SILERO_VAD, models.WESPEAKER),
          speech=True)
    def test_a_recording_becomes_its_files_and_the_speed_is_kept(self):
        speeds = self.folder / "speeds.json"
        result = jobs.run(jobs.Request(self.source, self.folder / "out", speakers=True, terms=("Cantiere Esempio", "cavedi")),
                          models_dir=MODELS, speeds_file=speeds)
        self.assertGreaterEqual(result.speakers, 2)
        md = Path(next(o.path for o in result.outputs if o.kind == "md")).read_text(encoding="utf-8")
        self.assertIn("cavedi", md)
        self.assertIn("**Speaker 1** [00:", md)
        self.assertEqual(sorted(jobs.load_speeds(speeds)), ["speakers|cpu", f"turbo|{result.device}"])
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["call.wav", "out", "speeds.json"])

    @real("ffmpeg", "ffprobe", "whisper-cli", needs=(models.WHISPER_TURBO, models.SILERO_VAD), speech=True)
    def test_a_video_gets_its_copy_with_the_subtitles(self):
        from test_engines import run_ffmpeg
        video = self.folder / "call.mp4"
        run_ffmpeg("-f", "lavfi", "-i", "testsrc=duration=20:size=64x64:rate=5", "-t", "20", "-i", str(self.source),
                   "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video))
        result = jobs.run(jobs.Request(video, self.folder / "out", video=True), models_dir=MODELS)
        copy = Path(next(o.path for o in result.outputs if o.kind == "video"))
        self.assertEqual(copy.name, "call_subtitles.mkv")
        self.assertEqual(ffmpeg.probe(copy).subtitle_tracks, 1)


if __name__ == "__main__":
    unittest.main()
