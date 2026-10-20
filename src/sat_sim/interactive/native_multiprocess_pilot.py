"""Test-only two-process Basilisk absolute scheduler checkpoint prototype.

Fixed 200ms spacecraft process (priority 20), 600ms SimpleNav process
(priority 5). Saves both independent native process scheduling tables, their
priorities, task schedule state, the native simulation next-process priority,
and spacecraft integration clock. Rejects other graphs and never claims
recoverability of hidden module memory, FSW or complete spacecraft state.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "basilisk-native-two-process-clock-pilot.v1"
PROFILE = "isolated-two-process-200ms-dynamics-600ms-nav-v1"
GRAPH = {"dynamics": (200_000_000, 20), "observer": (600_000_000, 5)}
_TASK_FIELDS = {"first_ns", "pickup_ns", "next_start_ns", "period_ns"}
_PROCESS_FIELDS = {"name", "priority", "active", "next_ns", "route_ns",
                   "scheduled_ns", "period_ns", "task_priority"}


def _checksum(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _read(sim: Any, processes: dict[str, Any], tasks: dict[str, Any], sc: Any) -> dict[str, Any]:
    if set(processes) != set(GRAPH) or set(tasks) != set(GRAPH) or len(sim.TaskList) != 2:
        raise ValueError("NATIVE_MULTIPROCESS_GRAPH_MISMATCH")
    if not all(any(t is obj for t in sim.TaskList) for obj in tasks.values()):
        raise ValueError("NATIVE_MULTIPROCESS_TASK_IDENTITY_MISMATCH")
    native = sim.TotalSim
    actual = list(native.processList)
    if len(actual) != 2 or [str(p.processName) for p in actual] != ["dyn", "nav"]:
        raise ValueError("NATIVE_MULTIPROCESS_PRIORITY_ORDER_MISMATCH")
    out_procs = {}
    out_tasks = {}
    for role, (period, priority) in GRAPH.items():
        obj = processes[role].processData
        entries = list(obj.processTasks)
        if len(entries) != 1:
            raise ValueError("NATIVE_MULTIPROCESS_PROCESS_TASK_COUNT_INVALID")
        entry = entries[0]
        out_procs[role] = {
            "name": str(obj.processName),
            "priority": int(obj.processPriority),
            "active": bool(obj.processActive),
            "next_ns": int(obj.nextTaskTime),
            "route_ns": int(obj.prevRouteTime),
            "scheduled_ns": int(entry.NextTaskStart),
            "period_ns": int(entry.TaskUpdatePeriod),
            "task_priority": int(entry.taskPriority),
        }
        obj_task = tasks[role].TaskData
        out_tasks[role] = {
            "first_ns": int(obj_task.FirstTaskTime),
            "pickup_ns": int(obj_task.NextPickupTime),
            "next_start_ns": int(obj_task.NextStartTime),
            "period_ns": int(obj_task.TaskPeriod),
        }
    return {
        "current_ns": int(native.CurrentNanos),
        "next_ns": int(native.NextTaskTime),
        "next_process_priority": int(native.nextProcPriority),
        "processes": out_procs,
        "tasks": out_tasks,
        "hub_before_s": float(sc.timeBefore),
        "hub_before_ns": int(sc.timeBeforeNanos),
        "hub_step_s": float(sc.timeStep),
    }


def _validate(state: dict[str, Any]) -> None:
    if not isinstance(state, dict) or set(state) != {
        "current_ns", "next_ns", "next_process_priority", "processes",
        "tasks", "hub_before_s", "hub_before_ns", "hub_step_s",
    }:
        raise ValueError("NATIVE_MULTIPROCESS_FIELDS_INVALID")
    now = state["current_ns"]
    if type(now) is not int or now <= 0 or now % 200_000_000 or now % 600_000_000 == 0:
        raise ValueError("NATIVE_MULTIPROCESS_CHECKPOINT_PHASE_INVALID")
    if not isinstance(state["processes"], dict) or set(state["processes"]) != set(GRAPH):
        raise ValueError("NATIVE_MULTIPROCESS_PROCESS_SET_INVALID")
    if not isinstance(state["tasks"], dict) or set(state["tasks"]) != set(GRAPH):
        raise ValueError("NATIVE_MULTIPROCESS_TASK_SET_INVALID")
    due = []
    for role, (period, priority) in GRAPH.items():
        task = state["tasks"][role]
        proc = state["processes"][role]
        if (not isinstance(task, dict) or set(task) != _TASK_FIELDS
                or any(type(v) is not int for v in task.values())
                or not isinstance(proc, dict) or set(proc) != _PROCESS_FIELDS):
            raise ValueError("NATIVE_MULTIPROCESS_FIELDS_INVALID")
        next_tick = (now // period + 1) * period
        if (task["first_ns"] != 0 or task["pickup_ns"] != period
                or task["period_ns"] != period
                or task["next_start_ns"] != next_tick):
            raise ValueError("NATIVE_MULTIPROCESS_TASK_PHASE_INVALID")
        if (proc["name"] != ("dyn" if role == "dynamics" else "nav")
                or type(proc["priority"]) is not int or proc["priority"] != priority
                or proc["active"] is not True
                or any(type(proc[k]) is not int for k in (
                    "next_ns", "route_ns", "scheduled_ns", "period_ns", "task_priority",
                ))
                or proc["next_ns"] != next_tick or proc["scheduled_ns"] != next_tick
                or proc["period_ns"] != period or proc["route_ns"] != 0
                or proc["task_priority"] != -1):
            raise ValueError("NATIVE_MULTIPROCESS_PROCESS_PHASE_OR_PRIORITY_INVALID")
        due.append((next_tick, priority))
    earliest = min(t for t, _ in due)
    winner_priority = max(p for t, p in due if t == earliest)
    if (type(state["next_ns"]) is not int or state["next_ns"] != earliest
            or type(state["next_process_priority"]) is not int
            or state["next_process_priority"] != winner_priority):
        raise ValueError("NATIVE_MULTIPROCESS_GLOBAL_SCHEDULE_INVALID")
    if (type(state["hub_before_ns"]) is not int
            or state["hub_before_ns"] != now):
        raise ValueError("NATIVE_MULTIPROCESS_HUB_PHASE_INVALID")
    if any(not isinstance(state[k], (int, float)) or
           not math.isfinite(state[k]) for k in ("hub_before_s", "hub_step_s")):
        raise ValueError("NATIVE_MULTIPROCESS_HUB_TIME_INVALID")
    if (abs(state["hub_before_s"] - now * 1.0e-9) > 1e-10
            or abs(state["hub_step_s"] - 0.2) > 1e-10):
        raise ValueError("NATIVE_MULTIPROCESS_HUB_TIME_INVALID")


def capture_two_process_clock(
    sim: Any, processes: dict[str, Any], tasks: dict[str, Any], spacecraft: Any,
) -> dict[str, Any]:
    state = _read(sim, processes, tasks, spacecraft)
    _validate(state)
    document = {
        "schema_version": SCHEMA, "profile": PROFILE, "state": state,
        "experimental_only": True, "integrator_history_restored": False,
        "module_memory_restored": False, "complete_satellite_resume": False,
    }
    document["digest_sha256"] = _checksum(document)
    return document


def validate_two_process_clock(doc: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("NATIVE_MULTIPROCESS_DOCUMENT_INVALID")
    clean = dict(doc)
    digest = clean.pop("digest_sha256", None)
    if digest != _checksum(clean):
        raise ValueError("NATIVE_MULTIPROCESS_DIGEST_INVALID")
    if clean.get("schema_version") != SCHEMA or clean.get("profile") != PROFILE:
        raise ValueError("NATIVE_MULTIPROCESS_PROFILE_INVALID")
    if (clean.get("experimental_only") is not True
            or clean.get("integrator_history_restored") is not False
            or clean.get("module_memory_restored") is not False
            or clean.get("complete_satellite_resume") is not False):
        raise ValueError("NATIVE_MULTIPROCESS_RESUME_CLAIM_REJECTED")
    state = clean.get("state")
    _validate(state)
    return state


def restore_two_process_clock_fresh(
    sim: Any, processes: dict[str, Any], tasks: dict[str, Any],
    sc: Any, doc: dict[str, Any],
) -> None:
    """Write only into a new, initialized, proven-isomorphic test graph."""
    target = validate_two_process_clock(doc)
    initial = _read(sim, processes, tasks, sc)
    if (initial["current_ns"] != 0 or initial["next_ns"] != 0
            or initial["hub_before_ns"] != 0 or initial["hub_before_s"] != 0.0
            or initial["hub_step_s"] != 0.0
            or any(initial["tasks"][r]["next_start_ns"] != 0 for r in GRAPH)
            or any(initial["processes"][r]["next_ns"] != 0 for r in GRAPH)
            or any(initial["processes"][r]["scheduled_ns"] != 0 for r in GRAPH)
            or any(initial["processes"][r]["priority"] != target["processes"][r]["priority"]
                   for r in GRAPH)
            or any(initial["processes"][r]["period_ns"] != GRAPH[r][0] for r in GRAPH)):
        raise ValueError("NATIVE_MULTIPROCESS_FRESH_GRAPH_REQUIRED")
    native = sim.TotalSim
    native.CurrentNanos = target["current_ns"]
    native.NextTaskTime = target["next_ns"]
    native.nextProcPriority = target["next_process_priority"]
    for role in GRAPH:
        obj = processes[role].processData
        proc = target["processes"][role]
        task = tasks[role].TaskData
        info = target["tasks"][role]
        obj.nextTaskTime = proc["next_ns"]
        obj.prevRouteTime = proc["route_ns"]
        task.FirstTaskTime = info["first_ns"]
        task.NextPickupTime = info["pickup_ns"]
        task.NextStartTime = info["next_start_ns"]
        entry = obj.processTasks[0]
        entry.NextTaskStart = proc["scheduled_ns"]
        obj.processTasks[0] = entry
    sc.timeBefore = target["hub_before_s"]
    sc.timeBeforeNanos = target["hub_before_ns"]
    sc.timeStep = target["hub_step_s"]
    observed = _read(sim, processes, tasks, sc)
    if observed != target:
        diff = {key: (target[key], observed[key]) for key in target if target[key] != observed[key]}
        raise ValueError(f"NATIVE_MULTIPROCESS_READBACK_FAILED:{diff}")
