"""Download and run the production voice set in a fresh, private test cache.

Only public test audio should be supplied. No installer or user profile is used.
The normal and exclusive RTTM must match the supplied reference byte for byte.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    for key in tuple(os.environ):
        if key.startswith('SCRIBA_DEV_COMMUNITY'):
            os.environ.pop(key)
    for key, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache'),
                      ('LOCALAPPDATA', 'local'), ('APPDATA', 'roaming'),
                      ('MESA_SHADER_CACHE_DIR', 'mesa'), ('__GL_SHADER_DISK_CACHE_PATH', 'nvidia'),
                      ('TEMP', 'tmp'), ('TMP', 'tmp')]:
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(folder)
    os.environ.update(SCRIBA_MODELS_DIR=str(out / 'models'),
                      SCRIBA_COMMUNITY_CACHE=str(out / 'worker-cache'),
                      SCRIBA_COMMUNITY_RUNTIME=str(args.runtime.resolve()),
                      OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1',
                      PYTHONDONTWRITEBYTECODE='1')
    import tempfile
    tempfile.tempdir = str(out / 'tmp')
    from scriba.app import models, voices
    from scriba.app.backend import Backend
    from scriba.app.config import Config
    from scriba.app.model_downloads import ModelDownloads
    from scriba.app.engines import audio
    from pyannote_benchmark import rttm

    spec = models.community_set()
    cache = out / 'models'
    backend = Backend(cache, None)
    downloads = ModelDownloads(backend, Config(), lambda: None,
                               dispatch=lambda f: f(), required=spec.models)
    started = time.perf_counter()
    downloads.start_required()
    try:
        if not downloads.wait(300) or downloads.state.phase != 'ready':
            raise RuntimeError('Production download failed: ' + str(downloads.state))
    finally:
        downloads.shutdown()
        downloads.wait(35)
    report = dict(download_seconds=time.perf_counter() - started,
                  downloaded_bytes=sum(m.size for m in spec.models),
                  active=models.set_ready(spec, cache),
                  selected=voices.select('auto', cache)[0], files=[])
    for model in spec.models:
        path = cache / model.filename
        report['files'].append(dict(url=model.url, bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), verified=models.verify(model, cache)))
    # Write the successful network gate before running inference, preserving it on failure.
    (out / 'download.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['active'] or report['selected'] != 'community' or not all(f['verified'] for f in report['files']):
        raise RuntimeError('The downloaded set was not verified and selected')
    print('Downloaded, verified and activated:', report['downloaded_bytes'], 'bytes', flush=True)
    samples = audio.read(args.wav)[:600 * 16000]
    if len(samples) != 600 * 16000:
        raise ValueError('At least 600 seconds of public test audio are required')
    report.update(pcm_sha256=hashlib.sha256(samples.tobytes()).hexdigest(), duration=600)
    started = time.perf_counter()
    result = voices.run(samples, cache, requested='auto')
    report.update(inference_seconds=time.perf_counter() - started,
                  provenance=result['provenance'], speakers=result['speakers'])
    for name, key in [('normal', 'turns'), ('exclusive', 'exclusive')]:
        target, reference = out / (name + '.rttm'), args.reference / (name + '.rttm')
        rttm(target, 'stress-2h', result[key])
        report[name + '_identical'] = target.read_bytes() == reference.read_bytes()
        report[name + '_reference_sha256'] = hashlib.sha256(reference.read_bytes()).hexdigest()
        result[key] = [asdict(turn) for turn in result[key]]
    report['passed'] = report['normal_identical'] and report['exclusive_identical']
    (out / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    (out / 'measurement.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
