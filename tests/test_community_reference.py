"""Public-corpus preparation without loading the reference runtime."""
import _setup  # noqa: F401
import importlib.util
from pathlib import Path
import tempfile
import unittest


class PublicReferenceTests(unittest.TestCase):
    def test_the_silence_diagnostic_cannot_certify_whole_meetings(self):
        tool = self.tool()
        manifest = {'silence_diagnostic': {'inserted_interval': [60, 120]}}
        for suite in ('dev', 'test', 'all', 'stress'):
            with self.subTest(suite=suite), self.assertRaises(ValueError):
                tool.validate_scope(manifest, suite)
        tool.validate_scope(manifest, 'brief')

    def test_reference_rttm_preserves_submillisecond_boundaries_like_the_candidate(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'normal.rttm'
            tool.write_rttm(path, 'public', [(1.00048828125, 2.00048828125, 'voice')])
            self.assertEqual(path.read_text(),
                             'SPEAKER public 1 1.000488 1.000000 <NA> <NA> voice <NA> <NA>\n')

    def test_inserted_silence_splits_a_crossing_turn_and_shifts_later_turns(self):
        tool = self.tool()
        self.assertEqual(tool.insert_silence_intervals([(1., 5.), (7., 8.)], 3., 60.),
                         [(1., 3.), (63., 65.), (67., 68.)])

    def tool(self):
        path = _setup.ROOT / 'tools' / 'pyannote_reference.py'
        self.assertTrue(path.exists(), 'reference tool is missing')
        spec = importlib.util.spec_from_file_location('reference_tool', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_words_merge_only_when_touching_and_non_words_are_ignored(self):
        tool = self.tool()
        xml = '<root><w starttime="1" endtime="2">one</w><w starttime="2" endtime="3">two</w><w starttime="4" endtime="5">three</w><vocal starttime="5" endtime="6"/></root>'
        self.assertEqual(tool.word_intervals(xml), [(1., 3.), (4., 5.)])

    def test_invalid_times_are_rejected(self):
        tool = self.tool()
        with self.assertRaises(ValueError):
            tool.word_intervals('<root><w starttime="2" endtime="1">bad</w></root>')

    def test_output_is_exclusive_and_preserves_previous_bytes(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'result.json'
            tool.write_json(path, {'value': 1})
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                tool.write_json(path, {'value': 2})
            self.assertEqual(path.read_bytes(), before)

    def test_manifest_detects_corruption_and_missing_files(self):
        tool = self.tool()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audio.wav'
            path.write_bytes(b'public synthetic data')
            entry = tool.file_record(path)
            tool.verify_record(path, entry)
            path.write_bytes(b'damaged synthetic data')
            with self.assertRaises(ValueError):
                tool.verify_record(path, entry)
