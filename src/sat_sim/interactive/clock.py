"""Pacing calculations for a soft real-time simulation loop."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .models import ALLOWED_RATES


@dataclass(frozen=True)
class PaceObservation:
    requested_sleep_s: float
    actual_sleep_s: float
    drift_s: float


class SoftRealtimeClock:
    def __init__(self, rate: float = 1.0, *, monotonic: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
        self._monotonic = monotonic
        self._sleeper = sleeper
        self._rate = self._validate_rate(rate)
        self._wall_epoch = monotonic()
        self._sim_epoch_s = 0.0

    @staticmethod
    def _validate_rate(value: float) -> float:
        value = float(value)
        if value not in ALLOWED_RATES:
            raise ValueError(f"rate must be one of {ALLOWED_RATES}")
        return value

    @property
    def rate(self) -> float:
        return self._rate

    def set_rate(self, rate: float, current_sim_time_s: float) -> None:
        self._rate = self._validate_rate(rate)
        self._wall_epoch = self._monotonic()
        self._sim_epoch_s = float(current_sim_time_s)

    def reset(self, current_sim_time_s: float = 0.0) -> None:
        self._wall_epoch = self._monotonic()
        self._sim_epoch_s = float(current_sim_time_s)

    def pace_to(self, target_sim_time_s: float) -> PaceObservation:
        due = self._wall_epoch + (float(target_sim_time_s) - self._sim_epoch_s) / self._rate
        before = self._monotonic()
        requested = max(0.0, due - before)
        if requested:
            self._sleeper(requested)
        after = self._monotonic()
        return PaceObservation(requested, max(0.0, after - before), after - due)

