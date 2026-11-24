import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401

from scriba.app import cuts, jobs, recording
from scriba.app.engines import ffmpeg
from scriba.core.recording import tail_cut
from scriba.data import atomic
from test_recording import flac


class TailCutTest(unittest.TestCase):
    def test_point_comes_from_call_end_or_five_minutes_of_output_silence(self):
        self.assertEqual(tail_cut(1000, 600, 500), ("call", 600))
        self.assertEqual(tail_cut(1000, None, 500), ("silent", 500))
        self.assertIsNone(tail_cut(1000, None, 900))
        self.assertIsNone(tail_cut(1000, None, 0))

    def test_cut_keeps_roles_tags_and_archives_the_unmodified_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mic = recording.Take("m", "me", "Mic", flac(root / "m.flac", 4, .1),
                                 muted=((1, 2),), meeting_title="Example", teams_people=8)
            out = recording.Take("o", "others", "Out", flac(root / "o.flac", 4, .2, 660),
                                 named_spans=(("Example Speaker", .5, 1.5),))
            source = recording.join([mic, out], root, "recording", cut=("call", 2))
            before = ffmpeg.probe(source)
            original = source.read_bytes()
            cut = recording.trim_tail(source, 2.5, revisions=jobs.revisions_folder())
            after = ffmpeg.probe(cut)
            self.assertIsNotNone(before.suggested_cut)
            self.assertEqual(after.roles, before.roles)
            self.assertEqual(after.meeting_title, "Example")
            self.assertEqual((before.teams_people, after.teams_people), (8, 8))
            self.assertIsNone(after.suggested_cut)
            self.assertAlmostEqual(after.duration, 2.5, delta=.2)
            self.assertFalse(source.exists())
            archived, = (root / jobs.revisions_folder()).glob("*.mka")
            self.assertEqual(archived.read_bytes(), original)

    def test_editing_a_cut_replaces_the_working_mka_and_mix_but_keeps_the_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            takes = [recording.Take("m", "me", "Mic", flac(root / "m.flac", 9, .1)),
                     recording.Take("o", "others", "Out", flac(root / "o.flac", 9, .2, 660))]
            source = recording.join(takes, root, "recording", call_range=(2, 7))
            whole = source.read_bytes()
            view = cuts.inspect(source, ffmpeg.probe(source))
            self.assertEqual(view.proposal, (2, 7))
            self.assertFalse(view.trimmed)
            self.assertEqual(cuts.change(source, 2, 7), source)
            first = cuts.inspect(source, ffmpeg.probe(source))
            self.assertEqual((first.selection, first.call, first.duration), ((2, 7), (2, 7), 9))
            self.assertEqual(first.original.read_bytes(), whole)
            self.assertEqual(cuts.change(source, 1, 8), source)
            second = cuts.inspect(source, ffmpeg.probe(source))
            self.assertEqual(second.selection, (1, 8))
            self.assertEqual(second.original, first.original)
            self.assertEqual(second.original.read_bytes(), whole)
            self.assertEqual(len(list(root.glob("*.mka"))), 1)
            self.assertEqual(len(list(root.glob("*.m4a"))), 1)
            self.assertAlmostEqual(ffmpeg.probe(next(root.glob("*.m4a"))).duration, 7, delta=.2)

    def test_a_legacy_cut_gets_its_interval_from_the_checkpoint(self):
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            take = recording.Take("m", "me", "Mic", flac(root / "m.flac", 6, .1))
            source = recording.join([take], root, "test")
            cut = recording.trim(source, 1, 5, revisions=jobs.revisions_folder())
            atomic.write_json(root / ".test.scriba-recording.json", {"joined": [cut.name], "excerpt": [1, 5]})
            legacy = replace(ffmpeg.probe(cut), trim_selection=None, trim_original="")
            view = cuts.inspect(cut, legacy)
            self.assertEqual(view.selection, (1, 5))
            self.assertAlmostEqual(view.duration, 6, delta=.2)
            self.assertTrue(view.original.is_file())

    def test_an_old_manual_cut_can_be_located_by_its_losslessly_copied_packets(self):
        from dataclasses import replace

        import numpy as np

        from scriba.app.engines import audio

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            wave = root / "mic.wav"
            audio.write(wave, np.random.default_rng(4).integers(-12000, 12000, 7 * audio.RATE, dtype=np.int16))
            source = recording.join([recording.Take("m", "me", "Mic", wave)], root, "test")
            excerpt = recording.trim(source, 2, 5, revisions=jobs.revisions_folder())
            legacy = replace(ffmpeg.probe(excerpt), trim_selection=None, trim_original="")
            view = cuts.inspect(excerpt, legacy)
            self.assertIsNotNone(view.selection)
            self.assertAlmostEqual(view.selection[0], 2, delta=.15)
            self.assertAlmostEqual(view.selection[1], 5, delta=.15)

    def test_keep_interval_shifts_and_clips_the_time_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mic = recording.Take("m", "me", "Mic", flac(root / "m.flac", 7, .1),
                                 off=((3, 4), (5, None)), muted=((0, 1), (1, 3), (4, None)), meeting_title="Example")
            out = recording.Take("o", "others", "Out", flac(root / "o.flac", 7, .2, 660),
                                 named_spans=(("Speaker A", .5, 3), ("Speaker B", 4, 7)))
            source = recording.join([mic, out], root, "recording", call_range=(2, 5))
            before = ffmpeg.probe(source)
            original = source.read_bytes()
            cut = recording.trim(source, 2, 5, revisions=jobs.revisions_folder())
            after = ffmpeg.probe(cut)
            self.assertEqual(before.suggested_range, (2, 5))
            self.assertIsNone(after.suggested_range)
            self.assertEqual(after.meeting_title, before.meeting_title)
            self.assertEqual([(t.title, t.role) for t in after.roles], [(t.title, t.role) for t in before.roles])
            self.assertEqual(after.roles[0].muted, ((0, 1), (2, 3)))
            self.assertEqual(after.roles[0].off, ((1, 2),))
            self.assertEqual(after.roles[1].named_spans, (("Speaker A", 0, 1), ("Speaker B", 2, 3)))
            self.assertAlmostEqual(after.duration, 3, delta=.12)
            archived, = (root / jobs.revisions_folder()).glob("*.mka")
            self.assertEqual(archived.read_bytes(), original)
