"""Reproduce the worst saved segmentation drift without changing a model."""
import argparse
import json
from pathlib import Path
import sys

from pyannote_reference import load_pipeline, segmentation_errors, write_json, file_record, centered_segmentation, frozen_sinc_segmentation, validate_manifest, validate_scope, verify_record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference', 'candidate', 'manifest', 'snapshot', 'models', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--try-centered-normalization', action='store_true')
    parser.add_argument('--try-frozen-sinc', action='store_true')
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(args.out)
    import numpy as np
    import onnxruntime as ort
    import soundfile as sf
    import torch
    manifest = json.loads(args.manifest.read_text())
    validate_manifest(manifest)
    validate_scope(manifest, 'dev')
    candidate = json.loads((args.candidate / 'results.json').read_text())
    if candidate['model_manifest'] != file_record(args.models / 'manifest.json'):
        raise ValueError('Diagnostic models differ from the saved candidate')
    worst = None
    for item in manifest['meetings']:
        if item['split'] != 'dev':
            continue
        folder = item['id'] + '-auto'
        with np.load(args.reference / folder / 'intermediates.npz', allow_pickle=False) as x, np.load(args.candidate / folder / 'intermediates.npz', allow_pickle=False) as y:
            difference = np.abs(np.exp(x['scores']) - np.exp(y['scores']))
            index = np.unravel_index(difference.argmax(), difference.shape)
            value = float(difference[index])
            if worst is None or value > worst[0]:
                worst = value, item, int(index[0]), int(index[1]), int(index[2]), x['scores'][index[0]].copy(), y['scores'][index[0]].copy()
    value, item, chunk, frame, label, saved_reference, saved_candidate = worst
    verify_record(args.manifest.parent / item['wav'], item['audio'])
    samples, rate = sf.read(args.manifest.parent / item['wav'], dtype='float32')
    if rate != 16000 or samples.ndim != 1:
        raise ValueError('Expected mono 16 kHz corpus')
    pipeline = load_pipeline(args.snapshot)
    model = pipeline._segmentation.model.eval()
    size, step, batch_size = 160000, round(pipeline._segmentation.step * rate), pipeline.segmentation_batch_size
    full_chunks = max(0, 1 + (len(samples) - size) // step)
    group = [chunk] if chunk >= full_chunks else list(range(chunk // batch_size * batch_size, min((chunk // batch_size + 1) * batch_size, full_chunks)))
    batch = np.zeros((len(group), 1, size), np.float32)
    for i, at in enumerate(group):
        piece = samples[at * step:at * step + size]
        batch[i, 0, :len(piece)] = piece
    position = group.index(chunk)
    waveform = batch[position:position + 1].copy()
    with torch.inference_mode():
        grouped = model(torch.from_numpy(batch)).numpy()[position]
        single = model(torch.from_numpy(waveform)).numpy()[0]
    options = ort.SessionOptions()
    options.intra_op_num_threads, options.inter_op_num_threads = 4, 1
    runtime = ort.InferenceSession(str(args.models / 'segmentation.onnx'), options, providers=['CPUExecutionProvider'])
    python_ort = runtime.run(['scores'], {'waveform': waveform})[0][0]
    report = {'id': item['id'], 'chunk': chunk, 'start_seconds': chunk * step / rate,
              'frame': frame, 'class': label, 'original_batch_size': len(group),
              'signal_rms': float(np.sqrt(np.mean(waveform.astype(np.float64) ** 2))),
              'zero_samples': int(np.count_nonzero(waveform == 0)), 'max_abs_sample': float(np.abs(waveform).max()),
              'stored_max_probability': value,
              'grouped_torch_vs_saved': segmentation_errors(saved_reference, grouped),
              'single_torch_vs_grouped': segmentation_errors(grouped, single),
              'python_ort_vs_grouped': segmentation_errors(grouped, python_ort),
              'saved_c_ort_vs_grouped': segmentation_errors(grouped, saved_candidate),
              'python_ort_vs_single': segmentation_errors(single, python_ort),
              'model_manifest': file_record(args.models / 'manifest.json')}
    if args.try_centered_normalization or args.try_frozen_sinc:
        for suffix in ('.onnx', '.npz'):
            if args.out.with_suffix(suffix).exists():
                raise FileExistsError(args.out.with_suffix(suffix))
        converted = model
        if args.try_frozen_sinc:
            converted, record = frozen_sinc_segmentation(converted)
            report['frozen_sinc'] = record
        if args.try_centered_normalization:
            converted, layers = centered_segmentation(converted)
            report['centered_layers'] = layers
        with torch.inference_mode():
            expected = converted(torch.from_numpy(waveform)).numpy()[0]
            torch.onnx.export(converted, (torch.from_numpy(waveform),), str(args.out.with_suffix('.onnx')),
                              opset_version=17, dynamo=False, input_names=['waveform'], output_names=['scores'])
        stable = ort.InferenceSession(str(args.out.with_suffix('.onnx')), options, providers=['CPUExecutionProvider'])
        actual = stable.run(['scores'], {'waveform': waveform})[0][0]
        report['converted_torch_vs_original'] = segmentation_errors(grouped, expected)
        report['converted_python_ort_vs_original'] = segmentation_errors(grouped, actual)
        np.savez(args.out.with_suffix('.npz'), waveform=waveform, scores=grouped)
    if report['grouped_torch_vs_saved']['max_abs_log'] != 0:
        raise ValueError('The diagnostic did not reproduce the saved reference exactly')
    report['passed'] = (report['converted_python_ort_vs_original']['passed']
                        if args.try_centered_normalization or args.try_frozen_sinc
                        else report['python_ort_vs_grouped']['passed'] and report['saved_c_ort_vs_grouped']['passed'])
    write_json(args.out, report)
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, ImportError, KeyError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(2)
