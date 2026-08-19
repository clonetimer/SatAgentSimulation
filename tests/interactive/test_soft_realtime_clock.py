from __future__ import annotations

import pytest

from sat_sim.interactive.clock import SoftRealtimeClock
from sat_sim.interactive.models import ALLOWED_RATES


class FakeWallClock:
    def __init__(self) -> None:
        self.now = 10.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_clock_paces_sim_time_and_reanchors_rate_changes() -> None:
    wall = FakeWallClock()
    clock = SoftRealtimeClock(1.0, monotonic=wall.monotonic, sleeper=wall.sleep)
    first = clock.pace_to(0.5)
    assert first.requested_sleep_s == pytest.approx(0.5)
    assert first.drift_s == pytest.approx(0.0)
    clock.set_rate(2.0, current_sim_time_s=0.5)
    second = clock.pace_to(1.5)
    assert second.requested_sleep_s == pytest.approx(0.5)


def test_clock_rejects_unfrozen_rate() -> None:
    with pytest.raises(ValueError, match="rate must be one of"):
        SoftRealtimeClock(3.0)


@pytest.mark.parametrize("rate", ALLOWED_RATES)
def test_every_frozen_rate_paces_without_drift_under_available_compute(rate: float) -> None:
    wall = FakeWallClock()
    clock = SoftRealtimeClock(rate, monotonic=wall.monotonic, sleeper=wall.sleep)
    observation = clock.pace_to(1.0)
    assert observation.requested_sleep_s == pytest.approx(1.0 / rate)
    assert observation.drift_s == pytest.approx(0.0)
