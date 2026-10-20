"""Fail-closed native checkpoint coverage audit inside the live Basilisk thread.

A state output message is an observation, not an integrator/module snapshot.
The registry deliberately enumerates *every* mandatory state owner and denies
restart eligibility until native export, import and parity are each proven.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SCHEMA = "sat-native-checkpoint-coverage.v1"
MANDATORY_STATE_OWNERS = (
    "simulation.scheduler_and_integrators",
    "spacecraft.dynamics_and_state_effectors",
    "adcs.reaction_wheels",
    "adcs.fsw_and_controller_memory",
    "adcs.sensors_and_noise",
    "eps.battery_and_power_network",
    "thermal.node_integrators_and_history",
    "comm_data.storage_downlink_and_queues",
    "propulsion.thrusters_fuel_and_control",
    "events.faults_and_timeline",
    "interactive.queued_commands_and_deduplication",
    "random_generators",
)


def _safe_vector(payload: Any, name: str, length: int) -> list[float]:
    raw = getattr(payload, name)
    result = [float(x) for x in raw]
    if len(result) != length or not all(math.isfinite(x) for x in result):
        raise ValueError(f"NONFINITE_OR_INVALID_NATIVE_STATE:{name}")
    return result


def _safe_scalar(payload: Any, name: str) -> float:
    value = float(getattr(payload, name))
    if not math.isfinite(value):
        raise ValueError(f"NONFINITE_NATIVE_STATE:{name}")
    return value


def capture_observations(*, spacecraft: Any, reference: Any, controller: Any, battery: Any = None, storage: Any = None) -> tuple[dict[str, Any], dict[str, str]]:
    """Read real native message data from the Basilisk owner thread.

    Missing or unsupported readers are reported, never invented or inferred
    from a previously produced CSV/telemetry row.
    """
    observations: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    readers = {
        "spacecraft": lambda: {
            "position_m": _safe_vector(spacecraft.scStateOutMsg.read(), "r_BN_N", 3),
            "velocity_m_s": _safe_vector(spacecraft.scStateOutMsg.read(), "v_BN_N", 3),
            "mrp": _safe_vector(spacecraft.scStateOutMsg.read(), "sigma_BN", 3),
            "angular_rate_rad_s": _safe_vector(spacecraft.scStateOutMsg.read(), "omega_BN_B", 3),
        },
        "adcs_reference": lambda: {"sigma_R0N": _safe_vector(reference, "sigma_R0N", 3)},
        "adcs_gains": lambda: {
            "K": _safe_scalar(controller, "K"),
            "P": _safe_scalar(controller, "P"),
            "Ki": _safe_scalar(controller, "Ki"),
        },
    }
    if battery is not None:
        readers["battery"] = lambda: {
            "stored_energy_j": _safe_scalar(battery.batPowerOutMsg.read(), "storageLevel"),
            "capacity_j": _safe_scalar(battery, "storageCapacity"),
        }
    if storage is not None:
        readers["data_storage"] = lambda: {
            "stored_bits": _safe_scalar(storage.storageUnitDataOutMsg.read(), "storageLevel"),
            "capacity_bits": _safe_scalar(storage, "storageCapacity"),
        }
    for key, reader in readers.items():
        try:
            observations[key] = reader()
        except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
            unavailable[key] = f"{type(exc).__name__}: {exc}"
    return observations, unavailable


def capture_scheduler_phase(sim: Any) -> dict[str, Any]:
    """Read native scheduler counters without mutating its state."""
    now = int(sim.TotalSim.CurrentNanos)
    next_task = int(sim.TotalSim.NextTaskTime)
    if now < 0 or next_task < now:
        raise ValueError("NATIVE_SCHEDULER_COUNTERS_INVALID")
    return {
        "current_nanos": now, "next_task_time_nanos": next_task,
        "until_next_task_nanos": next_task - now,
        "native_clock_restore_proven": False,
        "task_queue_restore_proven": False,
        "event_queue_restore_proven": False,
    }


def create_coverage_report(
    *, sim_time_s: float, observations: dict[str, Any],
    unavailable: dict[str, str], native_worker_ident: int,
    state_subset_pilot: dict[str, Any] | None = None,
    scheduler_phase: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not math.isfinite(sim_time_s) or sim_time_s <= 0:
        raise ValueError("NATIVE_CHECKPOINT_REQUIRES_ADVANCED_SIMULATION")
    coverage = [
        {
            "owner": owner,
            "native_state_export": "NOT_IMPLEMENTED",
            "native_state_import": "NOT_IMPLEMENTED",
            "restart_numerical_parity": "NOT_PROVEN",
        }
        for owner in MANDATORY_STATE_OWNERS
    ]
    if state_subset_pilot is not None:
        from .native_state_pilot import validate_state_subset
        validate_state_subset(state_subset_pilot)
    if scheduler_phase is not None and any(
        scheduler_phase.get(k) is not False for k in (
            "native_clock_restore_proven",
            "task_queue_restore_proven",
            "event_queue_restore_proven",
        )
    ):
        raise ValueError("NATIVE_SCHEDULER_UNPROVEN_RESTORE_CLAIM")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "sim_time_s": sim_time_s,
        "capture_source": "LIVE_BASILISK_WORKER_THREAD",
        "native_worker_ident": native_worker_ident,
        "observations": observations,
        "unavailable_observations": unavailable,
        "required_state_owners": coverage,
        "required_owner_count": len(coverage),
        "fully_restorable_owner_count": 0,
        "native_checkpoint_complete": False,
        "resume_supported": False,
        "reason_code": "NATIVE_FULL_PHYSICAL_STATE_SNAPSHOT_NOT_AVAILABLE",
        "telemetry_is_not_checkpoint": True,
        "native_state_subset_pilot": state_subset_pilot,
        "native_scheduler_phase": scheduler_phase,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    payload["report_sha256"] = hashlib.sha256(raw).hexdigest()
    return payload


def verify_coverage_report(report: dict[str, Any]) -> None:
    """Reject reports with changed evidence or a forged resume-ready flag."""
    content = dict(report)
    expected = content.pop("report_sha256", None)
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("NATIVE_CHECKPOINT_AUDIT_DIGEST_MISMATCH")
    if content.get("native_state_subset_pilot") is not None:
        from .native_state_pilot import validate_state_subset
        validate_state_subset(content["native_state_subset_pilot"])
    phase = content.get("native_scheduler_phase")
    if phase is not None and (
        any(phase.get(k) is not False for k in (
            "native_clock_restore_proven",
            "task_queue_restore_proven",
            "event_queue_restore_proven",
        ))
        or phase.get("next_task_time_nanos", -1) < phase.get("current_nanos", 0)
    ):
        raise ValueError("NATIVE_SCHEDULER_UNPROVEN_RESTORE_CLAIM")
    names = [r["owner"] for r in content["required_state_owners"]]
    if names != list(MANDATORY_STATE_OWNERS) or content.get("native_checkpoint_complete") is not False or content.get("resume_supported") is not False:
        raise ValueError("NATIVE_CHECKPOINT_AUDIT_UNSUPPORTED_RESUME_CLAIM")
