"""Isolated community-1 experiments. Never selected by the application."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def c_session(model, threads=4, inter=1, optimization=99, arena=True, parallel=False):
    """The same ORT C DLL, with explicit experimental session options.

    OrtApi indices 13,18,19,23 are from the append-only upstream C API.
    No global table or application defaults are modified.
    """
    from scriba.app.engines import onnx
    o = onnx.api()
    options = ctypes.c_void_p()
    o.ok(o.CreateSessionOptions(ctypes.byref(options)))
    result = onnx.Session.__new__(onnx.Session)
    result.api, result.session = o, ctypes.c_void_p()
    try:
        def call(index, *values):
            types = [ctypes.c_void_p, *[ctypes.c_int for _ in values]]
            function = ctypes.CFUNCTYPE(ctypes.c_void_p, *types)(o.table[index])
            o.ok(function(options, *values))
        call(13, int(parallel))
        call(18 if arena else 19)
        call(23, optimization)
        o.ok(o.SetIntraOpNumThreads(options, threads))
        o.ok(o.SetInterOpNumThreads(options, inter))
        name = os.fspath(model) if sys.platform == 'win32' else os.fsencode(model)
        o.ok(o.CreateSession(o.env, name, options, ctypes.byref(result.session)))
    finally:
        o.ReleaseSessionOptions(options)
    return result


def experiment(samples, models, args, speakers=None):
    import numpy as np
    from scriba.app.engines import community as c, community_math as m
    from scriba.app.engines.diarize import fbank
    from community_profile import Timings
    recipe, paths = c.validated_files(models)
    starts = m.chunk_starts(len(samples), 160000, round(recipe['step'] * 16000))
    times = Timings()

    def session(path):
        threads = (args.embedding_threads if Path(path).stem == 'embedding' and args.embedding_threads
                   else args.threads)
        if args.provider == 'c':
            return c_session(path, threads, args.inter, args.optimization,
                             not args.no_arena, args.parallel)
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads, opts.inter_op_num_threads = threads, args.inter
        opts.graph_optimization_level = ort.GraphOptimizationLevel(args.optimization)
        opts.enable_cpu_mem_arena = not args.no_arena
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.enable_mem_pattern = False
        opts.enable_profiling = args.profile
        if opts.enable_profiling:
            opts.profile_file_prefix = str(args.out / Path(path).stem)
        providers = ([('DmlExecutionProvider', {'device_id': args.device})]
                     if args.provider == 'dml' else ['CPUExecutionProvider'])
        native = ort.InferenceSession(str(path), opts, providers=providers)
        if args.provider == 'dml' and 'DmlExecutionProvider' not in native.get_providers():
            raise RuntimeError('DirectML provider was not activated')

        class PythonSession:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                nonlocal native
                if opts.enable_profiling:
                    native.end_profiling()
                native = None

            def run(self, *a):
                result = native.run(*a)
                if args.provider == 'dml' and 'DmlExecutionProvider' not in native.get_providers():
                    raise RuntimeError('DirectML provider was lost during inference')
                return result
        return PythonSession()

    def piece(at, scale):
        value = np.zeros(160000, np.float32)
        raw = samples[at:at + 160000]
        value[:len(raw)] = raw.astype(np.float32) / scale
        return value

    scores = np.empty((len(starts), 589, 7), np.float32)
    with times.wrap('segmentation_init', session)(paths['segmentation.onnx']) as sess:
        for i in range(0, len(starts), args.segmentation_batch):
            batch = np.stack([piece(at, 32768) for at in starts[i:i + args.segmentation_batch]])[:, None]
            scores[i:i + len(batch)] = times.wrap('segmentation', sess.run)(['scores'], {'waveform': batch})[0]
            if i % 100 == 0:
                print(1, i, len(starts), flush=True)
    binary = m.powerset(scores)
    if not binary.any():
        return c.CommunityResult([], [], 0, len(starts), recipe['recipe_id']), times
    vectors = np.empty((len(starts), 3, 256), np.float32)

    def features(at):
        feats = fbank(piece(at, 1))
        feats -= feats.mean(0, dtype=np.float32)
        return feats

    with times.wrap('embedding_init', session)(paths['embedding.onnx']) as sess:
        for i in range(0, len(starts), args.embedding_batch):
            feats = np.stack([times.wrap('fbank', features)(at) for at in starts[i:i + args.embedding_batch]])
            masks = binary[i:i + len(feats)].transpose(0, 2, 1).copy()
            if recipe['exclude_overlap']:
                clean = masks * (binary[i:i + len(feats)].sum(2) < 2)[:, None]
                minimum = np.ceil(589 * recipe['min_num_samples'] / 160000)
                masks = np.where((clean.sum(2) > minimum)[:, :, None], clean, masks)
            vectors[i:i + len(feats)] = times.wrap('embedding', sess.run)(
                ['embeddings'], {'feats': feats, 'weights': masks.astype(np.float32)})[0]
            if i % 100 == 0:
                print(2, i, len(starts), flush=True)
    transform, phi = times.wrap('plda_init', c.load_plda)(paths['xvec_transform.npz'], paths['plda.npz'])
    hard, _, _, details = times.wrap('plda_vbx', m.cluster)(vectors, binary, transform, phi, speakers,
                                                          **recipe['parameters']['clustering'])
    hard[binary.sum(1) == 0] = -2
    normal, exclusive = times.wrap('reconstruction', m.reconstruct)(
        binary, hard, recipe['frame'], recipe['duration'], recipe['step'], speakers)
    turns = m.turns(normal, recipe['frame'])
    return c.CommunityResult(turns, m.turns(exclusive, recipe['frame']), len({t.label for t in turns}),
                             len(starts), recipe['recipe_id'], dict(times.seconds),
                             {'scores': scores, 'segmentation': binary, 'embeddings': vectors,
                              'hard_clusters': hard, **{k: v for k, v in details.items()
                                                       if k in ('plda', 'responsibilities')}}), times


def run(args):
    import numpy as np
    from pyannote_benchmark import model_set, rttm, peak_rss
    from pyannote_reference import write_json, file_record
    from pyannote_load import MeasurementLoad
    from scriba.app.engines import audio
    args.out.mkdir(parents=True, exist_ok=False)
    models = model_set(args.models)
    if args.manifest:
        manifest = json.loads(args.manifest.read_text())
        items = [(item['id'], args.manifest.parent / item['wav'], count, mode)
                 for item in manifest['meetings'] if item['split'] == 'dev'
                 for mode, count in [('auto', None), ('known', item['speakers'])]]
    else:
        items = [(args.wav.stem, args.wav, None, 'auto')]
    runs = []
    for name, wav, count, mode in items:
        wav_record = file_record(wav)
        with MeasurementLoad(idle_limit=args.idle_limit) as load:
            started = time.perf_counter()
            decode = time.perf_counter()
            samples = audio.read(wav)
            decode = time.perf_counter() - decode
            if args.seconds:
                samples = samples[:int(args.seconds * 16000)]
            result, times = experiment(samples, models, args, count)
            elapsed = time.perf_counter() - started
        output = args.out / (name + '-' + mode)
        output.mkdir()
        rttm(output / 'normal.rttm', name, result.turns)
        rttm(output / 'exclusive.rttm', name, result.exclusive_turns)
        if args.manifest or args.save_intermediates:
            np.savez(output / 'intermediates.npz', **result.intermediates)
        row = dict(id=name, mode=mode, path=output.name, seconds=elapsed, duration=len(samples) / 16000,
                   phases={'decode': decode, **times.seconds}, calls=dict(times.calls), load=load.report,
                   peak_rss_mib=peak_rss(), speakers=result.speakers, wav=wav_record)
        if file_record(wav) != wav_record:
            raise ValueError('Public WAV changed during the measurement')
        write_json(output / 'measurement.json', row)
        runs.append(row)
        result = None
    write_json(args.out / 'results.json', {'runs': runs,
               'options': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
               'model_manifest': file_record(args.models / 'manifest.json'),
               'models': file_record(args.models / 'manifest.json')})


def export_batch(args):
    """Re-export only in the reference environment, from authorized weights."""
    import numpy as np
    import torch
    import onnx
    import shutil
    from pyannote_reference import (load_pipeline, write_json, file_record, verify_record,
                                   frozen_sinc_segmentation)
    if args.out.exists():
        raise FileExistsError(args.out)
    source = json.loads((args.models / 'manifest.json').read_text())
    for name, record in source['sources'].items():
        verify_record(args.snapshot / name, record)
    pipeline = load_pipeline(args.snapshot)
    args.out.mkdir(parents=True, exist_ok=False)
    segmenter, embedder = pipeline._segmentation.model.eval(), pipeline._embedding.model_.eval()
    if args.segmentation_batch != 1:
        segmenter, source['batch_frozen_sinc'] = frozen_sinc_segmentation(segmenter)

    class Weighted(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.resnet = embedder.resnet

        def forward(self, feats, weights):
            return self.resnet.forward_embedding(self.resnet.forward_frames(feats), weights=weights)[1]

    for name, model, inputs, input_names, output in (
        ('segmentation.onnx', segmenter, (torch.zeros(1, 1, 160000),), ['waveform'], 'scores'),
        ('embedding.onnx', Weighted().eval(), (torch.zeros(1, 998, 80), torch.ones(1, 3, 589)),
         ['feats', 'weights'], 'embeddings')):
        if name == 'segmentation.onnx' and args.segmentation_batch == 1:
            shutil.copyfile(args.models / name, args.out / name)
            continue
        with torch.inference_mode():
            torch.onnx.export(model, inputs, str(args.out / name), opset_version=17, dynamo=False,
                              input_names=input_names, output_names=[output],
                              dynamic_axes={key: {0: 'batch'} for key in [*input_names, output]})
        graph = onnx.load(args.out / name)
        onnx.checker.check_model(graph, full_check=True)
        if any(n.domain not in ('', 'ai.onnx') for n in graph.graph.node):
            raise ValueError('Non-standard ONNX operator')
        source['graphs'][name] = {
            'ir_version': graph.ir_version,
            'opsets': {entry.domain: entry.version for entry in graph.opset_import},
            'operators': sorted({node.op_type for node in graph.graph.node}),
            **{kind: {entry.name: {'dtype': entry.type.tensor_type.elem_type,
                                  'shape': [dim.dim_param or dim.dim_value
                                            for dim in entry.type.tensor_type.shape.dim]}
                       for entry in getattr(graph.graph, group)}
               for kind, group in [('inputs', 'input'), ('outputs', 'output')]}}
    for name in ('plda.npz', 'xvec_transform.npz'):
        shutil.copyfile(args.models / name, args.out / name)
    # Save independent exact inputs for batch-vs-single checks, including masks.
    rng = np.random.default_rng(42)
    inputs = dict(waveform=rng.normal(0, .01, (32, 1, 160000)).astype('float32'),
                  feats=rng.normal(0, 1, (32, 998, 80)).astype('float32'),
                  weights=rng.integers(0, 2, (32, 3, 589)).astype('float32'))
    inputs['weights'][0, 0] = 0
    inputs['weights'][0, 1] = 1
    np.savez(args.out / 'batch-inputs.npz', **inputs)
    import onnxruntime as ort
    from pyannote_reference import segmentation_errors, embedding_errors
    opts = ort.SessionOptions()
    opts.intra_op_num_threads, opts.inter_op_num_threads = 4, 1
    checks = []
    for name, keys, output, gate in (
        ('segmentation.onnx', ['waveform'], 'scores', segmentation_errors),
        ('embedding.onnx', ['feats', 'weights'], 'embeddings', embedding_errors)):
        baseline = ort.InferenceSession(str(args.models / name), opts, providers=['CPUExecutionProvider'])
        candidate = ort.InferenceSession(str(args.out / name), opts, providers=['CPUExecutionProvider'])
        sizes = (1,) if name == 'segmentation.onnx' and args.segmentation_batch == 1 else (1, 7, 32)
        expected = np.concatenate([baseline.run([output], {k: inputs[k][i:i+1] for k in keys})[0]
                                   for i in range(max(sizes))])
        for size in sizes:
            actual = candidate.run([output], {k: inputs[k][:size] for k in keys})[0]
            checks.append({'model': name, 'batch': size, **gate(expected[:size], actual)})
    source['files'] = {name: file_record(args.out / name) for name in source['files']}
    source['batch_experiment'] = {'embedding_dynamic_batch': True,
                                  'segmentation_dynamic_batch': args.segmentation_batch != 1, 'checks': checks,
                                  'base_manifest': file_record(args.models / 'manifest.json')}
    source['export_verified'] = all(c['passed'] for c in checks)
    source.pop('recipe_id')
    source['recipe_id'] = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    write_json(args.out / 'manifest.json', source)
    if not source['export_verified']:
        raise ValueError('Batch export failed its independent numeric checks')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['run', 'export-batch'])
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--snapshot', type=Path)
    p.add_argument('--wav', type=Path)
    p.add_argument('--manifest', type=Path)
    p.add_argument('--seconds', type=float)
    p.add_argument('--idle-limit', type=int, default=0)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--embedding-threads', type=int)
    p.add_argument('--inter', type=int, default=1)
    p.add_argument('--optimization', type=int, choices=[0, 1, 2, 99], default=99)
    p.add_argument('--no-arena', action='store_true')
    p.add_argument('--parallel', action='store_true')
    p.add_argument('--segmentation-batch', type=int, default=1)
    p.add_argument('--embedding-batch', type=int, default=1)
    p.add_argument('--provider', choices=['c', 'cpu', 'dml'], default='c')
    p.add_argument('--device', type=int, default=1)
    p.add_argument('--profile', action='store_true')
    p.add_argument('--save-intermediates', action='store_true')
    return p


if __name__ == '__main__':
    args = parser().parse_args()
    (export_batch if args.command == 'export-batch' else run)(args)
