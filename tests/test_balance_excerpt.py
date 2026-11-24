"""The listening comparison uses the same exchange at both slider positions."""

import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import _setup  # noqa: F401
from scriba.app import backend, jobs, recording
from scriba.app.engines import ffmpeg
from scriba.core import balance_excerpt
from scriba.core.balance_excerpt import Speech
from scriba.core.segments import Segment


class BalanceExcerptTest(unittest.TestCase):
    def test_clean_exchange_beats_solo_speech_and_a_doubtful_exchange(self):
        speech = [Speech(0, 30, "others"), Speech(42, 49, "me", -18), Speech(50, 62, "others", -20),
                  Speech(81, 90, "me", -48, True), Speech(91, 110, "others", -48, True)]
        start, end = balance_excerpt.choose(speech, 120)
        self.assertEqual(end - start, 25)
        self.assertLessEqual(start, 42)
        self.assertGreaterEqual(end, 62)

    def test_mute_off_and_echo_do_not_count_as_my_turn(self):
        speech = [Speech(0, 14, "me"), Speech(14, 25, "others"), Speech(30, 44, "me"),
                  Speech(44, 56, "others"), Speech(60, 73, "me"), Speech(73, 85, "others"),
                  Speech(94, 100, "me"), Speech(103, 118, "others")]
        start, end = balance_excerpt.choose(speech, 120, unavailable=((0, 14), (30, 44)), echo=((60, 73),))
        self.assertEqual(end - start, 25)
        self.assertLessEqual(start, 94)
        self.assertGreaterEqual(end, 118)

    def test_short_recording_uses_only_the_audio_that_exists(self):
        self.assertEqual(balance_excerpt.choose([Speech(1, 3, "me"), Speech(4, 7, "others")], 8), (0, 8))

    def test_no_exchange_uses_the_best_audible_side(self):
        speech = [Speech(10, 13, "others"), Speech(55, 71, "others")]
        start, end = balance_excerpt.choose(speech, 100)
        self.assertLessEqual(start, 55)
        self.assertGreaterEqual(end, 71)
        self.assertEqual(end - start, 25)

    def test_overlap_alone_is_not_an_exchange(self):
        speech = [Speech(0, 25, role) for role in ("me", "others")]
        speech += [Speech(51, 55, "me"), Speech(56, 62, "others")]
        start, end = balance_excerpt.choose(speech, 80)
        self.assertLessEqual(start, 51)
        self.assertGreaterEqual(end, 62)

    def test_backend_uses_the_recorded_roles_and_reuses_the_excerpt_when_rendering(self):
        import numpy as np

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "recording.mka"
        source.touch()
        media = ffmpeg.Media(85, 2, False, 0, tracks=(ffmpeg.Track(0, role="me", muted=((0, 14),)),
                                                      ffmpeg.Track(1, role="others")))
        lines = [Segment(0, 14, "Muted", "Speaker 1"), Segment(14, 25, "Early reply", "Speaker 2"),
                 Segment(49, 57, "Question", "Speaker 1"), Segment(58, 71, "Reply", "Speaker 2")]
        result = jobs.Result([], root / "meeting.json", lines, 2, 85, "cpu", {}, source=source, me="Speaker 2",
                             recorded_voice="Speaker 1", balance_echo=())
        pcm = (np.full(100, .1, dtype=np.float32), np.full(100, .25, dtype=np.float32))
        with patch.object(ffmpeg, "probe", return_value=media), \
                patch.object(recording, "balance_preview", return_value=pcm) as decode:
            clip = backend.Backend.balance_preview(None, result, threading.Event())
        decode.assert_called_once()
        _source, start, length = decode.call_args.args[:3]
        self.assertLessEqual(start, 49)
        self.assertGreaterEqual(start + length, 71)
        self.assertEqual(length, 25)
        into_mic = clip.render(0, clip.frames, 1, 1)
        into_others = clip.render(0, clip.frames, -1, -1)
        self.assertEqual(len(into_mic), len(into_others))
        self.assertFalse(np.array_equal(into_mic, into_others))

    def test_without_a_transcript_the_levels_find_an_exchange_instead_of_mute(self):
        import numpy as np

        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "recording.mka"
        source.touch()
        media = ffmpeg.Media(85, 2, False, 0, tracks=(ffmpeg.Track(0, role="me", off=((0, 14),)),
                                                      ffmpeg.Track(1, role="others")))
        result = jobs.Result([], root / "meeting.json", [], 0, 85, "cpu", {}, source=source)

        def decode(_source, dest, *, rate, stream, **_):
            samples = np.zeros(rate * 85, dtype=np.int16)
            spans = ((0, 14), (54, 59)) if stream == 0 else ((14, 22), (61, 69))
            for start, end in spans:
                samples[start * rate:end * rate] = 4000
            with wave.open(str(dest), "wb") as wav:
                wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                wav.writeframes(samples.tobytes())
            return dest

        pcm = (np.ones(100, dtype=np.float32),) * 2
        with patch.object(ffmpeg, "probe", return_value=media), patch.object(ffmpeg, "decode", side_effect=decode), \
                patch.object(recording, "balance_preview", return_value=pcm) as playback:
            backend.Backend.balance_preview(None, result, threading.Event())
        _source, start, length = playback.call_args.args[:3]
        self.assertLessEqual(start, 54)
        self.assertGreaterEqual(start + length, 69)
        self.assertEqual(length, 25)
