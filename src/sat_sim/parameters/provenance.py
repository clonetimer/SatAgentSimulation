"""Parameter provenance and calibration-confidence gates.

This module does not calibrate the satellite model by itself.  It provides a
machine-readable contract for the provenance of parameters used by engineering
profiles.  Demo defaults can still run, but they are explicitly reported as
``demo`` rather than silently qualifying engineering or ground-calibrated use.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import json
from typing import Any, Mapping

from parameters.resource_paths import resolve_parameter_registry

SCHEMA_VERSION = "parameter-provenance-calibration-v1.0"
BATCH = "PARAMETER-PROVENANCE-CALIBRATION-1"

CONFIDENCE_LEVELS: tuple[str, ...] = (
    "demo",
    "engineering_estimate",
    "ground_calibrated",
    "flight_correlated",
)
CONFIDENCE_RANK = {name: idx for idx, name in enumerate(CONFIDENCE_LEVELS)}

PROFILE_MIN_CONFIDENCE = {
    "demo": "demo",
    "engineering_estimate": "engineering_estimate",
    "ground_calibrated": "ground_calibrated",
    "flight_correlated": "flight_correlated",
}

ASSUMPTION_SOURCE_TYPES = {
    "demo_default",
    "engineering_assumption",
    "placeholder",
    "unknown",
}

CALIBRATED_CONFIDENCE = {"ground_calibrated", "flight_correlated"}


@dataclass(frozen=True)
class ParameterRecord:
    """One auditable simulation parameter.

    ``value`` may be scalar, vector or string because some configuration values
    are switches rather than numerical constants.  Numerical uncertainty is only
    required for engineering or higher quantitative parameters; demo records may
    use ``distribution='not_established'``.
    """

    parameter_id: str
    owner: str
    config_path: str
    native_target: str
    value: Any
    unit: str
    confidence: str
    source_type: str
    source_ref: str
    approval_status: str = "draft"
    uncertainty: dict[str, Any] = field(default_factory=dict)
    validity: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterGateIssue:
    code: str
    severity: str
    parameter_id: str
    message: str
    evidence: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def project_root_from_module() -> Path:
    return Path(__file__).resolve().parents[3]


def default_registry_path(root: str | Path | None = None) -> Path:
    return resolve_parameter_registry("demo_parameter_registry_v1.json", root=root)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_parameter_records(path: str | Path | None = None, *, root: str | Path | None = None) -> tuple[dict[str, Any], list[ParameterRecord]]:
    resolved = Path(path) if path is not None else default_registry_path(root)
    payload = _load_json(resolved)
    records = [ParameterRecord(**row) for row in payload.get("parameters", [])]
    return {**payload, "registry_path": str(resolved)}, records


def required_parameter_ids() -> tuple[str, ...]:
    """Required V16 mainline parameter set for the whole-spacecraft profile.

    This is intentionally limited to parameters used by the current V15 mission
    closure path.  Adding new model surfaces should extend this list rather than
    silently relying on anonymous defaults.
    """

    return (
        "whole.adcs_dyn_step_s",
        "whole.adcs_fsw_step_s",
        "whole.mission_initial_sigma_bn",
        "whole.mission_initial_omega_bn_b_rad_s",
        "whole.battery_capacity_wh",
        "whole.initial_soc",
        "whole.solar_power_w",
        "whole.payload_power_w",
        "whole.bus_power_w",
        "whole.instrument_baud_bps",
        "whole.storage_capacity_bits",
        "whole.storage_initial_bits",
        "whole.transmitter_baud_bps",
        "whole.native_downlink_bit_rate_request_bps",
        "whole.native_downlink_packet_size_bits",
        "whole.native_downlink_max_retransmissions",
        "whole.native_downlink_cnr_linear",
        "whole.native_downlink_distance_m",
        "whole.native_downlink_bandwidth_hz",
        "whole.native_downlink_frequency_hz",
        "whole.thermal_step_s",
        "whole.thermal_heat_power_w",
        "whole.orb_env_step_s",
        "whole.orb_env_sun_model",
        "whole.orb_env_magnetic_field_model",
        "whole.orb_env_spice_epoch_utc",
        "whole.orb_env_enable_eclipse",
        "adcs.spacecraft_mass_kg",
        "adcs.spacecraft_inertia_kg_m2",
        "adcs.rw.wheel_js",
        "adcs.rw.u_max_nm",
        "adcs.rw.omega_max_rad_s",
        "adcs.rw.fCoulomb",
        "adcs.rw.cViscous",
        "adcs.imu.gyro_scale",
        "adcs.magnetometer.scale_factor",
        "adcs.css.scale_factor",
        "comm.rf.spacecraft_antenna_tx_power_w",
        "comm.rf.frequency_hz",
        "comm.rf.bandwidth_hz",
        "eps.battery.fault_capacity_ratio_nominal",
        "eps.solar.panel_area_m2",
        "thermal.node.initial_temp_k",
        "thermal.node.heat_capacity_j_k",
        "propulsion.thruster.max_thrust_n",
        "propulsion.thruster.steady_isp_s",
        "propulsion.thruster.min_on_time_s",
        "propulsion.fuel_tank.initial_mass_kg",
        "propulsion.fuel_tank.leak_rate_kg_s",
    )


def _rank(level: str) -> int:
    if level not in CONFIDENCE_RANK:
        raise ValueError(f"unknown confidence level: {level}")
    return CONFIDENCE_RANK[level]


def _min_confidence(records: list[ParameterRecord]) -> str:
    if not records:
        return "demo"
    idx = min(CONFIDENCE_RANK.get(row.confidence, -1) for row in records)
    if idx < 0:
        return "invalid"
    return CONFIDENCE_LEVELS[idx]


def validate_parameter_registry(
    payload: Mapping[str, Any],
    records: list[ParameterRecord],
    *,
    requested_profile: str = "demo",
    required_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if requested_profile not in PROFILE_MIN_CONFIDENCE:
        raise ValueError(f"unknown parameter profile: {requested_profile}")
    required_ids = required_ids or required_parameter_ids()
    min_required = PROFILE_MIN_CONFIDENCE[requested_profile]
    min_required_rank = _rank(min_required)
    by_id: dict[str, ParameterRecord] = {}
    issues: list[ParameterGateIssue] = []
    duplicates: list[str] = []
    for row in records:
        if row.parameter_id in by_id:
            duplicates.append(row.parameter_id)
            issues.append(ParameterGateIssue("duplicate_parameter", "fail", row.parameter_id, "parameter_id must be unique"))
        by_id[row.parameter_id] = row
        if row.confidence not in CONFIDENCE_RANK:
            issues.append(ParameterGateIssue("invalid_confidence", "fail", row.parameter_id, "confidence level is not recognized", row.confidence))
        if not str(row.unit):
            issues.append(ParameterGateIssue("missing_unit", "fail", row.parameter_id, "unit must be explicit; use 'dimensionless' when applicable"))
        if not str(row.owner):
            issues.append(ParameterGateIssue("missing_owner", "fail", row.parameter_id, "owner subsystem/component must be explicit"))
        if not str(row.config_path):
            issues.append(ParameterGateIssue("missing_config_path", "fail", row.parameter_id, "config_path must link the record to an effective project parameter"))
        if not str(row.source_ref):
            issues.append(ParameterGateIssue("missing_source_ref", "fail", row.parameter_id, "source_ref must identify the data sheet, test, document, or demo rationale"))
        if row.confidence in CALIBRATED_CONFIDENCE and not row.calibration.get("dataset_id"):
            issues.append(ParameterGateIssue("missing_calibration_dataset", "fail", row.parameter_id, "ground/flight calibrated parameters require a calibration dataset_id"))
        if _rank(row.confidence) < min_required_rank:
            issues.append(ParameterGateIssue("insufficient_confidence", "fail", row.parameter_id, f"{requested_profile} requires >= {min_required}", row.confidence))
    missing = [pid for pid in required_ids if pid not in by_id]
    for pid in missing:
        issues.append(ParameterGateIssue("missing_required_parameter", "fail", pid, "required mainline parameter is absent from the registry"))
    unknown = [pid for pid in by_id if pid not in set(required_ids)]
    fail_count = sum(1 for issue in issues if issue.severity == "fail")
    assumed = [row for row in records if row.source_type in ASSUMPTION_SOURCE_TYPES or row.confidence == "demo"]
    uncalibrated = [row for row in records if row.confidence not in CALIBRATED_CONFIDENCE]
    extrapolated = [row for row in records if bool(row.validity.get("extrapolation_allowed", False))]
    min_conf = _min_confidence(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "status": "PASS" if fail_count == 0 else "FAIL",
        "registry_status": "PASS" if fail_count == 0 else "FAIL",
        "requested_profile": requested_profile,
        "minimum_required_confidence": min_required,
        "minimum_observed_confidence": min_conf,
        "record_count": len(records),
        "required_parameter_count": len(required_ids),
        "missing_parameter_count": len(missing),
        "duplicate_parameter_count": len(duplicates),
        "invalid_parameter_count": fail_count,
        "assumed_parameter_count": len(assumed),
        "uncalibrated_parameter_count": len(uncalibrated),
        "extrapolated_parameter_count": len(extrapolated),
        "unknown_extra_parameter_count": len(unknown),
        "confidence_counts": {level: sum(1 for row in records if row.confidence == level) for level in CONFIDENCE_LEVELS},
        "source_type_counts": {source_type: sum(1 for row in records if row.source_type == source_type) for source_type in sorted({row.source_type for row in records})},
        "issues": [issue.to_dict() for issue in issues],
        "missing_parameter_ids": missing,
        "duplicate_parameter_ids": duplicates,
        "unknown_extra_parameter_ids": unknown,
        "registry_metadata": {k: v for k, v in payload.items() if k != "parameters"},
    }


def calibration_status_from_gate(gate: Mapping[str, Any]) -> str:
    profile = str(gate.get("requested_profile", "demo"))
    status = str(gate.get("status", "FAIL"))
    if status != "PASS":
        return f"{profile}_not_qualified"
    if profile == "demo":
        return "demo_profile"
    if profile == "engineering_estimate":
        return "engineering_estimate_profile"
    if profile == "ground_calibrated":
        return "ground_calibrated_profile"
    if profile == "flight_correlated":
        return "flight_correlated_profile"
    return f"{profile}_profile"


def build_parameter_provenance_payload(
    root: str | Path | None = None,
    *,
    registry_path: str | Path | None = None,
    requested_profile: str = "demo",
) -> dict[str, Any]:
    payload, records = load_parameter_records(registry_path, root=root)
    gate = validate_parameter_registry(payload, records, requested_profile=requested_profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "status": gate["status"],
        "registry": {k: v for k, v in payload.items() if k != "parameters"},
        "summary": gate,
        "parameters": [row.to_dict() for row in records],
    }


__all__ = [
    "SCHEMA_VERSION",
    "BATCH",
    "CONFIDENCE_LEVELS",
    "ParameterRecord",
    "ParameterGateIssue",
    "default_registry_path",
    "load_parameter_records",
    "required_parameter_ids",
    "validate_parameter_registry",
    "calibration_status_from_gate",
    "build_parameter_provenance_payload",
]
