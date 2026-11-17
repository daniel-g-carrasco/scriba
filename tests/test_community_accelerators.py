"""Gates for development-only accelerator measurements."""
import _setup
import sys
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(_setup.ROOT / 'tools'))


class AcceleratorGateTests(unittest.TestCase):
    def test_linux_driver_caches_stay_in_the_new_run_without_creating_it_early(self):
        import os
        from community_accelerators import confine_linux_cache
        with TemporaryDirectory() as directory:
            output = Path(directory) / 'new-run'
            with patch('sys.platform', 'linux'), patch.dict(os.environ, {}, clear=True):
                confine_linux_cache(output)
                expected = str((output / 'cache').resolve())
                self.assertEqual(os.environ['MESA_SHADER_CACHE_DIR'], expected)
                self.assertEqual(os.environ['XDG_CACHE_HOME'], expected)
                self.assertFalse(output.exists())

    def test_windows_cache_configuration_is_left_to_the_platform(self):
        import os
        from community_accelerators import confine_linux_cache
        with patch('sys.platform', 'win32'), patch.dict(os.environ, {'XDG_CACHE_HOME': 'existing'}):
            before = dict(os.environ)
            confine_linux_cache(Path('unused'))
            self.assertEqual(dict(os.environ), before)

    def test_a_changed_speaker_count_fails_even_with_identical_turn_bytes(self):
        from community_accelerators import accepted
        self.assertFalse(accepted({'passed': True}, {'passed': True}, True, 3, 4))

    def test_neural_failure_is_not_overridden_by_identical_turns(self):
        from community_accelerators import accepted
        self.assertFalse(accepted({'passed': False}, {'passed': True}, True, 4, 4))

    def test_all_gates_are_required(self):
        from community_accelerators import accepted
        self.assertTrue(accepted({'passed': True}, {'passed': True}, True, 4, 4))
        self.assertFalse(accepted({'passed': True}, {'passed': True}, False, 4, 4))

    def test_a_missing_openvino_gpu_cannot_be_reported_as_a_cpu_measurement(self):
        from community_accelerators import Session
        fake = SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=['CPU']))
        with patch.dict(sys.modules, {'openvino': fake}):
            with self.assertRaisesRegex(RuntimeError, 'GPU.0 absent'):
                Session('unused.onnx', 'openvino', 'GPU.0', 8)

    def test_a_collection_cannot_be_certified_by_comparing_only_its_first_run(self):
        from community_accelerators import compare
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('reference', 'candidate'):
                (root / name).mkdir()
                (root / name / 'results.json').write_text(json.dumps({'runs': [{}, {}]}))
            with self.assertRaisesRegex(ValueError, 'exactly one run'):
                compare(root / 'reference', root / 'candidate')
