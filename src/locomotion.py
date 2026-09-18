"""Validate and sample the same climb travel table used by the authored model."""
from __future__ import annotations
import math
from dataclasses import dataclass


@dataclass
class ClimbCadence:
    """Host wait clock; native endpoints, not this timer, count the burst."""
    active: bool = False
    waiting: bool = True
    remaining: float = 30.0
    run: int = 0

    def start(self):
        self.active = True
        self.waiting = True
        self.remaining = 30.0
        self.run += 1

    def stop(self):
        self.active = False

    def tick(self, seconds: float, blocked: bool = False) -> bool:
        if not self.active or not self.waiting or blocked:
            return False
        self.remaining = max(0.0, self.remaining - max(0.0, seconds))
        if self.remaining <= 1e-8:
            self.waiting = False
            self.run += 1
            return True
        return False

    def finished(self, run: int) -> bool:
        if not self.active or self.waiting or run != self.run:
            return False
        self.waiting = True
        self.remaining = 30.0
        return True


def climb_spec(metadata: object) -> dict | None:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("climb"), dict):
        return None
    value = metadata["climb"]
    rise, period, rows = value.get("risePerCycle"), value.get("cycleDuration"), value.get("phaseTravel")
    if (not isinstance(rise, (int, float)) or not math.isfinite(rise) or not 0 < rise <= .5
            or not isinstance(period, (int, float)) or not math.isfinite(period) or not .2 <= period <= 10
            or not isinstance(rows, list) or not 2 <= len(rows) <= 100):
        return None
    previous = (-1.0, -1.0)
    for row in rows:
        if (not isinstance(row, list) or len(row) != 2
                or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in row)
                or row[0] <= previous[0] or row[1] < previous[1]):
            return None
        previous = row
    if rows[0] != [0, 0] or rows[-1] != [1, 1]:
        return None
    return {"risePerCycle": float(rise), "cycleDuration": float(period),
            "phaseTravel": [tuple(row) for row in rows]}


def climb_progress(spec: dict, phase: object, cycle: object) -> float | None:
    if (not isinstance(phase, (int, float)) or not math.isfinite(phase) or not 0 <= phase <= 1
            or not isinstance(cycle, int) or isinstance(cycle, bool) or not 0 <= cycle < 100000000):
        return None
    rows = spec["phaseTravel"]
    for (a, x), (b, y) in zip(rows, rows[1:]):
        if phase <= b:
            return cycle + x + (y - x) * (phase - a) / (b - a)
    return float(cycle + 1)
