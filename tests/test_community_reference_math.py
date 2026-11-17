"""Explicit reference-only tests, excluded from ordinary runtime validation."""
import _setup  # noqa: F401
import os
import unittest


@unittest.skipUnless(os.environ.get('SCRIBA_COMMUNITY_REFERENCE') == '1', 'separate reference environment required')
class ReferenceMathTests(unittest.TestCase):
    def test_frozen_sinc_filters_are_exactly_the_learned_torch_filters(self):
        import sys
        sys.path.insert(0, str(_setup.ROOT / 'tools'))
        from pyannote_reference import frozen_sinc_segmentation
        from asteroid_filterbanks import Encoder, ParamSincFB
        import torch
        model = torch.nn.Module()
        model.sincnet = torch.nn.Module()
        model.sincnet.conv1d = torch.nn.ModuleList([Encoder(ParamSincFB(8, 31, stride=3))])
        converted, record = frozen_sinc_segmentation(model)
        samples = torch.arange(128, dtype=torch.float32)[None, None] / 128
        with torch.inference_mode():
            self.assertTrue(torch.equal(model.sincnet.conv1d[0](samples), converted.sincnet.conv1d[0](samples)))
        self.assertEqual(record['shape'], [8, 1, 31])
        self.assertEqual(record['stride'], 3)

    def test_centered_normalization_preserves_learned_values_and_torch_outputs(self):
        import sys
        sys.path.insert(0, str(_setup.ROOT / 'tools'))
        from pyannote_reference import centered_segmentation
        import torch
        original = torch.nn.Sequential(torch.nn.InstanceNorm1d(2, affine=True)).eval()
        with torch.no_grad():
            original[0].weight.copy_(torch.tensor([.75, 1.2]))
            original[0].bias.copy_(torch.tensor([.1, -.2]))
        converted, layers = centered_segmentation(original)
        samples = torch.tensor([[[1., 2., 3., 4., 5., 6., 7.], [3.] * 7]])
        torch.testing.assert_close(converted(samples), original(samples), rtol=1e-5, atol=1e-5)
        self.assertEqual(layers, ['0'])
        self.assertEqual(converted[0].eps, original[0].eps)
        self.assertEqual(set(converted.state_dict()), set(original.state_dict()))
        for key, value in original.state_dict().items():
            self.assertTrue(torch.equal(value, converted.state_dict()[key]))

    def test_inserted_silence_rejects_more_false_alarm_seconds_than_reference(self):
        import sys
        sys.path.insert(0, str(_setup.ROOT / 'tools'))
        from pyannote_reference import silence_comparison
        from pyannote.core import Annotation, Segment
        original, candidate = Annotation(), Annotation()
        original[Segment(60, 60.5)] = 'original'
        candidate[Segment(60, 60.7)] = 'candidate'
        result = silence_comparison(original, candidate, [60, 120])
        self.assertFalse(result['passed'])
        self.assertAlmostEqual(result['original_seconds'], .5)
        self.assertAlmostEqual(result['candidate_seconds'], .7)
        candidate = Annotation()
        candidate[Segment(60, 60.5000005)] = 'candidate'
        self.assertFalse(silence_comparison(original, candidate, [60, 120])['passed'])

    def test_aggregation_matches_reference_nan_handling_and_clock(self):
        import numpy as np
        from pyannote.audio.core.inference import Inference
        from pyannote.core import SlidingWindow, SlidingWindowFeature
        from scriba.app.engines import community_math as m
        rng = np.random.default_rng(42)
        scores = rng.random((3, 589, 3))
        scores[1, :, 0] = np.nan
        frame = {'start': 0., 'step': .016875, 'duration': .0619375}
        for average in (True, False):
            expected = Inference.aggregate(SlidingWindowFeature(scores.copy(), SlidingWindow(duration=10., step=1.)),
                                           SlidingWindow(**frame), missing=0., hamming=False, skip_average=not average)
            actual = m.aggregate(scores, frame, 10., 1., average)
            np.testing.assert_array_equal(actual, expected.data)

    def test_reconstruction_matches_overlap_exclusive_and_permutations(self):
        import numpy as np
        from pyannote.audio.pipelines.speaker_diarization import SpeakerDiarization
        from pyannote.core import SlidingWindow, SlidingWindowFeature
        from scriba.app.engines import community_math as m
        rng = np.random.default_rng(42)
        segmentations = m.powerset(rng.random((3, 589, 7)))
        labels = np.array([[0, 1, 2], [2, 0, 1], [1, 2, 0]])
        frame = {'start': 0., 'step': .016875, 'duration': .0619375}
        feature = SlidingWindowFeature(segmentations, SlidingWindow(duration=10., step=1.))
        count = SpeakerDiarization.speaker_count(feature, SlidingWindow(**frame), warm_up=(0., 0.))
        expected = SpeakerDiarization.reconstruct(SpeakerDiarization, feature, labels, count)
        normal, exclusive = m.reconstruct(segmentations, labels, frame, 10., 1.)
        np.testing.assert_array_equal(normal, expected.data)
        count.data = np.minimum(count.data, 1)
        expected = SpeakerDiarization.reconstruct(SpeakerDiarization, feature, labels, count)
        np.testing.assert_array_equal(exclusive, expected.data)
        annotation = SpeakerDiarization.to_annotation(expected)
        want = sorted((s.start, s.end, f'SPEAKER_{label:02d}') for s, _, label in annotation.itertracks(yield_label=True))
        self.assertEqual([(t.start, t.end, t.label) for t in m.turns(exclusive, frame)], want)

    def test_vbx_is_numerically_identical_for_saved_inputs(self):
        import numpy as np
        from pyannote.audio.utils.vbx import cluster_vbx as reference
        from scriba.app.engines.vbx import cluster_vbx
        x = np.random.default_rng(17).normal(size=(8, 4)).astype(np.float64)
        labels, phi = np.array([0, 0, 1, 1, 0, 0, 1, 1]), np.array([.1, .2, .3, .4])
        expected = reference(labels, x, phi, .07, .8, maxIters=20)
        actual = cluster_vbx(labels, x, phi, .07, .8, maxIters=20)
        for left, right in zip(expected, actual, strict=True):
            np.testing.assert_allclose(left, right, rtol=1e-6, atol=1e-7)

    def test_plda_matches_original_on_the_authorized_parameters(self):
        import numpy as np
        from pathlib import Path
        from pyannote.audio.core.plda import PLDA
        from scriba.app.engines.community import load_plda
        root = Path(os.environ['SCRIBA_COMMUNITY_SNAPSHOT']) / 'plda'
        expected = PLDA(root / 'xvec_transform.npz', root / 'plda.npz')
        transform, phi = load_plda(root / 'xvec_transform.npz', root / 'plda.npz')
        x = np.random.default_rng(42).normal(size=(5, 256))
        np.testing.assert_allclose(transform(x), expected(x), rtol=1e-6, atol=1e-7)
        np.testing.assert_allclose(phi, expected.phi, rtol=1e-6, atol=1e-7)
