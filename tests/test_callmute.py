"""The call's mute from Windows' microphone indicator (core/callmute.py,
app/callmute.py), the stretches it leaves in a recording and what they do
to the transcription (app/jobs.py), with made-up texts and levels: no call,
no microphone opened."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import _setup

from scriba.core import callmute

# the indicator's names read on 2026-11-13, Windows 11 in Italian, with Teams in a call
MUTED_IT = ("Microfono Audio disattivato: Microsoft Teams\nPer attivare o disattivare l'audio, premi TASTO "
            "WINDOWS+ALT+K.\n\nApp che usano il microfono:\nScriba\nMicrosoft Teams")
UNMUTED_IT = ("Microfono Audio riattivato: Microsoft Teams\nPer attivare o disattivare l'audio, premi TASTO "
              "WINDOWS+ALT+K.\n\nApp che usano il microfono:\nScriba\nMicrosoft Teams")


class WordsTest(unittest.TestCase):
    def test_the_two_italian_texts_read_on_windows_11(self):
        self.assertEqual(callmute.state_of(MUTED_IT), callmute.MUTED)
        self.assertEqual(callmute.state_of(UNMUTED_IT), callmute.UNMUTED)

    def test_the_shortcuts_line_does_not_count(self):
        # "disattivare" is in the second line of both: only the first decides
        self.assertEqual(callmute.state_of(UNMUTED_IT.replace("riattivato", "in uso")), callmute.UNKNOWN)

    def test_texts_not_known_change_nothing(self):
        for name in (None, "", "   ", "Rete Esempio\nAccesso a Internet", "Volume Altoparlanti: 42%",
                     "Mikrofon Ton aus: Microsoft Teams", "Microfono in uso: Microsoft Teams",
                     "Microphone in use: Microsoft Teams"):
            self.assertEqual(callmute.state_of(name), callmute.UNKNOWN, name)

    def test_the_english_words_are_a_guess(self):
        self.assertEqual(callmute.state_of("Microphone Muted: Microsoft Teams"), callmute.MUTED)
        self.assertEqual(callmute.state_of("Microphone Unmuted: Microsoft Teams"), callmute.UNMUTED)

    def test_a_failed_reading_keeps_the_last_state_and_no_indicator_is_unknown(self):
        reading = callmute.Reading()
        self.assertEqual(reading.failed(), callmute.UNKNOWN)
        self.assertEqual(reading.read(MUTED_IT), callmute.MUTED)
        self.assertEqual(reading.failed(), callmute.MUTED)
        self.assertEqual(reading.read(UNMUTED_IT), callmute.UNMUTED)
        self.assertEqual(reading.failed(), callmute.UNMUTED)
        self.assertEqual(reading.read(None), callmute.UNKNOWN)      # the call let the microphone go


class SpansTest(unittest.TestCase):
    def test_the_stretches_go_into_a_tag_and_back(self):
        spans = ((12.5, 45.0), (78.25, None))
        self.assertEqual(callmute.write_spans(spans), "12.5-45;78.25-")
        self.assertEqual(callmute.read_spans("12.5-45;78.25-"), spans)
        self.assertEqual(callmute.read_spans(""), ())
        self.assertEqual(callmute.read_spans(None), ())
        self.assertEqual(callmute.read_spans("x-3;5-2;7-9"), ((7.0, 9.0),))

    def test_speech_inside_a_mute_is_left_out_and_what_crosses_it_is_cut(self):
        speech = [(1.0, 4.0), (5.0, 8.0), (9.0, 12.0), (20.0, 30.0)]
        muted = ((3.0, 10.0), (25.0, None))
        self.assertEqual(callmute.outside(speech, muted), [(1.0, 3.0), (10.0, 12.0), (20.0, 25.0)])
        self.assertAlmostEqual(callmute.taken(speech, muted), 1 + 3 + 1 + 5)
        self.assertEqual(callmute.outside(speech, ()), speech)
        self.assertEqual(callmute.outside([(2.0, 9.0)], ((4.0, 5.0),)), [(2.0, 4.0), (5.0, 9.0)])


class FakeIndicator:
    """The indicator's names in turn; an OSError where a reading fails."""

    def __init__(self, names):
        self.names, self.closed = list(names), False

    def read(self):
        name = self.names.pop(0) if self.names else None
        if isinstance(name, Exception):
            raise name
        return name

    def close(self):
        self.closed = True


@unittest.skipUnless(_setup.WINDOWS, "Windows only")
class FollowTest(unittest.TestCase):
    def test_each_reading_is_told_and_a_failure_keeps_the_last(self):
        from scriba.app import callmute as indicator
        fake = FakeIndicator([UNMUTED_IT, MUTED_IT, OSError("UIA"), MUTED_IT, UNMUTED_IT, None])
        told, stop = [], threading.Event()

        def heard(state):
            told.append(state)
            if len(told) == 6:
                stop.set()
        indicator.follow(heard, stop, 0.01, lambda: fake)
        self.assertEqual(told, ["unmuted", "muted", "muted", "muted", "unmuted", "unknown"])
        self.assertTrue(fake.closed)

    def test_ui_automation_that_cannot_open_says_unknown_once(self):
        from scriba.app import callmute as indicator

        def broken():
            raise OSError("no UI Automation")
        told = []
        with mock.patch.object(indicator, "complain") as said:
            indicator.follow(told.append, threading.Event(), 0.01, broken)
        self.assertEqual(told, ["unknown"])
        said.assert_called_once()

    def test_the_real_indicator_reads_without_failing(self):
        # read only: the taskbar's own elements; None when no app uses the microphone now
        from scriba.app import callmute as indicator
        reader = indicator.Indicator()
        try:
            name = reader.read()
        finally:
            reader.close()
        self.assertTrue(name is None or callmute.state_of(name) in (callmute.MUTED, callmute.UNMUTED, callmute.UNKNOWN))


class RecordingTagTest(unittest.TestCase):
    def test_the_microphones_muted_stretches_go_into_the_recording_and_come_back(self):
        from scriba.app import recording
        from scriba.app.engines import ffmpeg
        take = recording.Take("me-1", "me", "Mic", Path("a.flac"), muted=((3.5, 9.0), (20.0, None)))
        other = recording.Take("others-2", "others", "Out", Path("b.flac"))
        argv = recording.join_arguments("ffmpeg", [take, other], "x.mka")
        self.assertIn(f"{ffmpeg.MUTED_TAG}=3.5-9;20-", argv)
        self.assertEqual(sum(1 for a in argv if a.startswith(ffmpeg.MUTED_TAG)), 1)
        probe = ('{"format": {"duration": "30"}, "streams": ['
                 '{"codec_type": "audio", "tags": {"title": "Mic", "SCRIBA_ROLE": "me", '
                 '"SCRIBA_CALL_MUTED": "3.5-9;20-"}},'
                 '{"codec_type": "audio", "tags": {"title": "Out", "SCRIBA_ROLE": "others"}}]}')
        media = ffmpeg.parse_probe(probe)
        self.assertEqual(media.tracks[0].muted, ((3.5, 9.0), (20.0, None)))
        self.assertEqual(media.tracks[1].muted, ())
        self.assertEqual(len(media.roles), 2)

    def test_the_notes_say_where_the_user_was_muted(self):
        from scriba.app import recording
        take = recording.Take("me-1", "me", "Mic", Path("a.flac"), muted=((65.0, 70.0), (100.0, None)))
        notes = recording.muted_notes([take])
        self.assertEqual(len(notes), 2)
        self.assertIn("01:05", notes[0])
        self.assertIn("to the end", notes[1])


class ChainTest(unittest.TestCase):
    """The speech found on the microphone's track inside a muted stretch is
    not given to whisper; the computer's track is not touched."""

    def find(self, role, muted, found_seconds):
        import numpy as np

        from scriba.app import jobs
        from scriba.app.engines import audio, ffmpeg, vad
        part = jobs._Part(ffmpeg.Track(0, "", role, muted))
        part.heard = np.full(audio.RATE * 40, 1000, np.int16)
        rate = audio.RATE
        spans = [(round(s * rate), round(e * rate)) for s, e in found_seconds]
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        with mock.patch.object(vad, "detect", return_value=spans), \
                mock.patch.object(jobs.models, "path_of", return_value="silero.onnx"):
            jobs._find_speech(part, folder / "speech.wav", vad.FOR_SPEECH, None, None, None)
        return part

    def test_the_microphones_speech_while_muted_is_left_out(self):
        part = self.find("me", ((10.0, 20.0),), [(2.0, 5.0), (12.0, 15.0), (18.0, 25.0)])
        heard = [(p.start, p.end) for p in part.pieces]
        self.assertTrue(all(e <= 10.0 or s >= 20.0 for s, e in heard), heard)
        self.assertAlmostEqual(sum(e - s for s, e in heard), 3 + 5, delta=0.05)
        self.assertAlmostEqual(part.left_out, 3 + 2, delta=0.05)

    def test_the_computers_track_and_a_track_without_mutes_are_untouched(self):
        for role, muted in (("others", ((10.0, 20.0),)), ("me", ())):
            part = self.find(role, muted, [(2.0, 5.0), (12.0, 15.0)])
            self.assertAlmostEqual(sum(p.end - p.start for p in part.pieces), 6.0, delta=0.05)
            self.assertEqual(part.left_out, 0.0)


class RecorderTest(unittest.TestCase):
    """The recorder's own pipeline on synthetic tones, the indicator made up."""

    def test_the_muted_stretches_follow_the_indicator_and_the_sound_is_untouched(self):
        from test_recorder import gstreamer, peak, tone_source

        from scriba.app import callmute as indicator
        from scriba.app.engines import ffmpeg
        from scriba.core.devices import INPUT, OUTPUT, Device, Plan
        recorder = gstreamer()
        self.enterContext(mock.patch.object(recorder, "_source", tone_source(recorder)))
        self.enterContext(mock.patch.object(indicator, "EVERY_S", 0.1))
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        plan = Plan(Device("mic", "Tone", INPUT), (Device("out", "Other tone", OUTPUT),), automatic_input=False,
                    automatic_output=False)
        made = recorder.Recorder(plan, folder, "muto", lister=lambda: [plan.input, *plan.outputs])
        names = [UNMUTED_IT] * 8 + [MUTED_IT] * 4 + [OSError("UIA")] * 2 + [MUTED_IT] * 4 + [UNMUTED_IT] * 100
        made.follow_call, made.call_indicator = True, lambda: FakeIndicator(names)
        made.start()
        try:
            end = time.monotonic() + 5
            while not made.snapshot().call_muted and time.monotonic() < end:
                time.sleep(0.05)
            self.assertTrue(made.snapshot().call_muted)
            while made.snapshot().call_muted and time.monotonic() < end:
                time.sleep(0.05)
            self.assertFalse(made.snapshot().call_muted)
            time.sleep(0.3)
        finally:
            takes = made.stop()
        mic = next(t for t in takes if t.role == "me")
        output = next(t for t in takes if t.role == "others")
        (begin, until), = mic.muted
        self.assertAlmostEqual(until - begin, 1.0, delta=0.35)     # ten readings of 0.1 s, the failures kept muted
        self.assertEqual(output.muted, ())
        self.assertEqual(mic.off, ())
        self.assertGreater(peak(mic.path, begin + 0.1, until - begin - 0.2), 0)    # the sound is kept
        self.assertAlmostEqual(ffmpeg.decoded_seconds(mic.path), ffmpeg.decoded_seconds(output.path), delta=0.15)

    def test_no_level_warning_for_the_microphone_while_muted_in_the_call(self):
        from test_recorder import gstreamer

        from scriba.core import levels
        from scriba.core.devices import INPUT, Device, Plan
        recorder = gstreamer()
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        mic = Device("mic", "Tone", INPUT)
        made = recorder.Recorder(Plan(mic, (), automatic_input=False, automatic_output=False), folder, "avviso",
                                 lister=lambda: [mic])
        made._state = "recording"           # not started: no device opened
        made._watch.track("me-1", True)
        t = 0.0
        while t < 30:                       # faint speech, warned about
            made._watch.feed("me-1", t, -48.0 if int(t) % 3 else -66.0, -40.0)
            t += 0.1
        with mock.patch.object(recorder.time, "monotonic", return_value=t):
            self.assertIn(levels.QUIET, [a.kind for a in made.snapshot().alerts])
            made.call_state(callmute.MUTED)
            self.assertNotIn(levels.QUIET, [a.kind for a in made.snapshot().alerts])
            self.assertTrue(made.snapshot().call_muted)
            made.call_state(callmute.UNKNOWN)
            self.assertFalse(made.snapshot().call_muted)


if __name__ == "__main__":
    unittest.main()
