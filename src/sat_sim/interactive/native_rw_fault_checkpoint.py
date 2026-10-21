"""Fail-closed evidence for a single, deterministic native RW command dropout.

This test-only profile routes actual Basilisk RW command input to a zero
ArrayMotorTorqueMsg, then routes it back to the real FSW allocator output.
It is *interface command loss*, not a claim of internal wheel hardware failure.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "basilisk-rw-command-dropout-checkpoint.v1"
TARGET = "reaction_wheel_state_effector.all_three.motor_command"
FAULT_TYPE = "rw_command_dropout"
STEP_NS = 200_000_000


def _hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def validate_fault_checkpoint(doc: dict[str, Any]) -> dict[str, Any]:
    """Validate unambiguous phase, event times, effect route and safety flags."""
    if not isinstance(doc, dict):
        raise ValueError("RW_FAULT_DOCUMENT_INVALID")
    payload = dict(doc)
    digest = payload.pop("digest_sha256", None)
    if not isinstance(digest, str) or digest != _hash(payload):
        raise ValueError("RW_FAULT_DIGEST_INVALID")
    if set(payload) != {
        "schema_version", "fault_id", "fault_type", "target",
        "checkpoint_ns", "start_ns", "end_ns", "phase", "activated_ns",
        "cleared_ns", "effect_route", "native_checkpoint_complete",
        "resume_supported",
    } or payload["schema_version"] != SCHEMA:
        raise ValueError("RW_FAULT_SCHEMA_INVALID")
    if (payload["native_checkpoint_complete"] is not False
            or payload["resume_supported"] is not False):
        raise ValueError("RW_FAULT_FALSE_RESUME_CLAIM")
    if payload["fault_type"] != FAULT_TYPE or payload["target"] != TARGET:
        raise ValueError("RW_FAULT_UNSUPPORTED_PROFILE")
    identifier = payload["fault_id"]
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        raise ValueError("RW_FAULT_ID_INVALID")
    now, start, end = (payload[key] for key in ("checkpoint_ns", "start_ns", "end_ns"))
    if any(type(x) is not int or x < 0 or x % STEP_NS for x in (now, start, end)):
        raise ValueError("RW_FAULT_CLOCK_INVALID")
    if now <= 0 or not (0 < start < end):
        raise ValueError("RW_FAULT_WINDOW_INVALID")
    phase = "scheduled" if now < start else "active" if now < end else "cleared"
    if payload["phase"] != phase:
        raise ValueError("RW_FAULT_PHASE_MISMATCH")
    expected_activated = start if now >= start else None
    expected_cleared = end if now >= end else None
    expected_route = "zero_motor_command" if phase == "active" else "fsw_allocator"
    if (payload["activated_ns"] != expected_activated
            or payload["cleared_ns"] != expected_cleared
            or payload["effect_route"] != expected_route):
        raise ValueError("RW_FAULT_LIFECYCLE_MISMATCH")
    return payload


def capture_fault_checkpoint(*, fault_id: str, checkpoint_ns: int,
                             start_ns: int, end_ns: int) -> dict[str, Any]:
    phase = "scheduled" if checkpoint_ns < start_ns else (
        "active" if checkpoint_ns < end_ns else "cleared"
    )
    report = {
        "schema_version": SCHEMA,
        "fault_id": fault_id,
        "fault_type": FAULT_TYPE,
        "target": TARGET,
        "checkpoint_ns": checkpoint_ns,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "phase": phase,
        "activated_ns": start_ns if checkpoint_ns >= start_ns else None,
        "cleared_ns": end_ns if checkpoint_ns >= end_ns else None,
        "effect_route": "zero_motor_command" if phase == "active" else "fsw_allocator",
        "native_checkpoint_complete": False,
        "resume_supported": False,
    }
    report["digest_sha256"] = _hash(report)
    validate_fault_checkpoint(report)
    return report


def apply_native_motor_dropout(graph: dict[str, Any], *, active: bool) -> None:
    """Change actual RW native input routing; never fake the dynamics state."""
    rw_input = graph["rw"].rwMotorCmdInMsg
    if active:
        from Basilisk.architecture import messaging
        if "rw_fault_zero_message" not in graph:
            payload = messaging.ArrayMotorTorqueMsgPayload()
            # The motor command is a MAX_EFF_CNT vector, not just 3 items.
            payload.motorTorque = [0.] * len(
                graph["allocator"].rwMotorTorqueOutMsg.read().motorTorque
            )
            graph["rw_fault_zero_message"] = messaging.ArrayMotorTorqueMsg().write(payload)
        expected = graph["rw_fault_zero_message"]
    else:
        expected = graph["allocator"].rwMotorTorqueOutMsg
    rw_input.subscribeTo(expected)
    observed = [float(x) for x in rw_input.read().motorTorque]
    target = [float(x) for x in expected.read().motorTorque]
    if observed != target:
        raise ValueError("RW_FAULT_NATIVE_SUBSCRIPTION_READBACK_FAILED")


def restore_native_fault_route_fresh(
    graph: dict[str, Any], document: dict[str, Any],
) -> None:
    """Only restores one known command route on a new, unadvanced pilot graph."""
    data = validate_fault_checkpoint(document)
    if int(graph["sim"].TotalSim.CurrentNanos) != 0:
        raise ValueError("RW_FAULT_RESTORE_REQUIRES_FRESH_GRAPH")
    apply_native_motor_dropout(graph, active=data["phase"] == "active")
