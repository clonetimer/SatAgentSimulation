"""R9-E: test-only unified whole-spacecraft clock + C FSW memory memento.

Actual Basilisk scheduler SWIG objects and native mrpFeedback C module are
read on the SAME paused whole graph. The memento is NOT a restart checkpoint:
hidden integrator states, queues, message histories, RW private memory, and
EPS/thermal/comm modules remain un-restored. Never execute a tick after import.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "sat-native-whole-clock-fsw-memento-lab.v1"
SCOPE = "WHOLE_GRAPH_SINGLE_PROCESS_SINGLE_TASK_PRETICK_LAB"


def _checksum(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _clock(sim: Any, process: Any, task: Any, sc: Any) -> dict[str, Any]:
    if len(sim.TaskList) != 1 or sim.TaskList[0] is not task:
        raise ValueError("R9E_UNEXPECTED_TASK_GRAPH")
    native = sim.TotalSim
    if len(native.processList) != 1:
        raise ValueError("R9E_UNEXPECTED_PROCESS_GRAPH")
    p = process.processData
    entries = list(p.processTasks)
    if len(entries) != 1:
        raise ValueError("R9E_UNEXPECTED_PROCESS_TASKS")
    entry = entries[0]
    t = task.TaskData
    return {
        "current_ns": int(native.CurrentNanos),
        "next_ns": int(native.NextTaskTime),
        "process_name": str(p.processName),
        "process_priority": int(p.processPriority),
        "process_active": bool(p.processActive),
        "process_next_ns": int(p.nextTaskTime),
        "process_route_ns": int(p.prevRouteTime),
        "process_task_start_ns": int(entry.NextTaskStart),
        "process_task_period_ns": int(entry.TaskUpdatePeriod),
        "process_task_priority": int(entry.taskPriority),
        "task_first_ns": int(t.FirstTaskTime),
        "task_pickup_ns": int(t.NextPickupTime),
        "task_next_ns": int(t.NextStartTime),
        "task_period_ns": int(t.TaskPeriod),
        "hub_time_before_s": float(sc.timeBefore),
        "hub_time_before_ns": int(sc.timeBeforeNanos),
        "hub_step_s": float(sc.timeStep),
    }


def _fsw(controller: Any) -> dict[str, Any]:
    return {
        "cached_rw_count": int(controller.rwConfigParams.numRW),
        "prior_time_ns": int(controller.priorTime),
        "K": float(controller.K),
        "P": float(controller.P),
        "Ki": float(controller.Ki),
        "integral_limit": float(controller.integralLimit),
        "int_sigma": [float(v) for v in controller.int_sigma],
        "z": [float(v) for v in controller.z],
    }


def validate_memento(
    doc: dict[str, Any], *, source_time_ns: int | None = None,
    expected_rw_count: int | None = None,
    expected_fsw: dict[str, Any] | None = None,
) -> None:
    if not isinstance(doc, dict):
        raise ValueError("R9E_MEMENTO_INVALID")
    raw = dict(doc)
    checksum = raw.pop("sha256", None)
    if checksum != _checksum(raw):
        raise ValueError("R9E_MEMENTO_DIGEST_MISMATCH")
    if set(raw) != {"schema", "scope", "clock", "fsw",
                    "test_only", "private_states_complete",
                    "integrator_restored", "message_history_restored",
                    "whole_resume_supported", "fresh_process_parity_proven"}:
        raise ValueError("R9E_MEMENTO_SCHEMA_INVALID")
    if raw["schema"] != SCHEMA or raw["scope"] != SCOPE or raw["test_only"] is not True:
        raise ValueError("R9E_MEMENTO_SCOPE_INVALID")
    if any(raw[k] is not False for k in (
        "private_states_complete", "integrator_restored",
        "message_history_restored", "whole_resume_supported",
        "fresh_process_parity_proven",
    )):
        raise ValueError("R9E_MEMENTO_FALSE_RESTORE_CLAIM")
    c, f = raw["clock"], raw["fsw"]
    if not isinstance(c, dict) or set(c) != {
        "current_ns", "next_ns", "process_name", "process_priority",
        "process_active", "process_next_ns", "process_route_ns",
        "process_task_start_ns", "process_task_period_ns",
        "process_task_priority", "task_first_ns", "task_pickup_ns",
        "task_next_ns", "task_period_ns", "hub_time_before_s",
        "hub_time_before_ns", "hub_step_s",
    }:
        raise ValueError("R9E_CLOCK_SCHEMA_INVALID")
    integers = [k for k in c if k.endswith("_ns") or k in (
        "process_priority", "process_task_priority",
    )]
    if any(type(c[k]) is not int for k in integers):
        raise ValueError("R9E_CLOCK_INTEGER_INVALID")
    if (c["process_name"] != "UnifiedNativeProcess" or
        c["process_active"] is not True or
        c["task_period_ns"] <= 0 or
        c["process_task_period_ns"] != c["task_period_ns"] or
        c["current_ns"] <= 0 or
        (source_time_ns is not None and c["current_ns"] != source_time_ns) or
        c["next_ns"] < c["current_ns"] or
        c["task_next_ns"] < c["current_ns"] or
        c["process_next_ns"] < c["current_ns"] or
        c["process_task_start_ns"] < c["current_ns"]):
        raise ValueError("R9E_CLOCK_PHASE_INVALID")
    if any(not math.isfinite(c[k]) for k in ("hub_time_before_s", "hub_step_s")):
        raise ValueError("R9E_CLOCK_NONFINITE")
    if abs(c["hub_time_before_s"] - c["hub_time_before_ns"]*1e-9) > 1e-9:
        raise ValueError("R9E_CLOCK_HUB_TIME_INVALID")
    if not isinstance(f, dict) or set(f) != {
        "cached_rw_count", "prior_time_ns", "K", "P", "Ki",
        "integral_limit", "int_sigma", "z",
    }:
        raise ValueError("R9E_FSW_SCHEMA_INVALID")
    if type(f["cached_rw_count"]) is not int or f["cached_rw_count"] not in (3, 4):
        raise ValueError("R9E_FSW_TOPOLOGY_INVALID")
    if expected_rw_count is not None and f["cached_rw_count"] != expected_rw_count:
        raise ValueError("R9E_FSW_TOPOLOGY_INVALID")
    if type(f["prior_time_ns"]) is not int or not 0 <= f["prior_time_ns"] <= c["current_ns"]:
        raise ValueError("R9E_FSW_TIME_INVALID")
    for key in ("K", "P", "Ki", "integral_limit"):
        if type(f[key]) not in (int, float) or not math.isfinite(f[key]):
            raise ValueError("R9E_FSW_NONFINITE")
    for key in ("int_sigma", "z"):
        if (not isinstance(f[key], list) or len(f[key]) != 3 or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in f[key])):
            raise ValueError("R9E_FSW_VECTOR_INVALID")
    if expected_fsw is not None and f != expected_fsw:
        raise ValueError("R9E_FSW_OWNER_OBSERVATION_MISMATCH")


def capture_memento(
    *, sim: Any, process: Any, task: Any, spacecraft: Any,
    controller: Any, expected_rw_count: int,
) -> dict[str, Any]:
    result = {
        "schema": SCHEMA, "scope": SCOPE,
        "clock": _clock(sim, process, task, spacecraft),
        "fsw": _fsw(controller),
        "test_only": True,
        "private_states_complete": False,
        "integrator_restored": False,
        "message_history_restored": False,
        "whole_resume_supported": False,
        "fresh_process_parity_proven": False,
    }
    result["sha256"] = _checksum(result)
    validate_memento(result, expected_rw_count=expected_rw_count)
    return result


def import_memento_fresh_lab(
    *, sim: Any, process: Any, task: Any, spacecraft: Any,
    controller: Any, checkpoint: dict[str, Any],
    source_audit: dict[str, Any],
) -> dict[str, Any]:
    """Test-only import. Call from the private pre-tick hook; never run again."""
    from .native_checkpoint_audit import verify_coverage_report
    from .native_recovery_readiness import build_recovery_readiness
    from .native_state_pilot import validate_state_subset

    verify_coverage_report(source_audit)
    gate = build_recovery_readiness(source_audit)
    if gate["automatic_resume_allowed"] is not False or gate["restorable_owner_count"] != 0:
        raise ValueError("R9E_RESTART_GATE_NOT_BLOCKED")
    source_adcs = source_audit["native_adcs_owner_observations"]
    source_joint = source_audit["native_joint_owner_observations"]
    source_vectors = source_audit["native_state_subset_pilot"]
    if source_adcs is None or source_joint is None or source_vectors is None:
        raise ValueError("R9E_SOURCE_OWNER_EVIDENCE_MISSING")
    count = source_adcs["rw_count"]
    validate_state_subset(source_vectors, expected_rw_count=count)
    source_ns = int(source_joint["capture_time_ns"])
    validate_memento(checkpoint, source_time_ns=source_ns, expected_rw_count=count,
                     expected_fsw=source_adcs["fsw_memory"])
    fresh = _clock(sim, process, task, spacecraft)
    if (fresh["current_ns"] != 0 or fresh["next_ns"] != 0
            or fresh["process_next_ns"] != 0 or fresh["task_next_ns"] != 0
            or fresh["process_task_start_ns"] != 0 or
            fresh["task_period_ns"] != checkpoint["clock"]["task_period_ns"] or
            fresh["process_name"] != checkpoint["clock"]["process_name"] or
            fresh["process_priority"] != checkpoint["clock"]["process_priority"] or
            fresh["process_task_priority"] != checkpoint["clock"]["process_task_priority"]):
        raise ValueError("R9E_REQUIRES_FRESH_ISOMORPHIC_GRAPH")
    initial_fsw = _fsw(controller)
    expected_fsw = checkpoint["fsw"]
    if (initial_fsw["cached_rw_count"] != count or
        any(initial_fsw[k] != expected_fsw[k] for k in ("K", "P", "Ki", "integral_limit")) or
        initial_fsw["prior_time_ns"] != 0 or
        any(any(abs(v)>1e-14 for v in initial_fsw[k]) for k in ("int_sigma", "z"))):
        raise ValueError("R9E_REQUIRES_FRESH_FSW_MEMORY")

    # Owner inputs and target topology are checked BEFORE mutating a state.
    clock = checkpoint["clock"]
    sim.TotalSim.CurrentNanos = clock["current_ns"]
    sim.TotalSim.NextTaskTime = clock["next_ns"]
    p = process.processData
    p.nextTaskTime = clock["process_next_ns"]
    p.prevRouteTime = clock["process_route_ns"]
    t = task.TaskData
    t.FirstTaskTime = clock["task_first_ns"]
    t.NextPickupTime = clock["task_pickup_ns"]
    t.NextStartTime = clock["task_next_ns"]
    entry = p.processTasks[0]
    entry.NextTaskStart = clock["process_task_start_ns"]
    p.processTasks[0] = entry
    spacecraft.timeBefore = clock["hub_time_before_s"]
    spacecraft.timeBeforeNanos = clock["hub_time_before_ns"]
    spacecraft.timeStep = clock["hub_step_s"]
    controller.priorTime = expected_fsw["prior_time_ns"]
    controller.int_sigma = list(expected_fsw["int_sigma"])
    controller.z = list(expected_fsw["z"])
    read_clock = _clock(sim, process, task, spacecraft)
    read_fsw = _fsw(controller)
    if read_clock != clock or read_fsw != expected_fsw:
        raise ValueError("R9E_NATIVE_CLOCK_FSW_READBACK_FAILED")
    return {
        "original_source_time_ns": source_ns,
        "fresh_time_before_import_ns": fresh["current_ns"],
        "native_clock_readback": read_clock,
        "native_fsw_readback": read_fsw,
        "clock_and_fsw_subset_imported": True,
        "other_owner_states_restored": False,
        "integrator_hidden_stages_restored": False,
        "complete_scheduler_event_queues_restored": False,
        "whole_spacecraft_parity_proven": False,
        "automatic_resume_allowed": False,
        "resume_supported": False,
    }
