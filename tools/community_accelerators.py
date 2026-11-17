"""Isolated accelerator adapters for the public community-1 laboratory.

No production modules or runtime defaults are modified. Output directories
must be new; the existing experiment loop and load monitor are reused.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def confine_linux_cache(output):
    """Select laboratory caches before any Vulkan driver can be loaded."""
    if sys.platform == 'linux':
        cache = str((output / 'cache').resolve())
        os.environ['XDG_CACHE_HOME'] = cache
        os.environ['MESA_SHADER_CACHE_DIR'] = cache


def accepted(segmentation, embedding, turns_equal, reference_count, actual_count):
    return bool(segmentation['passed'] and embedding['passed'] and turns_equal
                and reference_count == actual_count)


class Session:
    def __init__(self, path, backend, device, threads, profile_prefix=None, layout=None):
        self.backend = backend
        if backend == 'openvino':
            # Use OpenVINO's own optional-telemetry stub. The laboratory must
            # neither create a consent file in the user's home nor send data.
            sys.modules['openvino_telemetry'] = None
            import openvino as ov
            core = ov.Core()
            if device not in core.available_devices:
                raise RuntimeError(f'Requested device {device} absent: {core.available_devices}')
            properties = {'INFERENCE_PRECISION_HINT': 'f32', 'PERFORMANCE_HINT': 'LATENCY'}
            if device == 'CPU':
                properties['INFERENCE_NUM_THREADS'] = threads
            self.native = core.compile_model(core.read_model(path), device, properties)
            self.outputs = {x.any_name: x for x in self.native.outputs}
            print('OpenVINO execution devices:', self.native.get_property('EXECUTION_DEVICES'), flush=True)
        else:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            if backend == 'webgpu':
                import onnxruntime_ep_webgpu as webgpu
                if not any(x.ep_name == webgpu.get_ep_name() for x in ort.get_ep_devices()):
                    ort.register_execution_provider_library('webgpu_lab', webgpu.get_library_path())
                devices = [x for x in ort.get_ep_devices() if x.ep_name == webgpu.get_ep_name()]
                if not devices:
                    raise RuntimeError('No WebGPU devices')
                selected = devices[int(device)]
                print('WebGPU device:', selected.device.metadata, flush=True)
                provider_options = {'enableGraphCapture': '0'}
                if layout:
                    provider_options['preferredLayout'] = layout
                options.add_provider_for_devices([selected], provider_options)
                options.enable_profiling = profile_prefix is not None
                if profile_prefix is not None:
                    options.profile_file_prefix = str(profile_prefix)
                self.native = ort.InferenceSession(str(path), sess_options=options)
                self.native.disable_fallback()
                if webgpu.get_ep_name() not in self.native.get_providers():
                    raise RuntimeError('WebGPU provider not activated')
            else:
                self.native = ort.InferenceSession(str(path), sess_options=options,
                                                  providers=['CPUExecutionProvider'])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.backend == 'webgpu':
            print('WebGPU profile:', self.native.end_profiling(), flush=True)
        self.native = None

    def run(self, names, feeds):
        if self.backend == 'openvino':
            result = self.native(feeds)
            return [result[self.outputs[name]].copy() for name in names]
        return self.native.run(names, feeds)


def compare(reference, candidate):
    import numpy as np
    from pyannote_reference import segmentation_errors, embedding_errors
    left_runs = json.loads((reference / 'results.json').read_text())['runs']
    right_runs = json.loads((candidate / 'results.json').read_text())['runs']
    if len(left_runs) != 1 or len(right_runs) != 1:
        raise ValueError('Accelerator comparison requires exactly one run per directory')
    left, right = left_runs[0], right_runs[0]
    if left['wav'] != right['wav'] or left['duration'] != right['duration']:
        raise ValueError('Reference audio identity or duration differs')
    a, b = reference / left['path'], candidate / right['path']
    with np.load(a / 'intermediates.npz') as x, np.load(b / 'intermediates.npz') as y:
        seg = segmentation_errors(x['scores'], y['scores'])
        emb = embedding_errors(x['embeddings'], y['embeddings'])
    identical = all((a / name).read_bytes() == (b / name).read_bytes()
                    for name in ('normal.rttm', 'exclusive.rttm'))
    return {'segmentation': seg, 'embedding': emb, 'rttm_identical': identical,
            'reference_speakers': left['speakers'], 'actual_speakers': right['speakers'],
            'passed': accepted(seg, emb, identical, left['speakers'], right['speakers'])}


def main():
    import community_experiments as experiments
    from pyannote_reference import write_json
    p = argparse.ArgumentParser()
    p.add_argument('--backend', choices=['openvino', 'ort', 'webgpu'], required=True)
    p.add_argument('--device', default='CPU')
    p.add_argument('--reference', type=Path)
    p.add_argument('--webgpu-layout', choices=['NCHW', 'NHWC'])
    own, rest = p.parse_known_args()
    args = experiments.parser().parse_args(['run', *rest, '--provider', 'c'])
    confine_linux_cache(args.out)
    def factory(path, threads, *unused):
        return Session(path, own.backend, own.device, threads, args.out / Path(path).stem, own.webgpu_layout)
    with patch.object(experiments, 'c_session', factory):
        experiments.run(args)
    write_json(args.out / 'adapter.json', {'backend': own.backend, 'device': own.device})
    if own.reference:
        gate = compare(own.reference, args.out)
        write_json(args.out / 'gate.json', gate)
        print(json.dumps(gate), flush=True)


if __name__ == '__main__':
    main()
