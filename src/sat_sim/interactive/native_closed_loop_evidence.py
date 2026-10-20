"""Read-only, integrity-checked native closed-loop ADCS checkpoint evidence.

Combines spacecraft/RW C++ StateData, two SysProcess scheduler states, native
mrpFeedback integral memory, and last emitted RW motor-torque message. This
is a *coverage report*, NOT a restartable snapshot: Basilisk module message
history and actuator-held input state are not rehydrated. Fail closed.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .native_state_pilot import capture_state_subset, validate_state_subset
from .native_multiprocess_pilot import capture_two_process_clock, validate_two_process_clock

SCHEMA = "basilisk-closed-loop-adcs-evidence.v2"
MISSING_RESTORE_OWNERS = (
    "rw_command_message_history",
    "attitude_nav_guidance_message_history",
    "reaction_wheel_unexposed_private_state",
    "fsw_module_full_cached_rw_config",
    "closed_loop_cross_process_parity",
    "fault_mode_and_pending_telecommands",
)


def _checksum(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _triple(value: Any, name: str) -> list[float]:
    try:
        sequence = list(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ADCS_EVIDENCE_VECTOR_INVALID:{name}") from exc
    if len(sequence) != 3:
        raise ValueError(f"ADCS_EVIDENCE_VECTOR_INVALID:{name}")
    result = []
    for x in sequence:
        if isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x):
            raise ValueError(f"ADCS_EVIDENCE_VECTOR_INVALID:{name}")
        result.append(float(x))
    return result


def _native_memory(controller: Any, checkpoint_ns: int) -> dict[str, Any]:
    prior = int(controller.priorTime)
    if prior <= 0 or prior > checkpoint_ns or prior != (checkpoint_ns // 600_000_000) * 600_000_000:
        raise ValueError("ADCS_EVIDENCE_CONTROLLER_TIME_INVALID")
    if int(controller.rwConfigParams.numRW) != 3:
        raise ValueError("ADCS_EVIDENCE_RW_COUNT_INVALID")
    return {
        "prior_time_ns": prior,
        "int_sigma": _triple(controller.int_sigma, "int_sigma"),
        "z": _triple(controller.z, "z"),
        "K": float(controller.K),
        "P": float(controller.P),
        "Ki": float(controller.Ki),
        "integral_limit": float(controller.integralLimit),
        "cached_rw_count": 3,
    }


RW_EXPOSED_FIELDS = ("Omega", "omegaBefore", "u_current", "frictionTorque")


def capture_rw_effector_memory(rw_effector: Any) -> list[dict[str, float]]:
    """Capture C++ RWConfig state that exists outside the StateData vectors.

    BalancedWheel pilot only. No blanket claim about RW friction/jitter
    models, pending commands, thermal or reaction wheel module state.
    """
    wheels = list(rw_effector.ReactionWheelData)
    if len(wheels) != 3:
        raise ValueError("ADCS_RW_MEMORY_COUNT_MISMATCH")
    result = []
    for i, wheel in enumerate(wheels):
        values = {key: float(getattr(wheel, key)) for key in RW_EXPOSED_FIELDS}
        if any(not math.isfinite(x) for x in values.values()):
            raise ValueError(f"ADCS_RW_MEMORY_NONFINITE:{i}")
        result.append(values)
    return result


def validate_rw_effector_memory(data: Any) -> None:
    if not isinstance(data, list) or len(data) != 3:
        raise ValueError("ADCS_RW_MEMORY_COUNT_MISMATCH")
    for wheel in data:
        if not isinstance(wheel, dict) or set(wheel) != set(RW_EXPOSED_FIELDS):
            raise ValueError("ADCS_RW_MEMORY_FIELDS_INVALID")
        if any(
            isinstance(v, bool) or not isinstance(v, (float, int))
            or not math.isfinite(v) for v in wheel.values()
        ):
            raise ValueError("ADCS_RW_MEMORY_NONFINITE")


def restore_rw_effector_memory_fresh(rw_effector: Any, saved: Any) -> None:
    """TEST-ONLY native pointer writeback on a fresh, unadvanced RW graph."""
    validate_rw_effector_memory(saved)
    original = capture_rw_effector_memory(rw_effector)
    if any(abs(w["u_current"]) > 1e-14 for w in original):
        raise ValueError("ADCS_RW_MEMORY_REQUIRES_FRESH_EFFECTOR")
    for wheel, fields in zip(rw_effector.ReactionWheelData, saved):
        for name in RW_EXPOSED_FIELDS:
            setattr(wheel, name, fields[name])
    if capture_rw_effector_memory(rw_effector) != saved:
        raise ValueError("ADCS_RW_MEMORY_NATIVE_WRITEBACK_FAILED")


def capture_closed_loop_evidence(
    *, sim: Any, processes: dict[str, Any], tasks: dict[str, Any],
    spacecraft: Any, controller: Any, allocator: Any, rw_effector: Any,
) -> dict[str, Any]:
    native = capture_state_subset(spacecraft, expected_rw_count=3)
    clock = capture_two_process_clock(sim, processes, tasks, spacecraft)
    checkpoint_ns = clock["state"]["current_ns"]
    memory = _native_memory(controller, checkpoint_ns)
    command = allocator.rwMotorTorqueOutMsg.read()
    held = _triple(list(command.motorTorque)[:3], "motor_torque")
    for name in ("K", "P", "Ki", "integral_limit"):
        if not math.isfinite(memory[name]):
            raise ValueError(f"ADCS_EVIDENCE_FSW_CONFIG_NONFINITE:{name}")
    if memory["Ki"] <= 0 or memory["integral_limit"] <= 0:
        raise ValueError("ADCS_EVIDENCE_UNSUPPORTED_FSW_CONTROL_MODE")
    if not any(abs(value) > 0 for value in held):
        raise ValueError("ADCS_EVIDENCE_MISSING_MOTOR_COMMAND")
    report = {
        "schema_version": SCHEMA,
        "checkpoint_sim_time_ns": checkpoint_ns,
        "spacecraft_rw_state": native,
        "native_scheduler": clock,
        "fsw_integrator": memory,
        "rw_effector_memory": capture_rw_effector_memory(rw_effector),
        "last_rw_motor_command_nm": held,
        "restore_gaps": list(MISSING_RESTORE_OWNERS),
        "actuator_command_message_restored": False,
        "native_message_history_restored": False,
        "fault_and_command_queue_restored": False,
        "closed_loop_restart_parity_proven": False,
        "resume_supported": False,
    }
    report["digest_sha256"] = _checksum(report)
    return report


def verify_closed_loop_evidence(document: dict[str, Any]) -> None:
    if not isinstance(document, dict):
        raise ValueError("ADCS_EVIDENCE_DOCUMENT_INVALID")
    data = dict(document)
    digest = data.pop("digest_sha256", None)
    if digest != _checksum(data):
        raise ValueError("ADCS_EVIDENCE_DIGEST_INVALID")
    if data.get("schema_version") != SCHEMA:
        raise ValueError("ADCS_EVIDENCE_SCHEMA_INVALID")
    if (
        data.get("resume_supported") is not False
        or data.get("closed_loop_restart_parity_proven") is not False
        or data.get("actuator_command_message_restored") is not False
        or data.get("native_message_history_restored") is not False
        or data.get("fault_and_command_queue_restored") is not False
        or data.get("restore_gaps") != list(MISSING_RESTORE_OWNERS)
    ):
        raise ValueError("ADCS_EVIDENCE_FALSE_RESTORE_CLAIM")
    validate_state_subset(data["spacecraft_rw_state"], expected_rw_count=3)
    validate_rw_effector_memory(data["rw_effector_memory"])
    phase = validate_two_process_clock(data["native_scheduler"])
    if data["checkpoint_sim_time_ns"] != phase["current_ns"]:
        raise ValueError("ADCS_EVIDENCE_TIME_MISMATCH")
    memory = data["fsw_integrator"]
    if not isinstance(memory, dict) or set(memory) != {
        "prior_time_ns", "int_sigma", "z", "K", "P", "Ki", "integral_limit", "cached_rw_count",
    }:
        raise ValueError("ADCS_EVIDENCE_FSW_FIELDS_INVALID")
    if (
        type(memory["prior_time_ns"]) is not int
        or memory["prior_time_ns"] != (phase["current_ns"] // 600_000_000) * 600_000_000
        or memory["cached_rw_count"] != 3
    ):
        raise ValueError("ADCS_EVIDENCE_FSW_PHASE_INVALID")
    _triple(memory["int_sigma"], "int_sigma")
    _triple(memory["z"], "z")
    _triple(data["last_rw_motor_command_nm"], "motor_torque")
    for name in ("K", "P", "Ki", "integral_limit"):
        val = memory[name]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            raise ValueError("ADCS_EVIDENCE_FSW_CONFIG_INVALID")
    if memory["Ki"] <= 0 or memory["integral_limit"] <= 0:
        raise ValueError("ADCS_EVIDENCE_UNSUPPORTED_FSW_CONTROL_MODE")
