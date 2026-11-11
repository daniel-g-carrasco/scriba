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


if __name__ == "__main__":
    unittest.main()
