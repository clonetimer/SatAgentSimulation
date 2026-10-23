"""R9-B read-only native ADCS state-owner evidence from the *whole* Basilisk graph.

Reads actual C++ state, RWConfig memory, C FSW integrator and native messages
only while the unified Basilisk worker is paused. This is NOT an exportable
complete checkpoint. No restore or message write is implemented here.
"""
from __future__ import annotations

import math
from typing import Any

from .native_closed_loop_evidence import (
    RW_EXPOSED_FIELDS, capture_rw_effector_memory, validate_rw_effector_memory,
)
from .native_state_pilot import validate_state_subset

SCHEMA = "sat-whole-native-adcs-owner-observation.v1"
SCOPE = "LIVE_UNIFIED_BASILISK_READ_ONLY"


def _finite_number(value: Any, key: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"NATIVE_ADCS_OBSERVATION_NONFINITE:{key}")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"NATIVE_ADCS_OBSERVATION_NONFINITE:{key}") from exc
    if not math.isfinite(converted):
        raise ValueError(f"NATIVE_ADCS_OBSERVATION_NONFINITE:{key}")
    return converted


def _vector(values: Any, count: int, key: str, *, exact: bool = False) -> list[float]:
    try:
        source = list(values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"NATIVE_ADCS_OBSERVATION_SHAPE:{key}") from exc
    if (len(source) != count if exact else len(source) < count):
        raise ValueError(f"NATIVE_ADCS_OBSERVATION_SHAPE:{key}")
    return [_finite_number(value, key) for value in source[:count]]


def validate_live_adcs_owner_observation(
    data: Any, *, native_state_subset: dict[str, Any] | None = None,
    expected_time_ns: int | None = None,
) -> None:
    if not isinstance(data, dict) or set(data) != {
        "schema_version", "scope", "capture_time_ns", "rw_count",
        "native_state_wheel_speeds_rad_s", "native_rw_speed_msg_rad_s",
        "native_rw_cpp_memory", "fsw_memory", "native_motor_command_nm",
        "native_private_state_complete", "message_history_restored",
        "cross_process_parity_proven", "resume_supported",
    }:
        raise ValueError("NATIVE_ADCS_OBSERVATION_SCHEMA_INVALID")
    if data["schema_version"] != SCHEMA or data["scope"] != SCOPE:
        raise ValueError("NATIVE_ADCS_OBSERVATION_SCOPE_INVALID")
    if any(data[k] is not False for k in (
        "native_private_state_complete", "message_history_restored",
        "cross_process_parity_proven", "resume_supported",
    )):
        raise ValueError("NATIVE_ADCS_OBSERVATION_FALSE_RESTORE_CLAIM")
    count = data["rw_count"]
    if type(count) is not int or count not in (3, 4):
        raise ValueError("NATIVE_ADCS_OBSERVATION_RW_COUNT_INVALID")
    t = data["capture_time_ns"]
    if type(t) is not int or t <= 0 or (expected_time_ns is not None and t != expected_time_ns):
        raise ValueError("NATIVE_ADCS_OBSERVATION_TIME_INVALID")
    for key in ("native_state_wheel_speeds_rad_s", "native_rw_speed_msg_rad_s",
                "native_motor_command_nm"):
        if not isinstance(data[key], list):
            raise ValueError(f"NATIVE_ADCS_OBSERVATION_SHAPE:{key}")
        _vector(data[key], count, key, exact=True)
    memory = data["native_rw_cpp_memory"]
    validate_rw_effector_memory(memory)
    if len(memory) != count:
        raise ValueError("NATIVE_ADCS_OBSERVATION_RW_MEMORY_COUNT_MISMATCH")
    if any(set(row) != set(RW_EXPOSED_FIELDS) for row in memory):
        raise ValueError("NATIVE_ADCS_OBSERVATION_RW_FIELDS_INVALID")
    fsw = data["fsw_memory"]
    if not isinstance(fsw, dict) or set(fsw) != {
        "cached_rw_count", "prior_time_ns", "K", "P", "Ki", "integral_limit",
        "int_sigma", "z",
    }:
        raise ValueError("NATIVE_ADCS_OBSERVATION_FSW_SCHEMA_INVALID")
    if type(fsw["cached_rw_count"]) is not int or fsw["cached_rw_count"] != count:
        raise ValueError("NATIVE_ADCS_OBSERVATION_FSW_TOPOLOGY_MISMATCH")
    prior = fsw["prior_time_ns"]
    if type(prior) is not int or prior < 0 or prior > t:
        raise ValueError("NATIVE_ADCS_OBSERVATION_FSW_TIME_INVALID")
    for key in ("K", "P", "Ki", "integral_limit"):
        _finite_number(fsw[key], key)
    for key in ("int_sigma", "z"):
        if not isinstance(fsw[key], list):
            raise ValueError(f"NATIVE_ADCS_OBSERVATION_SHAPE:{key}")
        _vector(fsw[key], 3, key, exact=True)
    if native_state_subset is not None:
        validate_state_subset(native_state_subset, expected_rw_count=count)
        if data["native_state_wheel_speeds_rad_s"] != (
            native_state_subset["vectors"]["reaction_wheel_speed_rad_s"]
        ):
            raise ValueError("NATIVE_ADCS_OBSERVATION_STATE_VECTOR_MISMATCH")


def capture_live_adcs_owner_observation(
    *, sim: Any, spacecraft: Any, rw_effector: Any, controller: Any,
    allocator: Any, native_state_subset: dict[str, Any],
) -> dict[str, Any]:
    """Observe live native owner memory; never alter any model or message."""
    count = int(controller.rwConfigParams.numRW)
    if count not in (3, 4):
        raise ValueError("NATIVE_ADCS_OBSERVATION_RW_COUNT_INVALID")
    validate_state_subset(native_state_subset, expected_rw_count=count)
    memory = capture_rw_effector_memory(rw_effector)
    time_ns = int(sim.TotalSim.CurrentNanos)
    fsw = {
        "cached_rw_count": count,
        "prior_time_ns": int(controller.priorTime),
        "K": _finite_number(controller.K, "K"),
        "P": _finite_number(controller.P, "P"),
        "Ki": _finite_number(controller.Ki, "Ki"),
        "integral_limit": _finite_number(controller.integralLimit, "integral_limit"),
        "int_sigma": _vector(controller.int_sigma, 3, "int_sigma", exact=True),
        "z": _vector(controller.z, 3, "z", exact=True),
    }
    report = {
        "schema_version": SCHEMA, "scope": SCOPE,
        "capture_time_ns": time_ns, "rw_count": count,
        "native_state_wheel_speeds_rad_s": list(
            native_state_subset["vectors"]["reaction_wheel_speed_rad_s"]
        ),
        "native_rw_speed_msg_rad_s": _vector(
            rw_effector.rwSpeedOutMsg.read().wheelSpeeds, count, "wheelSpeeds",
        ),
        "native_rw_cpp_memory": memory,
        "fsw_memory": fsw,
        "native_motor_command_nm": _vector(
            allocator.rwMotorTorqueOutMsg.read().motorTorque, count, "motorTorque",
        ),
        "native_private_state_complete": False,
        "message_history_restored": False,
        "cross_process_parity_proven": False,
        "resume_supported": False,
    }
    validate_live_adcs_owner_observation(
        report, native_state_subset=native_state_subset, expected_time_ns=time_ns,
    )
    return report
