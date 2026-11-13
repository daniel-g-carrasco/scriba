#!/usr/bin/env python3
"""The engines: a child process read and stopped, ffmpeg, the speech
filter, the voices, whisper-cli.

What can be checked without the programs is checked against their own
output, recorded on 11/11/2026 (tests/fixtures/whisper_cli_*: MSYS2's
whisper.cpp 1.9.4 on a machine with an Iris Xe and an RTX A2000). The
programs themselves run in the tests marked `real`, which are skipped
when a program, a model or the test recording is missing: the models in
SCRIBA_MODELS_DIR (never downloaded by a test: 600 MB), and for the tests
on speech a recording in SCRIBA_TEST_AUDIO (16 kHz mono WAV; the 10-minute
test clip, 4 people, for the numbers below). On the author's machine they
all run.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import _setup

from scriba.app import models
from scriba.app.engines import diarize, ffmpeg, process, vad, whisper
from scriba.app.task import Cancelled, Failure

MODELS = os.environ.get(models.MODELS_DIR_VARIABLE, "")
AUDIO = os.environ.get("SCRIBA_TEST_AUDIO", "")
HELP = _setup.fixture_text("whisper_cli_help.txt")
TWO_CARDS = _setup.fixture_text("whisper_cli_stderr_two_cards.txt")
ON_THE_PROCESSOR = _setup.fixture_text("whisper_cli_stderr_cpu.txt")
OUTPUT = json.loads(_setup.fixture_text("whisper_cli_output.json"))


def real(*programs: str, needs=(), speech: bool = False):
    """A test of the real programs: skipped, saying why, when one of them,
    a model or the recording is missing."""
    missing = [p for p in programs if (whisper.find() if p == "whisper-cli" else ffmpeg.find(p)) is None]
    if needs and not MODELS:
        missing.append(models.MODELS_DIR_VARIABLE)
    elif needs:
        missing += [m.filename for m in models.missing(needs, MODELS)]
    if speech and not os.path.isfile(AUDIO):
        missing.append("SCRIBA_TEST_AUDIO")
    return unittest.skipIf(missing, "needs " + ", ".join(missing))


def fixture_ffmpeg() -> str:
    """The ffmpeg that makes the tests' files (tones, made-up videos):
    the PATH's, a full one (MSYS2's), since the one the app prefers from
    the sources is the LGPL build, which can read and write what the app
    does and nothing more (no lavfi sources, no video encoders)."""
    import shutil
    return shutil.which("ffmpeg") or str(ffmpeg.find())


def run_ffmpeg(*args: str) -> None:
    subprocess.run([fixture_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True,
                   stdin=subprocess.DEVNULL)


class ProcessTest(unittest.TestCase):
    def test_the_lines_arrive_from_both_streams_and_the_exit_status_is_kept(self):
        out, err = [], []
        code = "import sys; print('one'); print('two'); sys.stderr.write('uno\\r\\ndue\\n'); sys.exit(3)"
        done = process.run([sys.executable, "-c", code], on_stdout=out.append, on_stderr=err.append)
        self.assertEqual((out, err, done.returncode), (["one", "two"], ["uno", "due"], 3))
        self.assertEqual(done.stderr_tail, ["uno", "due"])

    def test_a_cancelled_child_is_stopped_at_once(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        began = time.monotonic()
        with self.assertRaises(Cancelled):
            process.run([sys.executable, "-c", "import time; time.sleep(60)"], cancelled=stop)
        self.assertLess(time.monotonic() - began, 10)

    def test_a_program_that_is_not_there_is_an_os_error(self):
        with self.assertRaises(OSError):
            process.run([os.path.join(tempfile.gettempdir(), "no-such-program-here.exe")])


class FfmpegTest(unittest.TestCase):
    # what ffprobe 9.0.2 answered for an mp3 with its cover, and for a video
    COVER = ('{"streams": [{"codec_type": "audio", "disposition": {"attached_pic": 0}}, '
             '{"codec_type": "video", "disposition": {"attached_pic": 1}}], "format": {"duration": "3.000000"}}')
    VIDEO = ('{"streams": [{"codec_type": "video", "disposition": {"attached_pic": 0}}, '
             '{"codec_type": "audio", "disposition": {"attached_pic": 0}}], "format": {"duration": "3.000000"}}')

    def test_a_cover_picture_is_not_a_video(self):
        self.assertEqual(ffmpeg.parse_probe(self.COVER), ffmpeg.Media(3.0, 1, False, 0, (ffmpeg.Track(0),)))
        self.assertEqual(ffmpeg.parse_probe(self.VIDEO), ffmpeg.Media(3.0, 1, True, 0, (ffmpeg.Track(0),)))
        self.assertEqual(ffmpeg.parse_probe("{}"), ffmpeg.Media(0.0, 0, False, 0))

    def test_the_progress_lines(self):
        self.assertEqual(ffmpeg.progress_seconds("out_time_us=1500000"), 1.5)
        self.assertEqual(ffmpeg.progress_seconds("out_time_ms=2000000"), 2.0)     # microseconds too, as ffmpeg writes it
        self.assertIsNone(ffmpeg.progress_seconds("out_time=00:00:01.500000"))
        self.assertIsNone(ffmpeg.progress_seconds("out_time_us=N/A"))

    def test_the_subtitles_go_after_those_the_video_has_with_their_language(self):
        argv = ffmpeg.subtitle_arguments("ffmpeg", "in.mp4", "s.srt", "out.mkv", 2, "it", "Transcript")
        self.assertIn("-metadata:s:s:2", argv)
        self.assertIn("language=ita", argv)
        self.assertEqual(argv[argv.index("-c") + 1], "copy")            # no re-encoding
        self.assertIn("language=und", ffmpeg.subtitle_arguments("ffmpeg", "a", "b", "c", 0, "auto", "T"))

    @real("ffmpeg", "ffprobe")
    def test_any_sound_becomes_16_khz_mono_and_the_cover_is_no_video(self):
        from scriba.app.engines import audio
        with tempfile.TemporaryDirectory() as tmp:
            tone, cover = os.path.join(tmp, "tone.mp3"), os.path.join(tmp, "cover.mp3")
            run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=3:sample_rate=44100", "-ac", "2", tone)
            run_ffmpeg("-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-frames:v", "1", os.path.join(tmp, "c.png"))
            run_ffmpeg("-i", tone, "-i", os.path.join(tmp, "c.png"), "-map", "0", "-map", "1", "-c", "copy",
                       "-disposition:v", "attached_pic", cover)
            media = ffmpeg.probe(cover)
            self.assertEqual((media.sound_tracks, media.video), (1, False))
            seen = []
            wav = ffmpeg.decode(cover, os.path.join(tmp, "out.wav"), media.duration, lambda d, t: seen.append(d))
            samples = audio.read(wav)
            self.assertAlmostEqual(len(samples) / audio.RATE, 3.0, delta=0.1)
            self.assertTrue(seen)

    @real("ffmpeg", "ffprobe")
    def test_a_file_without_sound_is_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            silent = os.path.join(tmp, "silent.mp4")
            run_ffmpeg("-f", "lavfi", "-i", "testsrc=duration=1:size=32x32:rate=5", "-c:v", "mpeg4", silent)
            with self.assertRaises(Failure) as caught:
                ffmpeg.decode(silent, os.path.join(tmp, "out.wav"))
            self.assertIn("no sound track", str(caught.exception))

    @real("ffmpeg", "ffprobe")
    def test_the_video_with_subtitles_is_a_new_file_and_a_failure_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "call.mp4")
            run_ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=32x32:rate=5", "-f", "lavfi", "-i",
                       "sine=duration=2", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", video)
            srt = Path(tmp, "call.srt")
            srt.write_text("1\n00:00:00,000 --> 00:00:01,500\nP1: Buongiorno a tutti.\n", encoding="utf-8")
            Path(tmp, "call_subtitles.mkv").write_bytes(b"the user's own file")
            media = ffmpeg.probe(video)
            made = ffmpeg.subtitled_copy(video, srt, tmp, "call_subtitles", "it", media)
            self.assertEqual(made.name, "call_subtitles_R01.mkv")
            self.assertEqual(Path(tmp, "call_subtitles.mkv").read_bytes(), b"the user's own file")
            inside = subprocess.run([str(ffmpeg.find("ffprobe")), "-v", "error", "-show_entries",
                                     "stream=codec_type:stream_tags=language", "-of", "json", str(made)],
                                    capture_output=True, check=True).stdout
            streams = json.loads(inside)["streams"]
            self.assertIn({"codec_type": "subtitle", "tags": {"language": "ita"}}, streams)
            srt.write_text("not subtitles at all", encoding="utf-8")
            before = sorted(os.listdir(tmp))
            with self.assertRaises(Failure):
                ffmpeg.subtitled_copy(video, srt, tmp, "call_subtitles", "it", media)
            self.assertEqual(sorted(os.listdir(tmp)), before)


class LevelTest(unittest.TestCase):
    def test_the_level_of_a_line_is_that_of_its_loud_part(self):
        import numpy as np

        from scriba.app.engines import audio
        rate = audio.RATE
        tone = 0.1 * 32768 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate)          # -23 dBFS
        samples = np.concatenate([tone, np.zeros(rate // 2)]).astype(np.int16)        # with a pause after it
        self.assertAlmostEqual(audio.level(samples, 0.0, 1.5), -23.0, delta=0.2)
        self.assertLess(audio.level(samples, 1.0, 1.5), -100)
        self.assertIsNone(audio.level(samples, 0.0, 0.01))


class VadTest(unittest.TestCase):
    def test_a_stretch_of_speech_with_its_pads(self):
        probs = [0.0] * 10 + [0.9] * 40 + [0.0] * 100
        n = len(probs) * vad.FRAME
        start, end = vad.stretches(probs, n, vad.FOR_VOICES)[0]
        self.assertEqual(start, 10 * vad.FRAME - 320)          # 20 ms of pad at 16 kHz
        self.assertEqual(end, 50 * vad.FRAME + 320)
        self.assertEqual(vad.seconds([(start, end)], n, vad.FOR_VOICES), [(0.3, 1.6)])     # diarize's tenths

    def test_a_short_silence_does_not_cut_and_the_filter_for_whisper_waits_two_seconds(self):
        speech, pause = [0.9] * 40, [0.0] * 20                  # 0.64 s of silence
        probs = [0.0] * 10 + speech + pause + speech + [0.0] * 200
        n = len(probs) * vad.FRAME
        self.assertEqual(len(vad.stretches(probs, n, vad.FOR_VOICES)), 2)
        self.assertEqual(len(vad.stretches(probs, n, vad.FOR_SPEECH)), 1)

    def test_nothing_heard_nothing_found(self):
        self.assertEqual(vad.stretches([0.1] * 500, 500 * vad.FRAME, vad.FOR_SPEECH), [])


class DiarizeTest(unittest.TestCase):
    def test_the_windows_are_cut_as_diarize_cuts_them(self):
        self.assertEqual(diarize.windows([(0.0, 0.3)]), [])
        self.assertEqual(diarize.windows([(0.0, 1.5)]), [(0, 0.0, 1.5)])
        cut = diarize.windows([(5.0, 5.2), (10.0, 13.0)])
        self.assertEqual([round(a, 1) for _i, a, _b in cut], [10.0, 10.6, 11.2, 11.8, 12.4])
        self.assertEqual({i for i, _a, _b in cut}, {1})
        self.assertEqual(cut[-1][2], 13.0)

    def test_the_filter_banks_have_kaldi_s_frames(self):
        import numpy as np
        wave = np.random.default_rng(1).normal(0, 3000, 16000)
        self.assertEqual(diarize.fbank(wave).shape, (98, 80))      # 1 s: (16000 - 400) // 160 + 1 frames
        self.assertEqual(diarize.fbank(wave[:300]).shape, (0, 80))

    @real(needs=(models.SILERO_VAD, models.WESPEAKER), speech=True)
    def test_on_the_test_clip_four_voices_and_diarize_s_58_segments(self):
        from scriba.app.engines import audio
        samples = audio.read(AUDIO)
        result = diarize.diarize(samples, models.path_of(models.SILERO_VAD, MODELS),
                                 models.path_of(models.WESPEAKER, MODELS), threads=4)
        self.assertEqual((result.speakers, len(result.turns)), (4, 58))
        self.assertEqual(result.turns[0].label, "SPEAKER_01")       # diarize's own label for the first voice
        fixed = diarize.diarize(samples[: 120 * audio.RATE], models.path_of(models.SILERO_VAD, MODELS),
                                models.path_of(models.WESPEAKER, MODELS), speakers=2, threads=4)
        self.assertEqual(fixed.speakers, 2)


class WhisperTest(unittest.TestCase):
    MODEL = Path("C:/models/ggml-large-v3-turbo-q5_0.bin")

    def test_the_cards_are_read_and_the_one_with_its_own_memory_is_chosen(self):
        cards = whisper.parse_devices(TWO_CARDS)
        self.assertEqual([(c.index, c.integrated) for c in cards], [(0, True), (1, False)])
        self.assertEqual(cards[1].name, "NVIDIA RTX A2000 8GB Laptop GPU")
        self.assertEqual(whisper.choose(cards), cards[1])
        self.assertEqual(whisper.choose(cards, "Intel(R) Iris(R) Xe Graphics"), cards[0])
        self.assertIsNone(whisper.choose(cards, "cpu"))
        self.assertEqual(whisper.choose(cards[:1]), cards[0])           # an integrated card beats the processor
        self.assertIsNone(whisper.choose([]))
        self.assertEqual(whisper.parse_devices(ON_THE_PROCESSOR)[1].vendor, "NVIDIA")

    def test_the_options_are_read_from_the_program_s_own_help(self):
        known = whisper.parse_flags(HELP)
        for flag in ("-ojf", "-pp", "-mc", "-ng", "--prompt", "--carry-initial-prompt", "-of", "-l", "-t"):
            self.assertIn(flag, known)

    def test_the_arguments_for_turbo_on_a_card_large_with_its_cure_and_the_processor(self):
        known = whisper.parse_flags(HELP)
        card = whisper.parse_devices(TWO_CARDS)[1]
        options = whisper.Options(self.MODEL, "it", ("Officina Esempio", "Cantiere Esempio", "Officina Esempio"), 4, card)
        argv = whisper.arguments(Path("whisper-cli"), Path("speech.wav"), Path("out"), options, known)
        self.assertEqual(argv[argv.index("--prompt") + 1], "Officina Esempio, Cantiere Esempio")
        self.assertNotIn("-ng", argv)
        self.assertEqual(argv[argv.index("-mc") + 1], "0")              # turbo loops on its own context too
        self.assertIn("--carry-initial-prompt", argv)
        self.assertIn("-ojf", argv)
        large = whisper.Options(self.MODEL, "auto", ("Officina Esempio",), 4, card, precise=True)
        argv = whisper.arguments(Path("whisper-cli"), Path("speech.wav"), Path("out"), large, known)
        self.assertEqual(argv[argv.index("-mc") + 1], "0")
        self.assertIn("--carry-initial-prompt", argv)
        self.assertEqual(argv[argv.index("-l") + 1], "auto")
        cpu = whisper.Options(self.MODEL, "it", (), 10, None)
        self.assertIn("-ng", whisper.arguments(Path("whisper-cli"), Path("a.wav"), Path("o"), cpu, known))
        self.assertEqual(whisper.environment(options, {})[whisper.DEVICES_VARIABLE], "1")
        self.assertNotIn(whisper.DEVICES_VARIABLE, whisper.environment(cpu, {whisper.DEVICES_VARIABLE: "0"}))

    def test_a_whisper_cli_without_the_full_json_is_said_to_be_too_old(self):
        known = whisper.parse_flags(HELP.replace("-ojf", "").replace("--output-json-full", ""))
        with self.assertRaises(Failure) as caught:
            whisper.arguments(Path("w"), Path("a.wav"), Path("o"), whisper.Options(self.MODEL), known)
        self.assertIn("too old", str(caught.exception))

    def test_the_prompt_keeps_whole_terms(self):
        text = whisper.prompt([f"term{n:03d}" for n in range(200)])
        self.assertLessEqual(len(text), whisper.PROMPT_MAX_CHARACTERS)
        self.assertTrue(text.endswith(text.split(", ")[-1]) and len(text.split(", ")[-1]) == 7)

    def test_the_output_its_times_and_the_confidence_of_its_words(self):
        segments, language = whisper.parse_output(OUTPUT)
        self.assertEqual(language, "it")
        self.assertEqual(len(segments), 1)
        self.assertEqual((segments[0].start, segments[0].end), (0.0, 29.08))
        self.assertEqual(segments[0].text, " Oggi proviamo una riunione inventata e controlliamo la trascrizione.")
        self.assertIsNone(segments[0].no_speech_prob)                   # whisper-cli does not write it
        self.assertTrue(-1.0 < segments[0].avg_logprob < 0.0)
        tokens = [{"text": "[_BEG_]", "p": 0.001}, {"text": " ciao", "p": 0.5}, {"text": "[_TT_150]", "p": 0.01}]
        self.assertAlmostEqual(whisper.confidence(tokens), math.log(0.5))
        self.assertIsNone(whisper.confidence([{"text": "[_BEG_]", "p": 0.9}]))

    def test_the_times_of_the_words_from_their_tokens(self):
        def token(text, start, end):
            return {"text": text, "offsets": {"from": start, "to": end}}
        tokens = [token("[_BEG_]", 0, 0), token(" por", 100, 200), token("ta", 200, 320), token(" c", 790, 790),
                  token("'", 790, 850), token("è", 850, 910), token(",", 910, 930), token(" sì", 1000, 1200),
                  token("[_TT_60]", 1200, 1200)]
        self.assertEqual(whisper.word_times(" porta c'è, sì", tokens), ((0.1, 0.32), (0.79, 0.93), (1.0, 1.2)))
        self.assertEqual(whisper.word_times(" porta c'è, no", tokens), ())        # tokens that do not spell the text
        segments, _language = whisper.parse_output({"transcription": [
            {"offsets": {"from": 0, "to": 1200}, "text": " porta c'è, sì", "tokens": tokens}]})
        self.assertEqual(segments[0].words, ((0.1, 0.32), (0.79, 0.93), (1.0, 1.2)))

    def test_the_language_found_and_the_progress_lines(self):
        # as whisper-cli 1.9.4 printed them with -l auto and -pp
        line = "whisper_full_with_state: auto-detected language: it (p = 0.999109)"
        self.assertEqual(whisper._LANGUAGE.search(line).groups(), ("it", "0.999109"))
        self.assertEqual(whisper._PROGRESS.search("whisper_print_progress_callback: progress =  35%").group(1), "35")
        self.assertIn("no GPU found", ON_THE_PROCESSOR)

    @real("whisper-cli")
    def test_the_cards_of_this_computer(self):
        cards = whisper.list_devices()
        self.assertIsInstance(cards, list)
        if cards:
            self.assertEqual(whisper.choose(cards), next((c for c in cards if not c.integrated), cards[0]))


class WhisperRealTest(unittest.TestCase):
    """whisper-cli on 30 s of the test clip, on the card and on the processor."""

    def setUp(self):
        from scriba.app.engines import audio
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        samples = audio.read(AUDIO)
        self.sample = self.work / "sample.wav"
        audio.write(self.sample, samples[60 * audio.RATE: 90 * audio.RATE])
        self.model = models.path_of(models.WHISPER_TURBO, MODELS)

    @real("whisper-cli", needs=(models.WHISPER_TURBO,), speech=True)
    def test_a_transcript_with_the_terms_on_the_best_card(self):
        card = whisper.choose(whisper.list_devices())
        states = []
        result = whisper.transcribe(self.sample, whisper.Options(self.model, "it", ("Cantiere Esempio", "cavedi"), 4, card),
                                    self.work, on_state=states.append)
        text = " ".join(s.text for s in result.segments)
        self.assertIn("cavedi", text)
        self.assertEqual(result.device, card)
        self.assertEqual(states[:2], ["loading", "transcribing"])
        self.assertEqual(result.fallback, "")

    @real("whisper-cli", needs=(models.WHISPER_TURBO,), speech=True)
    def test_a_card_that_is_not_there_falls_back_on_the_processor_and_says_so(self):
        ghost = whisper.Device(7, "No such card", "Nobody", False)
        result = whisper.transcribe(self.sample, whisper.Options(self.model, "it", (), 4, ghost), self.work)
        self.assertIsNone(result.device)
        self.assertTrue(result.fallback)
        self.assertTrue(result.segments)

    @real("whisper-cli", needs=(models.WHISPER_TURBO,), speech=True)
    def test_cancelled_while_loading_leaves_no_output(self):
        stop = threading.Event()
        with self.assertRaises(Cancelled):
            whisper.transcribe(self.sample, whisper.Options(self.model, "it", (), 4, None), self.work,
                               cancelled=stop, on_state=lambda state: stop.set())
        self.assertFalse((self.work / "whisper.json").exists())

    @real("whisper-cli", needs=(models.WHISPER_TURBO,), speech=True)
    def test_the_benchmark_measures_every_card_and_the_processor(self):
        cards = whisper.list_devices()
        trials = whisper.benchmark(self.sample, self.model, cards, self.work)
        self.assertEqual([t.device for t in trials], [*cards, None])
        for trial in trials:
            self.assertEqual(trial.error, "")
            self.assertGreater(trial.speed, 0)
            self.assertIn("discorso", trial.text)
            self.assertGreater(trial.first, 0)
        if cards:
            best = whisper.choose(cards)
            self.assertGreater(next(t for t in trials if t.device == best).speed, trials[-1].speed)


class LightImportTest(unittest.TestCase):
    def test_the_engines_load_no_heavy_library_until_they_work(self):
        code = ("import sys; import scriba.app.models, scriba.app.engines.whisper, scriba.app.engines.ffmpeg, "
                "scriba.app.engines.vad, scriba.app.engines.diarize; "
                "print(','.join(m for m in ('numpy', 'sklearn', 'scipy', 'ctypes', 'urllib.request', 'subprocess', "
                "'gi') if m in sys.modules))")
        environ = {**os.environ, "PYTHONPATH": str(_setup.ROOT / "src")}
        loaded = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=environ, check=True)
        self.assertEqual(loaded.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
