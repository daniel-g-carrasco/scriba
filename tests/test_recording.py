"""A recording made whole (app/recording.py), with the real ffmpeg on
FLAC files made for the purpose: the tracks joined with their roles, the
mix, the silent output left out, and nothing removed when joining fails."""

import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401

from scriba.app import jobs, recording
from scriba.app.engines import ffmpeg
from scriba.app.recording import Take


def flac(path: Path, seconds: float, volume: float, frequency: int = 440) -> Path:
    """`seconds` of a tone at `volume` (0: digital silence), 48 kHz, 24 bits."""
    source = f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}" if volume else \
        f"anullsrc=r=48000:cl=mono:d={seconds}"
    from test_engines import fixture_ffmpeg
    subprocess.run([fixture_ffmpeg(), "-v", "error", "-f", "lavfi", "-i", source, "-af", f"volume={volume or 1}",
                    "-ac", "1", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", "-c:a", "flac", "-y", str(path)],
                   check=True)
    return path


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg")
class FinishTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def takes(self):
        return [Take("me-1", "me", "Cuffia auricolare (Headset BT)", flac(self.folder / "r_mic.flac", 3, 0.05),
                     loudest=-26.0),
                Take("others-2", "others", "Cuffie (Headset BT)", flac(self.folder / "r_out 1.flac", 3, 0.3, 660),
                     loudest=-10.0),
                Take("others-3", "others", "Altoparlanti (Realtek(R) Audio)", flac(self.folder / "r_out 2.flac", 3, 0),
                     loudest=-350.0)]

    def test_muted_microphone_is_silent_in_new_and_rebuilt_mixes_but_original_is_exact(self):
        microphone = flac(self.folder / "mic.flac", 6, .3)
        take = Take("mic", "me", "Mic", microphone, loudest=-20, muted=((2, 4),))
        saved = ffmpeg.decode(microphone, self.folder / "saved.wav").read_bytes()
        done = recording.finish([take], self.folder, "r")
        original = done.recording.read_bytes()
        self.assertEqual(ffmpeg.decode(done.recording, self.folder / "original.wav").read_bytes(), saved)
        rebuilt = recording.listening_mix(done.recording)
        self.assertEqual(done.recording.read_bytes(), original)
        for n, copy in enumerate((done.together, rebuilt)):
            from scriba.app.engines import audio
            samples = audio.read(ffmpeg.decode(copy, self.folder / f"copy{n}.wav"))
            self.assertGreater(abs(samples[:audio.RATE]).max(), 100)
            self.assertLessEqual(abs(samples[3 * audio.RATE: 7 * audio.RATE // 2]).max(), 1)

    def test_echo_is_measured_and_cancelled_only_outside_mute(self):
        from scriba.app.engines import echo as canceller
        from scriba.core.echo import Window

        mic = Take("mic", "me", "Mic", flac(self.folder / "mic.flac", 40, .2), muted=((10, 30),))
        out = Take("out", "others", "Output", flac(self.folder / "out.flac", 40, .2))
        work = self.folder / "work"
        work.mkdir()

        def measure(_path, _progress, _cancelled, *, began, ended):
            verdict = .8 if began == 0 else 0
            return [Window(began, ended, verdict, 0, -20, -20)], 40

        with mock.patch.object(canceller, "measure", side_effect=measure) as measured, \
                mock.patch.object(canceller, "cancel", wraps=canceller.cancel) as cancel:
            takes, _, notes = recording.without_echo([mic, out], work)
        self.assertEqual([(c.kwargs["began"], c.kwargs["ended"]) for c in measured.call_args_list],
                         [(0, 10), (30, 40)])
        self.assertEqual(cancel.call_count, 1)
        self.assertIn("50 % of the time you were unmuted", notes[0])
        from scriba.app.engines import audio
        samples = audio.read(takes[0].path)
        self.assertFalse(samples[10 * audio.RATE:30 * audio.RATE].any())

    def test_legacy_listening_copy_is_preserved_and_the_new_revision_is_used(self):
        from scriba.app.backend import Backend
        from scriba.app.engines import audio

        microphone = flac(self.folder / "mic.flac", 6, .3)
        take = Take("mic", "me", "Mic", microphone, loudest=-20, muted=((2, 4),))
        joined = recording.join([take], self.folder, "old")
        media = ffmpeg.probe(joined)
        original = joined.read_bytes()
        legacy = self.folder / "old_together.m4a"
        subprocess.run([str(ffmpeg.need("ffmpeg")), "-nostdin", "-v", "error", "-i", str(microphone),
                        "-c:a", "aac", str(legacy)], check=True)
        old_bytes = legacy.read_bytes()
        self.assertEqual(jobs.listening_copy(joined, media), joined)
        service = Backend(self.folder, self.folder / "speeds.json")
        clip = service.preview_listening_copy(joined, legacy, 2.5, 3.5, None)
        self.addCleanup(clip.close)
        self.assertFalse(audio.read(ffmpeg.decode(clip.path, self.folder / "preview.wav")).any())
        rebuilt = recording.listening_mix(joined)
        self.assertEqual(rebuilt.name, "old_together_R01.m4a")
        self.assertEqual(jobs.listening_copy(joined, media), rebuilt)
        self.assertEqual(joined.read_bytes(), original)
        self.assertEqual(legacy.read_bytes(), old_bytes)

    def test_the_tracks_go_into_one_file_with_their_roles_and_the_silent_output_is_left_out(self):
        takes = self.takes()
        takes[0].meeting_title = "Verifica continuità"
        done = recording.finish(takes, self.folder, "r")
        self.assertEqual(done.recording, self.folder / "r.mka")
        media = ffmpeg.probe(done.recording)
        self.assertEqual(media.meeting_title, "Verifica continuità")
        self.assertEqual([(t.role, t.title) for t in media.roles],
                         [("me", "Microphone: Cuffia auricolare (Headset BT)"), ("others", "Output: Cuffie (Headset BT)")])
        self.assertAlmostEqual(media.duration, 3.0, delta=0.1)
        self.assertEqual([t.key for t in done.dropped], ["others-3"])
        self.assertIn("Altoparlanti (Realtek(R) Audio): silent from start to end, not kept.", done.notes)
        self.assertEqual(done.together, self.folder / "r_together.m4a")
        self.assertAlmostEqual(ffmpeg.probe(done.together).duration, 3.0, delta=0.15)
        # the FLAC files are gone: their bits are in the .mka
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["r.mka", "r_together.m4a"])

    def test_the_mix_brings_the_quiet_microphone_and_the_loud_output_to_one_loudness(self):
        takes = self.takes()[:2]
        done = recording.finish(takes, self.folder, "r")
        measure = ffmpeg.measure_loudness(done.together)
        # two tones (not in phase) at -23 LUFS each, summed: about -20, and under the ceiling
        self.assertAlmostEqual(measure.integrated, -20.0, delta=1.5)
        self.assertLessEqual(measure.true_peak, -0.5)

    def test_each_step_after_the_stop_is_said_as_it_begins(self):
        steps = []
        recording.finish(self.takes()[:2], self.folder, "r", progress=steps.append)
        self.assertEqual(steps, [recording.JOINING, recording.CLEANING, recording.MIXING])
        steps.clear()
        recording.finish([Take("others-1", "others", "Cuffie", flac(self.folder / "o.flac", 3, 0.3), loudest=-10.0)],
                         self.folder, "o", progress=steps.append)
        self.assertEqual(steps, [recording.JOINING, recording.MIXING])     # no microphone: nothing to clean

    def test_a_name_that_is_taken_is_not_overwritten(self):
        (self.folder / "r.mka").write_bytes(b"another meeting")
        done = recording.finish(self.takes(), self.folder, "r")
        self.assertEqual(done.recording.name, "r_R01.mka")
        self.assertEqual((self.folder / "r.mka").read_bytes(), b"another meeting")

    def test_skipping_before_join_keeps_the_original_tracks(self):
        takes = self.takes()
        cancel = threading.Event()
        cancel.set()
        done = recording.finish(takes, self.folder, "r", cancelled=cancel)
        self.assertTrue(done.pending)
        self.assertTrue(all(t.path.is_file() for t in takes))
        self.assertFalse(list(self.folder.glob("*.mka")))

    def test_skipping_after_join_allows_rebuilding_the_listening_copy(self):
        takes = self.takes()
        cancel = threading.Event()

        def step(name):
            if name == recording.CLEANING:
                cancel.set()

        done = recording.finish(takes, self.folder, "r", cancelled=cancel, progress=step)
        self.assertFalse(done.pending)
        self.assertIsNone(done.together)
        self.assertEqual(ffmpeg.probe(done.recording).sound_tracks, 2)
        mix = recording.listening_mix(done.recording)
        self.assertAlmostEqual(ffmpeg.probe(mix).duration, 3, delta=.15)

    def test_an_overlong_track_is_bounded_in_both_outputs_and_the_source_is_retained(self):
        takes = self.takes()[:2]
        out = takes[1].path
        flac(out, 30, 0.3)
        original = out.read_bytes()
        progress = []
        done = recording.finish(takes, self.folder, "r", elapsed=3, mix_progress=lambda *p: progress.append(p))
        self.assertEqual(out.read_bytes(), original)
        media = ffmpeg.probe(done.recording)
        self.assertEqual(media.sound_tracks, 2)
        self.assertAlmostEqual(media.duration, 3, delta=0.15)
        self.assertAlmostEqual(ffmpeg.probe(done.together).duration, 3, delta=0.15)
        self.assertTrue(any(n == 2 and reached > 0 for n, _, reached, _ in progress))
        self.assertTrue(any(n == 0 and reached > 0 for n, _, reached, _ in progress))

    def test_when_joining_fails_every_track_stays_and_the_reason_is_said(self):
        takes = self.takes()
        broken = self.folder / "r_out 3.flac"
        broken.write_bytes(b"not a flac")
        takes.append(Take("others-4", "others", "Rotto", broken, loudest=-20.0))
        done = recording.finish(takes, self.folder, "r")
        self.assertEqual(done.recording, takes[0].path)      # the microphone's own file
        self.assertIsNone(done.together)
        self.assertIn("The tracks are kept as separate files.", done.notes[0])
        for take in takes:
            self.assertTrue(take.path.exists(), take.path)
        self.assertFalse(any(p.suffix in (".mka", ".m4a", ".tmp") for p in self.folder.iterdir()))


class OffNotesTest(unittest.TestCase):
    def test_the_stretches_switched_off_in_scriba_are_said_for_each_track(self):
        mic = Take("me-1", "me", "Cuffia", Path("m.flac"), off=((65.0, 130.0), (3000.0, None)))
        self.assertEqual(recording.off_notes([mic, Take("others-2", "others", "Cuffie", Path("o.flac"))]), [
            "Microphone: Cuffia: switched off in Scriba from 01:05 to 02:10, from 50:00 to the end. "
            "There is silence on its track during these intervals."])


class MeetingFolderTest(unittest.TestCase):
    def test_each_recording_has_a_folder_named_with_its_date_and_time_and_never_one_that_is_there(self):
        import datetime

        from scriba.core import recording as rec
        when = datetime.datetime(2026, 11, 13, 14, 20, 5)
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            first = recording.meeting_folder(recordings, when)
            self.assertEqual(first, recordings / "2026-11-13 14-20-05")
            self.assertTrue(rec.stem(when, "registrazione").startswith(first.name + " "))
            first.mkdir()
            self.assertEqual(recording.meeting_folder(recordings, when), recordings / "2026-11-13 14-20-05_R01")

    def test_a_recording_that_cannot_start_leaves_no_empty_folder_behind(self):
        try:
            from scriba.app import recorder
        except (ImportError, ValueError) as e:
            self.skipTest(f"GStreamer: {e}")
        from unittest import mock

        from scriba.core.devices import INPUT, Device, Plan
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "2026-11-13 14-20-05"
            made = recorder.Recorder(Plan(Device("mic", "Mic", INPUT), ()), folder, "x", lister=lambda: [])
            self.assertTrue(folder.is_dir())
            with mock.patch.object(recorder.rec, "may_start", return_value=False), \
                    self.assertRaises(recorder.Failure):
                made.start()
            self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
