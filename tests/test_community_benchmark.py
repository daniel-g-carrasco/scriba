"""Development benchmarks must reject hidden runtime dependencies and gaps."""
import _setup  # noqa: F401
import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(_setup.ROOT / 'tools'))
import pyannote_benchmark as benchmark  # noqa: E402


class BenchmarkTests(unittest.TestCase):
    def test_clustering_replay_rejects_a_different_saved_plda_result(self):
        import numpy as np
        saved = {'embeddings': np.eye(2)[None].astype(np.float32),
                 'segmentation': np.array([[[1., 0.], [1., 0.], [0., 1.], [0., 1.]]]),
                 'plda': np.zeros((2, 2)), 'responsibilities': np.zeros((2, 2)),
                 'hard_clusters': np.zeros((1, 2))}
        result = benchmark.clustering_control(saved, lambda x: x, np.ones(2), None, {})
        self.assertFalse(result['plda']['passed'])
        self.assertEqual(result['plda']['max_abs'], 1.)

    def test_a_nested_python_archive_cannot_hide_torch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, 'w') as archive:
                archive.writestr('torch/__init__.py', '')
            with zipfile.ZipFile(root / 'library.zip', 'w') as archive:
                archive.writestr('runtime.whl', nested.getvalue())
            report = root / 'audit.json'
            code = benchmark.audit_bundle(argparse.Namespace(bundle=root, out=report))
            value = json.loads(report.read_text())
            self.assertEqual(code, 1)
            self.assertTrue(any('torch' in failure for failure in value['failures']))

    def test_numerical_array_adapters_are_not_the_pytorch_distribution(self):
        self.assertFalse(benchmark.forbidden('scipy._external.array_api_compat.torch._aliases'))
        self.assertFalse(benchmark.forbidden('lib/python3.13/site-packages/scipy/_external/array_api_compat/torch/__init__.py'))
        self.assertTrue(benchmark.forbidden('torch._C'))
        self.assertTrue(benchmark.forbidden('lib/python3.13/site-packages/torch/__init__.py'))
        self.assertTrue(benchmark.forbidden('torch-2.15.0.dist-info/METADATA'))

    def test_legacy_missing_models_is_a_file_precondition(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root / 'manifest.json'
            manifest.write_text('{"meetings": []}')
            args = argparse.Namespace(models=root, manifest=manifest, engine='legacy', out=root / 'out')
            with self.assertRaises(FileNotFoundError):
                benchmark.run(args)

    def test_plain_python_module_does_not_load_torch_or_pyannote(self):
        import subprocess
        code = "import sys; sys.path.insert(0, 'tools'); import pyannote_benchmark; assert not any(k == 'torch' or k.startswith('pyannote.') for k in sys.modules)"
        result = subprocess.run([sys.executable, '-c', code], cwd=_setup.ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
