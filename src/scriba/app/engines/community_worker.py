"""Private cancellable child for community inference and monolithic clustering."""
from __future__ import annotations
import json
import sys
from pathlib import Path
from dataclasses import asdict


def main(args=None):
    args = sys.argv[1:] if args is None else args
    root = Path(args[0])
    try:
        import numpy as np
        from scriba.app import models
        from . import community, webgpu
        job = json.loads((root / 'request.json').read_text(encoding='utf-8'))
        files = models.community_files(job['models'])
        provenance = {'used':'community','recipe':files['recipe']['recipe_id'],
                      'revision': files['recipe']['revision'], 'segmentation_batch':32, 'threads':8,
                      'assignment':'exclusive', 'requested':job['requested']}
        if job.get('force_cpu'):
            from . import onnx
            provenance.update(accelerator='cpu', selection='processor-requested')
            factory = onnx.Session
        else:
            factory = webgpu.session_factory(provenance)
        def progress(done,total):
            print(json.dumps([done,total]),flush=True)
        result = community.diarize(np.load(root/'samples.npy',allow_pickle=False),files,
            speakers=job['speakers'],threads=8,progress=progress,session_factory=factory,segmentation_batch=32)
        output = dict(turns=[asdict(t) for t in result.turns],
                      exclusive=[asdict(t) for t in result.exclusive_turns],centroids=result.centroids,
                      fingerprints=community.named_fingerprints(result,job.get('named',()),files['recipe']),
                      provenance=provenance,speakers=result.speakers)
        (root/'result.json').write_text(json.dumps(output,allow_nan=False),encoding='utf-8')
        return 0
    except Exception as error:
        (root/'error.json').write_text(json.dumps({'kind':type(error).__name__,'detail':str(error)}),encoding='utf-8')
        return 1


if __name__ == '__main__':
    sys.exit(main())
