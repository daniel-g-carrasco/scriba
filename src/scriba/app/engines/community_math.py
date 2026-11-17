"""Numerical community-1 operations, independent of files and model runtimes.

Adapted from pyannote.audio 4.0.0 inference, clustering and diarization.
Copyright (c) 2020- CNRS. MIT license: LICENSES/pyannote-MIT.txt.
Changes: plain arrays and explicit clocks; no Torch, pyannote or file lookup.
"""
from __future__ import annotations


def chunk_starts(length, window, step):
    if length < 0 or window <= 0 or step <= 0:
        raise ValueError('Invalid sample count or window')
    complete = max(0, 1 + (length - window) // step)
    starts = list(range(0, complete * step, step))
    if length < window or (length - window) % step:
        starts.append(complete * step)
    return starts


def powerset(scores):
    import numpy as np
    mapping = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                        [1, 1, 0], [1, 0, 1], [0, 1, 1]], np.float32)
    if scores.shape[-1] != 7:
        raise ValueError('Expected seven powerset scores')
    return mapping[scores.argmax(-1)]


def stats_pool(sequences, weights):
    import numpy as np
    frames = sequences.shape[-1]
    indices = np.floor(np.arange(frames) * weights.shape[-1] / frames).astype(int)
    masks = weights[..., indices]
    out = []
    for weight in masks.transpose(1, 0, 2):
        weight = weight[:, None]
        v1 = weight.sum(2) + 1e-8
        mean = (sequences * weight).sum(2) / v1
        v2 = (weight ** 2).sum(2)
        var = (((sequences - mean[..., None]) ** 2) * weight).sum(2) / (v1 - v2 / v1 + 1e-8)
        out.append(np.concatenate([mean, np.sqrt(var)], 1))
    return np.stack(out, 1)


def aggregate(scores, frame, duration, step, average):
    import numpy as np
    chunks, count, classes = scores.shape
    size = int(np.rint((duration + (chunks - 1) * step) / frame['step'])) + 1
    summed = np.zeros((size, classes), np.float32)
    weights = np.zeros_like(summed)
    for i, chunk in enumerate(scores):
        at = int(np.rint(i * step / frame['step']))
        mask = ~np.isnan(chunk)
        summed[at:at + count] += np.nan_to_num(chunk, nan=0.) * mask
        weights[at:at + count] += mask
    return summed / np.maximum(weights, 1e-12) if average else summed


def reconstruct(segmentations, clusters, frame, duration, step, max_speakers=None):
    import numpy as np
    chunks, frames, _ = segmentations.shape
    count = np.rint(aggregate(segmentations.sum(-1, keepdims=True), frame, duration, step, True)).astype(np.uint8)
    if max_speakers is not None:
        count = np.minimum(count, max_speakers)
    total = int(np.max(clusters)) + 1
    grouped = np.full((chunks, frames, max(0, total)), np.nan)
    for i, labels in enumerate(clusters):
        for label in np.unique(labels):
            if label >= 0:
                grouped[i, :, label] = segmentations[i, :, labels == label].max(0)
    activations = aggregate(grouped, frame, duration, step, False)
    if activations.shape[-1] < count.max():
        activations = np.pad(activations, ((0, 0), (0, int(count.max()) - activations.shape[-1])))
    order = np.argsort(-activations, axis=-1)
    normal, exclusive = np.zeros_like(activations), np.zeros_like(activations)
    for i, n in enumerate(count[:, 0]):
        normal[i, order[i, :n]] = 1
        exclusive[i, order[i, :min(n, 1)]] = 1
    return normal, exclusive


def cluster(embeddings, segmentations, transform, phi, speakers, threshold=.6, Fa=.07, Fb=.8):
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.optimize import linear_sum_assignment
    from scipy.spatial.distance import cdist
    from .vbx import cluster_vbx
    clean = (segmentations.sum(2, keepdims=True) == 1) * segmentations
    active = clean.sum(1) >= .2 * segmentations.shape[1]
    valid = active & ~np.isnan(embeddings).any(2)
    train = embeddings[valid]
    chunks, local, dimension = embeddings.shape
    if len(train) < 2:
        return (np.zeros((chunks, local), np.int8), np.ones((chunks, local, 1)),
                np.mean(train, axis=0, keepdims=True), {'valid': len(train)})
    normed = train / np.linalg.norm(train, axis=1, keepdims=True)
    ahc = fcluster(linkage(normed, method='centroid', metric='euclidean'), threshold, criterion='distance') - 1
    _, ahc = np.unique(ahc, return_inverse=True)
    features = transform(train)
    q, priors = cluster_vbx(ahc, features, phi, Fa=Fa, Fb=Fb, maxIters=20)
    responsibilities = q[:, priors > 1e-7]
    centroids = responsibilities.T @ train.reshape(-1, dimension) / responsibilities.sum(0, keepdims=True).T
    constrained = True
    if speakers and speakers != len(centroids):
        from sklearn.cluster import KMeans
        if speakers > len(train):
            raise ValueError('Requested speakers exceed valid embeddings; reference KMeans cannot fit')
        labels = KMeans(n_clusters=speakers, n_init=3, random_state=42, copy_x=False).fit_predict(normed)
        centroids = np.vstack([train[labels == k].mean(0) for k in range(speakers)])
        constrained = False
    soft = 2 - cdist(embeddings.reshape(-1, dimension), centroids, metric='cosine').reshape(chunks, local, -1)
    if constrained:
        soft[segmentations.sum(1) == 0] = soft.min() - 1
        cost = np.nan_to_num(soft, nan=np.nanmin(soft))
        hard = np.full((chunks, local), -2, np.int8)
        for i, score in enumerate(cost):
            rows, cols = linear_sum_assignment(score, maximize=True)
            hard[i, rows] = cols
    else:
        hard = soft.argmax(2)
    return hard, soft, centroids, {'valid': len(train), 'plda': features, 'responsibilities': q, 'priors': priors, 'ahc': ahc}


def turns(binary, frame):
    from scriba.core.segments import Turn
    result = []
    for label, activity in enumerate(binary.T):
        start = None
        for i, active in enumerate(activity):
            left = frame['start'] + i * frame['step']
            at = .5 * (left + (left + frame['duration']))
            if active and start is None:
                start = at
            elif not active and start is not None:
                if at > start:
                    result.append(Turn(start, at, f'SPEAKER_{label:02d}'))
                start = None
        if start is not None and at > start:
            result.append(Turn(start, at, f'SPEAKER_{label:02d}'))
    return sorted(result, key=lambda turn: (turn.start, turn.end, turn.label))
