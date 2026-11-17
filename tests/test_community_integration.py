import unittest
import tempfile
from unittest.mock import patch
from pathlib import Path
import _setup  # noqa: F401
from scriba.app import config, jobs, models
from scriba.app.task import Cancelled, Failure
from scriba.core import naming
from scriba.core.segments import Segment

class Integration(unittest.TestCase):
    def test_old_config_migrates_conservatively(self):
        value = config.Config.from_json(config.migrate({'version': 1}))
        self.assertEqual(value.diarization_backend, 'auto')
        self.assertEqual(config.CONFIG_VERSION, 2)

    def test_explicit_community_does_not_silently_run_legacy(self):
        from scriba.app import voices
        with patch.object(models, 'community_set', return_value=None):
            with self.assertRaises(models.ModelError):
                voices.select('community', Path('missing'))
            self.assertEqual(voices.select('auto', Path('missing'))[0], 'legacy')

    def test_community_estimates_never_inherit_legacy_speed(self):
        request = jobs.Request(Path('fake'), speakers=True, diarization_backend='community')
        self.assertTrue(jobs.first_estimate(request, 'cpu', {'turbo|cpu': 10, 'speakers|cpu': 100}))

    def test_different_voices_behind_one_tile_have_numbered_proposals(self):
        lines = [Segment(0,10,'hello','A'),Segment(12,22,'yes','B')]
        proposed, notes = naming.propose_with_voices(lines, [('Ada',0,25)], {'A':[1.,0.], 'B':[0.,1.]})
        self.assertEqual([proposed[k].name for k in ('A','B')], ['Ada, voice 1','Ada, voice 2'])
        self.assertIn('multiple_voices', notes)

    def test_a_split_voice_proposes_one_shared_name(self):
        lines = [Segment(0,10,'hello','A'),Segment(12,22,'yes','B')]
        found, notes = naming.propose_with_voices(lines,[('Ada',0,25)],{'A':[1.,0.], 'B':[1.,.001]})
        self.assertEqual([found[k].name for k in ('A','B')],['Ada','Ada'])
        self.assertIn('merge_voices', notes)

    def test_one_voice_under_two_tiles_stays_unnamed(self):
        found, notes = naming.propose_with_voices([Segment(0,20,'hello','A')],
            [('Ada',0,12),('Bob',12,20)], {'A':[1.,0.]})
        self.assertEqual(found,{})
        self.assertIn('multiple_tiles',notes)


class VoiceSafety(unittest.TestCase):
    def test_cancelled_selection_never_substitutes_legacy(self):
        from scriba.app import voices
        with patch.object(models,'community_set',return_value=object()), patch.object(models,'community_files',side_effect=Cancelled):
            with self.assertRaises(Cancelled):
                voices.select('auto',Path('fake'))

    def test_corrupt_auto_models_have_an_explicit_legacy_note(self):
        from scriba.app import voices
        with patch.object(models,'community_set',return_value=object()), patch.object(models,'community_files',side_effect=models.ModelError('broken')):
            used,note = voices.select('auto',Path('fake'))
            self.assertEqual(used,'legacy')
            self.assertIn('previous',note)
            with self.assertRaises(models.ModelError): voices.select('community',Path('fake'))

    def test_gpu_failure_retries_the_same_embedding_batch_on_cpu(self):
        from scriba.app.engines import webgpu
        calls=[]
        class Inner:
            def run(self,*args):
                calls.append(args)
                if len(calls)==1: raise Failure('GPU lost')
                return [42]
            def close(self): pass
        class Runtime:
            api=type('API',(),{'version':'test'})()
            def session(self,path,threads,gpu): return Inner()
        metadata={}
        with patch.object(webgpu,'Runtime',Runtime):
            with webgpu.session_factory(metadata)(Path('embedding.onnx'),8) as session:
                self.assertEqual(session.run(['embeddings'],{'feats':42}),[42])
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(metadata['accelerator'],'cpu')
        self.assertIn('fallback',metadata)

    def test_unavailable_vulkan_uses_the_cpu_runtime(self):
        from scriba.app.engines import webgpu
        metadata = {}
        path = Path('embedding.onnx')
        with patch.object(webgpu, 'Runtime', side_effect=webgpu.Unavailable('No Vulkan device')), \
                patch.object(webgpu.onnx, 'Session') as cpu_session:
            self.assertIs(webgpu.session_factory(metadata)(path, 8), cpu_session.return_value)
        cpu_session.assert_called_once_with(path, threads=8)
        self.assertEqual(metadata, {'accelerator': 'cpu', 'fallback': 'No Vulkan device'})

    def test_numeric_error_is_not_a_gpu_availability_fallback(self):
        from scriba.app.engines import webgpu
        class Inner:
            def run(self,*args): raise ValueError('nonfinite')
            def close(self): pass
        class Runtime:
            api=type('API',(),{'version':'test'})()
            def session(self,*args): return Inner()
        metadata={}
        with patch.object(webgpu,'Runtime',Runtime):
            with webgpu.session_factory(metadata)(Path('embedding.onnx'),8) as session:
                with self.assertRaises(ValueError): session.run([], {})
        self.assertNotIn('fallback',metadata)

    def test_saved_centroids_and_both_turns_reopen_without_models(self):
        from scriba.data import markdown
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            lines=[Segment(0,10,'hello','Speaker 1'),Segment(12,22,'yes','Speaker 2')]
            result=jobs.Result([],root/'fake.json',lines,2,25,'cpu',{},source=root/'absent.wav',
                folder=root,stem='fake',header=markdown.Header('fake',25),
                named_spans=(('Ada',0,25),),voice_centroids={'Speaker 1':[1.,0.],'Speaker 2':[0.,1.]},
                diarization={'used':'community','normal_turns':[{'start':0,'end':10,'label':'A'}]})
            path=jobs._save_session(result,jobs.Request(root/'absent.wav',diarization_backend='community'))
            before=path.read_bytes()
            with patch.object(models,'community_files',side_effect=AssertionError('offline')):
                reopened,request=jobs.open_session(path)
            self.assertEqual(reopened.diarization,result.diarization)
            self.assertEqual(reopened.voice_centroids,result.voice_centroids)
            self.assertEqual(request.diarization_backend,'community')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual([p.name for p in reopened.proposals.values()],['Ada, voice 1','Ada, voice 2'])


    def test_remote_count_excludes_microphone_and_text_uses_exclusive(self):
        from scriba.app import voices
        from scriba.core.segments import Turn
        import numpy as np
        mine=jobs._Part(jobs.Track(0,'mic','me'))
        remote=jobs._Part(jobs.Track(1,'computer','others'))
        mine.samples=remote.samples=np.zeros(160000,np.int16)
        mine.lines=[Segment(0,2,'microphone words')]
        remote.lines=[Segment(3,5,'remote words')]
        data=dict(turns=[Turn(0,10,'A'),Turn(0,10,'B')],exclusive=[Turn(0,10,'A')],
            centroids={'A':[1.,0.]},provenance={'used':'community','accelerator':'webgpu-vulkan'})
        details={}
        with patch.object(voices,'select',return_value=('community','')),patch.object(voices,'run',return_value=data) as engine:
            lines,me=jobs._voices([mine,remote],jobs.Request(Path('fake'),speakers=True,speaker_count=3),
                                  Path('fake'),jobs.Progress(),None,details)
        self.assertEqual(engine.call_args.args[2],2)
        self.assertEqual(me,'Speaker 1')
        self.assertEqual([s.text for s in lines],['microphone words','remote words'])
        self.assertEqual(len(details['normal_turns']),2)
        with patch.object(voices,'run',side_effect=AssertionError('single remote should bypass')):
            lines,me=jobs._voices([mine,remote],jobs.Request(Path('fake'),speakers=True,speaker_count=2),
                                  Path('fake'),jobs.Progress(),None,{})
        self.assertEqual(len(set(s.speaker for s in lines)),2)
