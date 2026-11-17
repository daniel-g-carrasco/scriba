"""Development-only community-1 reference, export and public AMI evaluation.

Never imported by Scriba. All outputs are new files/directories; input models
and recordings are read-only. Heavy dependencies are loaded on demand.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

REVISION = '3533c8cf8e369892e6b79ff1bf80f7b0286a54ee'
DEV = ('ES2011a', 'IS1008a', 'TS3004a')
TEST = ('ES2004a', 'IS1009a', 'TS3003a')
AMI_LICENSE = 'https://groups.inf.ed.ac.uk/ami/corpus/license.shtml'
PLDA_LICENSE = 'https://huggingface.co/BUT-FIT/diarizen-wavlm-large-s80-md/raw/6285693ddd5b38e8229acb93f864f3d04a82bee1/plda/LICENSE'
WEIGHTS = ('segmentation/pytorch_model.bin', 'embedding/pytorch_model.bin',
           'plda/plda.npz', 'plda/xvec_transform.npz')


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write('\n')


def write_rttm(path, meeting, turns):
    """Use the same six-decimal serialization for both inference runtimes."""
    with Path(path).open('x', encoding='utf-8') as stream:
        for start, end, label in turns:
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                raise ValueError('Invalid diarization turn')
            stream.write(f'SPEAKER {meeting} 1 {start:.6f} {end-start:.6f} <NA> <NA> {label} <NA> <NA>\n')


def file_record(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def verify_record(path, entry):
    if file_record(path) != {key: entry[key] for key in ('bytes', 'sha256')}:
        raise ValueError(f'File integrity check failed: {path}')


def download(url, path):
    request = urllib.request.Request(url, headers={'User-Agent': 'Scriba-public-evaluation/1'})
    with urllib.request.urlopen(request, timeout=60) as response, Path(path).open('xb') as stream:
        while block := response.read(1024 * 1024):
            stream.write(block)
    return {'url': url, **file_record(path)}


def word_intervals(xml):
    """BUT words-only convention, retaining every positive-duration pause."""
    words = []
    for element in ET.fromstring(xml).iter():
        if element.tag.rsplit('}', 1)[-1] != 'w':
            continue
        if 'starttime' not in element.attrib or 'endtime' not in element.attrib:
            continue
        start, end = float(element.attrib['starttime']), float(element.attrib['endtime'])
        if not math.isfinite(start + end) or start < 0 or end < start:
            raise ValueError('Invalid word interval')
        if end > start:
            words.append((start, end))
    merged = []
    for start, end in sorted(words):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def select_intervals(intervals, duration):
    """Freeze short diagnostics from annotations before running any model."""
    events = sorted({0., duration, *(t for start, end, _ in intervals for t in (start, end))})
    selected = {}
    previous = set()
    for start, end in zip(events, events[1:], strict=False):
        active = {label for left, right, label in intervals if left <= start < right}
        kind = 'silence' if not active else 'overlap' if len(active) > 1 else None
        if kind and kind not in selected and end - start >= .1:
            selected[kind] = [max(0, start - 1), min(duration, end + 1)]
        if previous and active and previous != active and 'change' not in selected:
            selected['change'] = [max(0, start - 2), min(duration, end + 2)]
        previous = active
    return selected


def load_pipeline(snapshot):
    os.environ['PYANNOTE_METRICS_ENABLED'] = '0'
    os.environ['HF_HUB_OFFLINE'] = '1'
    import torch
    from pyannote.audio import Pipeline
    from pyannote.audio.core.task import Specifications, Problem, Resolution
    torch.set_num_threads(4)
    torch.serialization.add_safe_globals([Specifications, Problem, Resolution])
    return Pipeline.from_pretrained(Path(snapshot)).to(torch.device('cpu'))


def versions():
    names = ('pyannote.audio', 'torch', 'torchaudio', 'onnx', 'onnxruntime',
             'numpy', 'scipy', 'scikit-learn', 'pyannote.metrics', 'soundfile')
    return {'python': sys.version, 'platform': platform.platform(),
            **{name: importlib.metadata.version(name) for name in names}}


def inspect(args):
    if args.out.exists():
        raise FileExistsError(args.out)
    import dataclasses
    import torch
    root = args.snapshot
    for name in (*WEIGHTS, 'config.yaml', 'README.md', 'embedding/README.md', 'plda/README.md'):
        if not (root / name).is_file():
            raise FileNotFoundError(root / name)
    files = {p.relative_to(root).as_posix(): file_record(p)
             for p in sorted(root.rglob('*')) if p.is_file() and '.cache' not in p.parts}
    metadata = root / '.cache/huggingface/download'
    revisions = set()
    for name in (*WEIGHTS, 'config.yaml'):
        record = metadata / (name + '.metadata')
        if record.exists():
            lines = record.read_text().splitlines()
            revisions.add(lines[0])
            source = root / name
            expected = lines[1]
            actual = (files[name]['sha256'] if len(expected) == 64 else
                      hashlib.sha1(b'blob ' + str(source.stat().st_size).encode() + b'\x00' + source.read_bytes()).hexdigest())
            if actual != expected:
                raise ValueError(f'Snapshot metadata hash differs for {name}')
    if revisions != {REVISION}:
        raise ValueError(f'Unverified/mixed snapshot revision: {revisions}')
    pipeline = load_pipeline(root)
    segmenter, embedder = pipeline._segmentation.model, pipeline._embedding.model_
    with torch.inference_mode():
        waveform = torch.zeros(1, 1, 160000)
        scores = segmenter(waveform)
        feats = embedder.compute_fbank(waveform)
        embedding = embedder(waveform)
    spec = dataclasses.asdict(segmenter.specifications)
    spec['problem'], spec['resolution'] = str(spec['problem']), str(spec['resolution'])
    rf = segmenter.receptive_field
    value = {'revision': REVISION, 'files': files, 'versions': versions(),
             'config': (root / 'config.yaml').read_text(),
             'parameters': pipeline.parameters(instantiated=True),
             'segmentation': {'class': str(type(segmenter)), 'specifications': spec,
                              'hparams': dict(segmenter.hparams), 'shape': list(scores.shape),
                              'dtype': str(scores.dtype),
                              'receptive_field': {'start': rf.start, 'step': rf.step, 'duration': rf.duration}},
             'embedding': {'class': str(type(embedder)), 'hparams': dict(embedder.hparams),
                           'shape': list(embedding.shape), 'features_shape': list(feats.shape),
                           'min_num_samples': pipeline._embedding.min_num_samples},
             'resolved': {'step': pipeline._segmentation.step,
                          'segmentation_batch_size': pipeline.segmentation_batch_size,
                          'embedding_batch_size': pipeline.embedding_batch_size,
                          'embedding_exclude_overlap': pipeline.embedding_exclude_overlap},
             'licenses': {'models': 'CC-BY-4.0, root README',
                          'embedding': (root / 'embedding/README.md').read_text(encoding='utf-8'),
                          'plda': (root / 'plda/README.md').read_text(encoding='utf-8'),
                          'plda_specific_source': PLDA_LICENSE,
                          'plda_specific_verified': False}}
    # A downloaded specific license is supplied explicitly, never inferred from
    # the unrelated noncommercial license of the DiariZen neural checkpoint.
    if args.plda_license:
        text = args.plda_license.read_text(encoding='utf-8')
        if not all(item in text for item in ('xvec_transform.npz', 'plda.npz', '4.0')):
            raise ValueError('Specific PLDA license does not identify both files')
        value['licenses']['plda_specific_verified'] = True
        value['licenses']['plda_license'] = file_record(args.plda_license)
    write_json(args.out, value)
    print(json.dumps({'out': str(args.out), 'revision': REVISION, 'shape': list(scores.shape)}))


def prepare_ami(args):
    import numpy as np
    import scipy
    from scipy.signal import resample_poly
    import soundfile as sf
    root = args.out
    root.mkdir(parents=True, exist_ok=False)
    sources = {}
    sources['license'] = download(AMI_LICENSE, root / 'LICENSE.html')
    sources['annotations'] = download('https://groups.inf.ed.ac.uk/ami/AMICorpusAnnotations/ami_public_manual_1.6.2.zip', root / 'annotations.zip')
    # Pin the recipe repository, and retain the words-only references to check
    # our conversion rather than silently accepting a different convention.
    api = 'https://api.github.com/repos/BUTSpeechFIT/AMI-diarization-setup/commits/main'
    sources['but_revision'] = download(api, root / 'but-revision.json')
    but_revision = json.loads((root / 'but-revision.json').read_text())['sha']
    sources['but_recipe'] = download(f'https://raw.githubusercontent.com/BUTSpeechFIT/AMI-diarization-setup/{but_revision}/README.md', root / 'BUT-README.md')
    meetings = []
    with zipfile.ZipFile(root / 'annotations.zip') as annotations:
        for index, meeting in enumerate((*DEV, *TEST)):
            print(f'Preparing {meeting}', flush=True)
            original = root / f'{meeting}.Array1-01.wav'
            url = f'https://groups.inf.ed.ac.uk/ami/AMICorpusMirror/amicorpus/{meeting}/audio/{original.name}'
            source = download(url, original)
            samples, rate = sf.read(original, dtype='float64', always_2d=True)
            if samples.shape[1] != 1:
                raise ValueError('Expected single-channel Array1-01')
            divisor = math.gcd(rate, 16000)
            converted = resample_poly(samples[:, 0], 16000 // divisor, rate // divisor) if rate != 16000 else samples[:, 0]
            wav = root / f'{meeting}.wav'
            sf.write(wav, converted, 16000, subtype='PCM_16')
            duration = len(converted) / 16000
            rttm, intervals = [], []
            members = sorted(n for n in annotations.namelist() if '/words/' in '/' + n and Path(n).name.startswith(meeting + '.') and n.endswith('.words.xml'))
            if len(members) != 4:
                raise ValueError(f'{meeting}: expected four annotated speakers, got {members}')
            for member in members:
                speaker = Path(member).name.split('.')[1]
                for start, end in word_intervals(annotations.read(member)):
                    if end > duration + .01:
                        raise ValueError(f'Annotation outside audio: {meeting} {end} > {duration}')
                    rttm.append((start, f'SPEAKER {meeting} 1 {start:.6f} {end-start:.6f} <NA> <NA> {speaker} <NA> <NA>\n'))
                    intervals.append((start, end, speaker))
            reference = root / f'{meeting}.rttm'
            reference.write_text(''.join(line for _, line in sorted(rttm)), encoding='utf-8')
            uem = root / f'{meeting}.uem'
            uem.write_text(f'{meeting} 1 0.000000 {duration:.6f}\n', encoding='utf-8')
            meetings.append({'id': meeting, 'split': 'dev' if index < 3 else 'test',
                             'wav': wav.name, 'audio': file_record(wav), 'source': source,
                             'rttm': reference.name, 'reference': file_record(reference),
                             'uem': uem.name, 'uem_hash': file_record(uem), 'duration': duration,
                             'speakers': 4, 'channel': 'Array1-01',
                             'source_rate': rate, 'rate': 16000, 'resampling': 'scipy.signal.resample_poly',
                             'brief': [0, min(600, duration)],
                             'diagnostic_intervals': select_intervals(intervals, duration),
                             'annotation_files': members})
    manifest = {'version': 1, 'sources': sources, 'license': 'CC-BY-4.0',
                'attribution': 'AMI Meeting Corpus, Carletta et al.; BUT Speech@FIT, Landini et al.',
                'but_revision': but_revision, 'convention': 'words-only, merge touching same speaker, retain pauses, full UEM',
                'versions': {'numpy': np.__version__, 'scipy': scipy.__version__, 'soundfile': sf.__version__},
                'meetings': meetings}
    if args.with_stress_2h:
        stress = root / 'stress-2h.wav'
        joins, total = [], 0
        with sf.SoundFile(stress, 'x', samplerate=16000, channels=1, subtype='PCM_16') as stream:
            while total < 7200 * 16000:
                for meeting in meetings[:3]:
                    samples, _ = sf.read(root / meeting['wav'], dtype='int16')
                    length = min(len(samples), 7200 * 16000 - total)
                    joins.append({'meeting': meeting['id'], 'start': total / 16000, 'source_start': 0, 'duration': length / 16000})
                    stream.write(samples[:length])
                    total += length
                    if total == 7200 * 16000:
                        break
        manifest['stress'] = {'wav': stress.name, **file_record(stress), 'duration': 7200, 'joins': joins,
                              'purpose': 'performance only, never a quality sample'}
    write_json(root / 'manifest.json', manifest)


def insert_silence_intervals(intervals, at, duration):
    result = []
    for start, end in intervals:
        if start < at:
            result.append((start, min(end, at)))
        if end > at:
            result.append((max(start, at) + duration, end + duration))
    return result


def prepare_silence(args):
    """A fixed dev-only diagnostic: 180 seconds of real audio plus 60 zeros."""
    import copy
    import numpy as np
    import soundfile as sf
    if args.out.exists():
        raise FileExistsError(args.out)
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    manifest = copy.deepcopy(manifest)
    for item in manifest['meetings']:
        for key in ('wav', 'rttm', 'uem'):
            item[key] = str((args.manifest.parent / item[key]).resolve())
    first = manifest['meetings'][0]
    if first['id'] != DEV[0]:
        raise ValueError('Expected first frozen development meeting')
    verify_record(Path(first['wav']), first['audio'])
    samples, rate = sf.read(first['wav'], dtype='int16', frames=180 * 16000)
    if rate != 16000 or samples.ndim != 1:
        raise ValueError('Expected 16 kHz mono public audio')
    args.out.mkdir(parents=True, exist_ok=False)
    wav, rttm, uem = (args.out / name for name in ('silence.wav', 'silence.rttm', 'silence.uem'))
    sf.write(wav, np.concatenate([samples[:60 * rate], np.zeros(60 * rate, np.int16), samples[60 * rate:]]),
             rate, subtype='PCM_16')
    lines = []
    for line in Path(first['rttm']).read_text().splitlines():
        fields = line.split()
        start, end = float(fields[3]), float(fields[3]) + float(fields[4])
        if start >= 180:
            continue
        for left, right in insert_silence_intervals([(start, min(end, 180))], 60, 60):
            changed = fields.copy()
            changed[3:5] = [f'{left:.6f}', f'{right-left:.6f}']
            lines.append(' '.join(changed) + '\n')
    rttm.write_text(''.join(lines), encoding='utf-8')
    uem.write_text(f'{first["id"]} 1 0 240\n', encoding='utf-8')
    first.update(wav=str(wav.resolve()), audio=file_record(wav), rttm=str(rttm.resolve()),
                 reference=file_record(rttm), uem=str(uem.resolve()), uem_hash=file_record(uem), duration=240.)
    manifest['silence_diagnostic'] = {'source_manifest': file_record(args.manifest),
                                      'source_seconds': 180, 'inserted_interval': [60, 120],
                                      'purpose': 'development diagnostic, not whole-meeting quality'}
    write_json(args.out / 'manifest.json', manifest)


def segmentation_errors(expected, actual):
    import numpy as np
    if expected.shape != actual.shape or not np.isfinite(expected).all() or not np.isfinite(actual).all():
        return {'passed': False, 'reason': 'shape or nonfinite'}
    error = float(np.max(np.abs(expected - actual)))
    probability_error = float(np.max(np.abs(np.exp(expected) - np.exp(actual))))
    argmax = float(np.mean(expected.argmax(-1) != actual.argmax(-1)))
    return {'max_abs_log': error, 'max_abs_probability': probability_error,
            'argmax_difference': argmax, 'passed': probability_error <= 1e-3 and argmax <= .001}


def embedding_errors(expected, actual):
    import numpy as np
    if expected.shape != actual.shape or not np.array_equal(np.isnan(expected), np.isnan(actual)):
        return {'passed': False, 'reason': 'shape or NaN mismatch'}
    valid = np.isfinite(expected).all(-1)
    x, y = expected[valid].astype(np.float64), actual[valid].astype(np.float64)
    if not len(x):
        return {'passed': True, 'valid': 0}
    nx, ny = np.linalg.norm(x, axis=-1), np.linalg.norm(y, axis=-1)
    if np.any(nx == 0) or np.any(ny == 0) or not np.isfinite(y).all():
        return {'passed': False, 'reason': 'invalid norm'}
    cosine = float(np.max(1 - (x * y).sum(-1) / (nx * ny)))
    relative = float(np.max(np.abs(nx - ny) / nx))
    return {'cosine': cosine, 'relative_norm': relative, 'valid': len(x),
            'passed': cosine <= 1e-4 and relative <= 1e-3}


def frozen_sinc_segmentation(model):
    """Materialize the checkpoint's learned Sinc filters using Torch once."""
    import copy
    import torch
    from asteroid_filterbanks import ParamSincFB
    converted = copy.deepcopy(model)
    source = converted.sincnet.conv1d[0]
    if type(source.filterbank) is not ParamSincFB or source.is_pinv or not source.as_conv1d:
        raise ValueError('Expected the inspected mono ParamSincFB encoder')
    with torch.inference_mode():
        filters = source.get_filters().detach().clone().contiguous()

    class FrozenEncoder(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer('filters', filters)
            self.stride, self.padding = source.stride, source.padding

        def forward(self, waveform):
            return torch.nn.functional.conv1d(waveform, self.filters, stride=self.stride, padding=self.padding)

    converted.sincnet.conv1d[0] = FrozenEncoder()
    record = {'shape': list(filters.shape), 'stride': source.stride, 'padding': source.padding,
              'sha256': hashlib.sha256(filters.numpy().tobytes()).hexdigest(), 'dtype': str(filters.dtype),
              'source': 'checkpoint Encoder.get_filters(), evaluated by Torch on CPU'}
    return converted.eval(), record


def centered_segmentation(model):
    """Experimental centered variance, only for diagnostics, not accepted export."""
    import copy
    import torch

    class CenteredNorm(torch.nn.Module):
        def __init__(self, source):
            super().__init__()
            if source.track_running_stats:
                raise ValueError('Expected instance normalization using input statistics')
            self.eps = source.eps
            self.weight, self.bias = source.weight, source.bias

        def forward(self, value):
            mean = value.mean(dim=-1, keepdim=True)
            centered = value - mean
            variance = (centered * centered).mean(dim=-1, keepdim=True)
            scale = torch.rsqrt(variance + self.eps)
            if self.weight is not None:
                scale = scale * self.weight[None, :, None]
            shift = -mean * scale
            if self.bias is not None:
                shift = self.bias[None, :, None] - mean * scale
            return value * scale + shift

    converted = copy.deepcopy(model)
    layers = []
    for name, module in list(converted.named_modules()):
        if isinstance(module, torch.nn.InstanceNorm1d):
            parent, _, leaf = name.rpartition('.')
            setattr(converted.get_submodule(parent) if parent else converted, leaf, CenteredNorm(module))
            layers.append(name)
    return converted.eval(), layers


def export(args):
    import numpy as np
    import onnx
    import onnxruntime as ort
    import soundfile as sf
    import torch
    if args.out.exists():
        raise FileExistsError(args.out)
    corpus = json.loads(args.manifest.read_text())
    pipeline = load_pipeline(args.snapshot)
    segmenter, embedder = pipeline._segmentation.model.eval(), pipeline._embedding.model_.eval()
    if segmenter.specifications.duration != 10 or len(segmenter.specifications.classes) != 3:
        raise ValueError('Checkpoint differs from the inspected export contract')

    class WeightedEmbedding(torch.nn.Module):
        def __init__(self, resnet):
            super().__init__()
            self.resnet = resnet

        def forward(self, feats, weights):
            frames = self.resnet.forward_frames(feats)
            return self.resnet.forward_embedding(frames, weights=weights)[1]

    wrapper = WeightedEmbedding(embedder.resnet).eval()
    rng = np.random.default_rng(42)
    waveform = torch.from_numpy(rng.normal(0, .01, (1, 1, 160000)).astype(np.float32))
    with torch.inference_mode():
        scores = segmenter(waveform)
        feats = embedder.compute_fbank(waveform)
    frames = scores.shape[1]
    weights = torch.zeros(1, 3, frames)
    weights[:, 1] = 1
    weights[:, 2, :frames // 2] = 1
    args.out.mkdir(parents=True, exist_ok=False)
    graph_inventory = {}
    for name, model, inputs, names, output in (
        ('segmentation.onnx', segmenter, (waveform,), ['waveform'], 'scores'),
        ('embedding.onnx', wrapper, (feats, weights), ['feats', 'weights'], 'embeddings'),
    ):
        path = args.out / name
        with torch.inference_mode():
            torch.onnx.export(model, inputs, str(path), opset_version=args.opset,
                              dynamo=False, input_names=names, output_names=[output])
        graph = onnx.load(path)
        onnx.checker.check_model(graph, full_check=True)
        if any(node.domain not in ('', 'ai.onnx') for node in graph.graph.node):
            raise ValueError('Custom/ATen operator in export')
        if any(item.domain not in ('', 'ai.onnx') or item.version != args.opset for item in graph.opset_import):
            raise ValueError('Unexpected opset')
        if any(item.data_type != onnx.TensorProto.FLOAT for item in graph.graph.initializer if item.data_type in (10, 11)):
            raise ValueError('Unexpected floating point initializer')
        graph_inventory[name] = {'ir_version': graph.ir_version,
                                  'opsets': {item.domain: item.version for item in graph.opset_import},
                                  'operators': sorted({node.op_type for node in graph.graph.node}),
                                  'inputs': {item.name: {'dtype': item.type.tensor_type.elem_type,
                                                        'shape': [d.dim_value for d in item.type.tensor_type.shape.dim]}
                                             for item in graph.graph.input},
                                  'outputs': {item.name: {'dtype': item.type.tensor_type.elem_type,
                                                         'shape': [d.dim_value for d in item.type.tensor_type.shape.dim]}
                                              for item in graph.graph.output}}
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
    segmentation = ort.InferenceSession(str(args.out / 'segmentation.onnx'), options, providers=['CPUExecutionProvider'])
    embedding = ort.InferenceSession(str(args.out / 'embedding.onnx'), options, providers=['CPUExecutionProvider'])
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from scriba.app.engines.diarize import fbank
    cases = [('noise', waveform.numpy()[0, 0]), ('silence', np.zeros(160000, np.float32)),
             ('short', np.pad(waveform.numpy()[0, 0, :8000], (0, 152000))),
             ('tail', np.pad(waveform.numpy()[0, 0, :159999], (0, 1)))]
    first = corpus['meetings'][0]
    verify_record(args.manifest.parent / first['wav'], first['audio'])
    dev, rate = sf.read(args.manifest.parent / first['wav'], dtype='float32')
    if rate != 16000:
        raise ValueError('Corpus sample rate differs')
    for label, bounds in {'dev-first': [0, 10], **{'dev-' + k: v for k, v in first['diagnostic_intervals'].items()}}.items():
        at = int(bounds[0] * 16000)
        piece = dev[at:at + 160000]
        cases.append((label, np.pad(piece, (0, 160000 - len(piece)))))
    checks = []
    for label, samples in cases:
        w = torch.from_numpy(samples[None, None])
        with torch.inference_mode():
            expected_scores = segmenter(w).numpy()
            expected_feats = embedder.compute_fbank(w)
            expected_embed = wrapper(expected_feats, weights).numpy()
            separate = np.stack([pipeline._embedding(w, masks=weights[:, k]) for k in range(3)], axis=1)
        computed = fbank(samples * 32768)
        computed -= computed.mean(0, dtype=np.float32)
        actual_scores = segmentation.run(['scores'], {'waveform': w.numpy()})[0]
        actual_embed = embedding.run(['embeddings'], {'feats': expected_feats.numpy(), 'weights': weights.numpy()})[0]
        numpy_embed = embedding.run(['embeddings'], {'feats': computed[None], 'weights': weights.numpy()})[0]
        fbank_max = float(np.max(np.abs(computed - expected_feats.numpy()[0])))
        checks.append({'case': label, 'segmentation': segmentation_errors(expected_scores, actual_scores),
                       'embedding': embedding_errors(expected_embed, actual_embed),
                       'fbank_to_embedding': embedding_errors(expected_embed, numpy_embed),
                       'shared_vs_three': embedding_errors(separate, expected_embed), 'fbank_max_abs': fbank_max})
        np.savez(args.out / f'fixture-{label}.npz', waveform=w.numpy(), scores=expected_scores,
                 feats=expected_feats.numpy(), numpy_feats=computed[None], weights=weights.numpy(),
                 embeddings=expected_embed, three_embeddings=separate)
    for source in WEIGHTS[2:]:
        shutil.copyfile(args.snapshot / source, args.out / Path(source).name)
    passed = all(c['segmentation']['passed'] and c['embedding']['passed'] and c['shared_vs_three']['passed']
                 and c['fbank_to_embedding']['passed'] for c in checks)
    recipe = {'segmentation_gate': 'probability-1e-3-argmax-0.001',
              'export_verified': passed, 'revision': REVISION, 'opset': args.opset, 'exporter': 'torch.onnx.export dynamo=False',
              'precision': 'float32', 'segmentation': {'input': 'waveform', 'output': 'scores', 'shape': [1, 1, 160000],
                                                     'output_shape': [1, frames, 7]},
              'embedding': {'inputs': ['feats', 'weights'], 'output': 'embeddings', 'shapes': [[1, 998, 80], [1, 3, frames]],
                            'output_shape': [1, 3, 256]},
              'frame': {'start': segmenter.receptive_field.start, 'step': segmenter.receptive_field.step,
                        'duration': segmenter.receptive_field.duration},
              'step': pipeline._segmentation.step, 'duration': 10, 'min_num_samples': pipeline._embedding.min_num_samples,
              'exclude_overlap': pipeline.embedding_exclude_overlap,
              'parameters': pipeline.parameters(instantiated=True), 'versions': versions(),
              'licenses': {'models': 'CC-BY-4.0', 'plda': PLDA_LICENSE},
              'files': {name: file_record(args.out / name) for name in ('segmentation.onnx', 'embedding.onnx', 'plda.npz', 'xvec_transform.npz')},
              'sources': {name: file_record(args.snapshot / name) for name in WEIGHTS}, 'checks': checks,
              'graphs': graph_inventory}
    recipe['recipe_id'] = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    write_json(args.out / 'manifest.json', recipe)
    print(json.dumps({'passed': passed, 'checks': checks}), flush=True)
    if not passed:
        raise NumericalFailure('Export comparison failed, see manifest')


class NumericalFailure(ValueError):
    """A measurement failed an acceptance threshold (exit 1)."""


def corpus_items(manifest, suite):
    if suite in ('dev', 'brief'):
        return [m for m in manifest['meetings'] if m['split'] == 'dev'][:1 if suite == 'brief' else 3]
    return [m for m in manifest['meetings'] if suite == 'all' or m['split'] == suite]


def validate_scope(manifest, suite):
    if 'silence_diagnostic' in manifest and suite != 'brief':
        raise ValueError('The silence diagnostic is not the frozen whole-meeting corpus')


def validate_manifest(manifest):
    meetings = manifest['meetings']
    if len(meetings) != 6 or {m['id'] for m in meetings} != set(DEV + TEST):
        raise ValueError('All six frozen AMI meetings are required')
    for meeting in meetings:
        if meeting['split'] != ('dev' if meeting['id'] in DEV else 'test'):
            raise ValueError('AMI split differs from the frozen protocol')


def fidelity_component_passes(components, limit):
    if components['total'] == 0:
        return components['false alarm'] == 0
    return components['der'] is not None and components['der'] <= limit


def reference(args):
    import numpy as np
    import soundfile as sf
    import torch
    from pyannote_load import MeasurementLoad
    if args.out.exists():
        raise FileExistsError(args.out)
    if args.suite in ('all', 'test') and not args.recipe:
        raise ValueError('Freeze a recipe with --recipe before opening test results')
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    validate_scope(manifest, args.suite)
    pipeline = load_pipeline(args.snapshot)
    args.out.mkdir(parents=True, exist_ok=False)
    runs = []
    for item in corpus_items(manifest, args.suite):
        path = args.manifest.parent / item['wav']
        verify_record(path, item['audio'])
        samples, rate = sf.read(path, dtype='int16')
        if args.suite == 'brief':
            samples = samples[:600 * rate]
        for mode, speakers in (('auto', None), ('known', item['speakers'])):
            name = item['id'] + '-' + mode
            print('Reference ' + name, flush=True)
            output = args.out / name
            output.mkdir()
            logs, captured = [], {}

            def save_log(module, inputs, scores, saved=logs):
                saved.append(scores.detach().cpu().numpy())

            def hook(step, artifact, saved=captured, **kwargs):
                completed, total = kwargs.get('completed'), kwargs.get('total')
                if completed and (completed % 5 == 0 or completed == total):
                    print(f'  {step}: batch {completed}/{total}', flush=True)
                if 'completed' not in kwargs and artifact is not None:
                    if isinstance(artifact, np.ndarray):
                        saved[step] = artifact
                    elif hasattr(artifact, 'data') and isinstance(artifact.data, np.ndarray):
                        saved[step] = artifact.data.copy()

            handle = pipeline._segmentation.model.register_forward_hook(save_log)
            import pyannote.audio.pipelines.clustering as clustering_module
            original_vbx = clustering_module.cluster_vbx
            original_call = type(pipeline.clustering).__call__

            def save_vbx(labels, features, phi, *positional, saved=captured, original=original_vbx, **keywords):
                result = original(labels, features, phi, *positional, **keywords)
                saved['plda'], saved['responsibilities'] = features.copy(), result[0].copy()
                return result

            def save_clusters(instance, *positional, saved=captured, original=original_call, **keywords):
                result = original(instance, *positional, **keywords)
                saved['hard_clusters'] = result[0].copy()
                return result

            clustering_module.cluster_vbx = save_vbx
            type(pipeline.clustering).__call__ = save_clusters
            try:
                with MeasurementLoad() as load:
                    start = time.perf_counter()
                    result = pipeline({'waveform': torch.from_numpy(samples.astype(np.float32)[None] / 32768),
                                       'sample_rate': rate, 'uri': item['id']}, num_speakers=speakers, hook=hook)
                    elapsed = time.perf_counter() - start
            finally:
                handle.remove()
                clustering_module.cluster_vbx = original_vbx
                type(pipeline.clustering).__call__ = original_call
            for kind, annotation in (('normal', result.speaker_diarization), ('exclusive', result.exclusive_speaker_diarization)):
                write_rttm(output / (kind + '.rttm'), item['id'],
                           ((segment.start, segment.end, label)
                            for segment, _, label in annotation.itertracks(yield_label=True)))
            if logs:
                captured['scores'] = np.concatenate(logs)
            np.savez(output / 'intermediates.npz', **captured)
            record = {'id': item['id'], 'mode': mode, 'path': name, 'seconds': elapsed,
                      'duration': len(samples) / rate, 'speakers': len(result.speaker_diarization.labels()),
                      'load': load.report}
            write_json(output / 'measurement.json', record)
            runs.append(record)
    write_json(args.out / 'results.json', {'engine': 'reference', 'runs': runs, 'versions': versions(),
                                          'threads': 4, 'blas': {k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS')},
                                          'recipe': file_record(args.recipe) if args.recipe else None})


def rewrite_precision(args):
    """Recover exact annotations saved before the legacy three-decimal RTTM.

Both regenerated three-decimal files must match the originals byte-for-byte.
This is a serialization repair, never a new inference or timing measurement.
"""
    import numpy as np
    from pyannote.audio.pipelines.speaker_diarization import SpeakerDiarization as Pipeline
    from pyannote.core import SlidingWindow, SlidingWindowFeature
    if args.out.exists():
        raise FileExistsError(args.out)
    report = json.loads((args.reference / 'results.json').read_text())
    recipe = json.loads(args.recipe.read_text())
    manifest = json.loads(args.manifest.read_text())
    known = {m['id']: m['speakers'] for m in manifest['meetings']}
    args.out.mkdir(parents=True, exist_ok=False)
    for record in report['runs']:
        source, target = args.reference / record['path'], args.out / record['path']
        with np.load(source / 'intermediates.npz', allow_pickle=False) as saved:
            segmentation = SlidingWindowFeature(saved['segmentation'], SlidingWindow(duration=recipe['duration'], step=recipe['step']))
            hard = saved['hard_clusters'].copy()
            hard[saved['segmentation'].sum(1) == 0] = -2
            count = SlidingWindowFeature(saved['speaker_counting'].copy(), SlidingWindow(**recipe['frame']))
            if record['mode'] == 'known':
                count.data = np.minimum(count.data, known[record['id']]).astype(np.int8)
            normal = Pipeline.reconstruct(Pipeline, segmentation, hard, count)
            if not np.array_equal(normal.data, saved['discrete_diarization']):
                raise ValueError('Reconstructed reference differs from its saved discrete output')
            count.data = np.minimum(count.data, 1).astype(np.int8)
            exclusive = Pipeline.reconstruct(Pipeline, segmentation, hard, count)
        normal, exclusive = Pipeline.to_annotation(normal), Pipeline.to_annotation(exclusive)
        mapping = {label: f'SPEAKER_{i:02d}' for i, label in enumerate(normal.labels())}
        target.mkdir()
        for kind, value in (('normal', normal), ('exclusive', exclusive)):
            value = value.rename_labels(mapping=mapping)
            value.uri = record['id']
            buffer = io.StringIO()
            value.write_rttm(buffer)
            original_bytes = buffer.getvalue().replace('\n', os.linesep).encode('utf-8')
            if original_bytes != (source / (kind + '.rttm')).read_bytes():
                raise ValueError('Recovered reference does not reproduce its original RTTM')
            write_rttm(target / (kind + '.rttm'), record['id'],
                       ((segment.start, segment.end, label) for segment, _, label in value.itertracks(yield_label=True)))
        for name in ('intermediates.npz', 'measurement.json'):
            shutil.copyfile(source / name, target / name)
    report['precision_rewrite'] = {'source_results': file_record(args.reference / 'results.json'),
                                    'recipe': file_record(args.recipe), 'decimals': 6,
                                    'verified': 'discrete output exact, original normal and exclusive RTTM byte-identical at three decimals',
                                    'new_inference': False}
    write_json(args.out / 'results.json', report)


def annotation(path):
    from pyannote.database.util import load_rttm
    values = load_rttm(path)
    if not values:
        from pyannote.core import Annotation
        return Annotation()
    if len(values) != 1:
        raise ValueError(f'Expected one meeting per RTTM: {path}')
    return next(iter(values.values()))


def metric_components(ref, hyp, uem):
    from pyannote.metrics.diarization import DiarizationErrorRate
    metric = DiarizationErrorRate(collar=0., skip_overlap=False)
    components = metric(ref, hyp, uem=uem, detailed=True)
    keys = ('total', 'missed detection', 'false alarm', 'confusion')
    result = {key: float(components[key]) for key in keys}
    numerator = sum(result[key] for key in keys[1:])
    result['der'] = numerator / result['total'] if result['total'] else None
    return result


def aggregate_components(rows):
    result = {k: sum(row[k] for row in rows) for k in ('total', 'missed detection', 'false alarm', 'confusion')}
    result['der'] = sum(result[k] for k in ('missed detection', 'false alarm', 'confusion')) / result['total'] if result['total'] else None
    return result


def silence_comparison(original, candidate, interval):
    from pyannote.core import Annotation, Segment, Timeline
    uem = Timeline([Segment(*interval)])
    x = metric_components(Annotation(), original, uem)['false alarm']
    y = metric_components(Annotation(), candidate, uem)['false alarm']
    return {'original_seconds': x, 'candidate_seconds': y, 'passed': y <= x}


def boundary_comparison(reference, candidate, frame_step):
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from pyannote.metrics.diarization import DiarizationErrorRate
    mapping = DiarizationErrorRate(collar=0., skip_overlap=False).optimal_mapping(reference, candidate)
    candidate = candidate.rename_labels(mapping=mapping)
    deltas, unmatched = [], []
    for label in set(reference.labels()) | set(candidate.labels()):
        left = list(reference.label_timeline(label)) if label in reference.labels() else []
        right = list(candidate.label_timeline(label)) if label in candidate.labels() else []
        weights = np.array([[max(0., min(a.end, b.end) - max(a.start, b.start)) for b in right] for a in left])
        used_left, used_right = set(), set()
        if left and right:
            rows, cols = linear_sum_assignment(weights, maximize=True)
            for i, j in zip(rows, cols, strict=True):
                if weights[i, j] <= 0:
                    continue
                used_left.add(int(i))
                used_right.add(int(j))
                deltas.extend([abs(left[i].start - right[j].start), abs(left[i].end - right[j].end)])
        unmatched.extend({'side': 'reference', 'label': str(label), 'start': s.start, 'end': s.end}
                         for i, s in enumerate(left) if i not in used_left)
        unmatched.extend({'side': 'candidate', 'label': str(label), 'start': s.start, 'end': s.end}
                         for i, s in enumerate(right) if i not in used_right)
    nref, ncan = len(list(reference.itertracks())), len(list(candidate.itertracks()))
    percentile = float(np.percentile(deltas, 95)) if deltas else 0. if nref == ncan == 0 else None
    return {'p95': percentile, 'reference_turns': nref, 'candidate_turns': ncan, 'unmatched': unmatched,
            'passed': percentile is not None and percentile <= frame_step and abs(nref - ncan) <= max(2, .02 * nref)}


def compare(args):
    import numpy as np
    from pyannote.core import Segment, Timeline
    if args.out.exists():
        raise FileExistsError(args.out)
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    reference_runs = json.loads((args.reference / 'results.json').read_text())
    candidate_runs = json.loads((args.candidate / 'results.json').read_text())
    # Never accept a partial subset or a skipped measurement as a passing gate.
    brief = getattr(args, 'brief', False)
    if brief and args.mode != 'fidelity':
        raise ValueError('Brief evaluation is only a development fidelity diagnostic')
    validate_scope(manifest, 'brief' if brief else 'dev' if args.mode == 'fidelity' else 'test')
    items = corpus_items(manifest, 'brief' if brief else 'dev' if args.mode == 'fidelity' else 'test')
    required = {(m['id'], mode) for m in items for mode in ('auto', 'known')}
    ref_index = {(r['id'], r['mode']): r for r in reference_runs['runs']}
    can_index = {(r['id'], r['mode']): r for r in candidate_runs['runs']}
    if not required.issubset(ref_index) or not required.issubset(can_index):
        raise FileNotFoundError('Missing whole-meeting auto/known measurements')
    for item in items:
        expected_duration = min(600, item['duration']) if brief else item['duration']
        for mode in ('auto', 'known'):
            if any(abs(index[item['id'], mode]['duration'] - expected_duration) > 1 / 16000
                   for index in (ref_index, can_index)):
                raise ValueError('Measurement duration differs from the requested scope')
    baseline_index = None
    replay_index = None
    if args.mode == 'fidelity':
        if not getattr(args, 'replay', None):
            raise FileNotFoundError('Exact-input PLDA/VBx replay from the Scriba runtime is required')
        control = json.loads(args.replay.read_text())
        if control['reference_results'] != file_record(args.reference / 'results.json') or control['model_manifest'] != candidate_runs['model_manifest']:
            raise ValueError('Replay inputs or model recipe differ from the measured runs')
        replay_index = {(r['id'], r['mode']): r for r in control['runs']}
        if not required.issubset(replay_index):
            raise FileNotFoundError('Missing exact-input PLDA/VBx replay')
    if args.mode == 'quality':
        if args.baseline is None:
            raise ValueError('Quality requires --baseline')
        baseline_index = {(r['id'], r['mode']): r for r in json.loads((args.baseline / 'results.json').read_text())['runs']}
        if not required.issubset(baseline_index):
            raise FileNotFoundError('Missing legacy results')
    rows = []
    passed = True
    for item in manifest['meetings']:
        for mode in ('auto', 'known'):
            key = item['id'], mode
            if key not in required:
                continue
            verify_record(args.manifest.parent / item['rttm'], item['reference'])
            verify_record(args.manifest.parent / item['uem'], item['uem_hash'])
            uem = Timeline([Segment(0, min(600, item['duration']) if brief else item['duration'])])
            paths = {'original': args.reference / ref_index[key]['path'],
                     'candidate': args.candidate / can_index[key]['path']}
            if baseline_index:
                paths['legacy'] = args.baseline / baseline_index[key]['path']
            row = {'id': item['id'], 'mode': mode, 'measurements': {}}
            normal = {k: annotation(p / 'normal.rttm') for k, p in paths.items()}
            if args.mode == 'fidelity':
                row['count_equal'] = len(normal['original'].labels()) == len(normal['candidate'].labels())
                passed &= row['count_equal']
                for kind in ('normal', 'exclusive'):
                    x, y = annotation(paths['original'] / f'{kind}.rttm'), annotation(paths['candidate'] / f'{kind}.rttm')
                    detail = metric_components(x, y, uem)
                    row['measurements'][kind] = detail
                    row['measurements'][kind + '_inverse'] = metric_components(y, x, uem)
                    if 'silence_diagnostic' in manifest:
                        row[kind + '_silence'] = silence_comparison(x, y, manifest['silence_diagnostic']['inserted_interval'])
                        passed &= row[kind + '_silence']['passed']
                    passed &= fidelity_component_passes(detail, .01)
                    row[kind + '_boundaries'] = boundary_comparison(x, y, .016875)
                    passed &= row[kind + '_boundaries']['passed']
                # Intermediate acceptance is mandatory, never inferred from DER.
                with np.load(paths['original'] / 'intermediates.npz', allow_pickle=False) as x, np.load(paths['candidate'] / 'intermediates.npz', allow_pickle=False) as y:
                    for name in ('scores', 'segmentation', 'embeddings', 'plda', 'responsibilities', 'hard_clusters'):
                        if name not in x or name not in y:
                            raise FileNotFoundError(f'Missing intermediate {name} for {key}')
                    row['scores'] = segmentation_errors(x['scores'], y['scores'])
                    row['embeddings'] = embedding_errors(x['embeddings'], y['embeddings'])
                    passed &= row['scores']['passed'] and row['embeddings']['passed']
                    for name in ('plda', 'responsibilities'):
                        # Tight float64 tolerance is specified for identical inputs.
                        # Model output differences are already checked above and by DER.
                        same_shape = x[name].shape == y[name].shape
                        row[name + '_end_to_end'] = {'same_shape': same_shape,
                                                    'max_abs': float(np.max(np.abs(x[name]-y[name]))) if same_shape else None}
                        ok = replay_index[key][name]['passed']
                        row[name] = ok
                        passed &= ok
                    row['same_input_clusters_equal'] = replay_index[key]['clusters_equal']
                    passed &= row['same_input_clusters_equal']
            else:
                truth = annotation(args.manifest.parent / item['rttm'])
                events = sorted({0., item['duration'], *(t for segment in truth.itersegments() for t in (segment.start, segment.end))})
                mono, overlap = [], []
                for start, end in zip(events, events[1:], strict=False):
                    count = len(truth.crop(Segment(start, end), mode='intersection').labels())
                    if count == 1:
                        mono.append(Segment(start, end))
                    elif count > 1:
                        overlap.append(Segment(start, end))
                row['counts'] = {k: len(value.labels()) for k, value in normal.items()}
                row['truth_count'] = len(truth.labels())
                for label, value in normal.items():
                    row['measurements'][label] = metric_components(truth, value, uem)
                    exclusive = annotation(paths[label] / 'exclusive.rttm')
                    row['measurements'][label + '_mono'] = metric_components(truth, exclusive, Timeline(mono))
                    row['measurements'][label + '_overlap'] = metric_components(truth, exclusive, Timeline(overlap))
                if mode == 'auto':
                    new, old = row['measurements']['candidate'], row['measurements']['legacy']
                    passed &= (new['der'] <= old['der'] + .02 if new['total'] else new['false alarm'] <= old['false alarm'])
            rows.append(row)
    totals = {}
    for mode in ('auto', 'known'):
        subset = [row for row in rows if row['mode'] == mode]
        totals[mode] = {label: aggregate_components([row['measurements'][label] for row in subset]) for label in subset[0]['measurements']}
        if args.mode == 'fidelity':
            passed &= all(fidelity_component_passes(totals[mode][kind], .005) for kind in ('normal', 'exclusive'))
        else:
            old, new = totals[mode]['legacy']['der'], totals[mode]['candidate']['der']
            counts = {label: sum(abs(row['counts'][label] - row['truth_count']) for row in subset) / len(subset) for label in ('original', 'candidate', 'legacy')}
            totals[mode]['count_mae'] = counts
            if mode == 'auto':
                passed &= old is not None and new is not None and old - new >= .01 and new <= old * .9
                passed &= counts['candidate'] <= counts['legacy']
                cm, lm = totals[mode]['candidate_mono'], totals[mode]['legacy_mono']
                passed &= cm['der'] <= lm['der'] + .005 if cm['total'] else cm['false alarm'] <= lm['false alarm']
    write_json(args.out, {'passed': bool(passed), 'mode': args.mode, 'scope': 'brief' if brief else 'whole meetings',
                          'rows': rows, 'totals': totals})
    if not passed:
        raise NumericalFailure('Acceptance threshold failed')


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare-silence')
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=prepare_silence)
    p = commands.add_parser('inspect')
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--plda-license', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=inspect)
    p = commands.add_parser('prepare-ami')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--with-stress-2h', action='store_true')
    p.set_defaults(function=prepare_ami)
    p = commands.add_parser('export')
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--opset', type=int, default=17, choices=[17])
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=export)
    p = commands.add_parser('reference')
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--suite', choices=['dev', 'all', 'test', 'brief'], required=True)
    p.add_argument('--recipe', type=Path)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=reference)
    p = commands.add_parser('rewrite-precision')
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--recipe', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=rewrite_precision)
    p = commands.add_parser('compare')
    p.add_argument('--mode', choices=['fidelity', 'quality'], required=True)
    p.add_argument('--brief', action='store_true')
    p.add_argument('--replay', type=Path)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--baseline', type=Path)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=compare)
    return result


def main():
    args = parser().parse_args()
    try:
        args.function(args)
    except NumericalFailure as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, ImportError, urllib.error.URLError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
