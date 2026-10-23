"""Exact clock-grid and fail-closed scheduled-input mapping for isolated pilot."""
from __future__ import annotations

import pytest

from sat_sim.interactive.native_time_rebase_pilot import (
    PILOT_PROFILE, rebase_future_torque_events,
)


def _map(events, *, checkpoint_s=2.0, step_s=0.2, profile=PILOT_PROFILE):
    return rebase_future_torque_events(
        profile=profile, checkpoint_s=checkpoint_s, step_s=step_s,
        absolute_events=events,
    )


def test_strict_absolute_to_fresh_local_torque_mapping() -> None:
    assert _map(((3.0, 0.1), (4.0, 0.0))) == (
        (1.0, 0.1), (2.0, 0.0),
    )
    assert _map(()) == ()


@pytest.mark.parametrize(("schedule", "message"), (
    (((2.0, 0.1),), "PAST_OR_PENDING"),
    (((1.8, 0.1),), "PAST_OR_PENDING"),
    (((3.0, 0.1), (3.0, 0.0)), "NOT_INCREASING"),
    (((4.0, 0.0), (3.0, 0.1)), "NOT_INCREASING"),
    (((3.1, 0.1),), "OFF_TICK"),
    (((3.0, float("nan")),), "INVALID_TORQUE"),
    (((3.0, True),), "INVALID_TORQUE"),
))
def test_strict_absolute_mapping_rejects_unrestored_event_semantics(
    schedule, message,
) -> None:
    with pytest.raises(ValueError, match=message):
        _map(schedule)


def test_mapping_rejects_wrong_graph_and_phase_misalignment() -> None:
    with pytest.raises(ValueError, match="PROFILE_UNSUPPORTED"):
        _map(((3.0, 0.1),), profile="full-spacecraft")
    with pytest.raises(ValueError, match="OFF_TICK"):
        _map(((3.0, 0.1),), checkpoint_s=2.1)
    with pytest.raises(ValueError, match="INVALID_STEP"):
        _map(((3.0, 0.1),), step_s=0)
    with pytest.raises(ValueError, match="INVALID_CHECKPOINT"):
        _map(((3.0, 0.1),), checkpoint_s=0)
