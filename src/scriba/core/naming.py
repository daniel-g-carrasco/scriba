"""Conservative, one-to-one proposals from overlapping speech and named spans."""
from dataclasses import dataclass

MIN_SHARE = 0.5
MIN_SECONDS = 3.0  # a short acknowledgement is too little to identify a voice
MIN_MARGIN = 0.1  # near ties remain for the user, even when both reach 50 percent


@dataclass(frozen=True)
class Proposal:
    name: str
    seconds: float
    share: float


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


def propose(lines, named, microphone: str = "", minimum_share: float = MIN_SHARE,
            minimum_seconds: float = MIN_SECONDS) -> dict[str, Proposal]:
    voices, people = {}, {}
    for line in lines:
        if line.speaker and line.speaker != microphone and line.text.strip():
            voices.setdefault(line.speaker, []).append((line.start, line.end))
    for name, start, end in named:
        people.setdefault(name, []).append((start, end))
    people = {name: _union(times) for name, times in people.items()}
    candidates = []
    for voice, times in voices.items():
        times = _union(times)
        total = sum(b - a for a, b in times)
        if not total:
            continue
        scores = sorted(((sum(max(0, min(b, d) - max(a, c)) for a, b in times for c, d in other), name)
                         for name, other in people.items()), reverse=True)
        if not scores:
            continue
        seconds, name = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0
        share = seconds / total
        if seconds >= minimum_seconds and share >= minimum_share and (seconds - runner_up) / total >= MIN_MARGIN:
            candidates.append((seconds, voice, Proposal(name, seconds, share)))
    out, used = {}, set()
    for _seconds, voice, proposal in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if proposal.name not in used:
            out[voice] = proposal
            used.add(proposal.name)
    return out


# Conservative proposal thresholds, not automatic identity decisions.
# A cosine distance <= .15 suggests a split voice; >= .35 suggests distinct
# voices. The undecided band deliberately leaves names blank. AMI validation
# is recorded separately; these thresholds do not alter diarization clusters.
SIMILAR_DISTANCE = .15
DIFFERENT_DISTANCE = .35


def cosine_distance(a, b):
    import math
    if len(a) != len(b) or not a or not all(math.isfinite(v) for v in (*a, *b)):
        return None
    norm = math.sqrt(sum(v*v for v in a) * sum(v*v for v in b))
    return 1 - sum(x*y for x,y in zip(a,b,strict=True)) / norm if norm else None


def propose_with_voices(lines, named, centroids, microphone=""):
    """Return editable proposals and reason codes; never merge actual voices."""
    if not centroids:
        return propose(lines, named, microphone), []
    by_voice, notes = {}, []
    for voice in dict.fromkeys(s.speaker for s in lines if s.speaker and s.speaker != microphone):
        own = [s for s in lines if s.speaker == voice]
        times = _union((s.start, s.end) for s in own if s.text.strip())
        total = sum(b-a for a,b in times)
        if not total:
            continue
        scores = []
        for name in dict.fromkeys(n for n,_,_ in named):
            seconds = sum(max(0,min(b,d)-max(a,c)) for a,b in times
                          for c,d in _union((c,d) for n,c,d in named if n == name))
            if seconds >= MIN_SECONDS and seconds / total >= .2:
                scores.append(name)
        if len(scores) > 1:
            notes.append('multiple_tiles')
            continue
        match = propose(own, named, microphone)
        if voice in match:
            by_voice[voice] = match[voice]
    out = {}
    for name in dict.fromkeys(p.name for p in by_voice.values()):
        group = sorted(v for v,p in by_voice.items() if p.name == name)
        if len(group) == 1:
            out[group[0]] = by_voice[group[0]]
            continue
        distances = [cosine_distance(centroids.get(a,[]),centroids.get(b,[]))
                     for i,a in enumerate(group) for b in group[i+1:]]
        if any(d is None for d in distances):
            continue
        if all(d <= SIMILAR_DISTANCE for d in distances):
            out.update((v,by_voice[v]) for v in group)
            notes.append('merge_voices')
        elif all(d >= DIFFERENT_DISTANCE for d in distances):
            for i,v in enumerate(group,1):
                p = by_voice[v]
                out[v] = Proposal(f'{name}, voice {i}',p.seconds,p.share)
            notes.append('multiple_voices')
    return out, list(dict.fromkeys(notes))
