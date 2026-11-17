"""Experimental community-1 runtime. No download or fallback is performed.

Only an export that passed its numeric gates can be opened. Integration into
jobs and preferences is intentionally deferred until the independent gates pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time

from scriba.app.task import check, tell
from scriba.core.segments import Turn


@dataclass
class CommunityResult:
    turns: list[Turn]
    exclusive_turns: list[Turn]
    speakers: int
    windows: int
    recipe_id: str
    phase_seconds: dict = field(default_factory=dict)
    intermediates: dict = field(default_factory=dict, repr=False)
    centroids: dict = field(default_factory=dict)


def validate_recipe(recipe):
    if recipe.get('export_verified') is not True:
        raise ValueError('Community export has not passed numerical verification')
    expected = {'segmentation.onnx', 'embedding.onnx', 'plda.npz', 'xvec_transform.npz'}
    if set(recipe.get('files', {})) != expected:
        raise ValueError('Incomplete community model set')
    if recipe.get('opset') != 17 or recipe.get('precision') != 'float32':
        raise ValueError('Unsupported community recipe')
    if recipe.get('segmentation', {}).get('output_shape') != [1, 589, 7]:
        raise ValueError('Incompatible segmentation contract')
    if recipe.get('embedding', {}).get('output_shape') != [1, 3, 256]:
        raise ValueError('Incompatible embedding contract')
    body = {key: value for key, value in recipe.items() if key != 'recipe_id'}
    if recipe.get('recipe_id') != hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest():
        raise ValueError('Recipe identity differs')


def validated_files(model_files):
    recipe = model_files['recipe']
    validate_recipe(recipe)
    paths = {}
    for name, record in recipe['files'].items():
        path = Path(model_files[name])
        if path.stat().st_size != record['bytes']:
            raise ValueError(f'Model size differs: {name}')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != record['sha256']:
            raise ValueError(f'Model hash differs: {name}')
        paths[name] = path
    return recipe, paths


def load_plda(transform_path, plda_path):
    import numpy as np
    for path, keys in ((transform_path, {'mean1', 'mean2', 'lda'}), (plda_path, {'mu', 'tr', 'psi'})):
        with np.load(path, allow_pickle=False) as arrays:
            if set(arrays.files) != keys:
                raise ValueError('Unexpected PLDA keys')
            if any(not np.issubdtype(arrays[k].dtype, np.number) or not np.isfinite(arrays[k]).all() for k in keys):
                raise ValueError('Nonfinite or nonnumeric PLDA')
            if keys == {'mu', 'tr', 'psi'}:
                if arrays['tr'].shape != (128, 128) or arrays['mu'].shape != (128,) or arrays['psi'].shape != (128,):
                    raise ValueError('Unexpected PLDA dimensions')
            elif arrays['mean1'].shape != (256,) or arrays['mean2'].shape != (128,) or arrays['lda'].shape != (256, 128):
                raise ValueError('Unexpected xvector dimensions')
    from .vbx import vbx_setup
    xvec, plda, phi = vbx_setup(transform_path, plda_path)
    return lambda embeddings: plda(xvec(embeddings), lda_dim=128), phi[:128]


def diarize(samples, model_files, speakers=None, threads=4, progress=None, cancelled=None, session_factory=None, segmentation_batch=1):
    check(cancelled)
    import numpy as np
    from . import community_math as m, onnx
    from .diarize import fbank
    if samples.dtype != np.int16 or samples.ndim != 1:
        raise ValueError('Expected mono int16 samples at 16 kHz')
    if speakers is not None and (isinstance(speakers, bool) or not isinstance(speakers, int) or speakers < 1):
        raise ValueError('Expected positive integer speaker count')
    recipe, paths = validated_files(model_files)
    if not len(samples):
        return CommunityResult([], [], 0, 0, recipe['recipe_id'])
    factory = session_factory or onnx.Session
    starts = m.chunk_starts(len(samples), 160000, round(recipe['step'] * 16000))
    phases = {}
    begin = time.perf_counter()
    scores = np.empty((len(starts), 589, 7), np.float32)
    with factory(paths['segmentation.onnx'], threads=threads) as session:
        for i in range(0, len(starts), segmentation_batch):
            check(cancelled)
            batch = []
            for at in starts[i:i + segmentation_batch]:
                piece = np.zeros(160000, np.float32)
                raw = samples[at:at + 160000]
                piece[:len(raw)] = raw.astype(np.float32) / 32768
                batch.append(piece)
            batch = np.stack(batch)[:, None]
            scores[i:i + len(batch)] = session.run(['scores'], {'waveform': batch})[0]
            tell(progress, i + len(batch), 2 * len(starts))
    phases['segmentation'] = time.perf_counter() - begin
    binary = m.powerset(scores)
    if not binary.any():
        return CommunityResult([], [], 0, len(starts), recipe['recipe_id'])
    vectors = np.empty((len(starts), 3, 256), np.float32)
    begin = time.perf_counter()
    with factory(paths['embedding.onnx'], threads=threads) as session:
        for i, at in enumerate(starts):
            check(cancelled)
            piece = np.zeros(160000, np.float32)
            raw = samples[at:at + 160000]
            piece[:len(raw)] = raw
            feats = fbank(piece)
            feats -= feats.mean(0, dtype=np.float32)
            masks = binary[i].T.copy()
            if recipe['exclude_overlap']:
                clean = masks * (binary[i].sum(1) < 2)[None]
                minimum = np.ceil(589 * recipe['min_num_samples'] / 160000)
                masks = np.where((clean.sum(1) > minimum)[:, None], clean, masks)
            vectors[i] = session.run(['embeddings'], {'feats': feats[None], 'weights': masks[None].astype(np.float32)})[0][0]
            tell(progress, len(starts) + i + 1, 2 * len(starts))
    phases['embedding'] = time.perf_counter() - begin
    check(cancelled)
    transform, phi = load_plda(paths['xvec_transform.npz'], paths['plda.npz'])
    params = recipe['parameters']['clustering']
    begin = time.perf_counter()
    hard, _, centroids, details = m.cluster(vectors, binary, transform, phi, speakers, **params)
    phases['clustering'] = time.perf_counter() - begin
    check(cancelled)
    hard[binary.sum(1) == 0] = -2
    begin = time.perf_counter()
    normal, exclusive = m.reconstruct(binary, hard, recipe['frame'], recipe['duration'], recipe['step'], speakers)
    turns = m.turns(normal, recipe['frame'])
    phases['reconstruction'] = time.perf_counter() - begin
    return CommunityResult(turns, m.turns(exclusive, recipe['frame']), len({t.label for t in turns}),
                           len(starts), recipe['recipe_id'], phases,
                           {'scores': scores, 'segmentation': binary, 'embeddings': vectors,
                            'hard_clusters': hard, **{key: value for key, value in details.items()
                                                     if key in ('plda', 'responsibilities')}},
                           {f'SPEAKER_{i:02d}': row.tolist() for i,row in enumerate(centroids)
                            if np.isfinite(row).all()})
