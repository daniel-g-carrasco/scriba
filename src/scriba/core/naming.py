"""Editable Teams name proposals from labelled speech and voice similarity."""
from dataclasses import dataclass, replace

MIN_PURITY = 0.70
MIN_SECONDS = 8.0
MIN_MARGIN = 0.10

# The undecided band leaves a shared tile's ambiguous voices unnamed.
SIMILAR_DISTANCE = 0.15
DIFFERENT_DISTANCE = 0.35


@dataclass(frozen=True)
class Proposal:
    name: str
    seconds: float
    share: float     # coverage of the whole voice, for display
    source: str = "teams"


def _union(intervals):
    out = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(end, out[-1][1]))
        else:
            out.append((start, end))
    return out


def named_intervals(named):
    people = {}
    for name, start, end in named:
        people.setdefault(name, []).append((start, end))
    return {name: _union(times) for name, times in people.items()}


def duration(intervals):
    return sum(end - start for start, end in intervals)


def _intersect(left, right):
    i = j = 0
    while i < len(left) and j < len(right):
        a, b = left[i]
        c, d = right[j]
        start, end = max(a, c), min(b, d)
        if end > start:
            yield start, end
        if b <= d:
            i += 1
        else:
            j += 1


@dataclass
class _Evidence:
    total: float
    named: float
    scores: list[tuple[float, str]]

    def proposal(self, minimum_purity=MIN_PURITY, minimum_seconds=MIN_SECONDS):
        if self.named < minimum_seconds or not self.scores:
            return None
        seconds, name = self.scores[0]
        runner_up = self.scores[1][0] if len(self.scores) > 1 else 0
        if seconds >= self.named * minimum_purity and seconds - runner_up >= self.named * MIN_MARGIN:
            return Proposal(name, seconds, seconds / self.total)
        return None


def _evidence(lines, named, microphone):
    people = named_intervals(named)
    all_named = _union(interval for times in people.values() for interval in times)
    voices = {}
    for line in lines:
        if line.speaker and line.speaker != microphone and line.text.strip():
            voices.setdefault(line.speaker, []).append((line.start, line.end))
    evidence = {}
    for voice, times in voices.items():
        times = _union(times)
        if times:
            scores = [(duration(_intersect(times, intervals)), name) for name, intervals in people.items()]
            evidence[voice] = _Evidence(duration(times), duration(_intersect(times, all_named)),
                                        sorted(((s, name) for s, name in scores if s > 0), reverse=True))
    return evidence


def propose(lines, named, microphone: str = "", minimum_purity: float = MIN_PURITY,
            minimum_seconds: float = MIN_SECONDS) -> dict[str, Proposal]:
    candidates = [(voice, proposal) for voice, evidence in _evidence(lines, named, microphone).items()
                  if (proposal := evidence.proposal(minimum_purity, minimum_seconds)) is not None]
    out, used = {}, set()
    for voice, proposal in sorted(candidates, key=lambda item: (-item[1].seconds, item[0])):
        if proposal.name not in used:
            out[voice] = proposal
            used.add(proposal.name)
    return out


def automatic(lines, named, microphone: str = "") -> dict[str, str]:
    """Keep automatic assignment for a well-observed voice without a competitor."""
    competing, candidates = {}, {}
    for voice, evidence in _evidence(lines, named, microphone).items():
        match = evidence.proposal(minimum_seconds=3)
        if match is None:
            continue
        competing.setdefault(match.name.casefold(), set()).add(voice)
        if match.seconds >= 12 and match.share >= .85:
            candidates[voice] = match.name
    return {voice: name for voice, name in candidates.items() if len(competing[name.casefold()]) == 1}


def cosine_distance(a, b):
    import math
    if len(a) != len(b) or not a or not all(math.isfinite(v) for v in (*a, *b)):
        return None
    norm = math.sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    return 1 - sum(x * y for x, y in zip(a, b, strict=True)) / norm if norm else None


def _similar(a, b):
    distance = cosine_distance(a, b)
    return distance is not None and distance <= SIMILAR_DISTANCE


def propose_with_voices(lines, named, centroids, microphone="", *, fingerprints=None):
    """Use a dominant observed name or an unambiguous voice match."""
    if not centroids:
        return propose(lines, named, microphone), []
    evidence = _evidence(lines, named, microphone)
    groups, notes = {}, []
    for voice, match in evidence.items():
        proposal = match.proposal()
        if proposal is not None:
            groups.setdefault(proposal.name, {})[voice] = proposal
        elif match.named >= MIN_SECONDS and len(match.scores) > 1:
            notes.append("multiple_tiles")

    out, references = {}, dict(fingerprints or {})
    for name, direct in groups.items():
        group = sorted(direct)
        if len(group) > 1:
            distances = [cosine_distance(centroids.get(a, []), centroids.get(b, []))
                         for i, a in enumerate(group) for b in group[i + 1:]]
            if all(d is not None and d >= DIFFERENT_DISTANCE for d in distances):
                direct = {voice: replace(direct[voice], name=f"{name}, voice {i}")
                          for i, voice in enumerate(group, 1)}
                notes.append("multiple_voices")
            elif all(d is not None and d <= SIMILAR_DISTANCE for d in distances):
                notes.append("merge_voices")
            else:
                references.pop(name, None)
                continue
        out.update(direct)
        references.setdefault(name, [centroids[voice] for voice in group if voice in centroids])

    for voice, match in evidence.items():
        if match.named or voice not in centroids:
            continue
        vector = centroids[voice]
        matches = [name for name, examples in references.items() if any(_similar(vector, other) for other in examples)]
        if len(matches) != 1:
            continue
        name, = matches
        group = groups.get(name, {})
        labels = {out[v].name for v in group if v in out}
        if len(labels) > 1:
            labels = {out[v].name for v in group if _similar(vector, centroids.get(v, []))}
            if len(labels) != 1:
                continue
            name, = labels
        out[voice] = Proposal(name, 0, 0, "voice")
    return out, list(dict.fromkeys(notes))
