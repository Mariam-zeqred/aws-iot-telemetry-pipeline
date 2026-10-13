"""A small incubator simulator that produces records in the real firmware's format.

Thermal model: the first-order fit from incubator-telemetry-analysis (figures/model_params.json,
fitted on one day of the real device): dT/dt = a*u(t-d) - b*(T - T_out) with
a = 0.5539 degC/min, b = 0.0101 /min, dead time d = 210 s. The controller here is a plain
hysteresis switch; it is NOT the real firmware's logic (that is modelled in
incubator-thermal-control-sim). This module exists so the pipeline can be exercised with
plausible numbers, not to study control.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

A_C_PER_MIN = 0.5539
B_PER_MIN = 0.0101
DEAD_TIME_S = 210


@dataclass
class IncubatorModel:
    device_id: str = "hive-SIM001"
    target: float = 34.5
    ambient: float = 27.0
    hysteresis: float = 0.3  # switch on below target-h, off above target+h
    interval_s: int = 15
    start_ts: int = 1_791_000_000
    seed: int = 1
    cycle_code: str = "SIM00001"
    door_open_until: int = 0
    temp: float = field(init=False)
    heater: bool = field(init=False, default=False)
    _rng: random.Random = field(init=False, repr=False)
    _queue: deque[bool] = field(init=False, repr=False)
    _t: int = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self.temp = self.ambient
        self._t = self.start_ts
        self._queue = deque([False] * max(1, DEAD_TIME_S // self.interval_s))

    def open_door(self, seconds: int) -> None:
        self.door_open_until = self._t + seconds

    def step(self) -> dict[str, Any]:
        """Advance one interval and return the telemetry record the firmware would send."""
        door_open = self._t < self.door_open_until
        # hysteresis controller on the measured temperature
        if self.temp < self.target - self.hysteresis:
            self.heater = True
        elif self.temp > self.target + self.hysteresis:
            self.heater = False
        self._queue.append(self.heater)
        effective_u = 1.0 if self._queue.popleft() else 0.0
        loss = B_PER_MIN * (4.0 if door_open else 1.0)
        dt_min = self.interval_s / 60.0
        self.temp += (A_C_PER_MIN * effective_u - loss * (self.temp - self.ambient)) * dt_min
        self.ambient += self._rng.gauss(0, 0.005)  # slow ambient drift
        self._t += self.interval_s
        noise = self._rng.gauss(0, 0.02)
        hum = 70.0 + 5.0 * math.sin(self._t / 1800.0) + self._rng.gauss(0, 0.3)
        elapsed = self._t - self.start_ts
        return {
            "ts": self._t,
            "iso": datetime.fromtimestamp(self._t, tz=UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "temp": round(self.temp + noise, 2),
            "hum": round(min(100.0, max(0.0, hum)), 1),
            "tempOut": round(self.ambient, 2),
            "humOut": round(50.0 + self._rng.gauss(0, 0.5), 1),
            "targetTemp": self.target,
            "heater": self.heater,
            "door": "open" if door_open else "shut",
            "cycleDay": 1 + elapsed // 86400,
            "cycleCode": self.cycle_code,
            "elapsedSec": elapsed,
        }
