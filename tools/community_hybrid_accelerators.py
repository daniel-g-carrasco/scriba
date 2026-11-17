"""Development-only per-model accelerator selection, never used by Scriba."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from community_accelerators import Session, compare, confine_linux_cache


def backend_for(path, segmentation, embedding):
    if path.stem == 'segmentation':
        return segmentation
    if path.stem == 'embedding':
        return embedding
    raise ValueError(f'Unknown model: {path}')


def checked(code, operation):
    if code != 0:
        raise RuntimeError(f'ncnn {operation} failed: {code}')


class NcnnSession:
    def __init__(self, path, device, threads, converted):
        import ncnn
        if not 0 <= device < ncnn.get_gpu_count():
            raise RuntimeError(f'Vulkan device {device} absent')
        self.ncnn = ncnn
        self.native = ncnn.Net()
        self.native.opt.use_vulkan_compute = True
        self.native.opt.use_fp16_packed = False
        self.native.opt.use_fp16_storage = False
        self.native.opt.use_fp16_arithmetic = False
        self.native.opt.num_threads = threads
        self.native.set_vulkan_device(device)
        print('ncnn Vulkan device:', device, flush=True)
        checked(self.native.load_param(str(converted / f'{path.stem}.ncnn.param')), 'load_param')
        checked(self.native.load_model(str(converted / f'{path.stem}.ncnn.bin')), 'load_model')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.native.clear()
        self.native = None

    def run(self, names, feeds):
        import numpy as np
        if list(feeds) != ['feats', 'weights'] or feeds['feats'].shape[0] != 1:
            raise ValueError('Converted ncnn embedding requires batch=1 and feats,weights')
        with self.native.create_extractor() as extractor:
            for key, name in (('feats', 'in0'), ('weights', 'in1')):
                value = np.ascontiguousarray(feeds[key], dtype=np.float32)
                checked(extractor.input(name, self.ncnn.Mat(value, batch_index=0).clone()), 'input')
            code, output = extractor.extract('out0')
            checked(code, 'extract')
            result = output.numpy(batch_index=0).copy()
        if result.shape != (1, 3, 256) or not np.isfinite(result).all():
            raise ValueError(f'Invalid ncnn embedding: {result.shape}')
        return [result]


def main():
    import community_experiments as experiments
    from pyannote_reference import write_json
    parser = argparse.ArgumentParser()
    parser.add_argument('--segmentation-backend', choices=['ort', 'webgpu'], default='ort')
    parser.add_argument('--embedding-backend', choices=['ort', 'webgpu', 'ncnn'], required=True)
    parser.add_argument('--accelerator-device', type=int, default=0)
    parser.add_argument('--converted', type=Path)
    parser.add_argument('--reference', type=Path)
    own, rest = parser.parse_known_args()
    args = experiments.parser().parse_args(['run', *rest, '--provider', 'c'])
    confine_linux_cache(args.out)

    def factory(path, threads, *unused):
        backend = backend_for(Path(path), own.segmentation_backend, own.embedding_backend)
        if backend == 'ncnn':
            if own.converted is None:
                raise ValueError('--converted is required for ncnn')
            return NcnnSession(Path(path), own.accelerator_device, threads, own.converted)
        return Session(path, backend, str(own.accelerator_device), threads,
                       args.out / Path(path).stem if args.profile else None)

    with patch.object(experiments, 'c_session', factory):
        experiments.run(args)
    write_json(args.out / 'adapter.json', vars(own) | {
        'converted': str(own.converted), 'reference': str(own.reference)})
    if own.reference:
        gate = compare(own.reference, args.out)
        write_json(args.out / 'gate.json', gate)
        print(json.dumps(gate), flush=True)
    if own.embedding_backend == 'ncnn':
        import ncnn
        ncnn.destroy_gpu_instance()
        print('ncnn Vulkan instance destroyed', flush=True)


if __name__ == '__main__':
    main()
