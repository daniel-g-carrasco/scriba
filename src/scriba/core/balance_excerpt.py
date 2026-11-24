"""Choose a listening comparison from the speech heard on each role."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass

from . import callmute, levels

SECONDS = 25.0
ME, OTHERS = "me", "others"
ROLES = (ME, OTHERS)
MIN_TURN = 0.3


@dataclass(frozen=True)
class Speech:
    start: float
    end: float
    role: str
    level: float | None = None
    doubtful: bool = False

    def weight(self) -> float:
        if self.level is not None and self.level < levels.SPEECH_FROM:
            return 0.0
        quiet = self.level is not None and self.level < levels.QUIET_BELOW
        return (0.6 if quiet else 1.0) * (0.35 if self.doubtful else 1.0)


def microphone_unavailable(spans_by_track, duration: float) -> list[tuple[float, float]]:
    """The times when every microphone track was muted or switched off."""
    all_time = [(0.0, duration)]
    unavailable = all_time
    for spans in spans_by_track:
        unavailable = callmute.outside(unavailable, callmute.outside(all_time, spans))
    return unavailable


def choose(speech, duration: float, *, unavailable=(), echo=()) -> tuple[float, float]:
    """Prefer an exchange with good speech on both sides, then the best solo.

    A change needs audible time for each side on its own. Mute, a switched
    off microphone and strong echo cannot contribute a microphone turn.
    The other participants are still useful on their own when that happens.
    """
    if not math.isfinite(duration) or duration <= 0:
        return 0.0, 0.0
    length = min(duration, SECONDS)
    if length == duration:
        return 0.0, duration
    points = defaultdict(list, {0.0: [], duration: []})

    def add(start, end, role, weight):
        left, right = max(0.0, start), min(duration, duration if end is None else end)
        if weight and left < right:
            points[left].append((role, weight, 1))
            points[right].append((role, weight, -1))

    for line in speech:
        if line.role in ROLES:
            add(line.start, line.end, line.role, line.weight())
    for start, end in (*unavailable, *echo):
        add(start, end, "excluded", 1.0)

    # A sweep counts overlapping lines of a role once, using its best evidence.
    knots = sorted(points)
    active = {role: Counter() for role in (*ROLES, "excluded")}
    totals = [(0.0,) * 5]
    rates = []
    for left, right in zip(knots, knots[1:], strict=False):
        for role, weight, delta in points[left]:
            active[role][weight] += delta
            if not active[role][weight]:
                del active[role][weight]
        excluded = bool(active["excluded"])
        mine = 0.0 if excluded else max(active[ME], default=0.0)
        others = max(active[OTHERS], default=0.0)
        rate = mine, others, bool(mine and not others), bool(others and not mine), excluded
        rates.append(rate)
        totals.append(tuple(total + (right - left) * value for total, value in zip(totals[-1], rate, strict=True)))

    def before(second):
        if second >= duration:
            return totals[-1]
        i = bisect_right(knots, second) - 1
        return tuple(total + (second - knots[i]) * value
                     for total, value in zip(totals[i], rates[i], strict=True))

    def score(start):
        mine, others, mine_alone, others_alone, excluded = (
            end - begin for begin, end in zip(before(start), before(start + length), strict=True))
        exchange = min(mine_alone, others_alone) >= MIN_TURN
        speech = (min(mine, others), mine + others) if exchange else (0, max(mine, others))
        return exchange, *speech, -excluded, -start

    latest = duration - length
    candidates = {max(0.0, min(latest, time - offset)) for time in knots for offset in (0.0, length)}
    best = max(candidates, key=score)
    return best, best + length
