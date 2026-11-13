"""The computer's sound heard by the microphone: when it is there
(core/echo.py), how it is measured, taken out and spliced in
(app/engines/echo.py), and what the chain does with it (app/jobs.py).

No recording of a person: the "room" is made up, the computer's sound is
the app's own sample of speech (or noise) brought to the microphone late
and filtered, as loudspeakers would. The canceller runs where GStreamer
has it; the test says why when it is skipped."""

import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.app.engines import echo as canceller
from scriba.core import echo

RATE = 16000
DELAY = 0.232       # the loudspeakers' way to the microphone in the real meeting


def window(verdict_value: float | None, start: float = 0.0, mic: float = -30.0, output: float = -30.0):
    return echo.Window(start, start + echo.WINDOW, verdict_value, 0.232, mic, output)


def needs_canceller():
    lacking = canceller.missing()
    if lacking:
        raise unittest.SkipTest(f"GStreamer lacks {lacking}")


def room(computer, rate: int = RATE):
    """The computer's sound as a microphone across the desk hears it: late
    by DELAY, softer, with a reflection."""
    import numpy as np
    from scipy.signal import lfilter
    late = np.concatenate([np.zeros(int(DELAY * rate)), computer.astype(np.float64)])[:len(computer)]
    return lfilter([0.5, 0.0, 0.2], [1.0, -0.3], late)


def speech(seconds: float):
    """The app's sample of speech, 16 kHz, as long as asked (decoded by the
    real ffmpeg: the test is skipped without it)."""
    from scriba.app.engines import audio, ffmpeg
    from scriba.app.jobs import SAMPLE
    if ffmpeg.find() is None:
        raise unittest.SkipTest("needs ffmpeg")
    tmp = tempfile.TemporaryDirectory()
    try:
        samples = audio.read(ffmpeg.decode(SAMPLE, Path(tmp.name) / "sample.wav"))
    finally:
        tmp.cleanup()
    return samples[:int(seconds * RATE)]


def db(x) -> float:
    import numpy as np
    x = np.asarray(x, dtype=np.float64) / 32768.0
    return float(10 * np.log10(np.mean(x * x) + 1e-20))


def read(path):
    import numpy as np
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


class VerdictTest(unittest.TestCase):
    def test_a_window_says_something_only_where_the_computer_plays_and_the_microphone_is_on(self):
        self.assertEqual(echo.verdict(0.4, -30, -30), echo.ECHO)
        self.assertEqual(echo.verdict(0.05, -30, -30), echo.CLEAN)
        self.assertEqual(echo.verdict(0.4, -30, echo.PLAYING_ABOVE - 1), echo.UNKNOWN)
        self.assertEqual(echo.verdict(0.4, echo.MIC_ABOVE - 1, -30), echo.UNKNOWN)
        self.assertEqual(echo.verdict(None, -30, -30), echo.UNKNOWN)
        # the threshold between what the real meeting gave with and without echo
        self.assertLess(0.11, echo.ECHO_ABOVE)
        self.assertLess(echo.ECHO_ABOVE, 0.26)

    def test_echo_comes_with_one_window_and_goes_after_several_in_a_row(self):
        follow = echo.Follow()
        self.assertTrue(follow.feed(echo.ECHO))
        for _ in range(echo.CLEAN_AFTER - 1):
            self.assertTrue(follow.feed(echo.CLEAN))
        self.assertTrue(follow.feed(echo.UNKNOWN))      # a silent call changes nothing
        self.assertFalse(follow.feed(echo.CLEAN))
        self.assertTrue(follow.feed(echo.ECHO))


class StretchesTest(unittest.TestCase):
    def test_the_state_runs_to_the_ends_and_the_first_verdict_reaches_back(self):
        windows = [window(None, 0), window(None, 10), window(0.4, 20), window(0.3, 30)]
        self.assertEqual(echo.stretches(windows, 43.0), [echo.Stretch(0.0, 43.0, True)])
        self.assertEqual(echo.share(echo.stretches(windows, 43.0)), 1.0)

    def test_earphones_put_on_halfway_end_the_echo_after_clean_after_windows(self):
        values = [0.4, 0.4] + [0.03] * (echo.CLEAN_AFTER + 2)
        windows = [window(v, 10 * n) for n, v in enumerate(values)]
        parts = echo.stretches(windows, 10.0 * len(values))
        change = 10.0 * (2 + echo.CLEAN_AFTER - 1)
        self.assertEqual(parts, [echo.Stretch(0.0, change, True), echo.Stretch(change, 10.0 * len(values), False)])
        self.assertAlmostEqual(echo.echo_seconds(parts), change)

    def test_a_recording_that_never_hears_the_computer_is_one_clean_stretch(self):
        self.assertEqual(echo.stretches([window(0.02, 0), window(None, 10)], 25.0), [echo.Stretch(0.0, 25.0, False)])
        self.assertEqual(echo.stretches([], 5.0), [echo.Stretch(0.0, 5.0, False)])
        self.assertEqual(echo.share(echo.stretches([], 5.0)), 0.0)

    def test_each_change_is_a_short_ramp_centred_on_it(self):
        parts = [echo.Stretch(0.0, 10.0, False), echo.Stretch(10.0, 30.0, True), echo.Stretch(30.0, 40.0, False)]
        xs, ys = echo.blend_points(parts)
        half = echo.CROSSFADE / 2
        self.assertEqual(xs, [0.0, 10.0 - half, 10.0 + half, 30.0 - half, 30.0 + half, 40.0])
        self.assertEqual(ys, [0.0, 0.0, 1.0, 1.0, 0.0, 0.0])


class MeasureTest(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.rng = np.random.default_rng(13)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def noise(self, seconds: float, level: float = 3000.0):
        import numpy as np
        return (self.rng.standard_normal(int(seconds * RATE)) * level).astype(np.int16)

    def test_the_loudspeakers_are_found_with_their_delay_and_a_microphone_of_its_own_is_not(self):
        import numpy as np
        computer = self.noise(30)
        heard = np.clip(room(computer) + self.noise(30, 300), -32768, 32767).astype(np.int16)
        windows, duration = canceller.measure(canceller.write_pair(self.folder / "echo.wav", heard, [computer], RATE))
        self.assertEqual(duration, 30.0)
        self.assertEqual([w.verdict for w in windows], [echo.ECHO] * 3)
        for w in windows:
            self.assertAlmostEqual(w.delay, DELAY, delta=0.002)
        own = self.noise(30)        # the user alone, with earphones on
        windows, _ = canceller.measure(canceller.write_pair(self.folder / "own.wav", own, [computer], RATE))
        self.assertEqual([w.verdict for w in windows], [echo.CLEAN] * 3)
        self.assertTrue(all(w.correlation < echo.ECHO_ABOVE / 3 for w in windows))

    def test_a_silent_computer_says_nothing_and_the_outputs_are_summed(self):
        import numpy as np
        silent = np.zeros(20 * RATE, np.int16)
        windows, _ = canceller.measure(canceller.write_pair(self.folder / "s.wav", self.noise(20), [silent], RATE))
        self.assertEqual([w.verdict for w in windows], [echo.UNKNOWN] * 2)
        one, two = self.noise(20), self.noise(20)
        pair = canceller.write_pair(self.folder / "two.wav", self.noise(20), [one, two[:RATE]], RATE)
        blocks = canceller.read_pair(pair)
        self.assertEqual(next(blocks), RATE)
        _start, _mic, right = next(blocks)
        self.assertTrue(np.array_equal(right[:RATE], np.clip(one[:RATE].astype(np.int32) + two[:RATE], -32768,
                                                             32767).astype(np.int16)))
        self.assertTrue(np.array_equal(right[RATE:2 * RATE], one[RATE:2 * RATE]))

    def test_a_cancelled_measure_stops(self):
        cancelled = threading.Event()
        cancelled.set()
        from scriba.app.task import Cancelled
        pair = canceller.write_pair(self.folder / "c.wav", self.noise(20), [self.noise(20)], RATE)
        with self.assertRaises(Cancelled):
            canceller.measure(pair, cancelled=cancelled)

    def test_the_splice_takes_the_cleaned_track_in_the_echo_and_the_raw_one_elsewhere(self):
        import numpy as np
        raw = np.full(20 * RATE, 1000, np.int16)
        pair = canceller.write_pair(self.folder / "p.wav", raw, [np.zeros_like(raw)], RATE)
        cleaned = self.folder / "c.wav"
        late = canceller.ahead(len(raw), RATE)
        with wave.open(str(cleaned), "wb") as w:      # as the canceller writes it: first pass, late, a frame short
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(np.concatenate([np.full(late, 7, np.int16), np.full(len(raw) - 160, -1000, np.int16)])
                          .astype("<i2").tobytes())
        parts = [echo.Stretch(0.0, 10.0, False), echo.Stretch(10.0, 20.0, True)]
        out = read(canceller.splice(pair, cleaned, parts, self.folder / "out.wav"))
        self.assertEqual(len(out), len(raw))
        self.assertTrue((out[:9 * RATE] == 1000).all())
        self.assertTrue((out[11 * RATE:19 * RATE] == -1000).all())
        self.assertTrue((out[-100:] == 1000).all())                  # past the cleaned track's end, the raw one
        ramp = out[int((10 - echo.CROSSFADE) * RATE):int((10 + echo.CROSSFADE) * RATE)]
        self.assertTrue((np.diff(ramp.astype(int)) <= 0).all())       # from one to the other without a jump
        self.assertLess(np.abs(np.diff(ramp.astype(int))).max(), 10)


class CancellerTest(unittest.TestCase):
    """WebRTC's canceller through GStreamer, for real."""

    def setUp(self):
        needs_canceller()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def test_the_loudspeakers_are_taken_out_and_the_user_alone_is_left_as_is(self):
        import numpy as np
        voice = speech(27.0)
        n = 40 * RATE
        computer = np.zeros(n, np.int16)
        computer[:20 * RATE] = voice[:20 * RATE]                     # the others speak for 20 s, then stop
        user = np.zeros(n)
        user[25 * RATE:35 * RATE] = voice[::-1][:10 * RATE] * 0.8      # then the user, alone
        mic = np.clip(room(computer) + user + np.random.default_rng(1).standard_normal(n) * 20,
                      -32768, 32767).astype(np.int16)
        pair = canceller.write_pair(self.folder / "pair.wav", mic, [computer], RATE)
        steps = []
        cleaned = canceller.cancel(pair, self.folder / "clean.wav", lambda done, total: steps.append(done / total))
        windows, duration = canceller.measure(pair)
        parts = echo.stretches(windows, duration)
        self.assertEqual(parts, [echo.Stretch(0.0, 40.0, True)])
        out = read(canceller.splice(pair, cleaned, parts, self.folder / "out.wav"))
        self.assertEqual(len(out), n)
        taken = db(mic[5 * RATE:20 * RATE]) - db(out[5 * RATE:20 * RATE])
        kept = db(mic[25 * RATE:35 * RATE]) - db(out[25 * RATE:35 * RATE])
        self.assertGreater(taken, 20.0, f"echo taken out: {taken:.1f} dB")
        self.assertLess(abs(kept), 1.0, f"the user's voice changed by {kept:.2f} dB")
        self.assertEqual(steps[-1], 1.0)
        after, _ = canceller.measure(canceller.write_pair(self.folder / "after.wav", out, [computer], RATE))
        self.assertTrue(all(w.correlation < echo.ECHO_ABOVE for w in after if w.correlation is not None))

    def test_the_cleaned_track_is_late_by_the_latency_written_in_the_module(self):
        import numpy as np
        rng = np.random.default_rng(2)
        for rate in (16000, 48000):
            with self.subTest(rate=rate):
                n = 8 * rate
                mic = (rng.standard_normal(n) * 3000).astype(np.int16)
                pair = canceller.write_pair(self.folder / f"l{rate}.wav", mic, [np.zeros(n, np.int16)], rate)
                out = read(canceller.cancel(pair, self.folder / f"l{rate}-out.wav"))
                lead = canceller.lead_frames(n, rate)
                self.assertEqual(len(out), n + lead)
                out = out[lead:]
                _value, delay = canceller.correlate(out[rate:6 * rate], mic[rate:6 * rate], rate, 0.05)
                self.assertAlmostEqual(delay * rate, canceller.LATENCY[rate], delta=2)
                self.assertEqual(canceller.ahead(n, rate), lead + canceller.LATENCY[rate])

    def test_a_cancelled_cleaning_leaves_nothing(self):
        import numpy as np
        from scriba.app.task import Cancelled
        n = 20 * RATE
        pair = canceller.write_pair(self.folder / "pair.wav", np.ones(n, np.int16), [np.ones(n, np.int16)], RATE)
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            canceller.cancel(pair, self.folder / "clean.wav", cancelled=cancelled)
        self.assertFalse((self.folder / "clean.wav").exists())


class ChainTest(unittest.TestCase):
    """The chain with its engines stood in (tests/test_jobs.py), the
    decoded tracks a microphone that hears the loudspeakers and the
    computer's sound, the canceller real."""

    def setUp(self):
        import numpy as np
        from test_jobs import StandIns

        from scriba.app.engines import audio, ffmpeg, vad
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.source = self.folder / "call.mka"
        self.source.write_bytes(b"the user's recording")
        StandIns(self, tracks=(ffmpeg.Track(0, "Microphone", "me"), ffmpeg.Track(1, "Output", "others")))
        rng = np.random.default_rng(5)
        n = 20 * RATE
        self.computer = (rng.standard_normal(n) * 3000).astype(np.int16)
        self.mic = np.clip(room(self.computer) + rng.standard_normal(n) * 20, -32768, 32767).astype(np.int16)
        self.heard = []

        def decode(source, dest, duration=0.0, progress=None, cancelled=None, stream=0):
            audio.write(dest, self.mic if stream == 0 else self.computer)
            return Path(dest)

        def detect(samples, *args, **kwargs):
            self.heard.append(np.array(samples))
            return [(2 * RATE, 5 * RATE), (10 * RATE, 12 * RATE)]

        for module, function in ((ffmpeg, decode), (vad, detect)):
            patcher = mock.patch.object(module, function.__name__, function)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_microphone_is_transcribed_without_the_loudspeakers_and_that_is_said(self):
        needs_canceller()
        from scriba.app import jobs
        result = jobs.run(jobs.Request(self.source, self.folder / "out"))
        mic_heard, computer_heard = self.heard
        self.assertGreater(db(self.mic[5 * RATE:]) - db(mic_heard[5 * RATE:]), 20.0)
        self.assertEqual(db(computer_heard), db(self.computer))
        self.assertTrue(any("100 %" in note and "taken out" in note for note in result.notes), result.notes)
        meeting = result.session.read_text(encoding="utf-8")
        self.assertIn("taken out of your track", meeting)          # in the meeting's own file, for later

    def test_without_the_canceller_the_raw_track_is_transcribed_and_that_is_said(self):
        from scriba.app import jobs
        with mock.patch.object(canceller, "missing", lambda: ["webrtcdsp"]):
            result = jobs.run(jobs.Request(self.source, self.folder / "out"))
        self.assertEqual(db(self.heard[0]), db(self.mic))
        self.assertTrue(any("webrtcdsp" in note for note in result.notes), result.notes)


def doubled(mix, computer, rate: int) -> float:
    """How much of the computer's sound the mix holds a second time, DELAY
    later: the correlation at that delay over the one at none (0: heard
    once)."""
    import numpy as np
    a = np.asarray(mix, dtype=np.float64)
    b = np.asarray(computer, dtype=np.float64)
    n = min(len(a), len(b))
    size = 1 << (2 * n).bit_length()
    cc = np.abs(np.fft.irfft(np.fft.rfft(a[:n], size) * np.conj(np.fft.rfft(b[:n], size)), size))
    near = int(0.06 * rate)         # the AAC encoder's own delay, a few tens of milliseconds, either way
    late = int(DELAY * rate)
    once = max(cc[:near].max(), cc[-near:].max())
    twice = cc[late - near:late + near].max()
    return float(twice / once)


class ListeningCopyTest(unittest.TestCase):
    """The copy to listen to, made after the stop (app/recording.py), from
    a microphone that heard the loudspeakers: real ffmpeg, real canceller."""

    def setUp(self):
        needs_canceller()
        from scriba.app.engines import ffmpeg
        if ffmpeg.find() is None:
            self.skipTest("needs ffmpeg")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def write(self, name: str, samples, rate: int = 48000) -> Path:
        import numpy as np
        path = self.folder / name
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.asarray(samples, dtype="<i2").tobytes())
        return path

    def test_the_others_are_heard_once_and_the_recording_keeps_the_raw_microphone(self):
        import numpy as np
        from scriba.app import recording
        from scriba.app.engines import ffmpeg
        from scriba.app.recording import Take
        rate = 48000
        rng = np.random.default_rng(8)
        computer = (rng.standard_normal(30 * rate) * 3000).astype(np.int16)
        computer[20 * rate:] = 0                                      # the others speak, then the user
        user = np.zeros(30 * rate)
        user[21 * rate:29 * rate] = rng.standard_normal(8 * rate) * 4000
        mic = np.clip(room(computer, rate) * 2 + user + rng.standard_normal(30 * rate) * 20,
                      -32768, 32767).astype(np.int16)
        takes = [Take("me-1", "me", "Microfono", self.write("r_mic.wav", mic), loudest=-10.0),
                 Take("others-2", "others", "Altoparlanti", self.write("r_out.wav", computer), loudest=-5.0)]
        done = recording.finish(takes, self.folder, "r")
        self.assertTrue(any("(loudspeakers)" in note for note in done.notes), done.notes)
        raw, out, together = (sixteen(ffmpeg, source, self.folder / f"{n}.wav", stream) for n, (source, stream) in
                              enumerate(((done.recording, 0), (done.recording, 1), (done.together, 0))))
        # the joined recording keeps the microphone as recorded, echo and all
        value, delay = canceller.correlate(raw[:10 * 16000], out[:10 * 16000], 16000)
        self.assertGreater(value, 0.5)
        self.assertAlmostEqual(delay, DELAY, delta=0.002)
        import numpy as np
        as_before = np.clip(raw.astype(np.int32) + out, -32768, 32767)     # what the mix was before
        self.assertGreater(doubled(as_before, out, 16000), 0.5)
        self.assertLess(doubled(together, out, 16000), 0.05)


def sixteen(ffmpeg, source, dest, stream: int = 0):
    """A file's sound track at 16 kHz, as the engines read it."""
    from scriba.app.engines import audio
    return audio.read(ffmpeg.decode(source, dest, stream=stream))


class LiveTest(unittest.TestCase):
    """The check while recording (app/recorder.py feeds it), on samples
    made up at the recorder's rate, in buffers of 10 ms as GStreamer gives
    them."""

    def feed(self, live, mic, computer, rate=48000):
        step = rate // 100
        for first in range(0, len(mic), step):
            live.feed("me-1", first, mic[first:first + step])
            live.feed("others-2", first, computer[first:first + step])

    def signals(self, seconds, rate=48000, heard=True):
        import numpy as np
        rng = np.random.default_rng(3)
        computer = (rng.standard_normal(int(seconds * rate)) * 0.1).astype(np.float32)
        mic = room(computer * 32768, rate) / 32768 if heard else rng.standard_normal(len(computer)) * 0.1
        return mic.astype(np.float32), computer

    def test_the_loudspeakers_are_heard_after_one_window_and_earphones_after_several(self):
        live = canceller.Live(48000)
        mic, computer = self.signals(echo.WINDOW + 1)
        self.assertFalse(live.look("me-1", ["others-2"]))           # nothing yet
        self.feed(live, mic, computer)
        self.assertTrue(live.look("me-1", ["others-2"]))
        self.assertAlmostEqual(live.last.delay, DELAY, delta=0.002)
        self.assertTrue(live.look("me-1", ["others-2"]))            # no new window: as it was
        # earphones on: the microphone hears the user only; the warning stays a minute of the call, not 30 s
        # (three windows without echo were seen in the middle of a real echo, 13/11/2026)
        self.assertGreaterEqual(echo.LIVE_CLEAN_AFTER * echo.WINDOW, 2 * echo.CLEAN_AFTER * echo.WINDOW)
        own, played = self.signals(echo.WINDOW * echo.LIVE_CLEAN_AFTER, heard=False)
        size = int(echo.WINDOW * 48000)
        for n in range(echo.LIVE_CLEAN_AFTER):
            piece = slice(n * size, (n + 1) * size)
            live.feed("me-1", len(mic) + piece.start, own[piece])
            live.feed("others-2", len(mic) + piece.start, played[piece])
            self.assertEqual(live.look("me-1", ["others-2"]), n < echo.LIVE_CLEAN_AFTER - 1, n)

    def test_without_an_output_or_a_microphone_nothing_is_said(self):
        live = canceller.Live(48000)
        mic, computer = self.signals(echo.WINDOW + 1)
        self.feed(live, mic, computer)
        self.assertFalse(live.look("me-1", []))
        self.assertFalse(live.look("me-9", ["others-2"]))


class WithoutTheCancellerTest(unittest.TestCase):
    def test_the_failure_is_said_in_words(self):
        from scriba.app.task import Failure
        with mock.patch.object(canceller, "missing", lambda: ["webrtcdsp"]):
            with self.assertRaises(Failure) as said:
                canceller.cancel("nothing.wav", "clean.wav")
        self.assertIn("webrtcdsp", str(said.exception))


if __name__ == "__main__":
    unittest.main()
