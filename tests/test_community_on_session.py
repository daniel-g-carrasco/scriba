"""Read-only session reassignment, including a separate public-model smoke test."""
import _setup
from dataclasses import asdict, replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(_setup.ROOT / 'tools'))
from scriba.core.segments import Segment, Turn  # noqa: E402
import community_on_session as tool  # noqa: E402


class SessionToolTests(unittest.TestCase):
    def test_a_directory_alias_into_the_recording_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings, sessions, alias = root/'recordings', root/'sessions', root/'alias'
            recordings.mkdir()
            sessions.mkdir()
            recording, source = recordings/'invented.mka', sessions/'invented.json'
            recording.touch()
            source.touch()
            if os.name == 'nt':
                done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(recordings)],
                    capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if done.returncode:
                    self.skipTest('directory junction unavailable in this environment')
            else:
                alias.symlink_to(recordings, target_is_directory=True)
            with self.assertRaises(ValueError):
                tool.destination(source, recording, alias/'new')
            self.assertFalse((recordings/'new').exists())

    def test_output_cannot_be_inside_either_input_folder_or_an_existing_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings, sessions = root/'recordings', root/'sessions'
            recordings.mkdir()
            sessions.mkdir()
            source, session = recordings/'made-up.mka', sessions/'made-up.json'
            source.touch()
            session.touch()
            existing = root/'existing'
            existing.mkdir()
            sentinel = existing/'sentinel'
            sentinel.write_text('keep')
            for target in (recordings, recordings/'child', sessions/'child', existing,
                           root/'alias'/ '..'/'recordings'/'child'):
                with self.subTest(target=target), self.assertRaises((ValueError, FileExistsError)):
                    tool.destination(session, source, target)
            self.assertEqual(sentinel.read_text(), 'keep')
            self.assertEqual(tool.destination(session, source, root/'new'), (root/'new').resolve())

    def test_remote_names_do_not_follow_reused_numbers_and_microphone_details_survive(self):
        lines = [Segment(0, 1, 'Invented remote phrase', 'Speaker 2', avg_logprob=-.2,
                         words=((0, .3), (.3, .6), (.6, 1))),
                 Segment(.3, .8, 'Invented microphone', 'Speaker 1', doubts=('quiet',)),
                 Segment(2, 3, 'Another phrase', 'Speaker 3')]
        actual, me, names = tool.reassign(lines, 'Speaker 1',
            {'Speaker 1':'Ada', 'Speaker 2':'Bea', 'Speaker 3':'Cora'}, [Turn(0, 3, 'Speaker 1')])
        self.assertEqual(me, 'Speaker 2')
        self.assertEqual(names, {'Speaker 2':'Ada'})
        self.assertEqual(actual[0].speaker, actual[2].speaker)
        self.assertNotEqual(actual[0].speaker, me)
        self.assertEqual([replace(x, speaker=None) for x in actual],
                         [replace(x, speaker=None) for x in lines])

    def test_no_remote_turns_clear_old_remote_identity_without_losing_the_microphone(self):
        lines = [Segment(0, 1, 'Remote', 'Speaker 2'), Segment(1, 2, 'Microphone', 'Speaker 1')]
        actual, me, names = tool.reassign(lines, 'Speaker 1', {'Speaker 1':'Ada'}, [])
        self.assertIsNone(actual[0].speaker)
        self.assertEqual(actual[1].speaker, me)
        self.assertEqual(names, {me:'Ada'})

    def test_bad_cli_arguments_never_print_paths_or_names(self):
        done = subprocess.run([sys.executable, '-B', str(_setup.ROOT/'tools/community_on_session.py'),
                               '--private-argument', 'Secret Name'], capture_output=True,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout.strip(), b'2')
        self.assertEqual(done.stderr, b'')

    def test_old_sessions_without_snapshots_fail_safely_in_the_new_output_only(self):
        from scriba.data import session
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root/'input'
            inputs.mkdir()
            recording = inputs/'invented.mka'
            recording.write_bytes(b'invented audio container')
            source = inputs/'invented_trascrizione.json'
            source.write_text(session.dumps(session.Session(source=str(recording))), encoding='utf-8')
            before = {p:p.read_bytes() for p in (source, recording)}
            out = root/'output'
            done = subprocess.run([sys.executable, '-B', str(_setup.ROOT/'tools/community_on_session.py'),
                '--session', str(source), '--recording', str(recording), '--models', str(root/'absent'),
                '--out', str(out)], capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual((done.returncode, done.stdout.strip(), done.stderr), (2, b'2', b''))
            self.assertIn('snapshot', (out/'failure.txt').read_text(encoding='utf-8'))
            self.assertEqual([path.resolve() for path in out.iterdir()],
                             [(out/'failure.txt').resolve()])
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)


@unittest.skipUnless(os.environ.get('SCRIBA_COMMUNITY_AMI') and os.environ.get('SCRIBA_COMMUNITY_EXPORT'),
                     'public AMI and verified export not selected')
class RealSessionToolTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('FFREPORT', None)

    def test_public_audio_in_a_synthetic_two_track_session_reopens_without_transcribing(self):
        import numpy as np
        from scriba.app import jobs
        from scriba.app.engines import audio, ffmpeg
        from scriba.data import markdown, session
        from scriba.core import speakers
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inputs = root/'inputs'
            inputs.mkdir()
            remote, mic, recording = inputs/'remote.wav', inputs/'mic.wav', inputs/'synthetic meeting.mka'
            samples = audio.read(Path(os.environ['SCRIBA_COMMUNITY_AMI'])/'ES2011a.wav')[:60*16000]
            audio.write(remote, samples)
            audio.write(mic, (1000*np.sin(np.arange(len(samples))*.1)).astype(np.int16))
            # Inverted order deliberately: role tags, rather than stream number, decide.
            done = subprocess.run([str(ffmpeg.need('ffmpeg')), '-nostdin', '-v', 'error',
                '-i', str(remote), '-i', str(mic), '-map', '0:a', '-map', '1:a', '-c:a', 'flac',
                '-metadata:s:a:0', 'SCRIBA_ROLE=others', '-metadata:s:a:1', 'SCRIBA_ROLE=me',
                str(recording)], capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, done.stderr)
            np.testing.assert_array_equal(tool.computer_samples(recording), samples)
            lines = [Segment(1, 2, 'Invented remote sentence', 'Speaker 2', avg_logprob=-.1,
                             words=((1, 1.3), (1.3, 1.7), (1.7, 2))),
                     Segment(3, 4, 'Invented local sentence', 'Speaker 1', doubts=('quiet',)),
                     Segment(20, 22, 'A second invented sentence', 'Speaker 3')]
            header = markdown.Header(str(recording), 60, language='en', model='made-up-model')
            original = session.Session(source=str(recording), duration=60,
                options={'speakers':False, 'speaker_count':7}, timings={'speakers':1234., 'transcribe':2.},
                names={'Speaker 1':'Ada', 'Speaker 2':'Bea', 'Speaker 3':'Cora'},
                extra={'transcript':dict(segments=[asdict(x) for x in lines], header=asdict(header),
                    me='Speaker 1', listen=str(inputs/'invented listening mix.m4a'),
                    stem='synthetic_trascrizione', notes=[])})
            source = inputs/'synthetic meeting_trascrizione.json'
            source.write_text(session.dumps(original), encoding='utf-8')
            hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (recording, source)}
            out = root/'new output'
            extra = ['--threads', os.environ.get('SCRIBA_COMMUNITY_THREADS', '4'),
                     '--segmentation-batch', os.environ.get('SCRIBA_COMMUNITY_BATCH', '1')]
            done = subprocess.run([sys.executable, '-B', str(_setup.ROOT/'tools/community_on_session.py'),
                '--session', str(source), '--recording', str(recording),
                '--models', os.environ['SCRIBA_COMMUNITY_EXPORT'], '--out', str(out), *extra], capture_output=True,
                cwd=root, env={**os.environ, 'LANGUAGE':'en',
                               'FFREPORT':'file=unexpected-ffreport.log:level=32'},
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(done.returncode, 0, (done.stdout, done.stderr,
                (out/'failure.txt').read_text() if (out/'failure.txt').exists() else ''))
            self.assertEqual(done.stdout.strip(), b'0')
            self.assertEqual(done.stderr, b'')
            self.assertFalse((root/'unexpected-ffreport.log').exists())
            self.assertEqual({p.name for p in out.iterdir()},
                {'community_trascrizione.md', 'community_trascrizione.json', 'summary.txt'})
            saved = next(out.glob('*_trascrizione.json'))
            reopened, _request = jobs.open_session(saved)
            self.assertEqual(reopened.listen, Path(original.extra['transcript']['listen']))
            new_session = session.load(saved)
            metadata = new_session.options['community_reassignment']
            self.assertGreater(metadata['remote_speakers'], 0)
            self.assertTrue(new_session.options['speakers'])
            self.assertIsNone(new_session.options['speaker_count'])
            self.assertEqual(metadata['previous_voice_options'], {'speakers':False, 'speaker_count':7})
            self.assertEqual(new_session.timings['transcribe'], 2.)
            self.assertEqual(new_session.timings['speakers'], metadata['inference_seconds'])
            self.assertEqual(metadata['previous_timings'], original.timings)
            self.assertEqual([replace(x, speaker=None) for x in reopened.segments],
                             [replace(x, speaker=None) for x in lines])
            self.assertEqual(reopened.names, {reopened.me:'Ada'})
            self.assertEqual(reopened.segments[1].speaker, reopened.me)
            md = next(out.glob('*.md'))
            expected = markdown.render(reopened.header, reopened.segments,
                speakers.display_names(speakers_of(reopened.segments), reopened.names), jobs.paragraphs)
            self.assertEqual(md.read_text(encoding='utf-8'), expected)
            self.assertTrue((out/'summary.txt').is_file())
            self.assertIn('New: Ada | minutes=0.016667 | lines=1',
                          (out/'summary.txt').read_text(encoding='utf-8'))
            for path, identity in hashes.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), identity)
            # Existing output is refused without touching its contents.
            repeated = subprocess.run([sys.executable, '-B', str(_setup.ROOT/'tools/community_on_session.py'),
                '--session', str(source), '--recording', str(recording),
                '--models', os.environ['SCRIBA_COMMUNITY_EXPORT'], '--out', str(out), *extra], capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual(repeated.stderr, b'')


def speakers_of(lines):
    from scriba.core.segments import speakers_of as identities
    return identities(lines)
