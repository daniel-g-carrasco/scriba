"""Selection in seconds on the original clock of a recording."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Selection:
    duration: float
    start: float
    end: float

    @property
    def interval(self) -> tuple[float, float]:
        return self.start, self.end

    def position(self, edge: str, width: float) -> float:
        return getattr(self, edge) * width / self.duration if self.duration > 0 else 0.0

    def move(self, edge: str, seconds: float) -> None:
        if not math.isfinite(seconds):
            return
        gap = min(1.0, self.duration)
        if edge == "start":
            self.start = max(0.0, min(seconds, self.end - gap))
        else:
            self.end = min(self.duration, max(seconds, self.start + gap))

    def step(self, edge: str, seconds: float) -> None:
        self.move(edge, getattr(self, edge) + seconds)
