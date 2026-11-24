"""Listening balance: measured on synthetic tracks with the real ffmpeg.

No sound is played and no recording device is opened. Originals and
transcripts stay byte for byte as they were; a centre copy has the same
decoded audio as the recorder's automatic mix.
"""

import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup  # noqa: F401
from test_recording import flac

from scriba.app import jobs, recording
from scriba.app.engines import audio, ffmpeg
from scriba.app.recording import Take
from scriba.app.task import Failure
from scriba.core import loudness


class BalanceGainTest(unittest.TestCase):
    def test_the_centre_changes_nothing_and_each_end_only_attenuates_the_other_group(self):
        for position, mine, others in ((0, 0, 0), (1, 0, -12), (-1, -12, 0), (0.5, 0, -6)):
            with self.subTest(position=position):
                self.assertEqual(loudness.balance_gain("me", position), mine)
                self.assertEqual(loudness.balance_gain("others", position), others)
        for bad in (math.nan, math.inf, -1.1, 1.1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                loudness.balance_gain("me", bad)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg")
class BalanceFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def takes(self):
        return [Take(str(n), role, f"Device {n}", flac(self.folder / f"{n}.flac", 3, volume, hz))
                for n, (role, volume, hz) in enumerate((("me", 0.05, 440), ("others", 0.3, 660),
                                                       ("others", 0.15, 880), ("me", 0.1, 1100)))]

    def samples(self, source, name):
        return audio.read(ffmpeg.decode(source, self.folder / f"{name}.wav"))

    def balanced_samples(self, joined, position, name):
        # Keep the real AAC output; replay its actual filter graph losslessly
        # to measure gains independently of the encoder's masking/quantisation.
        with mock.patch.object(recording, "_run", wraps=recording._run) as run:
            made = recording.rebalance(joined, self.folder, "call", position)
        command = list(run.call_args.args[0])
        for option in ("-b:a", "-movflags"):
            at = command.index(option)
            del command[at:at + 2]
        command[command.index("-c:a") + 1] = "pcm_s16le"
        command[command.index("-f") + 1] = "wav"
        lossless = self.folder / f"{name}-lossless.wav"
        command[-1] = str(lossless)
        recording._run(command, "The lossless test mix could not be made.", None)
        return made, self.samples(lossless, f"{name}-pcm")

    def test_centre_matches_the_automatic_mix_and_both_ends_change_all_tracks_of_each_role(self):
        import numpy as np
        takes = self.takes()
        joined = recording.join(takes, self.folder, "call")
        automatic = recording.mix(takes, self.folder, "call")
        before = {p: p.read_bytes() for p in (joined, automatic, *(t.path for t in takes))}
        self.assertTrue(recording.balance_available(joined))
        centre, centre_pcm = self.balanced_samples(joined, 0, "centre")
        centre_aac = self.samples(centre, "centre")
        np.testing.assert_array_equal(centre_aac, self.samples(automatic, "automatic"))
        reference = np.abs(np.fft.rfft(centre_pcm[audio.RATE:2 * audio.RATE]))
        for position in (-1, 1):
            made, pcm = self.balanced_samples(joined, position, f"end{position}")
            # The user's AAC file is still made and decoded by the production path.
            self.assertEqual(len(self.samples(made, f"end{position}")), len(centre_aac))
            spectrum = np.abs(np.fft.rfft(pcm[audio.RATE:2 * audio.RATE]))
            for hz, role in ((440, "me"), (660, "others"), (880, "others"), (1100, "me")):
                change = 20 * math.log10(spectrum[hz] / reference[hz])
                expected = -12 if (position > 0 and role == "others") or (position < 0 and role == "me") else 0
                self.assertAlmostEqual(change, expected, delta=0.05, msg=f"{position}: {role}, {hz} Hz")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, path.name)

    def test_the_preview_keeps_the_roles_in_one_excerpt_and_changes_their_balance_at_the_current_position(self):
        import numpy as np

        from scriba.app import preview
        takes = [Take("0", "me", "Microphone", flac(self.folder / "mic.flac", 8, 0.05, 440)),
                 Take("1", "others", "Output", flac(self.folder / "out.flac", 8, 0.3, 660))]
        joined = recording.join(takes, self.folder, "call")
        before = {p: p.read_bytes() for p in self.folder.iterdir()}

        def spectrum(samples):
            return np.abs(np.fft.rfft(samples))           # one second: bins of 1 Hz

        mkdtemp = tempfile.mkdtemp
        with mock.patch.object(preview.tempfile, "mkdtemp",
                               lambda suffix, prefix, _dir: mkdtemp(suffix, prefix, self.folder)):
            clip = preview.balanced(joined, 2, 6)
        rate = recording.PREVIEW_RATE
        self.assertAlmostEqual(clip.frames / rate, 4, delta=0.05)
        self.assertEqual(set(self.folder.iterdir()), set(before))
        spectra = {position: spectrum(clip.render(rate, 2 * rate, position, position))
                   for position in (-1, 0, 0.5, 1)}
        for position in (-1, 1):
            for hz, role in ((440, "me"), (660, "others")):
                change = 20 * math.log10(spectra[position][hz] / spectra[0][hz])
                expected = -12 if (position > 0 and role == "others") or (position < 0 and role == "me") else 0
                self.assertAlmostEqual(change, expected, delta=0.05, msg=f"{position}: {role}")
        # the same road as the copy saved: the same ratio of the two sides
        _made, whole = self.balanced_samples(joined, 0.5, "half")
        saved = np.abs(np.fft.rfft(whole[3 * audio.RATE:4 * audio.RATE]))        # 16 kHz, also 1 Hz bins
        self.assertAlmostEqual(20 * math.log10(spectra[0.5][440] / spectra[0.5][660]),
                               20 * math.log10(saved[440] / saved[660]), delta=0.1)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content, path.name)

    def test_the_live_mix_ramps_the_gains_and_caps_the_combined_peak(self):
        import numpy as np

        from scriba.app.preview import BalanceClip

        clip = BalanceClip(np.full(5, 0.8, dtype=np.float32), np.full(5, 0.8, dtype=np.float32))
        self.assertLessEqual(np.max(np.abs(clip.render(0, 5, 0, 0))), recording.LIMIT + 1e-7)
        clip = BalanceClip(np.full(5, 0.25, dtype=np.float32), np.zeros(5, dtype=np.float32))
        faded = clip.render(0, 5, 0, -1)
        self.assertAlmostEqual(faded[0], 0.25)
        self.assertAlmostEqual(faded[-1], 0.25 * 10 ** (-12 / 20))
        self.assertTrue(np.all(np.diff(faded) < 0))

    def test_the_limiter_still_holds_a_coherent_sum_and_every_copy_has_a_free_name(self):
        tone = flac(self.folder / "tone.flac", 3, 0.2)
        takes = [Take(str(n), "me" if n % 2 else "others", f"Device {n}", tone) for n in range(12)]
        joined = recording.join(takes, self.folder, "loud")
        first = recording.rebalance(joined, self.folder, "loud", 0)
        before = first.read_bytes()
        second = recording.rebalance(joined, self.folder, "loud", 1)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), before)
        measured = ffmpeg.measure_loudness(first)
        self.assertLessEqual(measured.true_peak, -0.5)
        print(f"balance synthetic coherent sum: {measured.true_peak:.1f} dBTP")

    def test_missing_or_single_role_recordings_are_refused_without_writing_anything(self):
        takes = self.takes()
        single = recording.join(takes[:1], self.folder, "single")
        before = sorted(p.name for p in self.folder.iterdir())
        self.assertFalse(recording.balance_available(single))
        self.assertFalse(recording.balance_available(self.folder / "missing.mka"))
        for source in (single, self.folder / "missing.mka"):
            with self.assertRaises(Failure):
                recording.rebalance(source, self.folder, "wrong", 0)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)

    def test_a_reopened_session_keeps_the_balance_in_place_with_transcripts_untouched(self):
        import fake_engines
        from scriba.data import session
        joined = recording.join(self.takes(), self.folder, "meeting")
        made = fake_engines.made_result(self.folder, joined, fake_engines.lines(2, 4, 1))
        jobs.save_names(made, {"Speaker 1": "Io"})
        opened, request = jobs.open_session(made.session)
        before = {p.resolve(): p.read_bytes() for p in self.folder.iterdir() if p.is_file()}
        original_segments = list(opened.segments)
        saved = jobs.save_balance(opened, 0.5)
        self.assertEqual(saved.notes, [])
        self.assertEqual(opened.segments, original_segments)
        self.assertEqual(saved.outputs[0].kind, "audio-balanced")
        self.assertEqual(opened.listen, Path(saved.outputs[0].path))
        for path, content in before.items():
            if path != opened.session:
                self.assertEqual(path.read_bytes(), content, path.name)
        self.assertIn(opened.session, before)
        again, request = jobs.open_session(opened.session)
        self.assertEqual(again.listen, opened.listen)
        self.assertEqual(again.names, {"Speaker 1": "Io"})
        self.assertEqual(jobs.listening_balance(again), 0.5)
        self.assertIn("audio-balanced", {o.kind for o in session.load(opened.session).outputs})
