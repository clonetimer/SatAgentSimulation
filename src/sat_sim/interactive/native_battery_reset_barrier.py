"""R9-I: tested upstream Basilisk SimpleBattery public Reset seeding barrier.

This intentionally demonstrates why setting storedCharge_Init and calling
Reset is NOT a supported import of the protected native battery state.
The upstream v2.11.0 PowerStorageBase::Reset forces previousTime = 0;
its protected storedCharge, storageStatusMsg and power-node histories are not
exported through this API. Never use this function in any live session.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = "sat-basilisk-public-battery-reset-seed-negative-control.v1"


def seed_battery_via_public_reset_lab(
    *, battery: Any, source_audit: dict[str, Any],
    expected_clock_ns: int, target_clock_ns: int,
) -> dict[str, Any]:
    from .native_checkpoint_audit import verify_coverage_report

    verify_coverage_report(source_audit)
    joint = source_audit.get("native_joint_owner_observations")
    if not isinstance(joint, dict) or joint.get("scope") != "LIVE_UNIFIED_BASILISK_READ_ONLY":
        raise ValueError("R9I_REAL_WHOLE_GRAPH_EVIDENCE_REQUIRED")
    if (type(expected_clock_ns) is not int or type(target_clock_ns) is not int
        or expected_clock_ns != target_clock_ns
        or expected_clock_ns <= 0
        or expected_clock_ns != joint["capture_time_ns"]):
        raise ValueError("R9I_BATTERY_CAPTURE_TIME_MISMATCH")

    eps = joint["eps"]
    energy = eps["battery_energy_j"]
    capacity = eps["battery_capacity_j"]
    if (type(energy) not in (float,int) or type(capacity) not in (float,int)
        or not math.isfinite(energy) or not math.isfinite(capacity)
        or capacity <= 0 or not 0 <= energy <= capacity):
        raise ValueError("R9I_BATTERY_ENERGY_RANGE_INVALID")
    live_capacity = float(battery.storageCapacity)
    initial_charge = float(battery.storedCharge_Init)
    if (not math.isfinite(live_capacity)
        or not math.isfinite(initial_charge)
        or not math.isclose(live_capacity, capacity, rel_tol=0, abs_tol=1e-6)
        or initial_charge < 0 or initial_charge > capacity):
        raise ValueError("R9I_FRESH_BATTERY_TOPOLOGY_MISMATCH")

    # Only public native API is exercised. Calling Reset deliberately sets
    # PowerStorageBase::previousTime back to ZERO, even though the imported
    # scheduler clock is nonzero. It is scientifically invalid to call this
    # state recovery; this is an isolated negative-control experiment.
    original_msg = battery.batPowerOutMsg.read()
    before_msg_energy = float(original_msg.storageLevel)
    battery.storedCharge_Init = float(energy)
    battery.Reset(target_clock_ns)
    if float(battery.storedCharge_Init) != float(energy):
        raise ValueError("R9I_BATTERY_PUBLIC_INIT_WRITE_FAILED")
    # Reset() does not publish the protected storageStatusMsg and cannot
    # supply a readback of the protected storedCharge field.
    after_msg_energy = float(battery.batPowerOutMsg.read().storageLevel)
    if after_msg_energy != before_msg_energy:
        raise ValueError("R9I_RESET_UNEXPECTEDLY_PUBLISHED_MESSAGE")
    return {
        "schema_version": SCHEMA,
        "profile": "PUBLIC_INIT_PLUS_RESET_NOT_BATTERY_CHECKPOINT",
        "source_time_ns": expected_clock_ns,
        "source_energy_j": float(energy),
        "native_public_initial_charge_j": float(battery.storedCharge_Init),
        "native_output_message_pre_first_tick_j": after_msg_energy,
        "actual_protected_stored_charge_readback": None,
        "native_previous_time_rehydrated": False,
        "native_power_message_histories_restored": False,
        "battery_full_state_restored": False,
        "whole_spacecraft_resume_supported": False,
        "automatic_tc_replay_allowed": False,
        "isolated_negative_control": True,
    }
