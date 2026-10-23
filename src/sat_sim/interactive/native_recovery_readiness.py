"""R8: fail-closed, machine-readable native spacecraft recovery readiness.

This is a *coverage gate*, not a writer or importer. It consumes actual
paused Basilisk-worker checkpoint audit evidence, inventories every mandatory
native state owner and makes absent provenance explicit.

The existing R5 three-RW cross-process parity is a restricted laboratory
pilot, NOT evidence that every whole-spacecraft Basilisk subsystem can resume.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .native_checkpoint_audit import MANDATORY_STATE_OWNERS, verify_coverage_report
from .native_state_pilot import validate_state_subset

SCHEMA = "sat-native-recovery-readiness.v1"
PRECONDITIONS = (
    "SAME_SOURCE_BINARY_AND_NATIVE_MODULE_PROVENANCE",
    "ATOMIC_ALL_OWNER_SNAPSHOT_AT_ONE_SIM_TIME",
    "ALL_NATIVE_STATE_EXPORT_IMPORT_IMPLEMENTED",
    "SCHEDULER_TASK_EVENT_PHASE_EXACTLY_REHYDRATED",
    "FSW_SENSORS_INTEGRATORS_SUBSYSTEM_PRIVATE_MEMORY_RESTORED",
    "FAULT_AND_RANDOM_STREAMS_RESTORED",
    "SQLITE_OWNER_EPOCH_EXCLUSIVE_AND_TC_OUTCOMES_RECONCILED",
    "OLD_PHYSICS_PROCESS_TREE_QUIESCENCE_PROVEN",
    "FRESH_PROCESS_VS_UNINTERRUPTED_NATIVE_PARITY",
)


def _digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()


def build_recovery_readiness(
    coverage: dict[str, Any], *,
    capability_id: str = "whole_spacecraft.unified_native.v1",
) -> dict[str, Any]:
    """Check original integrity and map observations to owner-level blockers.

    Never promote an observation / same-graph state subset into a full
    serializable state owner, even when the source report was rehashed.
    """
    verify_coverage_report(coverage)
    if capability_id != "whole_spacecraft.unified_native.v1":
        raise ValueError("RECOVERY_READINESS_REQUIRES_WHOLE_NATIVE")
    t = coverage.get("sim_time_s")
    if type(t) not in (int, float) or not math.isfinite(t) or t <= 0:
        raise ValueError("RECOVERY_READINESS_BAD_SIM_TIME")
    if coverage.get("capture_source") != "LIVE_BASILISK_WORKER_THREAD":
        raise ValueError("RECOVERY_READINESS_REQUIRES_LIVE_NATIVE_CAPTURE")
    if coverage.get("native_checkpoint_complete") is not False or coverage.get("resume_supported") is not False:
        raise ValueError("RECOVERY_READINESS_SOURCE_FALSE_CLAIM")

    pilot = coverage.get("native_state_subset_pilot")
    if pilot is not None:
        validate_state_subset(pilot)
    adcs_owners = coverage.get("native_adcs_owner_observations")
    if adcs_owners is not None:
        from .native_whole_adcs_observation import validate_live_adcs_owner_observation
        validate_live_adcs_owner_observation(
            adcs_owners, native_state_subset=pilot,
            expected_time_ns=round(float(t) * 1.0e9),
        )
    phase = coverage.get("native_scheduler_phase")
    joint = coverage.get("native_joint_owner_observations")
    if joint is not None:
        from .native_joint_owner_observation import validate_joint_owner_observation
        validate_joint_owner_observation(
            joint, expected_time_ns=round(float(t) * 1.0e9),
            native_scheduler_phase=phase,
        )
    observations = coverage.get("observations")
    unavailable = coverage.get("unavailable_observations")
    if not isinstance(observations, dict) or not isinstance(unavailable, dict):
        raise ValueError("RECOVERY_READINESS_INVALID_OBSERVATION_SCHEMA")
    if not isinstance(phase, dict):
        raise ValueError("RECOVERY_READINESS_SCHEDULER_EVIDENCE_MISSING")
    required_phase = ("current_nanos", "next_task_time_nanos", "until_next_task_nanos")
    if any(type(phase.get(k)) is not int for k in required_phase):
        raise ValueError("RECOVERY_READINESS_SCHEDULER_PHASE_INVALID")
    if (phase["current_nanos"] < 0 or phase["next_task_time_nanos"] < phase["current_nanos"]
            or phase["until_next_task_nanos"] != phase["next_task_time_nanos"] - phase["current_nanos"]):
        raise ValueError("RECOVERY_READINESS_SCHEDULER_PHASE_INCONSISTENT")
    if abs(phase["current_nanos"] * 1.0e-9 - t) > 1.0e-9:
        raise ValueError("RECOVERY_READINESS_NATIVE_CLOCK_MISMATCH")
    rows = coverage.get("required_state_owners")
    if not isinstance(rows, list) or len(rows) != len(MANDATORY_STATE_OWNERS):
        raise ValueError("RECOVERY_READINESS_OWNER_MATRIX_INCOMPLETE")

    observation_map = {
        "simulation.scheduler_and_integrators": [
            "native_scheduler_phase", "native_joint_owner_observations",
        ],
        "spacecraft.dynamics_and_state_effectors": ["spacecraft"],
        "adcs.reaction_wheels": [
            "native_state_subset_pilot", "native_adcs_owner_observations",
        ],
        "adcs.fsw_and_controller_memory": [
            "adcs_gains", "adcs_reference", "native_adcs_owner_observations",
        ],
        "adcs.sensors_and_noise": [],
        "eps.battery_and_power_network": ["battery", "native_joint_owner_observations"],
        "thermal.node_integrators_and_history": ["native_joint_owner_observations"],
        "comm_data.storage_downlink_and_queues": ["data_storage", "native_joint_owner_observations"],
        "propulsion.thrusters_fuel_and_control": [],
        "events.faults_and_timeline": [],
        "interactive.queued_commands_and_deduplication": [],
        "random_generators": [],
    }
    mapped: list[dict[str, Any]] = []
    for idx, required in enumerate(MANDATORY_STATE_OWNERS):
        row = rows[idx]
        if (not isinstance(row, dict) or row.get("owner") != required
                or row.get("native_state_export") != "NOT_IMPLEMENTED"
                or row.get("native_state_import") != "NOT_IMPLEMENTED"
                or row.get("restart_numerical_parity") != "NOT_PROVEN"):
            raise ValueError("RECOVERY_READINESS_OWNER_FALSE_PROOF")
        observations_seen = [
            key for key in observation_map[required] if
            (key == "native_scheduler_phase" and phase is not None) or
            (key == "native_state_subset_pilot" and pilot is not None) or
            (key == "native_adcs_owner_observations" and adcs_owners is not None) or
            (key == "native_joint_owner_observations" and joint is not None) or
            key in observations
        ]
        mapped.append({
            "owner": required,
            "observation_keys": observations_seen,
            "observation_only": bool(observations_seen),
            "native_state_export": "NOT_IMPLEMENTED",
            "native_state_import": "NOT_IMPLEMENTED",
            "cross_process_restart_parity": "NOT_PROVEN_FOR_WHOLE",
            "status": "PARTIAL_OBSERVATION_ONLY" if observations_seen else "NO_RESTORABLE_STATE_EVIDENCE",
            "blocking_requirements": [
                "NATIVE_PRIVATE_STATE_EXPORT_MISSING",
                "FRESH_GRAPH_STATE_IMPORT_MISSING",
                "WHOLE_RESTART_PARITY_UNPROVEN",
            ],
        })
    if len({x["owner"] for x in mapped}) != len(MANDATORY_STATE_OWNERS):
        raise ValueError("RECOVERY_READINESS_DUPLICATE_OWNER")

    result = {
        "schema_version": SCHEMA,
        "capability_id": capability_id,
        "source_audit_sha256": coverage["report_sha256"],
        "sim_time_s": float(t),
        "source": "PAUSED_LIVE_BASILISK_CHECKPOINT_COVERAGE",
        "native_scheduler_phase": {
            "current_nanos": phase["current_nanos"],
            "next_task_time_nanos": phase["next_task_time_nanos"],
            "native_clock_restore_proven": False,
            "task_queue_restore_proven": False,
            "event_queue_restore_proven": False,
        },
        "native_state_pilot_present": pilot is not None,
        "native_state_pilot_scope": (
            "SAME_NATIVE_GRAPH_DYNAMICS_4RW_ONLY"
            if pilot is not None and len(pilot["vectors"]["reaction_wheel_speed_rad_s"]) == 4
            else "SAME_NATIVE_GRAPH_DYNAMICS_3RW_ONLY" if pilot is not None else "UNAVAILABLE"
        ),
        "native_joint_owner_observation_present": joint is not None,
        "native_joint_owner_observation_scope": (
            "LIVE_UNIFIED_BASILISK_READ_ONLY" if joint is not None else "UNAVAILABLE"
        ),
        "native_adcs_owner_observation_present": adcs_owners is not None,
        "native_adcs_owner_observation_scope": (
            "LIVE_UNIFIED_BASILISK_READ_ONLY" if adcs_owners is not None else "UNAVAILABLE"
        ),
        "pilot_cross_process_parity_scope": (
            "R9_A_ISOLATED_3_OR_4RW_ADCS_ONLY_NOT_THIS_WHOLE_GRAPH"
        ),
        "observations_unavailable": sorted(unavailable),
        "owners": mapped,
        "mandatory_owner_count": len(mapped),
        "restorable_owner_count": 0,
        "blocking_requirements": list(PRECONDITIONS),
        "full_native_checkpoint_verified": False,
        "automatic_resume_allowed": False,
        "automatic_tc_replay_allowed": False,
        "same_session_recovery_allowed": False,
        "restart_readiness": "BLOCKED",
        "reason_code": "WHOLE_NATIVE_OWNER_EXPORT_IMPORT_AND_PARITY_INCOMPLETE",
    }
    result["report_sha256"] = _digest(result)
    return result


def verify_recovery_readiness(report: dict[str, Any]) -> None:
    if not isinstance(report, dict):
        raise ValueError("RECOVERY_READINESS_NOT_A_REPORT")
    content = dict(report)
    digest = content.pop("report_sha256", None)
    if not isinstance(digest, str) or digest != _digest(content):
        raise ValueError("RECOVERY_READINESS_DIGEST_MISMATCH")
    if (content.get("schema_version") != SCHEMA
            or content.get("capability_id") != "whole_spacecraft.unified_native.v1"
            or content.get("restart_readiness") != "BLOCKED"
            or content.get("full_native_checkpoint_verified") is not False
            or content.get("automatic_resume_allowed") is not False
            or content.get("automatic_tc_replay_allowed") is not False
            or content.get("same_session_recovery_allowed") is not False
            or content.get("restorable_owner_count") != 0
            or content.get("native_adcs_owner_observation_scope") not in (
                "UNAVAILABLE", "LIVE_UNIFIED_BASILISK_READ_ONLY",
            )
            or content.get("native_joint_owner_observation_scope") not in (
                "UNAVAILABLE", "LIVE_UNIFIED_BASILISK_READ_ONLY",
            )
            or content.get("blocking_requirements") != list(PRECONDITIONS)):
        raise ValueError("RECOVERY_READINESS_UNSUPPORTED_RESTART_CLAIM")
    rows = content.get("owners")
    if not isinstance(rows, list) or [r.get("owner") for r in rows if isinstance(r, dict)] != list(MANDATORY_STATE_OWNERS):
        raise ValueError("RECOVERY_READINESS_OWNER_MATRIX_INVALID")
    for row in rows:
        if (row.get("native_state_export") != "NOT_IMPLEMENTED"
                or row.get("native_state_import") != "NOT_IMPLEMENTED"
                or row.get("cross_process_restart_parity") != "NOT_PROVEN_FOR_WHOLE"):
            raise ValueError("RECOVERY_READINESS_OWNER_FALSE_PROOF")
