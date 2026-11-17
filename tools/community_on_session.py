"""Reassign saved words using community-1. Terminal output is a numeric status only.

0: complete; 1: computation failed; 2: invalid arguments or unsafe destination.
Diagnostics, when the new output folder could be created, stay in failure.txt.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def destination(session, recording, output):
    """Resolve aliases before refusing all writes beside or below either input."""
    session, recording, output = (Path(x).resolve() for x in (session, recording, output))
    if any(output.is_relative_to(path.parent) for path in (session, recording)):
        raise ValueError('Output must be outside both input folders')
    if output.exists():
        raise FileExistsError('Output folder already exists')
    if not output.parent.is_dir():
        raise ValueError('Output parent must already exist')
    return output


def reassign(lines, me, names, turns):
    """Preserve the microphone, clear remote identities, then number new voices."""
    from scriba.core import segments as seg
    remote = [replace(line, speaker=None) for line in lines if not me or line.speaker != me]
    remote = iter(seg.assign_speakers(remote, turns))
    raw = []
    for line in lines:
        if me and line.speaker == me:
            raw.append(replace(line, speaker='microphone'))
        else:
            assigned = next(remote)
            raw.append(replace(assigned, speaker='community:' + assigned.speaker
                               if assigned.speaker is not None else None))
    numbered = seg.number_speakers(raw)
    new_me = next((after.speaker for before, after in zip(raw, numbered, strict=True)
                   if before.speaker == 'microphone'), '')
    new_names = {new_me: names[me]} if new_me and names.get(me) else {}
    return numbered, new_me, new_names


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def computer_samples(recording):
    """Decode exactly the tagged computer track to memory, with no scratch files."""
    import numpy as np
    from scriba.app.engines import ffmpeg
    media = ffmpeg.probe(recording)
    remote = [track for track in media.roles if track.role == 'others']
    if len(remote) != 1:
        raise ValueError('Exactly one tagged computer track is required')
    argv = ffmpeg.decode_arguments(str(ffmpeg.need('ffmpeg')), str(recording), 'pipe:1',
                                   stream=remote[0].index)
    index = argv.index('-progress')
    del argv[index:index + 2]  # Progress text cannot share the WAV byte stream.
    done = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={k: v for k, v in os.environ.items() if k != 'FFREPORT'},
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode:
        raise ValueError('Computer track decoding failed')
    with wave.open(io.BytesIO(done.stdout), 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 16000):
            raise ValueError('Unexpected decoded audio format')
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').copy()


def stats(lines):
    result = {}
    for line in lines:
        row = result.setdefault(line.speaker or '?', {'lines': 0, 'minutes': 0.})
        row['lines'] += 1
        row['minutes'] += (line.end - line.start) / 60
    return result


def process_session(args, out):
    from scriba import i18n
    from scriba.app import jobs
    from scriba.core import segments, speakers
    from scriba.data import markdown, session
    from community_experiments import experiment, parser
    from pyannote_benchmark import model_set
    i18n.setup('scriba', ROOT)
    started = time.perf_counter()
    source, recording = args.session.resolve(), args.recording.resolve()
    identities = {path: digest(path) for path in (source, recording)}
    meeting = session.load(source)
    snapshot = meeting.extra.get('transcript')
    if not isinstance(snapshot, dict) or 'me' not in snapshot:
        raise ValueError('A saved transcript snapshot with microphone identity is required')
    linked = Path(meeting.source)
    if not linked.is_absolute():
        linked = source.parent / linked
    if not linked.samefile(recording):
        raise ValueError('Session source differs from the supplied recording')
    old, _request = jobs.open_session(source)
    if old.me and not any(line.speaker == old.me for line in old.segments):
        raise ValueError('Saved microphone identity is inconsistent')
    decode = time.perf_counter()
    samples = computer_samples(recording)
    decoded = time.perf_counter() - decode
    options = parser().parse_args(['run', '--models', str(args.models.resolve()), '--out', str(out),
        '--threads', str(args.threads), '--segmentation-batch', str(args.segmentation_batch)])
    models = model_set(options.models)
    if args.segmentation_batch > 1 and not models['recipe'].get('batch_experiment', {}).get('segmentation_dynamic_batch'):
        raise ValueError('Segmentation batches require a verified dynamic export')
    computed = time.perf_counter()
    voices, timings = experiment(samples, models, options)
    inference = time.perf_counter() - computed
    lines, me, names = reassign(old.segments, old.me, old.names, voices.exclusive_turns)
    stem = 'community_trascrizione'
    md, saved = out/(stem + '.md'), out/(stem + '.json')
    header = replace(old.header, source=jobs.full_path(recording),
                     speakers_engine='community-1 ONNX', named=bool(names))
    elapsed = time.perf_counter() - started
    details = dict(recipe_id=voices.recipe_id, threads=args.threads,
        segmentation_batch=args.segmentation_batch, embedding_batch=1,
        blas={key: os.environ.get(key) for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
        computed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        total_seconds=elapsed, inference_seconds=inference,
        phases={'decode': decoded, **dict(timings.seconds)},
        remote_speakers=voices.speakers, transcript_speakers=len(segments.speakers_of(lines)),
        previous_voice_options={key: meeting.options.get(key) for key in ('speakers', 'speaker_count')},
        previous_timings=dict(old.timings),
        input_sha256={str(path): identity for path, identity in identities.items()},
        normal_turns=[asdict(turn) for turn in voices.turns],
        exclusive_turns=[asdict(turn) for turn in voices.exclusive_turns])
    result = replace(old, outputs=[session.Output('md', jobs.full_path(md))], session=saved,
        segments=lines, speakers=len(segments.speakers_of(lines)), source=recording, folder=out,
        stem=stem, header=header, me=me, listen=recording, names=names,
        timings={**old.timings, 'speakers': inference})
    saved_snapshot = jobs._snapshot(result)
    # Preserve the app's listening mix as an opaque reference, without opening
    # or resolving another audio file during reassignment.
    saved_snapshot['listen'] = snapshot.get('listen') or jobs.full_path(recording)
    updated = replace(meeting, source=jobs.full_path(recording), outputs=result.outputs, names=names,
        timings=result.timings, options={**meeting.options, 'speakers':True, 'speaker_count':None,
                                        'community_reassignment':details},
        extra={**meeting.extra, 'transcript':saved_snapshot})
    if any(digest(path) != identity for path, identity in identities.items()):
        raise ValueError('An input changed while it was being read')
    shown = speakers.display_names(segments.speakers_of(lines), names)
    with md.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(markdown.render(header, lines, shown, jobs.paragraphs))
    with saved.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(session.dumps(updated))
    summary = ['Minutes are sums of saved transcript intervals; overlaps are counted.',
               f'Old transcript voices: {len(segments.speakers_of(old.segments))}',
               f'New transcript voices: {result.speakers}', f'New remote audio voices: {voices.speakers}']
    for title, values, labels in [('Old', stats(old.segments), old.names), ('New', stats(lines), names)]:
        for label, row in values.items():
            summary.append(f'{title}: {labels.get(label, label)} | minutes={row["minutes"]:.6f} | lines={row["lines"]}')
    summary += [f'Total computation: {elapsed:.6f} seconds', f'Inference: {inference:.6f} seconds',
                f'Recipe: {voices.recipe_id}', json.dumps(details['phases'], sort_keys=True)]
    with (out/'summary.txt').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(summary) + '\n')


def worker(args):
    out = None
    try:
        # ffprobe also honors FFREPORT; this private worker must not create
        # environment-selected logs outside its output folder.
        os.environ.pop('FFREPORT', None)
        candidate = destination(args.session, args.recording, args.out)
        candidate.mkdir(exist_ok=False)
        out = candidate
        process_session(args, out)
        return 0
    except Exception as error:
        if out is not None:
            with (out/'failure.txt').open('x', encoding='utf-8') as stream:
                stream.write(f'{type(error).__name__}: {error}\n')
        return 2 if isinstance(error, (ValueError, FileNotFoundError, FileExistsError)) else 1


class NumericParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid arguments')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parser = NumericParser(add_help=False)
        for name in ('session', 'recording', 'models', 'out'):
            parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--threads', type=int, default=4)
        parser.add_argument('--segmentation-batch', type=int, default=1)
        parser.add_argument('--worker', action='store_true')
        args = parser.parse_args(argv)
        if args.threads < 1 or args.segmentation_batch < 1:
            return 2
        if args.worker:
            return worker(args) if os.environ.get('SCRIBA_COMMUNITY_WORKER') == '1' else 2
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'SCRIBA_COMMUNITY_WORKER':'1',
               'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'}
        with subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--worker', *argv],
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as child:
            try:
                code = child.wait()
            except BaseException:
                child.terminate()
                child.wait()
                return 1
        return code if code in (0, 1, 2) else 1
    except ValueError:
        return 2
    except Exception:
        return 1


if __name__ == '__main__':
    status = main()
    print(status)
    raise SystemExit(status)
