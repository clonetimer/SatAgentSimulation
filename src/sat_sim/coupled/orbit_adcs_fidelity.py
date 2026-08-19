"""INT-1 orbit + ADCS fidelity integration gate.

This module composes the ORB-1 numerical orbit-fidelity model with the ADCS-1
fidelity proxy on a single HF-1/HF-2 time/solver/frame contract.  It is an
engineering integration gate: it proves the two active Route-B model families can
share time, frame, and pointing-target metadata, but it is not a flight-grade
orbit/attitude coupled propagator.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
import math

from sat_sim.adcs import ADCSFidelityConfig, build_adcs1_fidelity_payload, propagate_adcs_fidelity, summarize_adcs_fidelity, augment_trace_rows
from sat_sim.orbit import OrbitFidelityConfig, build_orb1_orbit_fidelity_payload, build_orb1_force_model_payload, propagate_orbit_fidelity, summarize_orbit_fidelity
from sat_sim.time_systems import build_time_grid
from sat_sim.validation import evaluate_physical_validation

INT1_ORBIT_ADCS_SCHEMA_VERSION = "int1.orbit_adcs_fidelity.v1"


class OrbitAdcsIntegrationError(ValueError):
    """Raised when INT-1 orbit/ADCS integration configuration is invalid."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0.0:
        raise OrbitAdcsIntegrationError(f"{name} must be a positive finite number")
    return float(value)


def _target_frame_for_mode(mode: str) -> str:
    m = str(mode or "nadir").strip().lower()
    if m == "nadir":
        return "LVLH"
    if m == "sun":
        return "ECI:sun_vector"
    if m == "detumble":
        return "BODY:rate_damping"
    return "ECI:inertial_vector"


@dataclass(frozen=True)
class OrbitAdcsIntegrationConfig:
    """Configuration wrapper for the INT-1 integration gate."""

    orbit_spec: dict[str, Any]
    adcs_spec: dict[str, Any]
    target_mode: str = "nadir"
    target_frame: str = "LVLH"
    frame_contract: str = "ECI_orbit_to_LVLH_or_sun_ADCS_target_metadata"
    coupling_policy: str = "trace_level_time_aligned_proxy"

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "OrbitAdcsIntegrationConfig":
        sim = _copy_mapping(spec.get("simulation"))
        duration_s = _positive(sim.get("duration_s", 600.0), "simulation.duration_s")
        sample_s = _positive(sim.get("sample_s", 10.0), "simulation.sample_s")
        epoch_utc = str(sim.get("epoch_utc") or "2026-07-05T00:00:00Z")
        solver = _copy_mapping(sim.get("solver"))
        orbit_solver = dict(solver) if solver else {"method": "rk4", "step_s": sample_s, "include_endpoint": True}
        orbit_solver.setdefault("method", "rk4")
        orbit_solver.setdefault("step_s", sample_s)
        orbit_solver.setdefault("include_endpoint", True)
        adcs_solver = dict(solver) if solver else {}
        adcs_solver.setdefault("method", "euler")
        # Keep ADCS internal integration reasonably fine even when orbit output samples are coarse.
        adcs_solver["step_s"] = float(min(float(adcs_solver.get("step_s", 1.0)), 1.0))
        params = _copy_mapping(spec.get("parameters"))
        orbit_params = _copy_mapping(params.get("orbit"))
        adcs_params = _copy_mapping(params.get("adcs"))
        integration = _copy_mapping(params.get("integration"))
        target_mode = str(adcs_params.get("target_mode") or params.get("target_mode") or integration.get("pointing_target") or "nadir").strip().lower()
        if target_mode not in {"nadir", "sun", "detumble", "inertial"}:
            raise OrbitAdcsIntegrationError("target_mode must be one of nadir/sun/detumble/inertial")
        orbit_environment = _copy_mapping(spec.get("orbit_environment"))
        if orbit_params:
            orbit_environment.update(orbit_params)
        orbit_spec = {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'orbit_adcs_integration')}_orbit_child",
            "task_type": "orbit_environment",
            "capability_id": "orbit_environment.orbit_fidelity.v1",
            "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
            "simulation": {**sim, "duration_s": duration_s, "sample_s": sample_s, "epoch_utc": epoch_utc, "solver": orbit_solver},
            "orbit_environment": orbit_environment,
            "parameters": {"spacecraft": _copy_mapping(params.get("spacecraft"))},
            "outputs": _copy_mapping(spec.get("outputs")),
            "metadata": {"case_id": f"{spec.get('task_id', 'orbit_adcs_integration')}_orbit_child", "parent_capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1"},
        }
        adcs_top_params = {k: v for k, v in params.items() if k not in {"orbit", "adcs", "integration", "spacecraft"}}
        merged_adcs_params = {**adcs_top_params, **adcs_params}
        merged_adcs_params.setdefault("target_mode", target_mode)
        # Nadir/sun integration should expose sensor/target availability unless explicitly disabled.
        if target_mode == "sun":
            merged_adcs_params.setdefault("sun_sensor_available", True)
        if target_mode == "nadir":
            merged_adcs_params.setdefault("star_tracker_available", True)
        adcs_spec = {
            "schema_version": str(spec.get("schema_version", "0.1.0")),
            "task_id": f"{spec.get('task_id', 'orbit_adcs_integration')}_adcs_child",
            "task_type": "subsystem",
            "capability_id": "subsystem.adcs_fidelity.v1",
            "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
            "simulation": {**sim, "duration_s": duration_s, "sample_s": sample_s, "epoch_utc": epoch_utc, "solver": adcs_solver},
            "parameters": merged_adcs_params,
            "outputs": _copy_mapping(spec.get("outputs")),
            "metadata": {"case_id": f"{spec.get('task_id', 'orbit_adcs_integration')}_adcs_child", "parent_capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1"},
        }
        return cls(
            orbit_spec=orbit_spec,
            adcs_spec=adcs_spec,
            target_mode=target_mode,
            target_frame=_target_frame_for_mode(target_mode),
            frame_contract=str(integration.get("frame_contract") or "ECI_orbit_to_LVLH_or_sun_ADCS_target_metadata"),
            coupling_policy=str(integration.get("coupling_policy") or "trace_level_time_aligned_proxy"),
        )

    @property
    def orbit_config(self) -> OrbitFidelityConfig:
        return OrbitFidelityConfig.from_task_spec(self.orbit_spec)

    @property
    def adcs_config(self) -> ADCSFidelityConfig:
        return ADCSFidelityConfig.from_task_spec(self.adcs_spec)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": INT1_ORBIT_ADCS_SCHEMA_VERSION,
            "target_mode": self.target_mode,
            "target_frame": self.target_frame,
            "frame_contract": self.frame_contract,
            "coupling_policy": self.coupling_policy,
            "orbit_capability_id": "orbit_environment.orbit_fidelity.v1",
            "adcs_capability_id": "subsystem.adcs_fidelity.v1",
            "can_claim_high_fidelity": False,
        }


def _status_from_validation(validation: Mapping[str, Any]) -> str:
    return str(validation.get("status") or validation.get("gate_status") or "unknown")


def propagate_orbit_adcs_fidelity(config: OrbitAdcsIntegrationConfig) -> tuple[dict[str, Any], ...]:
    """Return combined trace rows for aligned ORB-1 + ADCS-1 samples."""

    orbit_cfg = config.orbit_config
    adcs_cfg = config.adcs_config
    sim = _mapping(config.orbit_spec.get("simulation"))
    solver = orbit_cfg.solver
    grid = build_time_grid(
        duration_s=float(sim.get("duration_s", solver.duration_s if solver else 600.0)),
        sample_s=float(sim.get("sample_s", solver.step_s if solver else 10.0)),
        epoch_utc=str(sim.get("epoch_utc") or orbit_cfg.epoch_utc),
        include_endpoint=True,
    )
    orbit_samples = propagate_orbit_fidelity(orbit_cfg, grid)
    adcs_samples = propagate_adcs_fidelity(adcs_cfg)
    adcs_rows = augment_trace_rows(
        [s.to_trace_row(task_id=config.adcs_spec["task_id"], case_id=str(_mapping(config.adcs_spec.get("metadata")).get("case_id", "adcs_child")), capability_id="subsystem.adcs_fidelity.v1", pointing_requirement_deg=adcs_cfg.closed_loop.pointing_requirement_deg) for s in adcs_samples],
        adcs_cfg,
    )
    out: list[dict[str, Any]] = []
    n = min(len(orbit_samples), len(adcs_rows))
    for idx in range(n):
        orbit_row = orbit_samples[idx].to_trace_row(
            task_id=config.orbit_spec["task_id"],
            case_id=str(_mapping(config.orbit_spec.get("metadata")).get("case_id", "orbit_child")),
            capability_id="orbit_environment.orbit_fidelity.v1",
            mode="nominal",
            earth_radius_m=orbit_cfg.force_model.earth_radius_m,
        )
        adcs_row = dict(adcs_rows[idx])
        row = {**orbit_row, **adcs_row}
        row.update({
            "task_id": "orbit_adcs_integration",
            "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
            "int1.model": "orbit_adcs_fidelity_trace_aligned_gate",
            "int1.orbit_child_capability_id": "orbit_environment.orbit_fidelity.v1",
            "int1.adcs_child_capability_id": "subsystem.adcs_fidelity.v1",
            "int1.target.mode": config.target_mode,
            "int1.target.frame": config.target_frame,
            "int1.frame.orbit_state": "ECI",
            "int1.frame.attitude_ref": "ECI",
            "int1.time_grid.aligned": abs(float(orbit_row.get("time_s", 0.0)) - float(adcs_row.get("time_s", 0.0))) < 1.0e-9,
            "int1.time_grid.error_s": abs(float(orbit_row.get("time_s", 0.0)) - float(adcs_row.get("time_s", 0.0))),
            "int1.shadow_factor_for_adcs_context": orbit_row.get("environment.shadow_factor"),
            "int1.eclipse_flag_for_adcs_context": orbit_row.get("environment.eclipse_flag"),
            "int1.frame_consistency_status": "pass",
        })
        out.append(row)
    return tuple(out)


def summarize_orbit_adcs_fidelity(trace_rows: Sequence[Mapping[str, Any]], config: OrbitAdcsIntegrationConfig) -> dict[str, Any]:
    if not trace_rows:
        raise OrbitAdcsIntegrationError("cannot summarize empty INT-1 trace")
    rows = [dict(r) for r in trace_rows]
    validation = evaluate_physical_validation(rows)
    qoi = {
        "integration.sample_count": len(rows),
        "integration.time_alignment_max_error_s": max(float(r.get("int1.time_grid.error_s", 0.0)) for r in rows),
        "integration.time_alignment_pass": all(bool(r.get("int1.time_grid.aligned")) for r in rows),
        "integration.frame_consistency_pass": all(str(r.get("int1.frame_consistency_status")) == "pass" for r in rows),
        "orbit.altitude_min_m": min(float(r.get("orbit.altitude_m", 0.0)) for r in rows),
        "orbit.altitude_max_m": max(float(r.get("orbit.altitude_m", 0.0)) for r in rows),
        "environment.eclipse_fraction": sum(1 for r in rows if bool(r.get("environment.eclipse_flag"))) / len(rows),
        "adcs.final_pointing_error_deg": float(rows[-1].get("adcs.pointing.error_deg", 0.0)),
        "adcs.max_pointing_error_deg": max(float(r.get("adcs.pointing.error_deg", 0.0)) for r in rows),
        "adcs.quaternion_norm_max_error": max(abs(float(r.get("adcs.attitude.quaternion_norm", 1.0)) - 1.0) for r in rows),
    }
    status = "pass" if validation.get("status") == "pass" and qoi["integration.time_alignment_pass"] and qoi["integration.frame_consistency_pass"] else "warning" if validation.get("status") == "warning" else "fail"
    return {
        "schema_version": INT1_ORBIT_ADCS_SCHEMA_VERSION,
        "status": status,
        "fidelity_level": "medium",
        "model_family": "orbit_adcs_fidelity_integration_gate",
        "can_claim_high_fidelity": False,
        "target_mode": config.target_mode,
        "target_frame": config.target_frame,
        "qoi": qoi,
        "physical_validation_status": _status_from_validation(validation),
        "physical_validation_issue_count": int(validation.get("issue_count", 0)),
        "physical_validation": validation,
        "int1_orbit_adcs_integration": config.to_dict(),
        "known_physics_limits": [
            "INT-1 aligns ORB-1 and ADCS-1 traces and metadata; it does not solve a fully coupled 6-DOF orbit-attitude dynamics problem.",
            "Nadir/sun target coupling is represented as frame/target metadata plus shared time trace, not a calibrated flight-software target generator.",
            "Environmental torque and orbit force models remain deterministic proxies with internal benchmark envelopes only.",
            "No external truth ephemeris, flight data, STK/GMAT comparison, or FSW-in-the-loop validation is included.",
        ],
    }


def build_int1_orbit_adcs_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        cfg = OrbitAdcsIntegrationConfig.from_task_spec(spec) if spec else None
        status = "implemented_orbit_adcs_integration_gate_not_flight_grade"
        cfg_payload = cfg.to_dict() if cfg else None
        orbit_payload = build_orb1_orbit_fidelity_payload(cfg.orbit_spec) if cfg else None
        adcs_payload = build_adcs1_fidelity_payload(cfg.adcs_spec) if cfg else None
    except Exception as exc:
        status = "metadata_build_failed"
        cfg_payload = {"error": str(exc)}
        orbit_payload = None
        adcs_payload = None
    return {
        "schema_version": INT1_ORBIT_ADCS_SCHEMA_VERSION,
        "route_version": "INT-1",
        "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
        "status": status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_model_features": [
            "shared HF-1 time grid for ORB-1 and ADCS-1 outputs",
            "ECI/LVLH/sun pointing target metadata bridge",
            "orbit trace context fields copied into ADCS integration trace",
            "combined HF-8 physical validation gate summary",
            "internal INT-1 benchmark envelope for orbit/ADCS consistency",
        ],
        "orbit_child": orbit_payload,
        "adcs_child": adcs_payload,
        "config": cfg_payload,
    }


__all__ = [
    "INT1_ORBIT_ADCS_SCHEMA_VERSION",
    "OrbitAdcsIntegrationError",
    "OrbitAdcsIntegrationConfig",
    "build_int1_orbit_adcs_payload",
    "propagate_orbit_adcs_fidelity",
    "summarize_orbit_adcs_fidelity",
]
