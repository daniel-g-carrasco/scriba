import tempfile
import unittest
from pathlib import Path

import _setup  # noqa: F401

from scriba.app import jobs, recording
from scriba.app.engines import ffmpeg
from scriba.core.recording import tail_cut
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
                                 muted=((1, 2),), meeting_title="Example")
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
            self.assertIsNone(after.suggested_cut)
            self.assertAlmostEqual(after.duration, 2.5, delta=.2)
            self.assertFalse(source.exists())
            archived, = (root / jobs.revisions_folder()).glob("*.mka")
            self.assertEqual(archived.read_bytes(), original)

    def test_keep_interval_shifts_and_clips_the_time_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mic = recording.Take("m", "me", "Mic", flac(root / "m.flac", 7, .1),
                                 muted=((0, 1), (1, 3), (4, None)), meeting_title="Example")
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
            self.assertEqual(after.roles[1].named_spans, (("Speaker A", 0, 1), ("Speaker B", 2, 3)))
            self.assertAlmostEqual(after.duration, 3, delta=.12)
            archived, = (root / jobs.revisions_folder()).glob("*.mka")
            self.assertEqual(archived.read_bytes(), original)
