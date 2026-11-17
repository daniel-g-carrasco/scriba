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
