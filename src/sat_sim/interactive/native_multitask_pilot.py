"""Isolated Basilisk TWO-task scheduling phase export/import pilot.

For a single native process with exactly two tasks (200ms dynamics, 600ms
observer). Snapshots are taken on a fast tick but OFF the slow task tick.
This module never restores general Basilisk/FSM state or exposes production
checkpoint/restart. All compatibility guards run before native field writes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "basilisk-native-two-task-clock-pilot.v1"
PROFILE = "isolated-one-process-200ms-dynamics-600ms-nav-v1"
_FAST_NS = 200_000_000
_SLOW_NS = 600_000_000
_KEYS = (
    "sim_current_ns", "sim_next_task_ns", "process_next_task_ns",
    "process_prev_route_ns", "hub_time_before_s", "hub_time_before_ns",
    "hub_time_step_s", "tasks",
)
_TASK_KEYS = (
    "first_ns", "pickup_ns", "next_start_ns", "period_ns",
)


def _hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _read(sim: Any, process: Any, fast: Any, slow: Any, spacecraft: Any) -> dict[str, Any]:
    if len(sim.TaskList) != 2 or not all(
        any(task is candidate for candidate in sim.TaskList)
        for task in (fast, slow)
    ) or fast is slow:
        raise ValueError("NATIVE_MULTITASK_GRAPH_MISMATCH")
    t = sim.TotalSim
    p = process.processData
    return {
        "sim_current_ns": int(t.CurrentNanos),
        "sim_next_task_ns": int(t.NextTaskTime),
        "process_next_task_ns": int(p.nextTaskTime),
        "process_prev_route_ns": int(p.prevRouteTime),
        "hub_time_before_s": float(spacecraft.timeBefore),
        "hub_time_before_ns": int(spacecraft.timeBeforeNanos),
        "hub_time_step_s": float(spacecraft.timeStep),
        "tasks": {
            "dynamics": {
                "first_ns": int(fast.TaskData.FirstTaskTime),
                "pickup_ns": int(fast.TaskData.NextPickupTime),
                "next_start_ns": int(fast.TaskData.NextStartTime),
                "period_ns": int(fast.TaskData.TaskPeriod),
            },
            "observer": {
                "first_ns": int(slow.TaskData.FirstTaskTime),
                "pickup_ns": int(slow.TaskData.NextPickupTime),
                "next_start_ns": int(slow.TaskData.NextStartTime),
                "period_ns": int(slow.TaskData.TaskPeriod),
            },
        },
    }


def _validate(fields: dict[str, Any]) -> None:
    if not isinstance(fields, dict) or set(fields) != set(_KEYS):
        raise ValueError("NATIVE_MULTITASK_FIELDS_INVALID")
    now = fields["sim_current_ns"]
    if type(now) is not int or now <= 0 or now % _FAST_NS != 0:
        raise ValueError("NATIVE_MULTITASK_OFF_FAST_GRID")
    if now % _SLOW_NS == 0:
        raise ValueError("NATIVE_MULTITASK_CHECKPOINT_MUST_EXPOSE_SLOW_PHASE")
    expected = {"dynamics": _FAST_NS, "observer": _SLOW_NS}
    if not isinstance(fields["tasks"], dict) or set(fields["tasks"]) != set(expected):
        raise ValueError("NATIVE_MULTITASK_TASK_SET_INVALID")
    for name, period in expected.items():
        row = fields["tasks"][name]
        if not isinstance(row, dict) or set(row) != set(_TASK_KEYS):
            raise ValueError("NATIVE_MULTITASK_TASK_FIELDS_INVALID")
        if any(type(value) is not int for value in row.values()):
            raise ValueError("NATIVE_MULTITASK_TASK_TYPE_INVALID")
        if row["period_ns"] != period or row["first_ns"] != 0:
            raise ValueError("NATIVE_MULTITASK_PERIOD_MISMATCH")
        if row["pickup_ns"] != period:
            raise ValueError("NATIVE_MULTITASK_PICKUP_MISMATCH")
        next_expected = (now // period + 1) * period
        if row["next_start_ns"] != next_expected:
            raise ValueError("NATIVE_MULTITASK_PHASE_MISMATCH")
    next_fast = fields["tasks"]["dynamics"]["next_start_ns"]
    next_slow = fields["tasks"]["observer"]["next_start_ns"]
    next_due = min(next_fast, next_slow)
    for key in ("sim_next_task_ns", "process_next_task_ns"):
        if type(fields[key]) is not int or fields[key] != next_due:
            raise ValueError("NATIVE_MULTITASK_NEXT_TASK_MISMATCH")
    if type(fields["process_prev_route_ns"]) is not int or fields["process_prev_route_ns"] != 0:
        raise ValueError("NATIVE_MULTITASK_UNSUPPORTED_PROCESS_ROUTING")
    if type(fields["hub_time_before_ns"]) is not int or fields["hub_time_before_ns"] != now:
        raise ValueError("NATIVE_MULTITASK_HUB_PHASE_MISMATCH")
    if (type(fields["hub_time_before_s"]) not in (int, float)
            or type(fields["hub_time_step_s"]) not in (int, float)
            or abs(fields["hub_time_before_s"] - now * 1e-9) > 1e-10
            or abs(fields["hub_time_step_s"] - _FAST_NS * 1e-9) > 1e-10):
        raise ValueError("NATIVE_MULTITASK_HUB_TIME_MISMATCH")


def capture_two_task_clock(
    sim: Any, process: Any, fast: Any, slow: Any, spacecraft: Any,
) -> dict[str, Any]:
    fields = _read(sim, process, fast, slow, spacecraft)
    _validate(fields)
    data = {
        "schema_version": SCHEMA,
        "profile": PROFILE,
        "fields": fields,
        "test_only": True,
        "native_scheduler_and_hub_phase_writable": True,
        "observer_module_private_state_restored": False,
        "integrator_internal_history_restored": False,
        "full_spacecraft_resume_supported": False,
    }
    data["digest_sha256"] = _hash(data)
    return data


def validate_two_task_clock(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("NATIVE_MULTITASK_DOCUMENT_INVALID")
    data = dict(document)
    checksum = data.pop("digest_sha256", None)
    if checksum != _hash(data):
        raise ValueError("NATIVE_MULTITASK_DIGEST_INVALID")
    if data.get("schema_version") != SCHEMA or data.get("profile") != PROFILE:
        raise ValueError("NATIVE_MULTITASK_PROFILE_INVALID")
    if (
        data.get("test_only") is not True
        or data.get("native_scheduler_and_hub_phase_writable") is not True
        or data.get("observer_module_private_state_restored") is not False
        or data.get("integrator_internal_history_restored") is not False
        or data.get("full_spacecraft_resume_supported") is not False
    ):
        raise ValueError("NATIVE_MULTITASK_RESUME_CLAIM_REJECTED")
    fields = data.get("fields")
    _validate(fields)
    return fields


def restore_two_task_clock_fresh(
    sim: Any, process: Any, fast: Any, slow: Any, spacecraft: Any,
    document: dict[str, Any],
) -> None:
    """TEST-ONLY restore into an initialized, untouched two-task graph."""
    fields = validate_two_task_clock(document)
    initial = _read(sim, process, fast, slow, spacecraft)
    if (
        initial["sim_current_ns"] != 0
        or initial["sim_next_task_ns"] != 0
        or initial["process_next_task_ns"] != 0
        or initial["hub_time_before_ns"] != 0
        or initial["hub_time_before_s"] != 0
        or initial["hub_time_step_s"] != 0
        or initial["tasks"]["dynamics"]["period_ns"] != _FAST_NS
        or initial["tasks"]["observer"]["period_ns"] != _SLOW_NS
        or any(row["next_start_ns"] != 0 for row in initial["tasks"].values())
    ):
        raise ValueError("NATIVE_MULTITASK_FRESH_GRAPH_REQUIRED")
    native = sim.TotalSim
    pd = process.processData
    native.CurrentNanos = fields["sim_current_ns"]
    native.NextTaskTime = fields["sim_next_task_ns"]
    pd.nextTaskTime = fields["process_next_task_ns"]
    pd.prevRouteTime = fields["process_prev_route_ns"]
    for obj, row in ((fast.TaskData, fields["tasks"]["dynamics"]),
                     (slow.TaskData, fields["tasks"]["observer"])):
        obj.FirstTaskTime = row["first_ns"]
        obj.NextPickupTime = row["pickup_ns"]
        obj.NextStartTime = row["next_start_ns"]
    spacecraft.timeBefore = fields["hub_time_before_s"]
    spacecraft.timeBeforeNanos = fields["hub_time_before_ns"]
    spacecraft.timeStep = fields["hub_time_step_s"]
    if _read(sim, process, fast, slow, spacecraft) != fields:
        raise ValueError("NATIVE_MULTITASK_READBACK_FAILED")
