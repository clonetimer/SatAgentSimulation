"""Fake-native guard tests for isolated two-rate Basilisk scheduling."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from sat_sim.interactive.native_multitask_pilot import (
    PROFILE, capture_two_task_clock, restore_two_task_clock_fresh,
    validate_two_task_clock,
)


class Obj:
    def __init__(self, **args):
        self.__dict__.update(args)


def _fake(*, advanced=False, slow_next=2_400_000_000):
    ns = 2_000_000_000 if advanced else 0
    fast = Obj(TaskData=Obj(
        TaskPeriod=200_000_000, FirstTaskTime=0,
        NextPickupTime=200_000_000,
        NextStartTime=ns + 200_000_000 if advanced else 0,
    ))
    slow = Obj(TaskData=Obj(
        TaskPeriod=600_000_000, FirstTaskTime=0,
        NextPickupTime=600_000_000,
        NextStartTime=slow_next if advanced else 0,
    ))
    process = Obj(processData=Obj(
        nextTaskTime=ns + 200_000_000 if advanced else 0,
        prevRouteTime=0,
        processTasks=[
            Obj(TaskUpdatePeriod=200_000_000,
                NextTaskStart=ns + 200_000_000 if advanced else 0,
                taskPriority=-1),
            Obj(TaskUpdatePeriod=600_000_000,
                NextTaskStart=slow_next if advanced else 0,
                taskPriority=-1),
        ],
    ))
    sim = Obj(TaskList=[fast, slow], TotalSim=Obj(
        CurrentNanos=ns, NextTaskTime=ns + 200_000_000 if advanced else 0,
    ))
    sc = Obj(
        timeBefore=2.0 if advanced else 0.0,
        timeBeforeNanos=ns,
        timeStep=0.2 if advanced else 0.0,
    )
    return sim, process, fast, slow, sc


def test_two_rate_phase_can_roundtrip_only_on_fresh_graph() -> None:
    snapshot = capture_two_task_clock(*_fake(advanced=True))
    assert snapshot["profile"] == PROFILE
    assert snapshot["full_spacecraft_resume_supported"] is False
    assert snapshot["observer_module_private_state_restored"] is False
    assert snapshot["fields"]["tasks"]["observer"]["next_start_ns"] == 2_400_000_000
    fields = validate_two_task_clock(snapshot)
    fresh = _fake()
    restore_two_task_clock_fresh(*fresh, snapshot)
    assert capture_two_task_clock(*fresh)["fields"] == fields
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_FRESH_GRAPH_REQUIRED"):
        restore_two_task_clock_fresh(*fresh, snapshot)


def _rehash(document):
    content = dict(document)
    content.pop("digest_sha256", None)
    document["digest_sha256"] = hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def test_two_rate_pilot_rejects_modified_or_stale_slow_schedule() -> None:
    snapshot = capture_two_task_clock(*_fake(advanced=True))
    broken = copy.deepcopy(snapshot)
    broken["fields"]["tasks"]["observer"]["next_start_ns"] = 3_000_000_000
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_DIGEST_INVALID"):
        validate_two_task_clock(broken)
    _rehash(broken)
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_PHASE_MISMATCH"):
        validate_two_task_clock(broken)
    forged = copy.deepcopy(snapshot)
    forged["full_spacecraft_resume_supported"] = True
    _rehash(forged)
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_RESUME_CLAIM_REJECTED"):
        validate_two_task_clock(forged)


def test_multitask_pilot_refuses_wrong_graph_period_and_slow_boundary() -> None:
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_GRAPH_MISMATCH"):
        args = list(_fake(advanced=True))
        args[0].TaskList.pop()
        capture_two_task_clock(*args)
    args = list(_fake(advanced=True))
    args[3].TaskData.TaskPeriod = 400_000_000
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_PERIOD_MISMATCH"):
        capture_two_task_clock(*args)
    args = list(_fake(advanced=True))
    args[0].TotalSim.CurrentNanos = 2_400_000_000
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_CHECKPOINT_MUST_EXPOSE_SLOW_PHASE"):
        capture_two_task_clock(*args)
    args = list(_fake(advanced=True))
    args[4].timeBeforeNanos = 0
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_HUB_PHASE_MISMATCH"):
        capture_two_task_clock(*args)



def test_process_task_scheduler_entries_are_not_aliases_of_task_next_start() -> None:
    args = _fake(advanced=True)
    _, process, fast, slow, _ = args
    process.processData.processTasks[1].NextTaskStart = 2_200_000_000
    assert slow.TaskData.NextStartTime == 2_400_000_000
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_PROCESS_PHASE_MISMATCH"):
        capture_two_task_clock(*args)


def test_restore_rejects_mismatched_process_priority_before_writing() -> None:
    doc = capture_two_task_clock(*_fake(advanced=True))
    fresh = _fake()
    fresh[1].processData.processTasks[1].taskPriority = 8
    with pytest.raises(ValueError, match="NATIVE_MULTITASK_FRESH_GRAPH_REQUIRED"):
        restore_two_task_clock_fresh(*fresh, doc)
    assert fresh[0].TotalSim.CurrentNanos == 0
