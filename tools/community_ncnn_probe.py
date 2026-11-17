"""Localize a converted embedding mismatch using one public AMI window."""
import argparse
import json
from pathlib import Path


def main():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    import numpy as np
    import onnxruntime as ort
    from pyannote_reference import embedding_errors, write_json
    from scriba.app.engines import audio, community_math
    from scriba.app.engines.diarize import fbank

    parser = argparse.ArgumentParser()
    for name in ('models', 'converted', 'wav', 'reference', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    from community_accelerators import confine_linux_cache
    confine_linux_cache(args.out.parent)
    import ncnn
    with np.load(args.reference / 'stress-2h-auto/intermediates.npz') as saved:
        binary = community_math.powerset(saved['scores'][:1])
        expected = saved['embeddings'][:1].copy()
    recipe = json.loads((args.models / 'manifest.json').read_text())
    samples = audio.read(args.wav)[:160000]
    feats = fbank(samples.astype(np.float32))
    feats -= feats.mean(0, dtype=np.float32)
    masks = binary.transpose(0, 2, 1).copy()
    if recipe['exclude_overlap']:
        clean = masks * (binary.sum(2) < 2)[:, None]
        minimum = np.ceil(589 * recipe['min_num_samples'] / 160000)
        masks = np.where((clean.sum(2) > minimum)[:, :, None], clean, masks)
    feeds = [feats[None], masks.astype(np.float32)]
    result = {}
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    options.inter_op_num_threads = 1
    for name, path in (('original', args.models / 'embedding.onnx'),
                       ('pnnx_sim', args.converted / 'embedding.pnnxsim.onnx')):
        try:
            session = ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])
        except Exception as error:
            result[name] = {'error': f'{type(error).__name__}: {error}'}
            continue
        output = session.run(None, dict(zip([v.name for v in session.get_inputs()], feeds, strict=True)))[0]
        result[name] = embedding_errors(expected, output)
        del session
    with ncnn.Net() as net:
        net.opt.use_vulkan_compute = False
        net.opt.num_threads = 8
        for ext, load in (('param', net.load_param), ('bin', net.load_model)):
            code = load(str(args.converted / ('embedding.ncnn.' + ext)))
            if code != 0:
                raise RuntimeError(f'ncnn load {ext}: {code}')
        with net.create_extractor() as extractor:
            for name, value in zip(('in0', 'in1'), feeds, strict=True):
                code = extractor.input(name, ncnn.Mat(value, batch_index=0).clone())
                if code != 0:
                    raise RuntimeError(f'ncnn input: {code}')
            code, mat = extractor.extract('out0')
            if code != 0:
                raise RuntimeError(f'ncnn extract: {code}')
            output = mat.numpy(batch_index=0).copy()
    result['ncnn_cpu'] = embedding_errors(expected, output)
    result['ncnn_cpu']['range'] = [float(output.min()), float(output.max())]
    write_json(args.out, result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
