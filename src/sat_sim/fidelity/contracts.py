"""HF-0 high-fidelity readiness foundation contracts.

HF-0 is a governance and interface layer, not a claim that the current package
contains engineering-grade physics.  It records the minimum time/frame/unit,
solver, fidelity, and validation metadata that later HF models must satisfy
before the Agent can present them as high-fidelity capabilities.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from sat_sim.capability_registry import get_capability, list_capabilities
from sat_sim.fidelity_policy import evaluate_fidelity_request

HF0_SCHEMA_VERSION = "hf0.high_fidelity_readiness.v1"


@dataclass(frozen=True)
class UnitContract:
    """Canonical unit requirements for future HF-capable models."""

    schema_version: str = HF0_SCHEMA_VERSION
    canonical_units: Mapping[str, str] = field(default_factory=lambda: {
        "time": "s",
        "distance": "m",
        "mass": "kg",
        "angle": "rad_internal_deg_io",
        "temperature": "K_internal_C_io",
        "power": "W",
        "energy": "Wh",
        "torque": "N*m",
        "data": "bit",
        "data_rate": "bit/s",
    })
    accepted_input_units: tuple[str, ...] = (
        "s", "min", "h", "m", "km", "kg", "g", "rad", "deg", "K", "C",
        "W", "Wh", "N*m", "N", "bit", "bit/s", "bps",
    )
    normalization_policy: str = "normalize_at_task_boundary_and_record_metadata"
    strict_unknown_unit_policy: str = "reject_unknown_unit_in_high_fidelity_mode"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["canonical_units"] = dict(self.canonical_units)
        payload["accepted_input_units"] = list(self.accepted_input_units)
        return payload


@dataclass(frozen=True)
class FrameContract:
    """Reference-frame metadata contract for later HF models."""

    schema_version: str = HF0_SCHEMA_VERSION
    supported_frames: tuple[str, ...] = ("ECI", "ECEF", "LVLH", "BODY")
    required_state_metadata: tuple[str, ...] = (
        "frame_id", "frame_origin", "frame_orientation", "epoch", "units",
    )
    default_inertial_frame: str = "ECI"
    default_body_frame: str = "BODY"
    transform_status: str = "metadata_contract_only_until_HF1"
    high_fidelity_requirement: str = "HF claims require explicit frame transform implementation and tests."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimeContract:
    """Time-system metadata contract for later HF models."""

    schema_version: str = HF0_SCHEMA_VERSION
    supported_time_systems: tuple[str, ...] = ("relative_seconds", "UTC", "JulianDate")
    required_fields: tuple[str, ...] = ("epoch", "duration_s", "sample_s", "time_system")
    epoch_policy: str = "relative_seconds_allowed_for_basic; UTC/JD required for HF orbit/environment coupling"
    monotonic_grid_required: bool = True
    fixed_step_default_s: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SolverConfig:
    """Solver configuration contract for future HF-capable models."""

    schema_version: str = HF0_SCHEMA_VERSION
    solver_family: str = "deterministic_fixed_step"
    allowed_methods: tuple[str, ...] = ("euler", "rk4", "scipy_solve_ivp_ready")
    default_method: str = "euler"
    adaptive_step_allowed: bool = False
    tolerance_policy: str = "recorded_but_not_calibrated_until_physics_benchmarks"
    deterministic_seed_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationGate:
    """One HF validation gate definition."""

    gate_id: str
    description: str
    status: str
    blocking_for_high_fidelity_claim: bool = True
    required_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_VALIDATION_GATES: tuple[ValidationGate, ...] = (
    ValidationGate(
        "unit_contract",
        "TaskSpec inputs and outputs declare canonical units and reject unknown HF units.",
        "contract_defined_not_physics_validated",
        required_evidence=("unit normalization tests", "unknown unit rejection tests"),
    ),
    ValidationGate(
        "frame_contract",
        "State vectors carry ECI/ECEF/LVLH/BODY frame metadata and transform provenance.",
        "contract_defined_transform_implementation_pending",
        required_evidence=("round-trip frame transform tests", "frame-tagged trace samples"),
    ),
    ValidationGate(
        "time_contract",
        "Simulation grid records epoch, time system, monotonicity, duration, and sample step.",
        "contract_defined_epoch_implementation_pending",
        required_evidence=("UTC/JD conversion tests", "monotonic grid tests"),
    ),
    ValidationGate(
        "solver_config",
        "Numerical solver settings are explicit, deterministic, and benchmarked.",
        "contract_defined_benchmark_pending",
        required_evidence=("solver config manifest", "step-size sensitivity benchmark"),
    ),
    ValidationGate(
        "physics_validation",
        "Capability passes conservation/bounds/domain-specific physical checks.",
        "pending_HF7_physical_validation_gates",
        required_evidence=("energy/SOC bounds", "thermal bounds", "orbit sanity", "attitude convergence"),
    ),
    ValidationGate(
        "benchmark_scenario",
        "Capability is measured against named benchmark scenarios with expected envelopes.",
        "pending_HF8_benchmark_scenarios",
        required_evidence=("benchmark dataset", "tolerance envelope", "regression report"),
    ),
)


@dataclass(frozen=True)
class FidelityReadinessReport:
    """HF-0 readiness report for one request/capability boundary."""

    schema_version: str
    capability_id: str | None
    requested_level: str
    declared_level: str
    resolved_level: str
    status: str
    can_claim_high_fidelity: bool
    policy_action: str
    known_physics_limits: tuple[str, ...]
    missing_foundations: tuple[str, ...]
    unit_contract: UnitContract = field(default_factory=UnitContract)
    frame_contract: FrameContract = field(default_factory=FrameContract)
    time_contract: TimeContract = field(default_factory=TimeContract)
    solver_config: SolverConfig = field(default_factory=SolverConfig)
    validation_gates: tuple[ValidationGate, ...] = DEFAULT_VALIDATION_GATES
    request_fidelity: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok_for_high_fidelity_claim(self) -> bool:
        return self.can_claim_high_fidelity

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "requested_level": self.requested_level,
            "declared_level": self.declared_level,
            "resolved_level": self.resolved_level,
            "status": self.status,
            "can_claim_high_fidelity": self.can_claim_high_fidelity,
            "policy_action": self.policy_action,
            "known_physics_limits": list(self.known_physics_limits),
            "missing_foundations": list(self.missing_foundations),
            "unit_contract": self.unit_contract.to_dict(),
            "frame_contract": self.frame_contract.to_dict(),
            "time_contract": self.time_contract.to_dict(),
            "solver_config": self.solver_config.to_dict(),
            "validation_gates": [gate.to_dict() for gate in self.validation_gates],
            "request_fidelity": dict(self.request_fidelity),
        }


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _declared_level(contract_data: Mapping[str, Any] | None) -> str:
    level = str((contract_data or {}).get("fidelity_level") or "basic").strip().lower()
    return level or "basic"


def _known_limits(contract_data: Mapping[str, Any] | None) -> tuple[str, ...]:
    return _as_tuple((contract_data or {}).get("known_physics_limits"))


def _missing_foundations_for_level(declared_level: str) -> tuple[str, ...]:
    missing = [
        "HF1 time/frame/unit implementation and round-trip tests",
        "HF7 physical validation gates",
        "HF8 benchmark scenarios with tolerance envelopes",
    ]
    if declared_level != "high":
        missing.insert(0, f"capability declares fidelity_level={declared_level!r}, not 'high'")
    return tuple(missing)


def build_hf0_readiness_report(
    capability_id: str | None = None,
    *,
    request_text: str = "",
    requested_level: str | None = None,
) -> FidelityReadinessReport:
    """Build an HF-0 readiness report for a capability or request.

    Current A-line capabilities are basic/source-native/synthetic; therefore the
    default report intentionally blocks high-fidelity claims while preserving a
    concrete path to HF1/HF7/HF8 evidence.
    """

    contract_data: Mapping[str, Any] | None = None
    if capability_id:
        contract_data = get_capability(capability_id).data
    declared = _declared_level(contract_data)
    fidelity_decision = evaluate_fidelity_request(request_text or "", selected_capability_id=capability_id)
    request_level = requested_level or fidelity_decision.requested_level
    if request_level == "basic_or_unspecified" and "high" in declared:
        request_level = declared
    high_requested = request_level == "high"
    can_claim_high = declared == "high" and all(not gate.blocking_for_high_fidelity_claim or gate.status == "passed" for gate in DEFAULT_VALIDATION_GATES)
    if can_claim_high:
        status = "high_fidelity_ready"
        policy_action = "allow_high_fidelity_claim"
        resolved = "high"
        missing: tuple[str, ...] = ()
    elif high_requested:
        status = "not_high_fidelity_ready"
        policy_action = "reject_high_fidelity_claim"
        resolved = "unsupported"
        missing = _missing_foundations_for_level(declared)
    else:
        status = "basic_ready_hf_governance_recorded"
        policy_action = "allow_basic_or_readiness_report_only"
        resolved = declared
        missing = _missing_foundations_for_level(declared)
    return FidelityReadinessReport(
        schema_version=HF0_SCHEMA_VERSION,
        capability_id=capability_id,
        requested_level=request_level,
        declared_level=declared,
        resolved_level=resolved,
        status=status,
        can_claim_high_fidelity=can_claim_high,
        policy_action=policy_action,
        known_physics_limits=_known_limits(contract_data),
        missing_foundations=missing,
        request_fidelity=fidelity_decision.to_dict(),
    )


def build_hf0_readiness_matrix() -> dict[str, Any]:
    """Return an HF-0 readiness matrix for all registered capabilities."""

    reports = [build_hf0_readiness_report(c.capability_id).to_dict() for c in list_capabilities()]
    return {
        "schema_version": HF0_SCHEMA_VERSION,
        "capability_count": len(reports),
        "high_fidelity_ready_count": sum(1 for item in reports if item["can_claim_high_fidelity"]),
        "capabilities": reports,
    }


__all__ = [
    "HF0_SCHEMA_VERSION",
    "UnitContract",
    "FrameContract",
    "TimeContract",
    "SolverConfig",
    "ValidationGate",
    "FidelityReadinessReport",
    "DEFAULT_VALIDATION_GATES",
    "build_hf0_readiness_report",
    "build_hf0_readiness_matrix",
]
