"""One time estimate for closing, joining, cleaning and mixing."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from scriba.app import jobs
from scriba.app.task import check

RATES = {"joining": 180.0, "cleaning": 12.0, "mixing": 50.0}
CHOICE_SECONDS = 120


class CallChoice:
    def __init__(self, interval, *, timer=time.monotonic):
        self.interval = interval
        self._timer = timer
        self._until = timer() + CHOICE_SECONDS
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._only_call = True

    def left(self):
        return max(0, math.ceil(self._until - self._timer()))

    def answer(self, only_call):
        with self._lock:
            if self._ready.is_set() or not self.left():
                return False
            self._only_call = only_call
            self._ready.set()
            return True

    def wait(self, cancelled):
        while True:
            check(cancelled)
            with self._lock:
                if self._ready.is_set() or not self.left():
                    return self._only_call
            self._ready.wait(.2)


@dataclass(frozen=True)
class Progress:
    step: str
    fraction: float
    remaining: float
    slow: bool


class Estimate:
    def __init__(self, speed_file, duration, tracks, microphones, *, timer=time.monotonic):
        self._timer, self._file = timer, speed_file
        self._lock = threading.Lock()
        self._name, self._start, self._fraction = "closing", timer(), 0.0
        self._displayed = 0.0
        self._measured = {}
        outputs = tracks - microphones
        duration = max(1.0, duration)
        self._units = {"joining": duration * tracks,
                       "cleaning": duration * (microphones * (outputs + 1) if outputs else 0),
                       "mixing": duration * (tracks + 1)}
        saved = jobs.load_speeds(speed_file)
        self._cost = {"closing": 2.0}
        for key, rate in RATES.items():
            if key == "cleaning":
                self._cost.update(waiting=0.0, trimming=0.0)
            observed = saved.get(f"recording:{key}", rate)
            self._cost[key] = self._units[key] / (observed if math.isfinite(observed) else rate)
        self._duration = duration

    def use_excerpt(self, duration):
        with self._lock:
            duration = max(1.0, duration)
            ratio = duration / self._duration
            for step in ("cleaning", "mixing"):
                self._cost[step] *= ratio
                self._units[step] *= ratio
            self._duration = duration

    def _remember(self, now):
        spent = now - self._start
        units = self._units.get(self._name, 0)
        if spent >= .25 and units:
            self._measured[f"recording:{self._name}"] = units / spent

    def step(self, name):
        with self._lock:
            if name != self._name:
                if name == "trimming":
                    self._cost[name] = self._cost["joining"] / 4
                now = self._timer()
                self._remember(now)
                self._name, self._start, self._fraction = name, now, 0.0

    def report(self, fraction):
        with self._lock:
            self._fraction = max(self._fraction, min(1.0, fraction))

    def complete(self):
        with self._lock:
            self._remember(self._timer())
            self._name, self._displayed = "", 1.0
        try:
            jobs.remember_speeds(self._file, self._measured)
        except OSError:
            pass  # the time estimate is a cache

    def snapshot(self):
        with self._lock:
            if not self._name:
                return Progress("", 1.0, 0.0, False)
            steps = list(self._cost)
            index = steps.index(self._name)
            earlier = sum(self._cost[name] for name in steps[:index])
            later = sum(self._cost[name] for name in steps[index + 1:])
            cost = self._cost[self._name]
            fraction = self._fraction
            spent = max(0.0, self._timer() - self._start)
            slow = spent > max(2.0, 2 * cost)
            remaining = (max(cost * .25, spent / fraction) * (1 - fraction)
                         if .03 <= fraction < 1 else max(0.0, cost - spent))
            if slow and fraction < .03:
                remaining = max(cost, spent / 2)
            total = earlier + cost + later
            self._displayed = max(self._displayed, min(.99, (earlier + cost * fraction) / total))
            return Progress(self._name, self._displayed, remaining + later, slow)
