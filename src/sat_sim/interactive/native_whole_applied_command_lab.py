"""R9-H isolated whole-Basilisk applied RW motor message writeback experiment.

This laboratory helper copies the *actual post-fault-manager* wheel actuator
input recorded from a paused native graph. It is NOT a message-history replay
or a restart protocol. It must run after Basilisk InitializeSimulation and
before a single isolated test-only tick, never in the TC/session API.
"""
from __future__ import annotations

import math
from typing import Any

PROFILE = "native-wheel-applied-command-test-only-v1"


def restore_last_applied_rw_command_fresh(
    *, applied_command_manager: Any, source_audit: dict[str, Any],
    expected_rw_count: int, target_clock_ns: int,
) -> dict[str, Any]:
    from Basilisk.architecture import messaging  # type: ignore
    from .native_checkpoint_audit import verify_coverage_report

    verify_coverage_report(source_audit)
    if type(expected_rw_count) is not int or expected_rw_count not in (3, 4):
        raise ValueError("R9H_COMMAND_RW_COUNT_UNSUPPORTED")
    owner = source_audit.get("native_adcs_owner_observations")
    if not isinstance(owner, dict):
        raise ValueError("R9H_APPLIED_OWNER_MISSING")
    source = owner.get("native_applied_motor_command_nm")
    if (owner.get("rw_count") != expected_rw_count or
            not isinstance(source, list) or len(source) != expected_rw_count):
        raise ValueError("R9H_APPLIED_OWNER_TOPOLOGY_MISMATCH")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in source):
        raise ValueError("R9H_APPLIED_OWNER_NONFINITE")
    if (type(target_clock_ns) is not int or target_clock_ns <= 0 or
            target_clock_ns != owner["capture_time_ns"] or
            abs(source_audit["sim_time_s"] * 1e9 - target_clock_ns) > 1):
        raise ValueError("R9H_APPLIED_OWNER_TIME_MISMATCH")
    if int(applied_command_manager.wheel_count) != expected_rw_count:
        raise ValueError("R9H_COMMAND_MANAGER_TOPOLOGY_MISMATCH")
    max_torque = float(applied_command_manager.max_torque_nm)
    if any(abs(x) > max_torque + 1e-12 for x in source):
        raise ValueError("R9H_APPLIED_OWNER_TORQUE_OUT_OF_BOUNDS")
    # The command must come from an actual Basilisk paused message, not a
    # calculated force. The fresh model's Reset wrote its default output at
    # time zero. Only the command at this one sample is transferred.
    before = list(applied_command_manager.commandOutMsg.read().motorTorque)
    if any(abs(float(x)) > 1e-14 for x in before[:expected_rw_count]):
        raise ValueError("R9H_REQUIRES_FRESH_COMMAND_OWNER")
    payload = messaging.ArrayMotorTorqueMsgPayload()
    payload.motorTorque = list(source)  # Basilisk pads the remaining RW slots.
    applied_command_manager.commandOutMsg.write(
        payload, target_clock_ns, applied_command_manager.moduleID,
    )
    readback = [float(x) for x in list(
        applied_command_manager.commandOutMsg.read().motorTorque,
    )[:expected_rw_count]]
    if readback != [float(x) for x in source]:
        raise ValueError("R9H_APPLIED_COMMAND_READBACK_FAILED")
    return {
        "profile": PROFILE, "test_only": True,
        "source_time_ns": target_clock_ns,
        "rw_count": expected_rw_count,
        "native_applied_command_nm": readback,
        "single_message_writeback": True,
        "message_history_restored": False,
        "rw_private_state_complete": False,
        "whole_spacecraft_parity_proven": False,
        "automatic_resume_allowed": False,
        "automatic_tc_replay_allowed": False,
    }
