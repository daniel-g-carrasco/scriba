"""A recording made whole (app/recording.py), with the real ffmpeg on
FLAC files made for the purpose: the tracks joined with their roles, the
mix, the silent output left out, and nothing removed when joining fails."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401

from scriba.app import recording
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
        return [Take("me-1", "me", "Cuffia auricolare (Free BYRD)", flac(self.folder / "r_mic.flac", 3, 0.05),
                     loudest=-26.0),
                Take("others-2", "others", "Cuffie (Free BYRD)", flac(self.folder / "r_out 1.flac", 3, 0.3, 660),
                     loudest=-10.0),
                Take("others-3", "others", "Altoparlanti (Realtek(R) Audio)", flac(self.folder / "r_out 2.flac", 3, 0),
                     loudest=-350.0)]

    def test_the_tracks_go_into_one_file_with_their_roles_and_the_silent_output_is_left_out(self):
        takes = self.takes()
        done = recording.finish(takes, self.folder, "r")
        self.assertEqual(done.recording, self.folder / "r.mka")
        media = ffmpeg.probe(done.recording)
        self.assertEqual([(t.role, t.title) for t in media.roles],
                         [("me", "Microphone: Cuffia auricolare (Free BYRD)"), ("others", "Output: Cuffie (Free BYRD)")])
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

    def test_a_name_that_is_taken_is_not_overwritten(self):
        (self.folder / "r.mka").write_bytes(b"another meeting")
        done = recording.finish(self.takes(), self.folder, "r")
        self.assertEqual(done.recording.name, "r_R01.mka")
        self.assertEqual((self.folder / "r.mka").read_bytes(), b"another meeting")

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


if __name__ == "__main__":
    unittest.main()
