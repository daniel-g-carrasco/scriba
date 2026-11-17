"""Public-corpus Scriba benchmarks. This tool never imports Torch or pyannote."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pyannote_reference import corpus_items, file_record, verify_record, write_json, write_rttm, validate_scope  # noqa: E402


def peak_rss():
    """Whole-process peak, including engine allocations, in MiB."""
    if sys.platform != 'win32':
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / (1024 * 1024 if sys.platform == 'darwin' else 1024)
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in (
                        'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                        'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                        'PagefileUsage', 'PeakPagefileUsage')]]
    kernel, psapi = ctypes.WinDLL('kernel32'), ctypes.WinDLL('psapi')
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.PeakWorkingSetSize / (1024 * 1024)


def rttm(path, meeting, turns):
    write_rttm(path, meeting, ((turn.start, turn.end, turn.label) for turn in turns))


def model_set(root):
    recipe = json.loads((root / 'manifest.json').read_text())
    return {'recipe': recipe, **{name: root / name for name in recipe['files']}}


def clustering_control(saved, transform, phi, speakers, parameters):
    """Use the exact reference inputs for the float64 PLDA/VBx gate."""
    import numpy as np
    from scriba.app.engines.community_math import cluster
    hard, _, _, details = cluster(saved['embeddings'], saved['segmentation'], transform, phi,
                                  speakers, **parameters)
    result = {}
    for name in ('plda', 'responsibilities'):
        expected, actual = saved[name], details[name]
        same_shape = expected.shape == actual.shape
        result[name] = {'passed': same_shape and bool(np.allclose(expected, actual, rtol=1e-6, atol=1e-7, equal_nan=True)),
                        'max_abs': float(np.max(np.abs(expected-actual))) if same_shape else None,
                        'shapes': [list(expected.shape), list(actual.shape)]}
    result['clusters_equal'] = bool(np.array_equal(saved['hard_clusters'], hard))
    return result


def replay(args):
    import numpy as np
    from scriba.app.engines.community import validated_files, load_plda
    if args.out.exists():
        raise FileExistsError(args.out)
    recipe, paths = validated_files(model_set(args.models))
    transform, phi = load_plda(paths['xvec_transform.npz'], paths['plda.npz'])
    source = json.loads((args.reference / 'results.json').read_text())
    manifest = json.loads(args.manifest.read_text())
    known = {item['id']: item['speakers'] for item in manifest['meetings']}
    rows = []
    for record in source['runs']:
        with np.load(args.reference / record['path'] / 'intermediates.npz', allow_pickle=False) as saved:
            row = clustering_control(saved, transform, phi, known[record['id']] if record['mode'] == 'known' else None,
                                     recipe['parameters']['clustering'])
        rows.append({'id': record['id'], 'mode': record['mode'], **row})
    passed = bool(rows) and all(row['plda']['passed'] and row['responsibilities']['passed'] for row in rows)
    write_json(args.out, {'passed': passed, 'recipe_id': recipe['recipe_id'], 'runs': rows,
                          'reference_results': file_record(args.reference / 'results.json'),
                          'model_manifest': file_record(args.models / 'manifest.json'),
                          'input_policy': 'exact saved reference embeddings and segmentations, no model inference',
                          'python': sys.version})
    return 0 if passed else 1


def run(args):
    import numpy as np
    from scriba.app.engines import audio
    from scriba.app.engines import community
    from scriba.app.engines import diarize as legacy
    from pyannote_load import MeasurementLoad
    if args.out.exists():
        raise FileExistsError(args.out)
    manifest = json.loads(args.manifest.read_text())
    validate_scope(manifest, getattr(args, 'suite', 'all'))
    if args.engine == 'community':
        models = model_set(args.models)
        # Fail before allocating outputs or silently accepting a failed export.
        community.validated_files(models)
    else:
        models = None
        from scriba.app import models as catalogue
        for model in (catalogue.SILERO_VAD, catalogue.WESPEAKER):
            verify_record(args.models / model.filename, {'bytes': model.size, 'sha256': model.sha256})
    args.out.mkdir(parents=True, exist_ok=False)
    items = corpus_items(manifest, args.suite) if args.suite != 'stress' else [
        {'id': 'stress-2h', 'wav': manifest['stress']['wav'], 'duration': 7200,
         'audio': {key: manifest['stress'][key] for key in ('bytes', 'sha256')}}]
    runs = []
    failed = False
    for item in items:
        verify_record(args.manifest.parent / item['wav'], item['audio'])
        samples = audio.read(args.manifest.parent / item['wav'])
        if args.suite == 'brief':
            samples = samples[:600 * 16000]
        modes = [('auto', None), ('known', item['speakers'])] if args.suite != 'stress' else [
            *([('cold', None)] if args.cold_run else []),
            *[(f'warm-{i+1}', None) for i in range(args.repeats)]]
        for mode, speakers in modes:
            name = item['id'] + '-' + mode
            print(args.engine + ' ' + name, flush=True)
            output = args.out / name
            output.mkdir()
            # Release the preceding run before measuring a new one. In particular,
            # do not keep its full intermediate arrays alive during the stress run.
            result, intermediates = None, None
            with MeasurementLoad() as load:
                start = time.perf_counter()
                if args.engine == 'community':
                    def progress(done, total):
                        if done % 100 == 0 or done == total:
                            print(f'  window passes: {done}/{total}', flush=True)
                    result = community.diarize(samples, models, speakers=speakers, threads=args.threads, progress=progress)
                    exclusive, intermediates, phases = result.exclusive_turns, result.intermediates, result.phase_seconds
                else:
                    result = legacy.diarize(samples, args.models / catalogue.SILERO_VAD.filename,
                                            args.models / catalogue.WESPEAKER.filename, speakers=speakers, threads=args.threads)
                    exclusive, intermediates, phases = result.turns, {}, {}
                elapsed = time.perf_counter() - start
            rttm(output / 'normal.rttm', item['id'], result.turns)
            rttm(output / 'exclusive.rttm', item['id'], exclusive)
            np.savez(output / 'intermediates.npz', **intermediates)
            record = {'id': item['id'], 'mode': mode, 'path': name, 'seconds': elapsed,
                      'phase_seconds': phases, 'peak_rss_mib': peak_rss(), 'speakers': result.speakers,
                      'duration': len(samples) / 16000, 'load': load.report}
            write_json(output / 'measurement.json', record)
            runs.append(record)
    report = {'engine': args.engine, 'runs': runs, 'threads': args.threads,
              'platform': platform.platform(), 'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
              'python': sys.version, 'blas': {k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
              'model_manifest': file_record(args.models / 'manifest.json') if models else None}
    if args.suite == 'stress':
        warm = [r['seconds'] for r in runs if r['mode'].startswith('warm-')]
        report['median_warm_seconds'] = statistics.median(warm)
        report['max_seconds'] = max(r['seconds'] for r in runs)
        report['peak_rss_mib'] = max(r['peak_rss_mib'] for r in runs)
        failed = report['median_warm_seconds'] > args.max_seconds or report['peak_rss_mib'] > args.max_rss_mib
        report['passed'] = not failed
    write_json(args.out / 'results.json', report)
    return 1 if failed else 0


def forbidden(name):
    import re
    lower = name.lower().replace('\\', '/')
    # SciPy/sklearn vendor small array-API adapters for optional backends.
    # These are their own Python code, not a Torch distribution or library.
    # Keep them visible in the inventory, while still rejecting torch itself.
    lower = re.sub(r'(scipy[/.](?:_external|_lib)|sklearn[/.]externals)[/.]array_api_compat[/.]torch(?=[/._]|$)',
                   r'\1.array_api_compat.adapter', lower)
    return bool(re.search(r'(^|[/._-])(torch|torchaudio|torchcodec|libtorch|huggingface_hub|huggingface-hub|pyannote[/.]audio|directml|onnxruntime_providers_dml)([/._-]|$)', lower)
                or lower.endswith('.bin'))


def audit_bundle(args):
    """Inspect file names and embedded zip inventories, including Python wheels."""
    if args.out.exists():
        raise FileExistsError(args.out)
    if not args.bundle.exists():
        raise FileNotFoundError(args.bundle)
    names, failures = [], []

    def scan(archive, prefix, depth=0):
        if depth > 4:
            failures.append(prefix + ': nested archive depth exceeded')
            return
        for info in archive.infolist():
            name = prefix + '!' + info.filename
            names.append(name)
            if forbidden(info.filename):
                failures.append(name)
            if info.filename.lower().endswith(('.zip', '.whl', '.egg')):
                if info.file_size > 256 * 1024 * 1024:
                    failures.append(name + ': nested archive too large to certify')
                    continue
                data = io.BytesIO(archive.read(info))
                if zipfile.is_zipfile(data):
                    with zipfile.ZipFile(data) as nested:
                        scan(nested, name, depth + 1)
            elif info.filename.lower().endswith(('.pyz', '.exe', '.flatpak')):
                failures.append(name + ': opaque archive')
    paths = [args.bundle] if args.bundle.is_file() else sorted(p for p in args.bundle.rglob('*') if p.is_file())
    for path in paths:
        name = path.name if args.bundle.is_file() else path.relative_to(args.bundle).as_posix()
        names.append(name)
        if forbidden(name):
            failures.append(name)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                scan(archive, name)
        elif path.suffix.lower() == '.exe':
            try:
                from PyInstaller.archive.readers import CArchiveReader, ArchiveReadError
                try:
                    packed = CArchiveReader(str(path))
                except ArchiveReadError:
                    packed = None
                if packed is not None:
                    for member, entry in packed.toc.items():
                        names.append(name + '!' + member)
                        if forbidden(member):
                            failures.append(name + '!' + member)
                        if entry[-1] == 'z':
                            pyz = packed.open_embedded_archive(member)
                            for module in pyz.toc:
                                names.append(name + '!' + member + '!' + module)
                                if forbidden(module):
                                    failures.append(name + '!' + member + '!' + module)
                else:
                    # These are native engines; they cannot contain a Python
                    # module archive. Unknown PE inputs remain uncertified.
                    if path.stem.lower() not in ('ffmpeg','ffprobe','whisper-cli','whisper-bench','gdbus','gst-plugin-scanner','gspawn-win64-helper','gspawn-win64-helper-console'):
                        failures.append(name + ': unknown native executable')
            except (ImportError, OSError, ValueError) as error:
                failures.append(name + ': executable inventory unavailable: ' + str(error))
        elif path.suffix.lower() in ('.pyz', '.flatpak'):
            failures.append(name + ': opaque archive, unpack inventory first')
    lower = '\n'.join(names).lower()
    if 'onnxruntime' not in lower:
        failures.append('ONNX runtime missing')
    for notice in ('pyannote', 'vbx', 'apache', 'mit'):
        if not any(notice in name.lower() and ('license' in name.lower() or 'notice' in name.lower()) for name in names):
            failures.append('Missing notice: ' + notice)
    write_json(args.out, {'passed': not failures, 'inventory': names, 'failures': failures})
    return 1 if failures else 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest='command', required=True)
    p = commands.add_parser('run')
    p.add_argument('--engine', choices=['legacy', 'community'], required=True)
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--suite', choices=['dev', 'test', 'all', 'brief', 'stress'], required=True)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--cold-run', action='store_true')
    p.add_argument('--max-seconds', type=float, default=900)
    p.add_argument('--max-rss-mib', type=float, default=2048)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=run)
    p = commands.add_parser('audit-bundle')
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=audit_bundle)
    p = commands.add_parser('replay')
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--models', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.set_defaults(function=replay)
    return result


def main():
    args = parser().parse_args()
    try:
        return args.function(args)
    except (OSError, ValueError, ImportError, KeyError) as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
