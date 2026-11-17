import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import _setup  # noqa: F401
from scriba.app import models
from scriba.app.task import Cancelled
from test_models import BODY, SHA, DownloadTest

class ModelSets(unittest.TestCase):
    def test_production_community_is_pinned_and_ignores_developer_revision(self):
        spec = models.community_set(environ={})
        self.assertIsNotNone(spec)
        self.assertEqual(spec, models.community_set(environ={
            'SCRIBA_DEV_COMMUNITY_REVISION': 'a' * 40}))
        self.assertEqual(len(spec.models), 4)
        for name, model in zip(spec.recipe['files'], spec.models, strict=True):
            self.assertEqual(model.url, 'https://huggingface.co/daniel-g-c/scriba-models/resolve/'
                             'defe86adf9df365e3ebd70943e5608147d40fe8b/' + name)
            self.assertEqual(model.page, 'https://huggingface.co/daniel-g-c/scriba-models')
            self.assertIn('Daniel Grasso', model.publisher)
            self.assertEqual(model.licence, 'CC-BY-4.0')
            self.assertEqual(model.sha256, spec.recipe['files'][name]['sha256'])
            self.assertEqual(model.size, spec.recipe['files'][name]['bytes'])

    def test_community_redirects_stay_on_hugging_face_hosts(self):
        spec = models.community_set(environ={})
        self.assertIsNotNone(spec)
        for model in spec.models:
            for host in ('huggingface.co', 'cdn-lfs.hf.co', 'cas-bridge.xethub.hf.co'):
                self.assertTrue(models.allowed(model, 'https://' + host + '/file'))
            for url in ('http://huggingface.co/file', 'https://hf.co.example.com/file',
                        'https://evilhf.co/file', 'https://xethub.com/file'):
                self.assertFalse(models.allowed(model, url))

    def test_a_same_size_corruption_never_activates(self):
        with tempfile.TemporaryDirectory() as temp:
            model = models.Model('small', 'small.onnx', '', SHA, len(BODY), 'MIT', 'test', '', ())
            spec = models.ModelSet('tiny', (model,), {})
            Path(temp, model.filename).write_bytes(b'x' * len(BODY))
            with self.assertRaises(models.ModelError):
                models.activate_set(spec, temp)
            self.assertFalse(models.set_ready(spec, temp))

    def test_cancellation_never_writes_an_activation(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = models.ModelSet('tiny', (), {})
            stop = threading.Event(); stop.set()
            with self.assertRaises(Cancelled):
                models.download_set(spec, temp, cancelled=stop)
            self.assertFalse(models.set_ready(spec, temp))



class DownloadSets(DownloadTest):
    def spec(self, third="/ok"):
        from scriba.app.community_recipe import RECIPE
        recipe = json.loads(json.dumps(RECIPE))
        recipe['files'] = {name: {'bytes': len(BODY), 'sha256': SHA} for name in recipe['files']}
        recipe.pop('recipe_id')
        recipe['recipe_id'] = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
        items = tuple(replace(self.model(third if i == 2 else '/ok'), filename=name, key=name)
                      for i, name in enumerate(recipe['files']))
        return models.ModelSet('tiny', items, recipe)

    def test_interruption_at_third_file_resumes_without_partial_activation(self):
        spec = self.spec('/cut')
        with self.assertRaises(models.ModelError):
            models.download_set(spec, self.folder)
        self.assertFalse(models.set_ready(spec, self.folder))
        models.download_set(spec, self.folder)
        self.assertTrue(models.set_ready(spec, self.folder))
        self.assertIn(('/cut', f'bytes={len(BODY)//2}-'), self.server.requests)
        (self.folder / spec.models[0].filename).write_bytes(b'x' * len(BODY))
        self.assertFalse(models.set_ready(spec, self.folder))

    def test_two_downloaders_share_one_activation_and_keep_the_legacy_file(self):
        spec = self.spec()
        (self.folder / 'legacy.onnx').write_bytes(b'old')
        errors = []
        def fetch():
            try:
                models.download_set(spec, self.folder)
            except Exception as e:
                errors.append(e)
        workers = [threading.Thread(target=fetch) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(10)
        self.assertFalse(any(w.is_alive() for w in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.server.requests), 4)
        self.assertTrue(models.set_ready(spec, self.folder))
        self.assertEqual((self.folder / 'legacy.onnx').read_bytes(), b'old')

    def test_foreign_redirect_and_no_disk_space_leave_the_old_set_usable(self):
        spec = self.spec()
        models.download_set(spec, self.folder)
        bad = replace(spec, key='next', models=tuple(replace(m, filename='next-' + m.filename,
                      url=m.url.replace('/ok', '/away')) for m in spec.models))
        with self.assertRaises(models.ModelError):
            models.download_set(bad, self.folder)
        self.assertTrue(models.set_ready(spec, self.folder))
        with patch('shutil.disk_usage') as usage:
            usage.return_value.free = 0
            with self.assertRaises(models.ModelError):
                models.download_set(replace(bad, models=tuple(replace(m, url=m.url.replace('/away','/ok'))
                                                           for m in bad.models)), self.folder)
        self.assertTrue(models.set_ready(spec, self.folder))

    def test_application_queue_activates_all_files_before_auto_uses_community(self):
        from scriba.app.backend import Backend
        from scriba.app.config import Config
        from scriba.app.model_downloads import ModelDownloads
        from scriba.app import voices
        spec = self.spec()
        with patch.object(models, 'community_set', return_value=spec), patch.dict('os.environ', {}, clear=True):
            backend = Backend(self.folder, None)
            queue = ModelDownloads(backend, Config(), lambda: None, dispatch=lambda f: f(), required=spec.models)
            self.addCleanup(queue.shutdown)
            self.assertEqual(voices.select('auto', self.folder)[0], 'legacy')
            queue.start_required()
            self.assertTrue(queue.wait(5))
            self.assertEqual(queue.state.phase, 'ready')
            self.assertTrue(models.set_ready(spec, self.folder))
            self.assertEqual(voices.select('auto', self.folder), ('community', ''))
            self.assertEqual(voices.select('legacy', self.folder), ('legacy', ''))
            (self.folder / spec.models[0].filename).write_bytes(b'x' * len(BODY))
            self.assertEqual(voices.select('auto', self.folder)[0], 'legacy')
            with self.assertRaises(models.ModelError): voices.select('community', self.folder)

    def test_existing_verified_files_without_marker_are_activated_without_network(self):
        from scriba.app.backend import Backend
        spec = self.spec()
        with patch.object(models, 'community_set', return_value=spec):
            for model in spec.models: (self.folder / model.filename).write_bytes(BODY)
            backend = Backend(self.folder, None)
            self.assertFalse(backend.has(spec.models[0]))
            backend.download(spec.models[0])
            self.assertTrue(models.set_ready(spec, self.folder))
            self.assertEqual(self.server.requests, [])

    def test_an_incomplete_unmarked_set_can_repair_a_later_corrupt_file(self):
        from scriba.app.backend import Backend
        spec = self.spec()
        with patch.object(models, 'community_set', return_value=spec):
            for model in spec.models: (self.folder / model.filename).write_bytes(BODY)
            (self.folder / spec.models[-1].filename).write_bytes(b'x' * len(BODY))
            backend = Backend(self.folder, None)
            backend.download(spec.models[0])
            self.assertFalse(models.set_ready(spec, self.folder))
            backend.download(spec.models[-1])
            self.assertTrue(models.set_ready(spec, self.folder))

    def test_application_queue_resumes_a_cut_file_without_activating_a_partial_set(self):
        import contextlib
        import io
        from scriba.app.backend import Backend
        from scriba.app.config import Config
        from scriba.app.model_downloads import ModelDownloads
        spec = self.spec('/cut')
        legacy = self.folder / 'legacy.onnx'
        legacy.write_bytes(b'old')
        with patch.object(models, 'community_set', return_value=spec):
            queue = ModelDownloads(Backend(self.folder, None), Config(), lambda: None,
                                   dispatch=lambda f: f(), required=spec.models)
            self.addCleanup(queue.shutdown)
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                queue.start_required()
                self.assertTrue(queue.wait(5))
            self.assertIn('downloading models', errors.getvalue())
            self.assertEqual(queue.state.phase, 'error')
            self.assertFalse(models.set_ready(spec, self.folder))
            queue.resume()
            self.assertTrue(queue.wait(5))
            self.assertEqual(queue.state.phase, 'ready')
            self.assertTrue(models.set_ready(spec, self.folder))
            self.assertIn(('/cut', f'bytes={len(BODY)//2}-'), self.server.requests)
            self.assertEqual(legacy.read_bytes(), b'old')
