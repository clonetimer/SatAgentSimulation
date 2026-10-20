"""Contract and corruption tests for two independent Basilisk processes."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from sat_sim.interactive.native_multiprocess_pilot import (
    PROFILE, capture_two_process_clock, restore_two_process_clock_fresh,
    validate_two_process_clock,
)


class Box:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _graph(advanced=False):
    now = 2_000_000_000 if advanced else 0
    processes, tasks = {}, {}
    for role, name, period, priority in [
        ("dynamics", "dyn", 200_000_000, 20),
        ("observer", "nav", 600_000_000, 5),
    ]:
        nxt = (now // period + 1) * period if advanced else 0
        task = Box(TaskData=Box(
            FirstTaskTime=0, NextPickupTime=period,
            NextStartTime=nxt, TaskPeriod=period,
        ))
        proc_data = Box(
            processName=name, processPriority=priority, processActive=True,
            nextTaskTime=nxt, prevRouteTime=0,
            processTasks=[Box(NextTaskStart=nxt, TaskUpdatePeriod=period, taskPriority=-1)],
        )
        tasks[role] = task
        processes[role] = Box(processData=proc_data)
    sc = Box(timeBefore=2.0 if advanced else 0.0,
             timeBeforeNanos=now, timeStep=0.2 if advanced else 0.0)
    sim = Box(TaskList=list(tasks.values()), TotalSim=Box(
        CurrentNanos=now, NextTaskTime=now+200_000_000 if advanced else 0,
        nextProcPriority=20 if advanced else -1,
        processList=[processes["dynamics"].processData, processes["observer"].processData],
    ))
    return sim, processes, tasks, sc


def _digest(doc):
    d = dict(doc)
    d.pop("digest_sha256", None)
    doc["digest_sha256"] = hashlib.sha256(json.dumps(
        d, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def test_two_process_priority_and_phase_export_restore_roundtrip() -> None:
    source = capture_two_process_clock(*_graph(True))
    assert source["profile"] == PROFILE
    assert source["complete_satellite_resume"] is False
    assert source["module_memory_restored"] is False
    assert source["state"]["next_process_priority"] == 20
    assert source["state"]["processes"]["observer"]["scheduled_ns"] == 2_400_000_000
    fresh = _graph()
    restore_two_process_clock_fresh(*fresh, source)
    assert capture_two_process_clock(*fresh)["state"] == source["state"]
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_FRESH_GRAPH_REQUIRED"):
        restore_two_process_clock_fresh(*fresh, source)


def test_reject_process_priority_corruption_or_extra_process() -> None:
    report = capture_two_process_clock(*_graph(True))
    modified = copy.deepcopy(report)
    modified["state"]["processes"]["observer"]["priority"] = 20
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_DIGEST_INVALID"):
        validate_two_process_clock(modified)
    _digest(modified)
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_PROCESS_PHASE_OR_PRIORITY_INVALID"):
        validate_two_process_clock(modified)
    forged = copy.deepcopy(report)
    forged["complete_satellite_resume"] = True
    _digest(forged)
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_RESUME_CLAIM_REJECTED"):
        validate_two_process_clock(forged)
    graph = list(_graph(True))
    graph[0].TotalSim.processList.append(Box(processName="extra"))
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_PRIORITY_ORDER_MISMATCH"):
        capture_two_process_clock(*graph)


def test_reject_hidden_process_schedule_cursor_drift() -> None:
    graph = _graph(True)
    graph[1]["observer"].processData.processTasks[0].NextTaskStart = 2_200_000_000
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_PROCESS_PHASE_OR_PRIORITY_INVALID"):
        capture_two_process_clock(*graph)
    good = capture_two_process_clock(*_graph(True))
    bad_graph = _graph()
    bad_graph[1]["observer"].processData.processPriority = 20
    with pytest.raises(ValueError, match="NATIVE_MULTIPROCESS_FRESH_GRAPH_REQUIRED"):
        restore_two_process_clock_fresh(*bad_graph, good)
    assert bad_graph[0].TotalSim.CurrentNanos == 0
