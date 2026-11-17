"""Independent numerical and model-set safety checks for the candidate engine."""
import _setup  # noqa: F401
import importlib.util
import threading
import unittest


class CommunityMathTests(unittest.TestCase):
    def math(self):
        self.assertIsNotNone(importlib.util.find_spec('scriba.app.engines.community_math'))
        from scriba.app.engines import community_math
        return community_math

    def test_all_powerset_classes_include_silence_and_overlap(self):
        import numpy as np
        m = self.math()
        np.testing.assert_array_equal(m.powerset(np.eye(7, dtype=np.float32)),
                                      [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                                       [1, 1, 0], [1, 0, 1], [0, 1, 1]])

    def test_chunk_starts_preserve_tail_and_one_sample_past_boundary(self):
        m = self.math()
        for length, want in [(1, [0]), (159999, [0]), (160000, [0]), (160001, [0, 16000]),
                             (176000, [0, 16000]), (176001, [0, 16000, 32000])]:
            with self.subTest(length=length):
                self.assertEqual(m.chunk_starts(length, 160000, 16000), want)

    def test_pooling_uses_nearest_and_unbiased_weighted_variance(self):
        import numpy as np
        m = self.math()
        x = np.array([[[1., 3., 5., 7.]]], np.float32)
        masks = np.array([[[1., 0.], [1., 1.], [0., 0.]]], np.float32)
        pooled = m.stats_pool(x, masks)
        np.testing.assert_allclose(pooled[0], [[2., 2 ** .5], [4., (20 / 3) ** .5], [0., 0.]], atol=1e-6)

    def test_local_permutations_reconstruct_the_same_global_turns(self):
        import numpy as np
        m = self.math()
        scores = np.array([[[1., 0.], [1., 0.], [0., 1.], [0., 1.]]])
        labels = np.array([[0, 1]])
        frame = {'start': 0., 'step': 1., 'duration': 1.}
        a = m.reconstruct(scores, labels, frame, 4., 1.)
        b = m.reconstruct(scores[:, :, ::-1], labels[:, ::-1], frame, 4., 1.)
        np.testing.assert_array_equal(a[0], b[0])
        np.testing.assert_array_equal(a[1], b[1])
        self.assertTrue(np.all(a[1].sum(-1) <= 1))

    def test_model_set_without_successful_export_is_rejected(self):
        self.assertIsNotNone(importlib.util.find_spec('scriba.app.engines.community'))
        from scriba.app.engines.community import validate_recipe
        with self.assertRaises(ValueError):
            validate_recipe({'export_verified': False, 'files': {}})

    def test_cancellation_is_checked_before_models_are_opened(self):
        import numpy as np
        self.assertIsNotNone(importlib.util.find_spec('scriba.app.engines.community'))
        from scriba.app.engines.community import diarize
        from scriba.app.task import Cancelled
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            diarize(np.zeros(160000, np.int16), {}, cancelled=cancelled)

    def test_zero_valid_embeddings_preserve_the_reference_nan_centroid(self):
        import numpy as np
        m = self.math()
        with self.assertWarns(RuntimeWarning):
            hard, soft, centroids, _ = m.cluster(np.full((1, 3, 2), np.nan), np.zeros((1, 4, 3)), None, None, None)
        np.testing.assert_array_equal(hard, [[0, 0, 0]])
        self.assertTrue(np.isnan(centroids).all())
        self.assertEqual(soft.shape, (1, 3, 1))

    def test_known_count_uses_two_real_embeddings_and_never_invents_a_third(self):
        import numpy as np
        m = self.math()
        vectors = np.array([[[1., 0.], [0., 1.], [np.nan, np.nan]]])
        binary = np.array([[[1., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 1., 0.]]])
        hard, _, centroids, _ = m.cluster(vectors, binary, lambda x: x, np.ones(2), 2)
        self.assertEqual(len(centroids), 2)
        self.assertNotEqual(hard[0, 0], hard[0, 1])
        with self.assertRaises(ValueError):
            m.cluster(vectors, binary, lambda x: x, np.ones(2), 3)

    def test_one_valid_embedding_stays_one_cluster_even_with_a_requested_count(self):
        import numpy as np
        m = self.math()
        hard, _, centroids, _ = m.cluster(np.array([[[1., 0.]]]), np.ones((1, 4, 1)), None, None, 3)
        np.testing.assert_array_equal(hard, [[0]])
        np.testing.assert_array_equal(centroids, [[1., 0.]])

    def test_pickle_plda_is_rejected_before_any_transform_is_used(self):
        import numpy as np
        import tempfile
        from pathlib import Path
        from scriba.app.engines.community import load_plda
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.npz'
            np.savez(path, mean1=np.array([object()], dtype=object), mean2=np.ones(128), lda=np.ones((256, 128)))
            with self.assertRaises(ValueError):
                load_plda(path, path)
