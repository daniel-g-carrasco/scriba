"""Export safety and contracts, without Torch in the normal suite."""
import _setup  # noqa: F401
import sys
import os
import unittest

sys.path.insert(0, str(_setup.ROOT / 'tools'))
import pyannote_reference as reference  # noqa: E402


class ExportTests(unittest.TestCase):
    def test_brief_fidelity_has_an_explicit_scope(self):
        args = reference.parser().parse_args(['compare', '--mode', 'fidelity', '--brief',
                                             '--reference', 'ref', '--candidate', 'can',
                                             '--manifest', 'ami.json', '--out', 'out'])
        self.assertTrue(args.brief)

    def test_unlikely_log_scores_are_judged_on_probabilities_for_every_input(self):
        import numpy as np
        expected = np.array([[[0., -20., -30., -30., -30., -30., -30.]]])
        actual = expected.copy()
        actual[..., 1] -= 1.5
        result = reference.segmentation_errors(expected, actual)
        self.assertTrue(result['passed'], result)
        self.assertAlmostEqual(result['max_abs_log'], 1.5)
        self.assertLess(result['max_abs_probability'], 1e-3)

    def test_probability_agreement_cannot_hide_an_argmax_disagreement(self):
        import numpy as np
        expected = np.full((1, 589, 7), -10.)
        actual = expected.copy()
        expected[..., 0] += .001
        actual[..., 1] += .001
        self.assertFalse(reference.segmentation_errors(expected, actual)['passed'])

    def test_all_development_commands_are_parsed_without_loading_torch(self):
        for command, args in (
            ('export', ['--snapshot', 'snapshot', '--manifest', 'ami.json', '--opset', '17']),
            ('reference', ['--snapshot', 'snapshot', '--manifest', 'ami.json', '--suite', 'dev']),
            ('compare', ['--mode', 'fidelity', '--reference', 'ref', '--candidate', 'candidate', '--manifest', 'ami.json']),
        ):
            with self.subTest(command=command):
                parsed = reference.parser().parse_args([command, *args, '--out', 'unused'])
                self.assertEqual(parsed.command, command)

    def test_numerical_gate_rejects_segmentations_over_tolerance(self):
        self.assertTrue(hasattr(reference, 'segmentation_errors'))
        import numpy as np
        expected = np.tile(np.log(np.array([.8, .1, .02, .02, .02, .02, .02], np.float32)), (1, 589, 1))
        actual = expected.copy()
        actual[0, 0] = np.log(np.array([.79, .11, .02, .02, .02, .02, .02], np.float32))
        errors = reference.segmentation_errors(expected, actual)
        self.assertEqual(errors['argmax_difference'], 0.)
        self.assertFalse(errors['passed'])

    def test_numerical_gate_rejects_scaled_embeddings_despite_same_direction(self):
        self.assertTrue(hasattr(reference, 'embedding_errors'))
        import numpy as np
        expected = np.ones((1, 3, 256), dtype=np.float32)
        self.assertFalse(reference.embedding_errors(expected, expected * 2)['passed'])

    def test_silent_reference_uses_false_alarm_seconds(self):
        self.assertTrue(hasattr(reference, 'fidelity_component_passes'))
        self.assertTrue(reference.fidelity_component_passes({'total': 0, 'der': None, 'false alarm': 0}, .005))
        self.assertFalse(reference.fidelity_component_passes({'total': 0, 'der': None, 'false alarm': .1}, .005))

    def test_total_der_sums_errors_and_denominators(self):
        actual = reference.aggregate_components([
            {'total': 10., 'missed detection': 1., 'false alarm': 1., 'confusion': 0.},
            {'total': 90., 'missed detection': 0., 'false alarm': 0., 'confusion': 3.},
        ])
        self.assertEqual(actual['der'], .05)

    def test_a_shortened_manifest_cannot_turn_missing_meetings_into_a_pass(self):
        self.assertTrue(hasattr(reference, 'validate_manifest'))
        with self.assertRaises(ValueError):
            reference.validate_manifest({'meetings': [{'id': 'ES2011a', 'split': 'dev'}]})


@unittest.skipUnless(os.environ.get('SCRIBA_COMMUNITY_EXPORT'), 'export fixtures not selected')
class RealExportTests(unittest.TestCase):
    def test_digital_silence_never_creates_an_imposed_voice(self):
        import json
        import numpy as np
        from pathlib import Path
        from scriba.app.engines.community import diarize
        root = Path(os.environ['SCRIBA_COMMUNITY_EXPORT'])
        recipe = json.loads((root / 'manifest.json').read_text())
        models = {'recipe': recipe, **{name: root / name for name in recipe['files']}}
        for count in (None, 4):
            with self.subTest(speakers=count):
                result = diarize(np.zeros(160000, np.int16), models, speakers=count)
                self.assertEqual((result.turns, result.exclusive_turns, result.speakers), ([], [], 0))

    def test_the_packaged_c_runtime_matches_every_torch_fixture(self):
        import numpy as np
        from pathlib import Path
        from scriba.app.engines.onnx import Session
        root = Path(os.environ['SCRIBA_COMMUNITY_EXPORT'])
        with Session(root / 'segmentation.onnx', threads=4) as segmentation, Session(root / 'embedding.onnx', threads=4) as embedding:
            for path in sorted(root.glob('fixture-*.npz')):
                with np.load(path, allow_pickle=False) as fixture:
                    with self.subTest(case=path.stem, stage='segmentation'):
                        actual = segmentation.run(['scores'], {'waveform': fixture['waveform']})[0]
                        errors = reference.segmentation_errors(fixture['scores'], actual)
                        self.assertTrue(errors['passed'], errors)
                    with self.subTest(case=path.stem, stage='embedding'):
                        actual = embedding.run(['embeddings'], {'feats': fixture['numpy_feats'], 'weights': fixture['weights']})[0]
                        errors = reference.embedding_errors(fixture['embeddings'], actual)
                        self.assertTrue(errors['passed'], errors)
