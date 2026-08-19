"""HF-1/HF-2 implemented high-fidelity foundation metadata.

This module bridges HF-0 governance to the first concrete foundations: time,
unit, frame, state, and solver contracts.  It still does not mark any existing
capability as high-fidelity; it records the reusable foundations that later
HF-3+ models can depend on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sat_sim.frames import build_frame_contract
from sat_sim.solvers import build_solver_contract
from sat_sim.state import build_state_contract
from sat_sim.time_systems import normalize_time_contract
from sat_sim.units import normalize_unit_contract

HF1_HF2_FOUNDATION_SCHEMA_VERSION = "hf1_hf2.foundation.v1"


@dataclass(frozen=True)
class FoundationStatus:
    """One route-B foundation status item."""

    foundation_id: str
    status: str
    evidence: tuple[str, ...]
    blocking_for_high_fidelity_claim: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "foundation_id": self.foundation_id,
            "status": self.status,
            "evidence": list(self.evidence),
            "blocking_for_high_fidelity_claim": self.blocking_for_high_fidelity_claim,
        }


DEFAULT_FOUNDATION_STATUS: tuple[FoundationStatus, ...] = (
    FoundationStatus("hf1.time_systems", "implemented", ("UTC/JD/MJD conversion", "monotonic time grid")),
    FoundationStatus("hf1.units", "implemented", ("distance/angle/energy/temperature/data conversions", "unknown-unit rejection")),
    FoundationStatus("hf2.frames", "implemented_foundation", ("ECI/ECEF round-trip", "ECI/LVLH vector round-trip", "frame metadata")),
    FoundationStatus("hf2.state", "implemented_foundation", ("CartesianState", "AttitudeState", "SpacecraftState", "quaternion normalization")),
    FoundationStatus("hf2.solvers", "implemented_foundation", ("deterministic fixed-step config", "Euler/RK4 propagation trace")),
)


def build_hf1_hf2_foundation_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return serializable HF-1/HF-2 metadata derived from a TaskSpec.

    The payload is safe to include in compiled-task metadata and manifests.
    """

    spec = task_spec if isinstance(task_spec, Mapping) else {}
    simulation = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
    sim_dict = dict(simulation)
    return {
        "schema_version": HF1_HF2_FOUNDATION_SCHEMA_VERSION,
        "route_b_version": "B-1/HF-1+HF-2",
        "status": "foundation_implemented_not_physics_validated",
        "can_claim_high_fidelity": False,
        "reason_high_fidelity_still_blocked": (
            "HF-1/HF-2 foundations are implemented, but capability-specific physics models, "
            "validation gates, and benchmark envelopes are still pending."
        ),
        "implemented_foundations": [item.to_dict() for item in DEFAULT_FOUNDATION_STATUS],
        "time_contract": normalize_time_contract(sim_dict),
        "unit_contract": normalize_unit_contract(),
        "frame_contract": build_frame_contract(),
        "state_contract": build_state_contract(),
        "solver_config": build_solver_contract(sim_dict),
        "remaining_route_b_dependencies": [
            "HF-9 benchmark scenarios and tolerance envelopes",
        ],
        "implemented_route_b_models": [
            "HF-3 orbit/environment medium-fidelity model is available when using orbit_environment.medium_fidelity.v1",
            "HF-4 basic closed-loop ADCS model is available when using subsystem.adcs_closed_loop.basic.v1",
            "HF-5 power/thermal/orbit coupled model is available when using whole_spacecraft.power_thermal_orbit_coupled.v1",
            "HF-6 comm/payload mission-coupled model is available when using whole_spacecraft.comm_payload_mission_coupled.v1",
            "HF-7 propulsion/orbit/attitude maneuver-coupled model is available when using whole_spacecraft.maneuver_orbit_attitude.v1",
            "HF-8 physical validation gates are available for Route-B coupled model traces"
        ],
    }


def build_hf1_hf2_readiness_matrix(capability_count: int | None = None) -> dict[str, Any]:
    """Return package-level HF-1/HF-2 foundation readiness matrix."""

    payload = build_hf1_hf2_foundation_payload({"simulation": {"duration_s": 300.0, "sample_s": 10.0}})
    out = {
        "schema_version": HF1_HF2_FOUNDATION_SCHEMA_VERSION,
        "route_b_version": "B-1/HF-1+HF-2",
        "foundation_status": "implemented",
        "high_fidelity_ready_count": 0,
        "can_claim_package_high_fidelity": False,
        "implemented_foundation_count": len(DEFAULT_FOUNDATION_STATUS),
        "implemented_foundations": payload["implemented_foundations"],
        "remaining_route_b_dependencies": payload["remaining_route_b_dependencies"],
    }
    if capability_count is not None:
        out["capability_count"] = int(capability_count)
    return out


__all__ = [
    "HF1_HF2_FOUNDATION_SCHEMA_VERSION",
    "FoundationStatus",
    "DEFAULT_FOUNDATION_STATUS",
    "build_hf1_hf2_foundation_payload",
    "build_hf1_hf2_readiness_matrix",
]
