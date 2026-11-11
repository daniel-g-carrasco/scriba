# Copied from diarize 0.1.2 by Alexander Lukashov (FoxNoseTech),
# https://github.com/FoxNoseTech/diarize, under the Apache License 2.0
# (the licence's text: LICENSES/diarize-Apache-2.0.txt).
#
# What is here: diarize/clustering.py whole, and the assembly of the
# segments from diarize/__init__.py (_build_diarization_segments and its
# helpers). Changed by Scriba (11/11/2026): pydantic's models replaced by
# dataclasses with the same fields (pydantic is not in MSYS2); the
# `except Exception` around a GMM fit narrowed to ValueError, which is what
# scikit-learn raises for a mixture that cannot be fitted (numpy's
# LinAlgError is one), so that any other error is said instead of skipped;
# every long dash (U+2014) of its comments and log lines turned into a colon
# (the house rule); `strict=False` written out in three zip() calls, which
# is what zip() does anyway (the linter asks for it said); this header.
# Nothing else: the numbers must stay diarize's, and on the
# test clip they do (4 voices, the same 58 segments).
#
# The heavy imports stay at the top as diarize has them: this module is
# imported only inside the functions of diarize.py.
"""Speaker clustering: GMM BIC for speaker count estimation + Spectral Clustering.

The module provides two main capabilities:

1. **Speaker count estimation**: :func:`estimate_speakers` uses Gaussian
   Mixture Models with the Bayesian Information Criterion (BIC) to
   automatically determine how many speakers are present.
2. **Spectral clustering**: :func:`cluster_spectral` groups embedding
   vectors into *k* clusters using cosine-similarity affinity.

High-level convenience wrappers :func:`cluster_auto` and
:func:`cluster_speakers` combine both steps.
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.cluster import SpectralClustering
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import normalize

from dataclasses import dataclass, field


@dataclass
class SpeakerEstimationDetails:
    best_k: int
    method: str = "gmm_bic"
    pca_dim: int | None = None
    k_bics: dict = field(default_factory=dict)
    reason: str | None = None
    cosine_sim_p10: float | None = None

@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    speaker: str
    @property
    def duration(self): return self.end - self.start

@dataclass
class SpeechSegment:
    start: float
    end: float
    @property
    def duration(self): return self.end - self.start

@dataclass
class SubSegment:
    start: float
    end: float
    parent_idx: int

logger = logging.getLogger(__name__)

_SILHOUETTE_K_BONUS = 0.04

__all__ = [
    "estimate_speakers",
    "cluster_spectral",
    "cluster_auto",
    "cluster_speakers",
]


# ── Speaker count estimation ─────────────────────────────────────────────────


def estimate_speakers(
    embeddings: np.ndarray,
    min_k: int = 1,
    max_k: int = 20,
) -> tuple[int, SpeakerEstimationDetails]:
    """Estimate the number of speakers using GMM BIC.

    Algorithm:

    1. L2-normalise embeddings.
    2. PCA projection to 8 dimensions (optimal for GMM with full covariance).
    3. For each *k* from *min_k* to *max_k*, fit
       ``GaussianMixture(k, covariance_type="full")``.
    4. Select *k* with the minimum BIC.

    Args:
        embeddings: Speaker embeddings of shape ``(N, D)``.
        min_k: Minimum number of speakers to consider.
        max_k: Maximum number of speakers to consider.

    Returns:
        A ``(best_k, details)`` tuple where *best_k* is the estimated
        speaker count and *details* is a
        :class:`~diarize.utils.SpeakerEstimationDetails` instance with
        diagnostic information.

    Example::

        k, details = estimate_speakers(embeddings, min_k=1, max_k=10)
        print(f"Estimated {k} speakers (PCA dim={details.pca_dim})")
    """
    n = embeddings.shape[0]

    if n == 0:
        return max(1, min_k), SpeakerEstimationDetails(
            method="gmm_bic",
            best_k=max(1, min_k),
            reason="no_embeddings",
        )

    emb = normalize(embeddings, norm="l2")

    if n < 4:
        return max(1, min_k), SpeakerEstimationDetails(
            method="gmm_bic",
            best_k=max(1, min_k),
            reason="too_few_samples",
        )

    # ── Single-speaker pre-check (cosine similarity) ─────────────────────
    # If the 10th percentile of pairwise cosine similarities is high,
    # all embeddings likely belong to a single speaker.  This catches
    # cases where BIC overfits by splitting one speaker's irregular
    # embedding distribution into multiple Gaussians.
    _single_speaker_sim_p10: float = 0.16
    sim_matrix = cosine_similarity(emb)  # emb is already L2-normalised
    mask = ~np.eye(n, dtype=bool)
    sim_p10 = float(np.percentile(sim_matrix[mask], 10))

    if sim_p10 >= _single_speaker_sim_p10 and min_k <= 1:
        logger.info(
            "Cosine similarity p10=%.3f >= %.2f: single speaker detected",
            sim_p10,
            _single_speaker_sim_p10,
        )
        return 1, SpeakerEstimationDetails(
            method="gmm_bic",
            best_k=1,
            reason="cosine_similarity_single_speaker",
            cosine_sim_p10=round(sim_p10, 4),
        )

    # ── Parameters ────────────────────────────────────────────────────────
    n_pca: int = 8  # PCA dimensions (optimal for GMM full cov)
    gmm_n_init: int = 5  # number of GMM initialisations
    gmm_max_iter: int = 300  # max EM iterations

    # ── PCA projection ────────────────────────────────────────────────────
    actual_pca = min(n_pca, n - 1, emb.shape[1])
    emb_pca = PCA(n_components=actual_pca, random_state=42).fit_transform(emb)

    # ── Sweep k and compute BIC ───────────────────────────────────────────
    # Upper bound: at most n // 2 components (need ≥2 samples per cluster
    # on average), but never more than max_k.
    k_upper = max(min_k + 1, min(max_k + 1, n // 2 + 1))
    k_range = range(min_k, k_upper)
    k_to_bic: dict[int, float] = {}

    for k in k_range:
        try:
            gmm = GaussianMixture(
                n_components=k,
                covariance_type="full",
                random_state=42,
                n_init=gmm_n_init,
                max_iter=gmm_max_iter,
            )
            gmm.fit(emb_pca)
            bic = gmm.bic(emb_pca)
            k_to_bic[k] = bic

            delta_str = ""
            if k > min_k and (k - 1) in k_to_bic:
                delta = bic - k_to_bic[k - 1]
                delta_str = f"  delta={delta:+.0f}"
            logger.debug("k=%d: BIC=%.0f%s", k, bic, delta_str)
        except ValueError:
            logger.debug("GMM failed for k=%d, skipping", k)
            continue

    if not k_to_bic:
        return min_k, SpeakerEstimationDetails(
            method="gmm_bic",
            best_k=min_k,
            reason="gmm_failed",
        )

    # ── Select optimal k ──────────────────────────────────────────────────
    best_k = min(k_to_bic, key=k_to_bic.get)  # type: ignore[arg-type]

    details = SpeakerEstimationDetails(
        method="gmm_bic",
        best_k=best_k,
        pca_dim=actual_pca,
        k_bics={k: round(b, 1) for k, b in sorted(k_to_bic.items())},
    )

    logger.info("GMM BIC (PCA=%d) -> k=%d", actual_pca, best_k)
    return best_k, details


# ── Spectral Clustering ──────────────────────────────────────────────────────


def _refine_labels_spherical(
    embeddings: np.ndarray,
    labels: np.ndarray,
    *,
    max_iter: int = 8,
) -> np.ndarray:
    """Refine cluster labels with spherical centroid reassignment."""
    n = len(embeddings)
    if n == 0:
        return labels

    unique_labels = sorted({int(label) for label in labels})
    k = len(unique_labels)
    if k <= 1:
        return np.zeros(n, dtype=int)

    label_map = {label: idx for idx, label in enumerate(unique_labels)}
    refined = np.array([label_map[int(label)] for label in labels], dtype=int)
    emb = normalize(embeddings, norm="l2")

    for _ in range(max_iter):
        centroids = np.zeros((k, emb.shape[1]), dtype=float)
        valid = np.zeros(k, dtype=bool)
        for label in range(k):
            members = emb[refined == label]
            if len(members) == 0:  # pragma: no cover - defensive against future label changes.
                continue
            centroid = members.mean(axis=0)
            norm = float(np.linalg.norm(centroid))
            if norm == 0:
                continue
            centroids[label] = centroid / norm
            valid[label] = True

        if not np.all(valid):
            break

        scores = emb @ centroids.T
        updated = np.argmax(scores, axis=1)
        if len(set(updated)) < k:
            break
        if np.array_equal(updated, refined):
            break
        refined = updated

    return refined


def cluster_spectral(embeddings: np.ndarray, k: int) -> np.ndarray:
    """Cluster embeddings into *k* speakers using Spectral Clustering.

    Uses cosine similarity as the affinity metric, rescaled to [0, 1].
    The spectral assignment is then refined with a few spherical
    centroid-reassignment iterations, which reduces noisy window labels
    while preserving the selected number of speakers.

    Args:
        embeddings: Speaker embeddings of shape ``(N, D)``.
        k: Number of clusters (speakers).

    Returns:
        Integer label array of shape ``(N,)``.

    Example::

        labels = cluster_spectral(embeddings, k=3)
        print(set(labels))  # {0, 1, 2}
    """
    n = len(embeddings)
    if n == 0:
        return np.array([], dtype=int)

    # Clamp k to the number of available embeddings
    k = min(k, n)

    if k == 1:
        return np.zeros(n, dtype=int)

    affinity = (cosine_similarity(embeddings) + 1) / 2
    np.fill_diagonal(affinity, 1.0)
    affinity = np.maximum(affinity, 0)

    sc = SpectralClustering(
        n_clusters=k,
        affinity="precomputed",
        assign_labels="kmeans",
        random_state=42,
        n_init=10,
    )
    labels: np.ndarray = sc.fit_predict(affinity)
    labels = _refine_labels_spherical(embeddings, labels)
    logger.debug("Spectral clustering: %d clusters", k)
    return labels


# ── High-level wrappers ──────────────────────────────────────────────────────


def _silhouette_candidate_counts(
    k: int,
    n_embeddings: int,
    min_speakers: int,
    max_speakers: int,
) -> list[int]:
    """Return valid speaker counts for silhouette refinement."""
    if k < 2 or n_embeddings < 4:
        return []

    lower = max(2, min_speakers, k - 2)
    upper = min(max_speakers, n_embeddings - 1, k + 3)
    if upper < lower:
        return []
    return list(range(lower, upper + 1))


def _speaker_count_score(silhouette: float, k: int) -> float:
    """Score speaker-count candidates from silhouette plus a small k prior.

    Raw silhouette tends to prefer fewer, broader clusters on noisy
    speaker embeddings. The logarithmic k bonus counteracts that bias
    without overwhelming the separation score.
    """
    return silhouette + _SILHOUETTE_K_BONUS * np.log(max(k, 1))


def cluster_auto(
    embeddings: np.ndarray,
    min_speakers: int = 1,
    max_speakers: int = 20,
) -> tuple[np.ndarray, SpeakerEstimationDetails]:
    """Automatically determine speaker count and cluster embeddings.

    Combines :func:`estimate_speakers` and :func:`cluster_spectral`
    in a single call.

    Args:
        embeddings: Speaker embeddings of shape ``(N, D)``.
        min_speakers: Minimum number of speakers.
        max_speakers: Maximum number of speakers.

    Returns:
        A ``(labels, details)`` tuple where *labels* is an integer array
        of shape ``(N,)`` and *details* is
        :class:`~diarize.utils.SpeakerEstimationDetails`.
    """
    k, details = estimate_speakers(embeddings, min_speakers, max_speakers)
    n = len(embeddings)

    # Silhouette refinement: use BIC as an anchor, then score a small
    # neighbourhood around it. This catches both undercounts and overcounts.
    if k >= 2:
        candidates = _silhouette_candidate_counts(k, n, min_speakers, max_speakers)
        if len(candidates) > 1:
            distance = np.maximum(1 - (cosine_similarity(embeddings) + 1) / 2, 0)
            best_k, best_labels, best_score = k, None, -1.0
            for c in candidates:
                labels_c = cluster_spectral(embeddings, c)
                sil = silhouette_score(distance, labels_c, metric="precomputed")
                score = _speaker_count_score(sil, c)
                logger.debug(
                    "Silhouette refinement: k=%d  sil=%.4f  score=%.4f",
                    c,
                    sil,
                    score,
                )
                if score > best_score:
                    best_k, best_labels, best_score = c, labels_c, score
            if best_k != k:
                logger.info(
                    "Silhouette refinement: BIC k=%d -> k=%d (score=%.4f)",
                    k,
                    best_k,
                    best_score,
                )
                details.best_k = best_k
            return best_labels, details  # type: ignore[return-value]

    logger.info("Estimated %d speakers (auto: GMM BIC)", k)
    return cluster_spectral(embeddings, k), details


def cluster_speakers(
    embeddings: np.ndarray,
    min_speakers: int = 1,
    max_speakers: int = 20,
    num_speakers: int | None = None,
) -> tuple[np.ndarray, SpeakerEstimationDetails | None]:
    """Cluster speaker embeddings into groups.

    If *num_speakers* is provided, uses that exact number.  Otherwise
    automatically estimates the number of speakers via GMM BIC.

    Args:
        embeddings: Speaker embeddings of shape ``(N, D)``.
        min_speakers: Minimum number of speakers for auto-detection.
        max_speakers: Maximum number of speakers for auto-detection.
        num_speakers: If set, skip auto-detection and use this exact number.

    Returns:
        A ``(labels, details)`` tuple.  *details* is ``None`` when
        *num_speakers* is explicitly provided (no estimation performed).

    Example::

        labels, details = cluster_speakers(embeddings, num_speakers=3)
        # or
        labels, details = cluster_speakers(embeddings, min_speakers=2, max_speakers=10)
    """
    # ── Input validation ─────────────────────────────────────────────────
    if min_speakers < 1:
        raise ValueError(f"min_speakers must be >= 1, got {min_speakers}")
    if max_speakers < min_speakers:
        raise ValueError(f"max_speakers ({max_speakers}) must be >= min_speakers ({min_speakers})")
    if num_speakers is not None and num_speakers < 1:
        raise ValueError(f"num_speakers must be >= 1, got {num_speakers}")

    if len(embeddings) < 2:
        return np.zeros(len(embeddings), dtype=int), None

    if num_speakers is not None:
        logger.info("Clustering with fixed num_speakers=%d", num_speakers)
        return cluster_spectral(embeddings, num_speakers), None

    return cluster_auto(embeddings, min_speakers, max_speakers)


from collections import Counter
from typing import NamedTuple

class _RawSegment(NamedTuple):
    """Intermediate segment used during diarization assembly."""

    start: float
    end: float
    speaker: str



logger = logging.getLogger(__name__)

_TEMPORAL_SWITCH_PENALTY = 0.18
_MAX_FRAGMENT_DURATION = 1.2


def _majority_label(labels: list[int]) -> int | None:
    """Return the unique majority label, or ``None`` on ties."""
    if not labels:
        return None

    counts = Counter(labels)
    best_label, best_count = counts.most_common(1)[0]
    if sum(1 for count in counts.values() if count == best_count) > 1:
        return None
    return int(best_label)


def _smooth_window_labels(labels: list[int]) -> list[int]:
    """Apply a 3-window majority filter while preserving ties."""
    if len(labels) < 3:
        return labels

    smoothed: list[int] = []
    for idx, label in enumerate(labels):
        window = labels[max(0, idx - 1) : min(len(labels), idx + 2)]
        majority = _majority_label(window)
        smoothed.append(label if majority is None else majority)

    return smoothed


def _normalize_rows(values: np.ndarray) -> np.ndarray:
    """Return row-wise L2-normalised values, preserving zero rows."""
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return np.divide(
        values,
        norms,
        out=np.zeros_like(values, dtype=float),
        where=norms > 0,
    )


def _speaker_centroids(
    embeddings: np.ndarray,
    labels: np.ndarray,
) -> tuple[list[int], np.ndarray]:
    """Build L2-normalised speaker centroids from clustered embeddings."""
    if len(embeddings) == 0 or len(embeddings) != len(labels):
        return [], np.empty((0, 0), dtype=float)

    norm_embeddings = _normalize_rows(np.asarray(embeddings, dtype=float))
    label_values = sorted({int(label) for label in labels})
    centroids: list[np.ndarray] = []
    valid_labels: list[int] = []

    for label in label_values:
        members = norm_embeddings[labels == label]
        if len(members) == 0:  # pragma: no cover - label_values are derived from labels.
            continue
        centroid = members.mean(axis=0, keepdims=True)
        centroid = _normalize_rows(centroid)[0]
        if not np.any(centroid):
            continue
        valid_labels.append(label)
        centroids.append(centroid)

    if not centroids:
        return [], np.empty((0, norm_embeddings.shape[1]), dtype=float)

    return valid_labels, np.vstack(centroids)


def _viterbi_smooth_scores(
    scores: np.ndarray,
    *,
    switch_penalty: float = _TEMPORAL_SWITCH_PENALTY,
) -> list[int]:
    """Find the best label path with a penalty for short speaker switches."""
    n_frames, n_labels = scores.shape
    if n_frames == 0:
        return []
    if n_labels <= 1:
        return [0] * n_frames

    dp = np.empty((n_frames, n_labels), dtype=float)
    back = np.zeros((n_frames, n_labels), dtype=int)
    dp[0] = scores[0]

    transition = np.full((n_labels, n_labels), -switch_penalty, dtype=float)
    np.fill_diagonal(transition, 0.0)

    for frame_idx in range(1, n_frames):
        previous = dp[frame_idx - 1][:, None] + transition
        back[frame_idx] = np.argmax(previous, axis=0)
        dp[frame_idx] = scores[frame_idx] + np.max(previous, axis=0)

    path = [int(np.argmax(dp[-1]))]
    for frame_idx in range(n_frames - 1, 0, -1):
        path.append(int(back[frame_idx, path[-1]]))
    path.reverse()
    return path


def _smooth_window_labels_temporal(
    labels: list[int],
    embeddings: np.ndarray,
    indices: list[int],
    label_values: list[int],
    centroids: np.ndarray,
) -> list[int]:
    """Smooth a single VAD segment using centroid scores and Viterbi decoding."""
    if len(labels) < 3 or len(label_values) <= 1:
        return labels

    label_to_idx = {label: idx for idx, label in enumerate(label_values)}
    norm_embeddings = _normalize_rows(np.asarray(embeddings[indices], dtype=float))
    scores = norm_embeddings @ centroids.T

    # Small anchor to the original clustering label keeps confident labels
    # stable while the transition penalty removes low-value label flicker.
    for row_idx, label in enumerate(labels):
        label_idx = label_to_idx.get(label)
        if label_idx is not None:
            scores[row_idx, label_idx] += 0.02

    path = _viterbi_smooth_scores(scores)
    return [label_values[state] for state in path]


def _collapse_short_label_islands(
    labels: list[int],
    windows: list[tuple[float, float]],
    *,
    max_duration: float = _MAX_FRAGMENT_DURATION,
) -> list[int]:
    """Collapse short A-B-A label islands inside a continuous speech segment."""
    if len(labels) < 3 or len(labels) != len(windows):
        return labels

    runs: list[tuple[int, int, int, float]] = []
    run_start = 0
    for idx in range(1, len(labels) + 1):
        if idx == len(labels) or labels[idx] != labels[run_start]:
            duration = windows[idx - 1][1] - windows[run_start][0]
            runs.append((run_start, idx, labels[run_start], duration))
            run_start = idx

    if len(runs) < 3:
        return labels

    smoothed = labels.copy()
    for run_idx in range(1, len(runs) - 1):
        start, end, label, duration = runs[run_idx]
        _, _, previous_label, _ = runs[run_idx - 1]
        _, _, next_label, _ = runs[run_idx + 1]
        if previous_label == next_label and label != previous_label and duration <= max_duration:
            smoothed[start:end] = [previous_label] * (end - start)

    return smoothed


def _restore_sustained_label_runs(
    original_labels: list[int],
    smoothed_labels: list[int],
    windows: list[tuple[float, float]],
    *,
    min_duration: float = _MAX_FRAGMENT_DURATION,
) -> list[int]:
    """Keep sustained original runs even when Viterbi prefers fewer switches."""
    if len(original_labels) != len(smoothed_labels) or len(original_labels) != len(windows):
        return smoothed_labels

    restored = smoothed_labels.copy()
    run_start = 0
    for idx in range(1, len(original_labels) + 1):
        if idx == len(original_labels) or original_labels[idx] != original_labels[run_start]:
            duration = windows[idx - 1][1] - windows[run_start][0]
            if duration > min_duration:
                restored[run_start:idx] = original_labels[run_start:idx]
            run_start = idx

    return restored


def _window_boundaries(
    speech_segment: SpeechSegment,
    segment_subsegments: list[SubSegment],
) -> list[tuple[float, float]]:
    """Convert overlapping windows into non-overlapping intervals."""
    if not segment_subsegments:
        return []

    centers = [(sub.start + sub.end) / 2 for sub in segment_subsegments]
    boundaries = [speech_segment.start]
    for left, right in zip(centers, centers[1:], strict=False):
        midpoint = (left + right) / 2
        boundaries.append(min(speech_segment.end, max(speech_segment.start, midpoint)))
    boundaries.append(speech_segment.end)

    return list(zip(boundaries[:-1], boundaries[1:], strict=False))


def _build_diarization_segments(
    speech_segments: list[SpeechSegment],
    subsegments: list[SubSegment],
    labels: np.ndarray,
    embeddings: np.ndarray | None = None,
) -> list[Segment]:
    """Assemble diarization segments from subsegments and cluster labels.

    Overlapping embedding windows are converted to a non-overlapping
    timeline and smoothed with temporal label decoding. VAD segments
    without embeddings are assigned the nearest speaker.

    Args:
        speech_segments: Original speech segments from VAD.
        subsegments: Embedding windows with parent indices.
        labels: Cluster labels aligned with *subsegments*.
        embeddings: Optional speaker embeddings aligned with *subsegments*.

    Returns:
        Merged :class:`Segment` list sorted by start time.
    """
    raw_segments: list[_RawSegment] = []
    subsegments_by_parent: dict[int, list[int]] = {}
    for idx, sub in enumerate(subsegments):
        subsegments_by_parent.setdefault(sub.parent_idx, []).append(idx)

    label_values: list[int] = []
    centroids = np.empty((0, 0), dtype=float)
    if embeddings is not None:
        label_values, centroids = _speaker_centroids(embeddings, labels)

    for parent_idx, speech_segment in enumerate(speech_segments):
        indices = subsegments_by_parent.get(parent_idx)
        if not indices:
            continue

        indices.sort(key=lambda idx: subsegments[idx].start)
        parent_labels = [int(labels[idx]) for idx in indices]
        parent_subsegments = [subsegments[idx] for idx in indices]
        windows = _window_boundaries(speech_segment, parent_subsegments)

        if embeddings is not None and len(label_values) > 1:
            original_labels = parent_labels
            parent_labels = _smooth_window_labels_temporal(
                parent_labels,
                embeddings,
                indices,
                label_values,
                centroids,
            )
            parent_labels = _restore_sustained_label_runs(
                original_labels,
                parent_labels,
                windows,
            )
            parent_labels = _collapse_short_label_islands(parent_labels, windows)
        else:
            parent_labels = _smooth_window_labels(parent_labels)

        for (start, end), label in zip(windows, parent_labels, strict=False):
            if end <= start:
                continue
            raw_segments.append(
                _RawSegment(
                    start=start,
                    end=end,
                    speaker=f"SPEAKER_{label:02d}",
                )
            )

    # Add short VAD segments that were skipped during embedding extraction
    covered_indices = {sub.parent_idx for sub in subsegments}
    for idx, seg in enumerate(speech_segments):
        if idx in covered_indices:
            continue
        # Find nearest subsegment by time
        seg_mid = (seg.start + seg.end) / 2
        best_speaker = "SPEAKER_00"
        best_dist = float("inf")
        for raw in raw_segments:
            raw_mid = (raw.start + raw.end) / 2
            dist = abs(seg_mid - raw_mid)
            if dist < best_dist:
                best_dist = dist
                best_speaker = raw.speaker
        raw_segments.append(_RawSegment(start=seg.start, end=seg.end, speaker=best_speaker))

    # Sort by time
    raw_segments.sort(key=lambda s: s.start)

    # Merge adjacent subsegments of the same speaker
    if not raw_segments:
        return []

    # Use mutable list of lists [start, end, speaker] for merging
    merged: list[list[float | str]] = [
        [raw_segments[0].start, raw_segments[0].end, raw_segments[0].speaker]
    ]
    for seg in raw_segments[1:]:
        prev = merged[-1]
        gap = seg.start - float(prev[1])
        if seg.speaker == prev[2] and gap < 0.7:
            prev[1] = max(float(prev[1]), seg.end)
        else:
            merged.append([seg.start, seg.end, seg.speaker])

    return [Segment(start=float(m[0]), end=float(m[1]), speaker=str(m[2])) for m in merged]
