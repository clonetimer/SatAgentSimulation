"""Pure-Python guard tests for the experimental native single-task clock pilot."""
from __future__ import annotations

import copy

import pytest

from sat_sim.interactive.native_scheduler_pilot import (
    PROFILE, capture_single_task_clock, restore_single_task_clock_on_fresh_graph,
    validate_single_task_clock,
)


class Box:
    def __init__(self, **attributes):
        self.__dict__.update(attributes)


def fake_graph(*, advanced: bool):
    step = 200_000_000
    at = 2_000_000_000 if advanced else 0
    task = Box(TaskData=Box(
        TaskPeriod=step, FirstTaskTime=0, NextPickupTime=step,
        NextStartTime=at + step if advanced else 0,
    ))
    process = Box(processData=Box(
        nextTaskTime=at + step if advanced else 0, prevRouteTime=0,
    ))
    sim = Box(TaskList=[task], TotalSim=Box(
        CurrentNanos=at, NextTaskTime=at + step if advanced else 0,
    ))
    spacecraft = Box(
        timeBefore=2.0 if advanced else 0.0,
        timeBeforeNanos=at,
        timeStep=0.2 if advanced else 0.0,
    )
    return sim, process, task, spacecraft


def test_single_task_clock_export_import_preserves_explicit_phase_fields() -> None:
    original = fake_graph(advanced=True)
    document = capture_single_task_clock(*original)
    assert document["graph_profile"] == PROFILE
    assert document["full_spacecraft_resume_supported"] is False
    assert document["complete_integrator_history_restored"] is False
    assert validate_single_task_clock(document)["sim_current_ns"] == 2_000_000_000

    fresh = fake_graph(advanced=False)
    restore_single_task_clock_on_fresh_graph(*fresh, document)
    assert capture_single_task_clock(*fresh)["fields"] == document["fields"]
    with pytest.raises(ValueError, match="NATIVE_CLOCK_RESTORE_REQUIRES_FRESH_GRAPH"):
        restore_single_task_clock_on_fresh_graph(*fresh, document)


def test_clock_pilot_rejects_changed_fields_and_forged_resume_claim() -> None:
    snapshot = capture_single_task_clock(*fake_graph(advanced=True))
    for key, value in (
        ("task_next_start_ns", 2_000_000_000),
        ("spacecraft_time_before_ns", 0),
        ("spacecraft_time_before_s", 0.0),
        ("task_period_ns", 150_000_000),
    ):
        tampered = copy.deepcopy(snapshot)
        tampered["fields"][key] = value
        with pytest.raises(ValueError, match="NATIVE_CLOCK_DIGEST_INVALID"):
            validate_single_task_clock(tampered)
    forged = copy.deepcopy(snapshot)
    forged["full_spacecraft_resume_supported"] = True
    with pytest.raises(ValueError, match="NATIVE_CLOCK_DIGEST_INVALID"):
        validate_single_task_clock(forged)


def test_clock_pilot_refuses_multiple_tasks_and_invalid_local_graph() -> None:
    data = fake_graph(advanced=True)
    sim, process, task, spacecraft = data
    sim.TaskList.append(Box(TaskData=Box()))
    with pytest.raises(ValueError, match="NATIVE_CLOCK_PILOT_REQUIRES_SINGLE_TASK"):
        capture_single_task_clock(*data)
    fresh = list(fake_graph(advanced=False))
    fresh[-1].timeBeforeNanos = 123
    doc = capture_single_task_clock(*fake_graph(advanced=True))
    with pytest.raises(ValueError, match="NATIVE_CLOCK_RESTORE_REQUIRES_FRESH_GRAPH"):
        restore_single_task_clock_on_fresh_graph(*fresh, doc)


def test_clock_pilot_refuses_incorrect_grid_even_if_payload_sha_is_recomputed() -> None:
    import hashlib
    import json

    document = capture_single_task_clock(*fake_graph(advanced=True))
    invalid = copy.deepcopy(document)
    invalid["fields"]["spacecraft_time_before_ns"] += 200_000_000
    data = dict(invalid)
    data.pop("digest_sha256")
    invalid["digest_sha256"] = hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="NATIVE_CLOCK_UNSUPPORTED_PHASE"):
        validate_single_task_clock(invalid)
