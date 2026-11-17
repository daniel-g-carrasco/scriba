"""Separate real-model validation for the development experiment runner."""
import _setup
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(_setup.ROOT / 'tools'))


class ProviderTests(unittest.TestCase):
    def test_a_directml_provider_cannot_silently_fall_back_during_inference(self):
        import numpy as np
        from scriba.app.engines import community
        import community_experiments as experiments

        class Native:
            def __init__(self, *args, **kwargs):
                self.providers = ['DmlExecutionProvider', 'CPUExecutionProvider']

            def get_providers(self):
                return self.providers

            def run(self, *args):
                self.providers = ['CPUExecutionProvider']
                return [np.zeros((1, 589, 7), np.float32)]

        fake = SimpleNamespace(SessionOptions=type('Options', (), {}), GraphOptimizationLevel=int,
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0), InferenceSession=Native)
        args = experiments.parser().parse_args(['run', '--models', '.', '--out', 'unused', '--provider', 'dml'])
        recipe = {'step': 1, 'recipe_id': 'synthetic'}
        with patch.dict(sys.modules, {'onnxruntime': fake}), patch.object(community, 'validated_files',
                return_value=(recipe, {'segmentation.onnx':Path('segmentation.onnx')})):
            with self.assertRaisesRegex(RuntimeError, 'DirectML'):
                experiments.experiment(np.zeros(160000, np.int16), {}, args)


@unittest.skipUnless(os.environ.get('SCRIBA_COMMUNITY_AMI') and os.environ.get('SCRIBA_COMMUNITY_EXPORT'),
                     'public AMI and verified export not selected')
class RealExperimentTests(unittest.TestCase):
    def test_experiment_preserves_the_real_runtime_for_single_windows(self):
        import numpy as np
        from scriba.app.engines import audio, community
        from pyannote_benchmark import model_set
        import community_experiments as experiments
        models = model_set(Path(os.environ['SCRIBA_COMMUNITY_EXPORT']))
        samples = audio.read(Path(os.environ['SCRIBA_COMMUNITY_AMI']) / 'ES2011a.wav')[:60 * 16000]
        expected = community.diarize(samples, models)
        args = experiments.parser().parse_args(['run', '--models', '.', '--out', 'unused'])
        actual, _times = experiments.experiment(samples, models, args)
        self.assertEqual(actual.turns, expected.turns)
        self.assertEqual(actual.exclusive_turns, expected.exclusive_turns)
        for key in ('scores', 'segmentation', 'embeddings', 'hard_clusters'):
            np.testing.assert_array_equal(actual.intermediates[key], expected.intermediates[key])
