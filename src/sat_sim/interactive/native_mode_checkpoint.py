"""Experimental ADCS mode-transition journal checkpoint contract.

Pure control-plane evidence only: never claims Basilisk native graph or
physical mode switching is restorable. No side effects during validation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "sat-adcs-mode-checkpoint.v1"
MODES = frozenset({"standby", "inertial3d", "sun_safe", "nadir", "velocity"})
STATES = frozenset({"pending", "applied", "rejected"})
PERIOD_NS = 600_000_000


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _timestamp(value: Any, *, max_time: int, label: str) -> int:
    if type(value) is not int or value < 0 or value > max_time:
        raise ValueError(f"ADCS_MODE_INVALID_TIME:{label}")
    return value


def validate_mode_checkpoint(document: dict[str, Any]) -> dict[str, Any]:
    """Validate canonical, phase-aligned state and strictly ordered event IDs.

    A pending transition has not been applied to Basilisk. The caller must
    reconcile command outcomes and native mode-module state before replay.
    """
    if not isinstance(document, dict):
        raise ValueError("ADCS_MODE_INVALID_DOCUMENT")
    data = dict(document)
    digest = data.pop("digest_sha256", None)
    if not isinstance(digest, str) or digest != _digest(data):
        raise ValueError("ADCS_MODE_DIGEST_MISMATCH")
    if set(data) != {
        "schema_version", "checkpoint_ns", "current_mode", "revision",
        "transitions", "native_mode_state_restored", "resume_supported",
    } or data["schema_version"] != SCHEMA:
        raise ValueError("ADCS_MODE_SCHEMA_INVALID")
    if data["native_mode_state_restored"] is not False or data["resume_supported"] is not False:
        raise ValueError("ADCS_MODE_UNSUPPORTED_RESUME_CLAIM")
    clock = data["checkpoint_ns"]
    if type(clock) is not int or clock <= 0 or clock % 200_000_000:
        raise ValueError("ADCS_MODE_CLOCK_INVALID")
    if data["current_mode"] not in MODES:
        raise ValueError("ADCS_MODE_UNKNOWN")
    transitions = data["transitions"]
    if not isinstance(transitions, list) or type(data["revision"]) is not int:
        raise ValueError("ADCS_MODE_TRANSITIONS_INVALID")
    if data["revision"] != len(transitions):
        raise ValueError("ADCS_MODE_REVISION_INVALID")
    mode = "standby"
    ids = set()
    last_at = -1
    for index, event in enumerate(transitions, 1):
        if not isinstance(event, dict) or set(event) != {
            "event_id", "revision", "requested_ns", "resolved_ns",
            "from_mode", "to_mode", "status",
        }:
            raise ValueError("ADCS_MODE_EVENT_SCHEMA_INVALID")
        ident = event["event_id"]
        if not isinstance(ident, str) or not ident or len(ident) > 128 or ident in ids:
            raise ValueError("ADCS_MODE_EVENT_ID_INVALID")
        ids.add(ident)
        if type(event["revision"]) is not int or event["revision"] != index:
            raise ValueError("ADCS_MODE_EVENT_REVISION_INVALID")
        requested = _timestamp(event["requested_ns"], max_time=clock, label="requested")
        if requested < last_at:
            raise ValueError("ADCS_MODE_EVENT_ORDER_INVALID")
        last_at = requested
        status = event["status"]
        if status not in STATES or event["from_mode"] not in MODES or event["to_mode"] not in MODES:
            raise ValueError("ADCS_MODE_EVENT_VALUE_INVALID")
        if event["from_mode"] != mode:
            raise ValueError("ADCS_MODE_PREVIOUS_MODE_INVALID")
        if status == "pending":
            if event["resolved_ns"] is not None:
                raise ValueError("ADCS_MODE_PENDING_RESOLUTION_INVALID")
            if index != len(transitions):
                raise ValueError("ADCS_MODE_PENDING_NOT_LAST")
        else:
            resolved = _timestamp(event["resolved_ns"], max_time=clock, label="resolved")
            if resolved < requested or resolved % PERIOD_NS:
                raise ValueError("ADCS_MODE_RESOLUTION_PHASE_INVALID")
            if status == "applied":
                mode = event["to_mode"]
    if data["current_mode"] != mode:
        raise ValueError("ADCS_MODE_CURRENT_MISMATCH")
    return data


def capture_mode_checkpoint(*, checkpoint_ns: int, current_mode: str,
                            transitions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build auditable metadata; does not read/write native Basilisk memory."""
    document = {
        "schema_version": SCHEMA,
        "checkpoint_ns": checkpoint_ns,
        "current_mode": current_mode,
        "revision": len(transitions),
        "transitions": transitions,
        "native_mode_state_restored": False,
        "resume_supported": False,
    }
    document["digest_sha256"] = _digest(document)
    validate_mode_checkpoint(document)
    return document
