"""Two tracks of the computer's sound with the same sound: when they are
(core/duplicates.py), how it is measured (app/engines/echo.py, same_sound),
and what the recording's end and the transcript do with them
(app/recording.py, one_of_each; app/jobs.py, _one_of_each).

No recording of a person: the second output is the app's own sample of
speech processed as an effects program would (compressed, filtered,
louder) and 20 ms later, the case of 22/11/2026 (EasyEffects' sink and the
loudspeakers); another voice is the same sample from another point."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.core import duplicates, echo

RATE = 16000
SECONDS = 40   # four windows where both play (duplicates.LEAST is 3)


def window(correlation, delay=0.0204, first=-30.0, second=-30.0, start=0.0):
    return echo.Window(start, start + echo.WINDOW, correlation, delay, first, second)


class VerdictTest(unittest.TestCase):
    def test_alike_at_a_steady_delay_is_the_same(self):
        found = duplicates.verdict([window(0.64, start=10 * k) for k in range(5)])
        self.assertTrue(found.same)
        self.assertAlmostEqual(found.delay, 0.0204)

    def test_unrelated_sounds_are_not_the_same(self):
        self.assertFalse(duplicates.verdict([window(0.05, start=10 * k) for k in range(5)]).same)

    def test_alike_at_a_wandering_delay_is_not_the_same(self):
        windows = [window(0.6, delay=0.02 * k, start=10 * k) for k in range(5)]
        self.assertFalse(duplicates.verdict(windows).same)

    def test_too_little_heard_says_nothing(self):
        windows = [window(0.64), window(0.64, start=10), window(0.64, second=-80.0, start=20)]
        found = duplicates.verdict(windows)
        self.assertFalse(found.same)
        self.assertEqual(found.windows, 2)

    def test_a_few_unlike_windows_do_not_spoil_it(self):
        windows = [window(0.64, start=10 * k) for k in range(8)] + [window(0.1, start=90), window(None, start=100)]
        self.assertTrue(duplicates.verdict(windows).same)

    def test_the_later_track_is_left_out(self):
        self.assertEqual(duplicates.later(duplicates.Verdict(True, 0.02)), 0)
        self.assertEqual(duplicates.later(duplicates.Verdict(True, -0.02)), 1)

    def test_three_tracks_two_alike(self):
        def compare(a, b):
            return duplicates.Verdict(True, -0.02) if {a, b} == {0, 2} else duplicates.Verdict(False)
        out = duplicates.left_out(3, compare)
        self.assertEqual(out, {2: 0})
        self.assertEqual(duplicates.names(out, ["A", "B", "C"]), [("C", "A")])


def speech(seconds: float):
    from scriba.app.engines import audio, ffmpeg
    from scriba.app.jobs import SAMPLE
    if ffmpeg.find() is None:
        raise unittest.SkipTest("needs ffmpeg")
    with tempfile.TemporaryDirectory() as tmp:
        samples = audio.read(ffmpeg.decode(SAMPLE, Path(tmp) / "sample.wav"))
    import numpy as np
    want = int(seconds * RATE)
    return np.tile(samples, want // len(samples) + 1)[:want]     # the sample again, as long as asked


def processed(samples, delay: float = 0.0204):
    """What an effects program sends on: compressed, filtered, louder, late."""
    import numpy as np
    from scipy.signal import lfilter
    x = samples.astype(np.float64) / 32768.0
    squeezed = np.sign(x) * np.abs(x) ** 0.6                     # a compressor, roughly
    shaped = lfilter([0.6, 0.3, -0.1], [1.0, -0.2], squeezed)     # an equaliser, roughly
    late = np.concatenate([np.zeros(int(delay * RATE)), shaped])[:len(x)]
    return np.clip(late * 1.8 * 32768.0, -32768, 32767).astype(np.int16)


class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)

    def wav(self, name, samples):
        from scriba.app.engines import audio
        path = self.work / name
        audio.write(path, samples)
        return path

    def test_an_output_and_its_processed_copy_are_the_same(self):
        from scriba.app.engines import echo as canceller
        voice = speech(SECONDS)
        found = canceller.same_sound(self.wav("sink.wav", voice), self.wav("speakers.wav", processed(voice)),
                                     self.work)
        self.assertTrue(found.same, found)
        self.assertLess(found.delay, 0)          # the first (the sink) comes first: the speakers are left out
        self.assertEqual(duplicates.later(found), 1)

    def test_two_voices_are_not_the_same(self):
        from scriba.app.engines import echo as canceller
        voice = speech(SECONDS)
        import numpy as np
        other = np.roll(voice, int(11.3 * RATE))     # the same speech from another point: other words
        found = canceller.same_sound(self.wav("one.wav", voice), self.wav("two.wav", other), self.work)
        self.assertFalse(found.same, found)


class RecordingEndTest(unittest.TestCase):
    def test_the_copy_is_left_out_of_the_mix_and_said(self):
        from scriba.app import recording
        takes = [recording.Take("output-1", "others", "Easy Effects Sink", Path("a.flac")),
                 recording.Take("output-2", "others", "Speakers", Path("b.flac")),
                 recording.Take("input-1", "me", "Microphone", Path("c.flac"))]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(recording.ffmpeg, "decode", side_effect=lambda src, dest, **_kw: Path(dest)), \
                mock.patch("scriba.app.engines.echo.same_sound",
                           return_value=duplicates.Verdict(True, -0.0204, 30)):
            kept, notes = recording.one_of_each(takes, Path(tmp))
        self.assertEqual([t.key for t in kept], ["output-1", "input-1"])
        self.assertEqual(len(notes), 1)
        self.assertIn("Speakers", notes[0])
        self.assertIn("Easy Effects Sink", notes[0])

    def test_a_failed_measure_keeps_everything(self):
        from scriba.app import recording
        from scriba.app.task import Failure
        takes = [recording.Take("output-1", "others", "A", Path("a.flac")),
                 recording.Take("output-2", "others", "B", Path("b.flac"))]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(recording.ffmpeg, "decode", side_effect=Failure("no ffmpeg", "")):
            kept, notes = recording.one_of_each(takes, Path(tmp))
        self.assertEqual(kept, takes)
        self.assertEqual(notes, [])

    def test_one_output_is_not_measured(self):
        from scriba.app import recording
        takes = [recording.Take("output-1", "others", "A", Path("a.flac")),
                 recording.Take("input-1", "me", "M", Path("m.flac"))]
        with mock.patch.object(recording.ffmpeg, "decode", side_effect=AssertionError("nothing to measure")):
            self.assertEqual(recording.one_of_each(takes, Path("."))[0], takes)


if __name__ == "__main__":
    unittest.main()
