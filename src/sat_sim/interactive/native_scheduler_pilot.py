"""Isolated Basilisk native scheduler and spacecraft integration-time pilot.

Test-only explicit export and import of a *single* periodic task's observable
clock fields. Not a public session checkpoint: model-private variables, input
history, FSW, additional tasks/processes and integrator stages are not covered.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "basilisk-native-single-task-clock-pilot.v1"
PROFILE = "isolated-single-task-fixed-step-earth-hub-rw-v1"


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"NATIVE_CLOCK_INVALID_VALUE:{name}")
    num = float(value)
    if not math.isfinite(num):
        raise ValueError(f"NATIVE_CLOCK_INVALID_VALUE:{name}")
    return num


def _read_clock(sim: Any, process: Any, task: Any, spacecraft: Any) -> dict[str, Any]:
    if len(sim.TaskList) != 1 or sim.TaskList[0] is not task:
        raise ValueError("NATIVE_CLOCK_PILOT_REQUIRES_SINGLE_TASK")
    native = sim.TotalSim
    proc = process.processData
    model_task = task.TaskData
    return {
        "sim_current_ns": int(native.CurrentNanos),
        "sim_next_task_ns": int(native.NextTaskTime),
        "process_next_task_ns": int(proc.nextTaskTime),
        "process_prev_route_ns": int(proc.prevRouteTime),
        "task_first_ns": int(model_task.FirstTaskTime),
        "task_pickup_ns": int(model_task.NextPickupTime),
        "task_next_start_ns": int(model_task.NextStartTime),
        "task_period_ns": int(model_task.TaskPeriod),
        "spacecraft_time_before_s": _finite(spacecraft.timeBefore, "spacecraft_time_before_s"),
        "spacecraft_time_before_ns": int(spacecraft.timeBeforeNanos),
        "spacecraft_time_step_s": _finite(spacecraft.timeStep, "spacecraft_time_step_s"),
    }


def _validate_clock(fields: dict[str, Any]) -> None:
    expected = {
        "sim_current_ns", "sim_next_task_ns", "process_next_task_ns",
        "process_prev_route_ns", "task_first_ns", "task_pickup_ns",
        "task_next_start_ns", "task_period_ns",
        "spacecraft_time_before_s", "spacecraft_time_before_ns", "spacecraft_time_step_s",
    }
    if set(fields) != expected:
        raise ValueError("NATIVE_CLOCK_FIELDS_MISMATCH")
    for key, val in fields.items():
        _finite(val, key)
        if key.endswith("_ns") and (not isinstance(val, int) or isinstance(val, bool)):
            raise ValueError(f"NATIVE_CLOCK_NOT_INTEGER:{key}")
    now = fields["sim_current_ns"]
    step = fields["task_period_ns"]
    if now <= 0 or step <= 0 or now % step != 0:
        raise ValueError("NATIVE_CLOCK_OFF_FIXED_GRID")
    if (
        fields["sim_next_task_ns"] != now + step
        or fields["process_next_task_ns"] != now + step
        or fields["task_next_start_ns"] != now + step
        or fields["task_first_ns"] != 0
        or fields["task_pickup_ns"] != step
        or fields["process_prev_route_ns"] != 0
        or fields["spacecraft_time_before_ns"] != now
    ):
        raise ValueError("NATIVE_CLOCK_UNSUPPORTED_PHASE")
    if (
        abs(fields["spacecraft_time_before_s"] - now * 1e-9) > 1e-10
        or abs(fields["spacecraft_time_step_s"] - step * 1e-9) > 1e-10
    ):
        raise ValueError("NATIVE_CLOCK_INTEGRATION_TIME_MISMATCH")


def capture_single_task_clock(
    sim: Any, process: Any, task: Any, spacecraft: Any,
) -> dict[str, Any]:
    """Capture an initialized and advanced isolated test graph."""
    fields = _read_clock(sim, process, task, spacecraft)
    _validate_clock(fields)
    data = {
        "schema_version": SCHEMA, "graph_profile": PROFILE, "fields": fields,
        "single_task_test_only": True, "native_clock_fields_writable_in_pilot": True,
        "complete_integrator_history_restored": False,
        "hidden_subsystem_state_restored": False,
        "full_spacecraft_resume_supported": False,
    }
    data["digest_sha256"] = hashlib.sha256(_canonical(data)).hexdigest()
    return data


def validate_single_task_clock(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("NATIVE_CLOCK_PAYLOAD_INVALID")
    clean = dict(document)
    digest = clean.pop("digest_sha256", None)
    if digest != hashlib.sha256(_canonical(clean)).hexdigest():
        raise ValueError("NATIVE_CLOCK_DIGEST_INVALID")
    if clean.get("schema_version") != SCHEMA or clean.get("graph_profile") != PROFILE:
        raise ValueError("NATIVE_CLOCK_PROFILE_UNSUPPORTED")
    if (
        clean.get("single_task_test_only") is not True
        or clean.get("native_clock_fields_writable_in_pilot") is not True
        or clean.get("complete_integrator_history_restored") is not False
        or clean.get("hidden_subsystem_state_restored") is not False
        or clean.get("full_spacecraft_resume_supported") is not False
    ):
        raise ValueError("NATIVE_CLOCK_UNPROVEN_RESUME_CLAIM")
    fields = clean.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("NATIVE_CLOCK_FIELDS_MISMATCH")
    _validate_clock(fields)
    return fields


def restore_single_task_clock_on_fresh_graph(
    sim: Any, process: Any, task: Any, spacecraft: Any, document: dict[str, Any],
) -> None:
    """TEST-ONLY writeback to a fresh, initialized, single-task native graph.

    Do not expose to HTTP/TC: this contract has no guarantee for extra tasks,
    C++ integrator stages, model-private time dependence or absolute events.
    """
    fields = validate_single_task_clock(document)
    fresh = _read_clock(sim, process, task, spacecraft)
    if (
        fresh["sim_current_ns"] != 0 or fresh["sim_next_task_ns"] != 0
        or fresh["process_next_task_ns"] != 0 or fresh["task_next_start_ns"] != 0
        or fresh["spacecraft_time_before_ns"] != 0
        or fresh["task_period_ns"] != fields["task_period_ns"]
    ):
        raise ValueError("NATIVE_CLOCK_RESTORE_REQUIRES_FRESH_GRAPH")
    native = sim.TotalSim
    proc = process.processData
    model_task = task.TaskData
    native.CurrentNanos = fields["sim_current_ns"]
    native.NextTaskTime = fields["sim_next_task_ns"]
    proc.nextTaskTime = fields["process_next_task_ns"]
    proc.prevRouteTime = fields["process_prev_route_ns"]
    model_task.FirstTaskTime = fields["task_first_ns"]
    model_task.NextPickupTime = fields["task_pickup_ns"]
    model_task.NextStartTime = fields["task_next_start_ns"]
    spacecraft.timeBefore = fields["spacecraft_time_before_s"]
    spacecraft.timeBeforeNanos = fields["spacecraft_time_before_ns"]
    spacecraft.timeStep = fields["spacecraft_time_step_s"]
    if _read_clock(sim, process, task, spacecraft) != fields:
        raise ValueError("NATIVE_CLOCK_WRITEBACK_VERIFY_FAILED")
