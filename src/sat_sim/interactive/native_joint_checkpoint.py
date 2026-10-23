"""Restricted R5 cross-owner checkpoint evidence for a genuine Basilisk ADCS pilot.

This composes, does NOT replace, the R1-R4 independently verified native
physics, C-FSW, 3RW, native two-process clock, mode journal, RW input-dropout
lifecycle and durable TC checkpoint. It is a test-only cross-owner contract.

All digests are corruption indicators, NOT authenticated signatures.
No generic spacecraft or production resume entitlement is implied.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .native_closed_loop_evidence import verify_closed_loop_evidence
from .native_mode_checkpoint import validate_mode_checkpoint
from .native_rw_fault_checkpoint import validate_fault_checkpoint
from .native_tc_reconciliation import validate_tc_snapshot, recovery_plan

SCHEMA = "basilisk-joint-adcs-fault-tc-checkpoint.v1"
PROFILE = "two-process-200ms-rw-dropout-mode-tc-overlap-v1"
OWNER_KEYS = frozenset({"physics", "mode", "fault", "tc"})
STOPPED_OWNERS = (
    "general_fsw_event_task_activation",
    "basilisk_hidden_message_histories",
    "full_integrator_internal_stages",
    "other_spacecraft_subsystems",
    "production_tc_ack_outcome_reconciliation",
)
CP_NS = 2_000_000_000
FAULT_START_NS = 1_800_000_000
TC_DUE_NS = 2_200_000_000
FAULT_END_NS = 3_200_000_000


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _cross_owner_consistency(*, session_id: str, clock_ns: int,
                             physics: dict[str, Any], mode: dict[str, Any],
                             fault: dict[str, Any], tc: dict[str, Any]) -> None:
    """Check causal identity and task/event ordering independently of digests.

    Called only after each owner has validated its own integrity and schema.
    Exact times/profile intentionally restrict this pilot, not a general API.
    """
    if clock_ns != CP_NS or type(clock_ns) is not int:
        raise ValueError("JOINT_CHECKPOINT_UNSUPPORTED_PHASE")
    if (physics["checkpoint_sim_time_ns"] != clock_ns
            or physics["native_scheduler"]["state"]["current_ns"] != clock_ns
            or mode["checkpoint_ns"] != clock_ns
            or fault["checkpoint_ns"] != clock_ns
            or tc["checkpoint_ns"] != clock_ns):
        raise ValueError("JOINT_CHECKPOINT_OWNER_TIME_MISMATCH")
    if tc["session_id"] != session_id or tc["epoch"] != 1:
        raise ValueError("JOINT_CHECKPOINT_TC_SESSION_OR_EPOCH_MISMATCH")

    state = physics["native_scheduler"]["state"]
    if (state["next_ns"] != TC_DUE_NS
            or state["processes"]["dynamics"]["scheduled_ns"] != TC_DUE_NS
            or state["processes"]["observer"]["scheduled_ns"] != 2_400_000_000
            or physics["fsw_integrator"]["prior_time_ns"] != FAULT_START_NS):
        raise ValueError("JOINT_CHECKPOINT_NATIVE_CLOCK_PHASE_MISMATCH")
    if (mode["current_mode"] != "inertial3d" or not mode["transitions"]
            or mode["transitions"][-1]["status"] != "pending"):
        raise ValueError("JOINT_CHECKPOINT_MODE_NOT_PENDING")
    transition = mode["transitions"][-1]
    if (transition["from_mode"] != mode["current_mode"]
            or transition["requested_ns"] > clock_ns
            or transition["resolved_ns"] is not None):
        raise ValueError("JOINT_CHECKPOINT_MODE_CAUSALITY_INVALID")

    if (fault["phase"] != "active"
            or fault["start_ns"] != FAULT_START_NS
            or fault["end_ns"] != FAULT_END_NS
            or fault["effect_route"] != "zero_motor_command"):
        raise ValueError("JOINT_CHECKPOINT_ACTIVE_FAULT_MISMATCH")
    if (len(tc["commands"]) != 1 or tc["commands"][0]["state"] != "QUEUED"
            or tc["commands"][0]["execute_at_ns"] != TC_DUE_NS):
        raise ValueError("JOINT_CHECKPOINT_FUTURE_TC_MISMATCH")
    command = tc["commands"][0]
    if (command["command_id"] != transition["event_id"]
            or command["target_mode"] != transition["to_mode"]
            or command["target_mode"] != "nadir"):
        raise ValueError("JOINT_CHECKPOINT_TC_MODE_CAUSALITY_MISMATCH")
    if not (FAULT_START_NS < clock_ns < TC_DUE_NS < FAULT_END_NS):
        raise ValueError("JOINT_CHECKPOINT_EVENT_ORDER_INVALID")


def capture_joint_checkpoint(*, session_id: str, physics: dict[str, Any],
                             mode: dict[str, Any], fault: dict[str, Any],
                             tc: dict[str, Any]) -> dict[str, Any]:
    """Capture a strict immutable-format nested record, fail-closed by default."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        raise ValueError("JOINT_CHECKPOINT_SESSION_INVALID")
    # Independent owners must be self-consistent first.
    verify_closed_loop_evidence(physics)
    validate_mode_checkpoint(mode)
    validate_fault_checkpoint(fault)
    validate_tc_snapshot(tc)
    _cross_owner_consistency(
        session_id=session_id, clock_ns=CP_NS, physics=physics,
        mode=mode, fault=fault, tc=tc,
    )
    owners = {
        "physics": physics, "mode": mode, "fault": fault, "tc": tc,
    }
    report = {
        "schema_version": SCHEMA, "profile": PROFILE,
        "session_id": session_id, "checkpoint_ns": CP_NS,
        "owners": owners,
        "owner_digests_sha256": {key: _digest(value) for key, value in owners.items()},
        "event_order": {
            "fault_activate_ns": FAULT_START_NS,
            "tc_execute_ns": TC_DUE_NS,
            "fault_clear_ns": FAULT_END_NS,
        },
        "unsupported_owners": list(STOPPED_OWNERS),
        "experimental_only": True,
        "resume_supported": False,
    }
    report["digest_sha256"] = _digest(report)
    verify_joint_checkpoint(report)
    return report


def verify_joint_checkpoint(report: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict):
        raise ValueError("JOINT_CHECKPOINT_DOCUMENT_INVALID")
    data = dict(report)
    digest = data.pop("digest_sha256", None)
    if not isinstance(digest, str) or digest != _digest(data):
        raise ValueError("JOINT_CHECKPOINT_DIGEST_INVALID")
    if set(data) != {
        "schema_version", "profile", "session_id", "checkpoint_ns",
        "owners", "owner_digests_sha256", "event_order",
        "unsupported_owners", "experimental_only", "resume_supported",
    } or data["schema_version"] != SCHEMA or data["profile"] != PROFILE:
        raise ValueError("JOINT_CHECKPOINT_SCHEMA_INVALID")
    if (data["experimental_only"] is not True
            or data["resume_supported"] is not False
            or data["unsupported_owners"] != list(STOPPED_OWNERS)):
        raise ValueError("JOINT_CHECKPOINT_UNSUPPORTED_RESUME_CLAIM")
    owner_docs = data["owners"]
    hashes = data["owner_digests_sha256"]
    if (not isinstance(owner_docs, dict) or set(owner_docs) != OWNER_KEYS
            or not isinstance(hashes, dict) or set(hashes) != OWNER_KEYS):
        raise ValueError("JOINT_CHECKPOINT_OWNERS_INVALID")
    for key, owner in owner_docs.items():
        if not isinstance(owner, dict) or hashes[key] != _digest(owner):
            raise ValueError("JOINT_CHECKPOINT_OWNER_DIGEST_INVALID")
    physics = owner_docs["physics"]
    mode = owner_docs["mode"]
    fault = owner_docs["fault"]
    tc = owner_docs["tc"]
    verify_closed_loop_evidence(physics)
    mode_data = validate_mode_checkpoint(mode)
    fault_data = validate_fault_checkpoint(fault)
    tc_data = validate_tc_snapshot(tc)
    _cross_owner_consistency(
        session_id=data["session_id"], clock_ns=data["checkpoint_ns"],
        physics=physics, mode=mode_data, fault=fault_data, tc=tc_data,
    )
    if data["event_order"] != {
        "fault_activate_ns": FAULT_START_NS,
        "tc_execute_ns": TC_DUE_NS,
        "fault_clear_ns": FAULT_END_NS,
    }:
        raise ValueError("JOINT_CHECKPOINT_EVENT_ORDER_INVALID")
    recovery_plan(tc)
    return data
