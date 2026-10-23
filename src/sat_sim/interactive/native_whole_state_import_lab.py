"""R9-D restricted cross-process state import into a fresh Basilisk whole graph.

Only C++ StateData spacecraft/RW vectors are imported. This is an isolated
LAB READBACK proof, not a valid whole-satellite restart. Never route through
interactive/API/telecommand or use to enable automatic resumption.
"""
from __future__ import annotations

from typing import Any

from .native_checkpoint_audit import verify_coverage_report
from .native_recovery_readiness import build_recovery_readiness
from .native_state_pilot import (
    capture_state_subset,
    restore_state_subset_in_place,
    validate_state_subset,
)

SCHEMA = "sat-native-fresh-whole-kinematic-import-lab.v1"
PROFILE = "r9d-initialized-fresh-whole-graph-state-subset-readback-only-v1"
REMAINS_UNRESTORED = (
    "absolute_native_scheduler_time_and_pending_task_phases",
    "integrator_internal_stages_and_acceleration_cache",
    "wheel_private_memory_and_previous_motor_commands",
    "fsw_navigation_and_sensor_state_or_message_histories",
    "eps_battery_integrator_and_power_bus_state",
    "thermal_nodes_heater_radiator_and_energy_history",
    "comm_storage_downlink_and_protocol_state",
    "fault_events_pending_telecommands_and_acked_effects",
    "stochastic_streams_and_rng",
    "complete_whole_graph_cross_process_numerical_parity",
)


def import_fresh_native_whole_state_subset(
    *, sim: Any, spacecraft: Any, expected_rw_count: int,
    checkpoint_audit: dict[str, Any],
) -> dict[str, Any]:
    """Experiment only. Mutate *only* source-backed native StateData vectors.

    Requires a newly initialized graph which has not run a single time step.
    All audit integrity, native provenance, source phase and topology checks
    happen BEFORE the first C++ StateData write.
    """
    if type(expected_rw_count) is not int or expected_rw_count not in (3, 4):
        raise ValueError("R9D_RW_TOPOLOGY_INVALID")
    verify_coverage_report(checkpoint_audit)
    readiness = build_recovery_readiness(checkpoint_audit)
    if (
        readiness["restart_readiness"] != "BLOCKED"
        or readiness["restorable_owner_count"] != 0
        or readiness["automatic_resume_allowed"] is not False
    ):
        raise ValueError("R9D_FALSE_RESTORE_READINESS")
    if checkpoint_audit["capture_source"] != "LIVE_BASILISK_WORKER_THREAD":
        raise ValueError("R9D_SOURCE_NOT_NATIVE")
    if checkpoint_audit.get("native_joint_owner_observations") is None:
        raise ValueError("R9D_WHOLE_GRAPH_OWNER_EVIDENCE_REQUIRED")
    adcs = checkpoint_audit.get("native_adcs_owner_observations")
    if adcs is None or adcs["rw_count"] != expected_rw_count:
        raise ValueError("R9D_ADCS_TOPOLOGY_MISMATCH")
    if checkpoint_audit["native_joint_owner_observations"]["capture_time_ns"] != (
        adcs["capture_time_ns"]
    ):
        raise ValueError("R9D_SOURCE_OWNER_TIME_MISMATCH")
    payload = checkpoint_audit.get("native_state_subset_pilot")
    if payload is None:
        raise ValueError("R9D_NO_NATIVE_SUBSET")
    validate_state_subset(payload, expected_rw_count=expected_rw_count)
    source_time_ns = checkpoint_audit["native_joint_owner_observations"]["capture_time_ns"]
    if source_time_ns <= 0:
        raise ValueError("R9D_SOURCE_TIME_INVALID")
    fresh_clock_ns = int(sim.TotalSim.CurrentNanos)
    if fresh_clock_ns != 0:
        raise ValueError("R9D_IMPORT_REQUIRES_FRESH_ZERO_CLOCK")
    before = capture_state_subset(spacecraft, expected_rw_count=expected_rw_count)
    restore_state_subset_in_place(
        spacecraft, payload, expected_rw_count=expected_rw_count,
    )
    readback = capture_state_subset(spacecraft, expected_rw_count=expected_rw_count)
    if readback["vectors"] != payload["vectors"]:
        raise ValueError("R9D_NATIVE_READBACK_MISMATCH")
    if int(sim.TotalSim.CurrentNanos) != fresh_clock_ns:
        raise ValueError("R9D_IMPORT_ILLEGALLY_ADVANCED_CLOCK")
    return {
        "schema_version": SCHEMA, "profile": PROFILE,
        "source_sim_time_ns": source_time_ns,
        "fresh_scheduler_time_ns": fresh_clock_ns,
        "source_audit_sha256": checkpoint_audit["report_sha256"],
        "rw_count": expected_rw_count,
        "native_vectors_before": before["vectors"],
        "native_vectors_readback": readback["vectors"],
        "native_state_subset_imported": True,
        "new_process_scheduler_rehydrated": False,
        "full_native_integrator_rehydrated": False,
        "whole_spacecraft_physical_parity_proven": False,
        "automatic_resume_allowed": False,
        "automatic_tc_replay_allowed": False,
        "resume_supported": False,
        "not_restored": list(REMAINS_UNRESTORED),
    }
