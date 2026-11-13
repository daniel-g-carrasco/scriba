"""The recorder for real, on this computer's devices: GStreamer's pipeline,
the files it writes, and what is left when the process is killed.

Marked: they open the microphone and the outputs, so they run only when
asked (SCRIBA_TEST_RECORD=1) and when the computer has the devices. A
loopback of an output nobody plays on is silence: the tests look at the
files (there, readable, as long as the recording), not at the sound. No
sound is played.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import _setup  # noqa: F401

ASKED = os.environ.get("SCRIBA_TEST_RECORD") == "1"
SECONDS = 5.0


def gstreamer():
    try:
        from scriba.app import recorder
    except (ImportError, ValueError) as e:
        raise unittest.SkipTest(f"no GStreamer: {e}") from None
    if recorder.missing():
        raise unittest.SkipTest(f"GStreamer lacks {recorder.missing()}")
    return recorder


@unittest.skipUnless(ASKED, "records from the real devices: SCRIBA_TEST_RECORD=1")
class RealRecorderTest(unittest.TestCase):
    def setUp(self):
        self.recorder = gstreamer()
        tmp = tempfile.TemporaryDirectory(prefix="scriba-rec-")
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        from scriba.core import devices
        found = self.recorder.list_devices()
        self.plan = devices.plan(found)
        if self.plan.input is None or not self.plan.outputs:
            self.skipTest("this computer has no input or no output")

    def test_five_seconds_from_the_microphone_and_the_loopback_make_readable_files_that_long(self):
        from scriba.app.engines import ffmpeg
        from scriba.core.devices import Plan
        plan = Plan(self.plan.input, self.plan.outputs[:1], automatic_input=False, automatic_output=False)
        recording = self.recorder.Recorder(plan, self.folder, "2026-11-11 10-00-00 prova")
        recording.start()
        time.sleep(SECONDS)
        status = recording.snapshot()
        clock = recording._pipeline.get_clock()
        stopping = time.monotonic()
        takes = recording.stop()
        self.assertLess(time.monotonic() - stopping, self.recorder.STOP_WAIT + 1)
        self.assertEqual(clock, self.recorder.Gst.SystemClock.obtain())
        self.assertEqual(status.state, "recording")
        self.assertEqual(len(takes), 2)
        self.assertEqual([t.role for t in takes], ["me", "others"])
        self.assertEqual(recording.snapshot().state, "stopped", recording.snapshot().error)
        for take in takes:
            with self.subTest(take.device):
                self.assertTrue(take.path.is_file())
                seconds = ffmpeg.decoded_seconds(take.path)
                self.assertAlmostEqual(seconds, SECONDS, delta=0.5)
                media = ffmpeg.probe(take.path)
                self.assertEqual(media.sound_tracks, 1)
                self.assertAlmostEqual(media.duration, seconds, delta=0.15)    # closed properly: the header knows
        # the two tracks start together and end together
        self.assertAlmostEqual(*(ffmpeg.decoded_seconds(t.path) for t in takes), delta=0.15)
        self.assertGreater(takes[0].loudest, -200)     # the microphone hears the room

    def test_paused_the_tracks_stay_together_and_the_pause_is_not_recorded(self):
        from scriba.app.engines import ffmpeg
        from scriba.core.devices import Plan
        recording = self.recorder.Recorder(Plan(self.plan.input, self.plan.outputs[:1], automatic_input=False, automatic_output=False), self.folder, "pausa")
        recording.start()
        time.sleep(2.0)
        recording.pause()
        time.sleep(1.5)
        self.assertEqual(recording.snapshot().state, "paused")
        recording.resume()
        time.sleep(2.0)
        takes = recording.stop()
        lengths = [ffmpeg.decoded_seconds(t.path) for t in takes]
        for length in lengths:
            self.assertAlmostEqual(length, 4.0, delta=0.5)
        self.assertAlmostEqual(lengths[0], lengths[1], delta=0.15)

    def test_the_microphone_switched_off_in_scriba_records_silence_and_comes_back(self):
        from scriba.core.devices import Plan
        plan = Plan(self.plan.input, self.plan.outputs[:1], automatic_input=False, automatic_output=False)
        _takes, mic = switched_off_in_the_middle(self, self.recorder.Recorder(plan, self.folder, "interruttore"),
                                                 1.5)
        self.assertGreater(peak(mic.path, mic.off[0][1] + 0.25, 1.0), 0)     # the room again

    def test_finished_the_tracks_are_one_file_that_says_whose_each_is(self):
        from scriba.app import recording as finishing
        from scriba.app.engines import ffmpeg
        from scriba.core.devices import Plan
        recording = self.recorder.Recorder(Plan(self.plan.input, self.plan.outputs[:1], automatic_input=False, automatic_output=False), self.folder, "intera")
        recording.start()
        time.sleep(3.0)
        takes = recording.stop()
        done = finishing.finish(takes, self.folder, recording.stem)
        media = ffmpeg.probe(done.recording)
        self.assertEqual(media.roles[0].role, "me")
        self.assertEqual(len(media.roles), len(done.kept))
        self.assertTrue(all(not t.path.exists() for t in takes))     # their bits are in the .mka
        self.assertEqual(sorted(p.suffix for p in self.folder.iterdir()), sorted(
            [".mka"] + ([".m4a"] if done.together else [])))

    def test_killed_halfway_the_files_are_read_up_to_where_they_were(self):
        """The process dies at once (as in a crash or a power cut, the
        files never closed): every track is still read, about as long as
        the recording went."""
        script = textwrap.dedent(f"""
            import os, sys, time
            sys.path.insert(0, {str(_setup.ROOT / 'src')!r})
            from scriba.app import recorder
            from scriba.core import devices
            plan = devices.plan(recorder.list_devices())
            plan = devices.Plan(plan.input, plan.outputs[:1], automatic_input=False, automatic_output=False)
            r = recorder.Recorder(plan, {str(self.folder)!r}, "uccisa")
            r.start()
            time.sleep(3.0)
            print("\\n".join(str(t.path) for t in r.takes()), flush=True)
            os._exit(9)
        """)
        env = {k: v for k, v in os.environ.items() if k != "MSYSTEM"}
        done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(done.returncode, 9, done.stderr)
        paths = [Path(line) for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(len(paths), 2, done.stdout + done.stderr)
        from scriba.app.engines import ffmpeg
        for path in paths:
            with self.subTest(path.name):
                self.assertGreater(path.stat().st_size, 0)       # written as it went, not held in a buffer
                self.assertAlmostEqual(ffmpeg.decoded_seconds(path), 3.0, delta=0.6)
                # its header never learnt the length: the window still says it
                from scriba.app.backend import Backend
                self.assertEqual(ffmpeg.probe(path).duration, 0.0)
                self.assertAlmostEqual(Backend(self.folder, None).inspect(path).duration, 3.0, delta=0.6)


def tone_source(recorder):
    """A live source that sounds (a tone), in place of a device: no hardware."""
    if recorder.Gst.ElementFactory.find("audiotestsrc") is None:
        raise unittest.SkipTest("GStreamer lacks audiotestsrc")

    def source(_device):
        element = recorder.Gst.ElementFactory.make("audiotestsrc", None)
        element.set_property("is-live", True)
        element.set_property("volume", 0.3)
        return element
    return source


def peak(path: Path, start: float, length: float) -> int:
    """The largest sample (as 32-bit integers) of `length` seconds from `start`."""
    from array import array

    from test_engines import fixture_ffmpeg
    done = subprocess.run([fixture_ffmpeg(), "-v", "error", "-ss", f"{start}", "-t", f"{length}", "-i", str(path),
                           "-f", "s32le", "-ac", "1", "-"], capture_output=True, check=True)
    samples = array("i", done.stdout)
    assert len(samples) > 0, path
    return max(abs(s) for s in samples)


def switched_off_in_the_middle(test, recording, second: float = 1.0):
    """Records `second` on, `second` with the microphone off, `second` on
    again; the tracks, checked to be as long as each other."""
    from scriba.app.engines import ffmpeg
    recording.start()
    time.sleep(second)
    recording.switch("me", False)
    test.assertEqual(recording.snapshot().off, {"me"})
    time.sleep(second)
    recording.switch("me", True)
    time.sleep(second)
    takes = recording.stop()
    lengths = [ffmpeg.decoded_seconds(t.path) for t in takes]
    for length in lengths:
        test.assertAlmostEqual(length, 3 * second, delta=0.5)
    test.assertAlmostEqual(min(lengths), max(lengths), delta=0.15)
    mic = next(t for t in takes if t.role == "me")
    (begin, end), = mic.off
    test.assertAlmostEqual(begin, second, delta=0.2)
    test.assertAlmostEqual(end, 2 * second, delta=0.2)
    test.assertEqual([e.kind for e in recording.snapshot().events][-2:], ["off", "on"])
    # silence where it was off, a margin of a quarter of a second either side
    test.assertEqual(peak(mic.path, begin + 0.25, end - begin - 0.5), 0)
    return takes, mic


class SyntheticRecordingTest(unittest.TestCase):
    """The recorder's own pipeline on live synthetic sources: no hardware, no sound played."""

    def setUp(self):
        from unittest import mock
        self.recorder = gstreamer()
        patcher = mock.patch.object(self.recorder, "_source", tone_source(self.recorder))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_the_time_recorded_stops_when_the_stop_is_asked_not_when_the_files_are_done(self):
        from scriba.core.devices import INPUT, Device, Plan
        mic = Device("mic", "Tone", INPUT)
        take = self.recorder.Recorder(Plan(mic, (), automatic_input=False, automatic_output=False), self.folder,
                                      "stop", lister=lambda: [mic])
        take.start()
        time.sleep(1.0)
        take.stop()
        stopped = take.snapshot().elapsed
        time.sleep(0.5)
        self.assertEqual(take.snapshot().elapsed, stopped)
        self.assertAlmostEqual(stopped, 1.0, delta=0.3)

    def test_the_microphone_switched_off_records_silence_in_step_with_the_other_track(self):
        from scriba.core.devices import INPUT, OUTPUT, Device, Plan
        plan = Plan(Device("mic", "Tone", INPUT), (Device("out", "Other tone", OUTPUT),), automatic_input=False,
                    automatic_output=False)
        made = self.recorder.Recorder(plan, self.folder, "spento", lister=lambda: [plan.input, *plan.outputs])
        takes, mic = switched_off_in_the_middle(self, made)
        begin, end = mic.off[0]
        self.assertGreater(peak(mic.path, 0.2, begin - 0.4), 0)          # the tone before
        self.assertGreater(peak(mic.path, end + 0.25, 0.5), 0)           # and after
        output = next(t for t in takes if t.role == "others")
        self.assertGreater(peak(output.path, begin + 0.25, end - begin - 0.5), 0)   # the other side went on

    def test_a_microphone_that_hears_what_the_computer_plays_is_said_while_recording(self):
        from unittest import mock

        from scriba.core import echo
        from scriba.core.devices import INPUT, OUTPUT, Device, Plan
        # the same tone on both tracks: the microphone "hears" the output; windows of 1 s, to be quick
        self.enterContext(mock.patch.object(echo, "WINDOW", 1.0))
        plan = Plan(Device("mic", "Tone", INPUT), (Device("out", "Same tone", OUTPUT),), automatic_input=False,
                    automatic_output=False)
        made = self.recorder.Recorder(plan, self.folder, "eco", lister=lambda: [plan.input, *plan.outputs])
        made.start()
        try:
            self.assertNotIn(echo.ECHO, [a.kind for a in made.snapshot().alerts])
            end = time.monotonic() + 5
            while echo.ECHO not in [a.kind for a in made.snapshot().alerts] and time.monotonic() < end:
                time.sleep(0.2)
                made._look_for_echo()
            self.assertIn(echo.ECHO, [a.kind for a in made.snapshot().alerts])
            self.assertGreater(made._echo.last.correlation, 0.9)
            made.switch("others", False)        # nothing to hear: no warning
            self.assertNotIn(echo.ECHO, [a.kind for a in made.snapshot().alerts])
        finally:
            made.stop()


class DeviceWatchTest(unittest.TestCase):
    """Changing the available devices, with live synthetic sources and no hardware."""

    def test_an_automatic_plan_adds_a_new_output_once_and_an_explicit_plan_does_not(self):
        from unittest import mock

        from scriba.core.devices import OUTPUT, Device, Plan
        recorder = gstreamer()
        if recorder.Gst.ElementFactory.find("audiotestsrc") is None:
            self.skipTest("GStreamer lacks audiotestsrc")
        first = Device("one", "First output", OUTPUT)
        added = Device("two", "Second output", OUTPUT)

        def source(_device):
            element = recorder.Gst.ElementFactory.make("audiotestsrc", None)
            element.set_property("is-live", True)
            recorder.Gst.util_set_object_arg(element, "wave", "silence")
            return element

        for automatic, expected in ((False, 1), (True, 2)):
            with self.subTest(automatic=automatic), tempfile.TemporaryDirectory() as folder:
                plan = Plan(None, (first,), automatic_input=False, automatic_output=automatic)
                with mock.patch.object(recorder, "_source", source):
                    take = recorder.Recorder(plan, Path(folder), "watch", lister=lambda: [first, added])
                    take.start()
                    try:
                        take._look_at_devices()
                        take._look_at_devices()
                        self.assertEqual(len(take.takes()), expected)
                        end = time.monotonic() + 3
                        while len(take.snapshot().levels) < expected and time.monotonic() < end:
                            time.sleep(0.01)
                        self.assertEqual(len(take.snapshot().levels), expected)
                    finally:
                        take.stop()


class FollowTheCallTest(unittest.TestCase):
    """A call program opens another microphone while recording: the recorder's own pipeline on live tones."""

    def setUp(self):
        from unittest import mock
        self.recorder = gstreamer()
        self.enterContext(mock.patch.object(self.recorder, "_source", tone_source(self.recorder)))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def devices(self, teams_on_webcam: bool):
        from scriba.core.devices import INPUT, OUTPUT, Device
        teams = ("ms-teams.exe",) if teams_on_webcam else ()
        return [Device("out", "Altoparlanti", OUTPUT, default=True),
                Device("array", "Gruppo microfoni", INPUT, default=True),
                Device("webcam", "Microfono (HD Webcam)", INPUT, programs=teams)]

    def test_the_automatic_choice_moves_to_the_microphone_teams_opens_and_closes_the_one_before(self):
        from scriba.app import recording
        from scriba.app.engines import ffmpeg
        from scriba.core.devices import Plan
        listed = self.devices(False)
        plan = Plan(listed[1], (listed[0],))
        made = self.recorder.Recorder(plan, self.folder, "segui", lister=lambda: listed)
        made.start()
        try:
            time.sleep(1.0)
            made._look_at_devices()
            self.assertEqual(len(made.takes()), 2)
            listed = self.devices(True)                 # the call begins after Record
            made._look_at_devices()
            self.assertEqual(len(made.takes()), 2)      # not at the first look
            made._look_at_devices()
            time.sleep(1.0)
        finally:
            takes = made.stop()
        mics = [t for t in takes if t.role == "me"]
        self.assertEqual([t.device for t in mics], ["Gruppo microfoni", "Microfono (HD Webcam)"])
        before, after = mics
        self.assertTrue(before.replaced)
        self.assertAlmostEqual(before.ended, 1.0, delta=0.4)
        self.assertEqual(after.began, before.ended)
        self.assertIsNone(after.ended)
        event = made.snapshot().events[-1]
        self.assertEqual((event.kind, event.device, event.detail),
                         (recording.FOLLOWED, "Microfono (HD Webcam)", "Teams"))
        # the closed track is readable up to where it was closed; the new one is in step with the others
        total = ffmpeg.decoded_seconds(next(t for t in takes if t.role == "others").path)
        self.assertAlmostEqual(ffmpeg.decoded_seconds(before.path), before.ended, delta=0.3)
        self.assertAlmostEqual(ffmpeg.decoded_seconds(after.path), total, delta=0.3)
        self.assertGreater(peak(after.path, after.began + 0.3, 0.5), 0)
        # joined, both microphone tracks are the user's
        joined = recording.join(takes, self.folder, "segui")
        self.assertEqual([t.role for t in ffmpeg.probe(joined).roles], ["me", "others", "me"])

    def test_a_microphone_chosen_by_hand_stays_and_the_call_s_is_said_once(self):
        from scriba.app import recording
        from scriba.core.devices import Plan
        listed = self.devices(True)
        made = self.recorder.Recorder(Plan(listed[1], (listed[0],), automatic_input=False), self.folder, "mano",
                                      lister=lambda: listed)
        made.start()
        try:
            for _ in range(5):
                made._look_at_devices()
        finally:
            takes = made.stop()
        self.assertEqual([t.device for t in takes if t.role == "me"], ["Gruppo microfoni"])
        kinds = [e.kind for e in made.snapshot().events]
        self.assertEqual(kinds.count(recording.CALL_ELSEWHERE), 1)
        self.assertNotIn(recording.FOLLOWED, kinds)


if __name__ == "__main__":
    unittest.main()
