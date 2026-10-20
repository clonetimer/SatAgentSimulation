"""Experimental Basilisk dynamics/RW state vector export and in-place restore.

Native StateData API pilot only, NOT a satellite process checkpoint. Only
use inside a paused, initialized, *same* Basilisk graph, never in HTTP/TC code.
Scheduler, integrators, FSW memory and other subsystem states are NOT saved.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

PILOT_SCHEMA = "basilisk-native-state-subset-pilot.v1"
PILOT_NAMES = (
    ("hub_position_m", "hubPosition", 3),
    ("hub_velocity_m_s", "hubVelocity", 3),
    ("hub_mrp", "hubSigma", 3),
    ("hub_angular_rate_rad_s", "hubOmega", 3),
    ("reaction_wheel_speed_rad_s", "reactionWheelOmegas", None),
)


def _digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _column(matrix: Any, *, expected_rows: int | None, key: str) -> list[float]:
    if not isinstance(matrix, (list, tuple)) or not matrix:
        raise ValueError(f"NATIVE_STATE_INVALID_SHAPE:{key}")
    if expected_rows is not None and len(matrix) != expected_rows:
        raise ValueError(f"NATIVE_STATE_INVALID_SHAPE:{key}")
    if len(matrix) > 1024:
        raise ValueError(f"NATIVE_STATE_EXCESSIVE_DIMENSION:{key}")
    values: list[float] = []
    for row in matrix:
        if not isinstance(row, (list, tuple)) or len(row) != 1:
            raise ValueError(f"NATIVE_STATE_INVALID_SHAPE:{key}")
        raw = row[0]
        if isinstance(raw, bool) or not isinstance(raw, (float, int)):
            raise ValueError(f"NATIVE_STATE_INVALID_VALUE:{key}")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"NATIVE_STATE_NONFINITE:{key}")
        values.append(value)
    return values


def capture_state_subset(spacecraft: Any, *, expected_rw_count: int | None = None) -> dict[str, Any]:
    """Capture live registered C++ StateData vectors, not telemetry messages."""
    manager = spacecraft.dynManager
    vectors = {}
    for key, native_name, rows in PILOT_NAMES:
        state = manager.getStateObject(native_name)
        if state is None:
            raise ValueError(f"NATIVE_STATE_NOT_REGISTERED:{native_name}")
        values = _column(state.getState(), expected_rows=rows, key=key)
        if key == "reaction_wheel_speed_rad_s":
            if expected_rw_count is not None and len(values) != expected_rw_count:
                raise ValueError("NATIVE_STATE_REACTION_WHEEL_COUNT_MISMATCH")
        vectors[key] = values
    result = {
        "schema_version": PILOT_SCHEMA,
        "vectors": vectors,
        "native_state_import_supported_for_pilot": True,
        "same_instance_roundtrip_test_only": True,
        "scheduler_time_restored": False,
        "full_subsystem_memory_restored": False,
        "resume_supported": False,
    }
    result["digest_sha256"] = _digest(result)
    return result


def validate_state_subset(payload: dict[str, Any], *, expected_rw_count: int | None = None) -> None:
    if not isinstance(payload, dict):
        raise ValueError("NATIVE_STATE_PILOT_INVALID")
    content = dict(payload)
    digest = content.pop("digest_sha256", None)
    if content.get("schema_version") != PILOT_SCHEMA or digest != _digest(content):
        raise ValueError("NATIVE_STATE_PILOT_HASH_INVALID")
    if (content.get("resume_supported") is not False
            or content.get("scheduler_time_restored") is not False
            or content.get("full_subsystem_memory_restored") is not False):
        raise ValueError("NATIVE_STATE_PILOT_RESUME_CLAIM_REJECTED")
    vectors = content.get("vectors")
    if not isinstance(vectors, dict) or set(vectors) != {key for key, _, _ in PILOT_NAMES}:
        raise ValueError("NATIVE_STATE_PILOT_KEYS_INVALID")
    for key, _, size in PILOT_NAMES:
        values = vectors[key]
        if not isinstance(values, list) or not values:
            raise ValueError(f"NATIVE_STATE_INVALID_SHAPE:{key}")
        if size is not None and len(values) != size:
            raise ValueError(f"NATIVE_STATE_INVALID_SHAPE:{key}")
        if (len(values) > 1024 or any(
            isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x)
            for x in values
        )):
            raise ValueError(f"NATIVE_STATE_NONFINITE:{key}")
    if expected_rw_count is not None and len(vectors["reaction_wheel_speed_rad_s"]) != expected_rw_count:
        raise ValueError("NATIVE_STATE_REACTION_WHEEL_COUNT_MISMATCH")


def restore_state_subset_in_place(
    spacecraft: Any, payload: dict[str, Any], *, expected_rw_count: int | None = None,
) -> None:
    """TEST-ONLY partial restore inside the same paused native graph.

    This cannot reconstruct a Basilisk scheduler clock or new-process state;
    public simulation session endpoints must not invoke this operation.
    """
    import numpy as np

    validate_state_subset(payload, expected_rw_count=expected_rw_count)
    manager = spacecraft.dynManager
    bindings = []
    for key, native_name, rows in PILOT_NAMES:
        state = manager.getStateObject(native_name)
        if state is None:
            raise ValueError(f"NATIVE_STATE_NOT_REGISTERED:{native_name}")
        current = _column(state.getState(), expected_rows=rows, key=key)
        target = payload["vectors"][key]
        if len(current) != len(target):
            raise ValueError(f"NATIVE_STATE_GRAPH_DIMENSION_MISMATCH:{native_name}")
        bindings.append((state, np.array(target, dtype=float).reshape(-1, 1)))
    # Validate all native bindings before mutating any state.
    for state, values in bindings:
        state.setState(values)
