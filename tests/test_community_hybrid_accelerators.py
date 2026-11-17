"""Guard device selection and native failures in the isolated hybrid lab."""
import _setup
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(_setup.ROOT / 'tools'))


class HybridTests(unittest.TestCase):
    def test_each_model_uses_its_requested_backend(self):
        from community_hybrid_accelerators import backend_for
        self.assertEqual(backend_for(Path('embedding.onnx'), 'ort', 'ncnn'), 'ncnn')
        self.assertEqual(backend_for(Path('segmentation.onnx'), 'ort', 'ncnn'), 'ort')
        with self.assertRaises(ValueError):
            backend_for(Path('other.onnx'), 'ort', 'ncnn')

    def test_missing_vulkan_device_cannot_silently_run_on_cpu(self):
        from community_hybrid_accelerators import NcnnSession
        with patch.dict(sys.modules, {'ncnn': SimpleNamespace(get_gpu_count=lambda: 0)}):
            with self.assertRaisesRegex(RuntimeError, 'Vulkan device'):
                NcnnSession(Path('embedding.onnx'), 0, 8, Path('converted'))

    def test_native_errors_cannot_produce_a_valid_measurement(self):
        from community_hybrid_accelerators import checked
        with self.assertRaisesRegex(RuntimeError, 'extract.*-1'):
            checked(-1, 'extract')
        checked(0, 'load')

    def test_native_network_is_cleared_before_python_shutdown(self):
        from community_hybrid_accelerators import NcnnSession
        from unittest.mock import Mock
        session = NcnnSession.__new__(NcnnSession)
        native = session.native = Mock()
        session.__exit__(None, None, None)
        native.clear.assert_called_once_with()
        self.assertIsNone(session.native)
