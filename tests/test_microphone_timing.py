"""A timing error at a packed-audio seam cannot move a microphone reply across it."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401
from test_jobs import StandIns

from scriba.app import jobs
from scriba.app.engines import audio, ffmpeg, vad, whisper
from scriba.core.segments import Segment


class MicrophoneTimingTest(unittest.TestCase):
    def test_the_real_reply_stays_after_the_large_jump_even_with_earlier_echo(self):
        import numpy as np

        rate = audio.RATE
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = folder / "call.mka"
        source.touch()
        roles = (ffmpeg.Track(0, role="me", muted=((5, 11),)), ffmpeg.Track(1, role="others"))
        StandIns(self, tracks=roles)
        remote = np.zeros(20 * rate, dtype=np.int16)
        remote[rate:4 * rate] = np.random.default_rng(7).integers(-8000, 8000, 3 * rate)
        microphone = np.zeros_like(remote)
        delay = round(.223 * rate)
        microphone[rate + delay:4 * rate + delay] = remote[rate:4 * rate] // 4
        microphone[12 * rate:16 * rate] = np.random.default_rng(8).integers(-15000, 15000, 4 * rate)
        chunks = []

        def decode(_source, dest, *args, stream, **kwargs):
            audio.write(dest, (microphone, remote)[stream])
            return Path(dest)

        def engine(path, *_args):
            samples = audio.read(path)
            if len(samples) > 6 * rate:
                # With both pieces in one input, the reply is timestamped over the echo.
                line = Segment(.5, 2.0, "The answer is five.", words=((.5, .7), (.8, 1.0), (1.1, 1.3), (1.4, 1.9)))
            elif max(abs(samples), default=0) > 10000:
                line = Segment(.5, 2.0, "The answer is five.", words=((.5, .7), (.8, 1.0), (1.1, 1.3), (1.4, 1.9)))
            elif max(abs(samples), default=0) < 3000:
                line = Segment(.5, 40.0, "An echo fragment.", words=((.5, .7), (.8, 1.0), (38.0, 40.0)))
            else:
                line = Segment(.5, 2.0, "What is the answer?")
            return whisper.Transcript([line], "en", None, None)

        def batch(paths, *args):
            chunks.append(paths)
            return [engine(path, *args) for path in paths]

        vad_results = [[(rate, 5 * rate), (12 * rate, 16 * rate)], [(rate, 4 * rate)]]
        with patch.object(ffmpeg, "decode", decode), patch.object(vad, "detect", side_effect=vad_results), \
                patch.object(jobs, "_without_echo", lambda parts, wavs, *_: (wavs, [], ())), \
                patch.object(whisper, "transcribe", engine), \
                patch.object(whisper, "transcribe_chunks", batch, create=True):
            made = jobs.run(jobs.Request(source, out=folder, speakers=False, cpu=True))
        answer, = (line for line in made.segments if line.text == "The answer is five.")
        self.assertEqual((answer.start, answer.end), (12.5, 14.0))
        self.assertTrue(all(12 <= start <= end <= 16 for start, end in answer.words))
        self.assertEqual([len(paths) for paths in chunks], [2])
        echo_line, = (line for line in made.segments if line.text == "An echo fragment.")
        self.assertLessEqual(echo_line.end, 5.0)
