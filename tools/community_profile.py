"""Read-only public-audio profiling; no changes to the production runtime."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


class Timings:
    def __init__(self):
        self.seconds = defaultdict(float)
        self.calls = defaultdict(int)

    def wrap(self, name, function):
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.seconds[name] += time.perf_counter() - start
                self.calls[name] += 1
        return measured


def run(args):
    from scriba.app.engines import audio, community, community_math, diarize, onnx
    from pyannote_benchmark import model_set, peak_rss, rttm
    from pyannote_reference import write_json, file_record
    from pyannote_load import MeasurementLoad
    args.out.mkdir(parents=True, exist_ok=False)
    times = Timings()
    base = onnx.Session

    class MeasuredSession:
        def __init__(self, model, threads=4):
            self.kind = Path(model).stem
            self.session = times.wrap(self.kind + '_init', base)(model, threads=threads)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.session.close()

        def run(self, *a, **kw):
            return times.wrap(self.kind + '_inference', self.session.run)(*a, **kw)

    models = model_set(args.models)
    with MeasurementLoad() as load, ExitStack() as stack:
        for module, name, key in ((diarize, 'fbank', 'fbank'), (community_math, 'cluster', 'plda_vbx'),
                                  (community_math, 'reconstruct', 'reconstruction'),
                                  (community, 'validated_files', 'validate'), (audio, 'read', 'decode')):
            stack.enter_context(patch.object(module, name, times.wrap(key, getattr(module, name))))
        stack.enter_context(patch.object(onnx, 'Session', MeasuredSession))
        start = time.perf_counter()
        samples = audio.read(args.wav)
        if args.seconds:
            samples = samples[:int(args.seconds * 16000)]

        def progress(done, total):
            if done % 100 == 0 or done == total:
                print(done, total, round(time.perf_counter() - start, 3), flush=True)

        result = community.diarize(samples, models, threads=args.threads, progress=progress)
        elapsed = time.perf_counter() - start
    rttm(args.out / 'normal.rttm', args.wav.stem, result.turns)
    rttm(args.out / 'exclusive.rttm', args.wav.stem, result.exclusive_turns)
    write_json(args.out / 'profile.json', dict(seconds=elapsed, phases=dict(times.seconds),
               calls=dict(times.calls), coarse=result.phase_seconds, load=load.report,
               duration=len(samples) / 16000, threads=args.threads, peak_rss_mib=peak_rss(),
               wav=file_record(args.wav), models=file_record(args.models / 'manifest.json')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wav', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--seconds', type=float)
    run(parser.parse_args())
