"""Experimental scheduling epoch mapping for an isolated Basilisk restart pilot.

This is only a strict map from source absolute event times to a fresh graph's
relative scheduler time. It does NOT modify or restore the native scheduler,
integrator phase, message history, or past event/command effects.
"""
from __future__ import annotations

import math
from typing import Sequence

PILOT_PROFILE = "isolated-autonomous-hub-earth-rw-scripted-torque-v1"


def _tick(value: float, step_s: float, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"NATIVE_REBASE_INVALID_TIME:{label}")
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(f"NATIVE_REBASE_INVALID_TIME:{label}")
    steps = round(value / step_s)
    if abs(value - steps * step_s) > 1.0e-9:
        raise ValueError(f"NATIVE_REBASE_OFF_TICK:{label}")
    return steps


def rebase_future_torque_events(
    *, profile: str, checkpoint_s: float, step_s: float,
    absolute_events: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Only map future prescribed torque changes in a fixed-step pilot.

    Deny past/at-checkpoint effects, unaligned grid events, duplicates and
    non-monotonic schedule; these would need additional state reconciliation.
    Torque is a deterministic *scripted input*, not a native queued TC.
    """
    if profile != PILOT_PROFILE:
        raise ValueError("NATIVE_REBASE_PROFILE_UNSUPPORTED")
    if isinstance(step_s, bool) or not isinstance(step_s, (float, int)) or not math.isfinite(step_s) or step_s <= 0:
        raise ValueError("NATIVE_REBASE_INVALID_STEP")
    checkpoint_tick = _tick(checkpoint_s, step_s, label="checkpoint")
    if checkpoint_tick <= 0:
        raise ValueError("NATIVE_REBASE_INVALID_CHECKPOINT")
    last_tick = checkpoint_tick
    result: list[tuple[float, float]] = []
    for time_s, torque_nm in absolute_events:
        tick = _tick(time_s, step_s, label="event")
        if tick <= checkpoint_tick:
            raise ValueError("NATIVE_REBASE_PAST_OR_PENDING_EVENT")
        if tick <= last_tick:
            raise ValueError("NATIVE_REBASE_EVENTS_NOT_INCREASING")
        if isinstance(torque_nm, bool) or not isinstance(torque_nm, (float, int)) or not math.isfinite(torque_nm):
            raise ValueError("NATIVE_REBASE_INVALID_TORQUE")
        result.append(((tick - checkpoint_tick) * step_s, float(torque_nm)))
        last_tick = tick
    return tuple(result)
