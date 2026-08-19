"""Deterministic TaskSpec parameter-consumption and provenance audit.

The capability contract is the source of truth for user-facing model
parameters.  This audit prevents silent spelling mistakes and records which
free-form compatibility containers remain opaque at the adapter boundary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

from .capability_registry import get_capability
from .task_models import canonicalize_task_spec

PARAMETER_CONSUMPTION_SCHEMA_VERSION = "sat-sim.parameter-consumption.v1"

# Event parameters are intentionally small, explicit contracts.  ``fmea`` is a
# cross-cutting evidence payload accepted for every event.
_CAPABILITY_PARAMETER_ALIASES: dict[str, dict[str, str]] = {
    "subsystem.adcs_fidelity.v1": {"wheel_geometry": "wheel_configuration"},
}

_EFFECT_PARAMETER_KEYS: dict[str, frozenset[str]] = {
    "adcs_rw_jamming": frozenset({"wheel_index", "brake_torque_nm", "jam_torque_nm", "fmea"}),
    "rw_jamming": frozenset({"wheel_index", "fmea"}),
    "adcs_rw_motor_failure": frozenset({"wheel_index", "remaining_torque_ratio", "torque_scale", "fmea"}),
    "adcs_rw_torque_authority_loss": frozenset({"wheel_index", "remaining_torque_ratio", "torque_scale", "fmea"}),
    "rw_motor_failure": frozenset({"wheel_index", "remaining_torque_ratio", "fmea"}),
    "adcs_reaction_wheel_speed_limit": frozenset({"wheel_index", "max_speed_rad_s", "max_speed_rpm", "fmea"}),
    "gyro_bias_step": frozenset({"bias_step_deg_s", "fmea"}),
    "adcs_gyro_bias_step": frozenset({"bias_deg_s", "fmea"}),
    "gyro_noise_increase": frozenset({"noise_scale", "fmea"}),
    "adcs_gyro_noise_increase": frozenset({"noise_multiplier", "fmea"}),
    "adcs_rw_friction_increase": frozenset({"wheel_index", "drag_torque_nm", "fmea"}),
    "payload_instrument_off": frozenset({"fmea"}),
    "comm_data_downlink_link_loss": frozenset({"fmea"}),
    "eps_battery_capacity_loss": frozenset({"remaining_capacity_ratio", "remaining_ratio", "loss_fraction", "loss_pct", "fmea"}),
    "battery_capacity_loss_30pct": frozenset({"capacity_loss_pct", "fmea"}),
    "capacity_fade": frozenset({"capacity_loss_pct", "fmea"}),
    "capacity_loss_pct": frozenset({"value_pct", "capacity_loss_pct", "fmea"}),
    "internal_resistance_increase_pct": frozenset({"value_pct", "internal_resistance_increase_pct", "fmea"}),
    "efficiency_loss_pct": frozenset({"value_pct", "efficiency_loss_pct", "fmea"}),
    "radiation_damage_factor": frozenset({"factor", "radiation_damage_factor", "fmea"}),
    "pdu_efficiency_loss_pct": frozenset({"value_pct", "pdu_efficiency_loss_pct", "fmea"}),
    "radiator_degradation_factor": frozenset({"factor", "degradation_factor", "fmea"}),
    "comm_tx_power_loss_pct": frozenset({"value_pct", "loss_pct", "fmea"}),
    "comm_tx_gain_loss_db": frozenset({"loss_db", "fmea"}),
    "comm_misc_loss_increase_db": frozenset({"increase_db", "fmea"}),
    "comm_storage_capacity_loss_pct": frozenset({"value_pct", "loss_pct", "fmea"}),
    "power_safe_mode_threshold": frozenset({"soc_threshold", "fmea"}),
    "rw_friction_degradation": frozenset({"wheel_index", "drag_nms", "friction_scale", "remaining_ratio", "fmea"}),
    "solar_panel_efficiency_loss": frozenset({"remaining_efficiency_ratio", "remaining_ratio", "loss_fraction", "loss_pct", "fmea"}),
    "thermal_radiator_rejection_loss": frozenset({"remaining_rejection_ratio", "remaining_ratio", "loss_fraction", "loss_pct", "radiator_id", "fmea"}),
    "propulsion_thruster_ignition_failure": frozenset({"fmea"}),
    "propulsion_burn_impulse_loss": frozenset({"remaining_impulse_ratio", "remaining_ratio", "fmea"}),
    "reaction_wheel_speed_limit": frozenset({"wheel_index", "speed_limit_scale", "max_speed_rad_s", "max_speed_rpm", "fmea"}),
    "adcs_reaction_wheel_speed_limit": frozenset({"wheel_index", "speed_limit_scale", "max_speed_rad_s", "max_speed_rpm", "fmea"}),
    "mtb_coil_open": frozenset({"axis_index", "remaining_dipole_ratio", "fmea"}),
    "mtb_coil_short": frozenset({"axis_index", "remaining_dipole_ratio", "fmea"}),
    "mtb_communication_loss": frozenset({"fmea"}),
    "mtb_dipole_capacity_loss": frozenset({"axis_index", "remaining_capacity_ratio", "fmea"}),
    "mtb_response_lag_increase": frozenset({"axis_index", "lag_multiplier", "fmea"}),
    "radiator_rejection_loss": frozenset({"remaining_capacity_ratio", "remaining_rejection_ratio", "remaining_ratio", "loss_fraction", "loss_pct", "fmea"}),
    "radiator_surface_contamination": frozenset({"emissivity_loss_ratio", "fmea"}),
    "radiator_emissivity_decay": frozenset({"emissivity_loss_ratio", "fmea"}),
    "radiator_area_degradation": frozenset({"area_loss_ratio", "fmea"}),
}

_MISSION_KEYS = frozenset({
    "template", "configuration_source", "required_couplings",
    "access_window_s", "access_period_s", "max_pointing_error_deg",
})

# These containers are retained for backward compatibility.  They are handed to
# legacy adapters as complete mappings, so they are explicitly visible as
# opaque-forwarded rather than silently labelled fully consumed.
_OPAQUE_MODEL_CONTAINERS = (
    "spacecraft", "orbit_environment", "legacy_degradations", "campaign",
    "simulation_extra", "validation",
)

@dataclass(frozen=True)
class ParameterConsumptionAudit:
    capability_id: str
    status: str
    consumed_paths: tuple[str, ...] = field(default_factory=tuple)
    alias_paths: tuple[str, ...] = field(default_factory=tuple)
    unknown_paths: tuple[str, ...] = field(default_factory=tuple)
    conflict_paths: tuple[str, ...] = field(default_factory=tuple)
    opaque_forwarded_paths: tuple[str, ...] = field(default_factory=tuple)
    ignored_paths: tuple[str, ...] = field(default_factory=tuple)
    provenance_gaps: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, Any] = field(default_factory=dict)
    schema_version: str = PARAMETER_CONSUMPTION_SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return not self.unknown_paths and not self.conflict_paths

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def _leaf_paths(value: Any, prefix: str) -> list[str]:
    if isinstance(value, Mapping):
        out: list[str] = []
        for key in sorted(value, key=str):
            out.extend(_leaf_paths(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return out or [prefix]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        out: list[str] = []
        for index, item in enumerate(value):
            out.extend(_leaf_paths(item, f"{prefix}[{index}]"))
        return out or [prefix]
    return [prefix]


def _provenance_source(fields: Mapping[str, Any], path: str) -> str | None:
    item = fields.get(path)
    return str(item.get("source")) if isinstance(item, Mapping) and item.get("source") else None


def audit_parameter_consumption(spec: Mapping[str, Any]) -> ParameterConsumptionAudit:
    canonical = canonicalize_task_spec(spec)
    model = canonical.get("model") if isinstance(canonical.get("model"), Mapping) else {}
    capability_id = str(model.get("capability_id") or "")
    contract = get_capability(capability_id)
    declared = contract.data.get("parameters") if isinstance(contract.data.get("parameters"), Mapping) else {}
    declared_keys = {str(key) for key in declared}

    consumed: list[str] = []
    aliases: list[str] = []
    unknown: list[str] = []
    conflicts: list[str] = []
    opaque: list[str] = []
    ignored: list[str] = []
    provenance_gaps: list[str] = []

    params = canonical.get("parameters") if isinstance(canonical.get("parameters"), Mapping) else {}
    values = params.get("values") if isinstance(params.get("values"), Mapping) else {}
    provenance = canonical.get("provenance") if isinstance(canonical.get("provenance"), Mapping) else {}
    provenance_fields = provenance.get("fields") if isinstance(provenance.get("fields"), Mapping) else {}
    profile = str(params.get("profile") or "demo")

    for key in sorted(values, key=str):
        path = f"parameters.values.{key}"
        key_text = str(key)
        alias_target = _CAPABILITY_PARAMETER_ALIASES.get(capability_id, {}).get(key_text)
        if key_text in declared_keys:
            consumed.extend(_leaf_paths(values[key], path))
            if profile != "demo" and not _provenance_source(provenance_fields, path):
                provenance_gaps.append(path)
        elif alias_target and alias_target in declared_keys:
            aliases.extend(_leaf_paths(values[key], path))
            if profile != "demo" and not _provenance_source(provenance_fields, path):
                provenance_gaps.append(path)
        else:
            unknown.append(path)

    for index, override in enumerate(params.get("overrides") or []):
        if not isinstance(override, Mapping):
            continue
        raw_path = str(override.get("path") or "")
        key = raw_path.removeprefix("parameters.values.").removeprefix("parameters.")
        path = f"parameters.overrides[{index}].path"
        if key in declared_keys:
            consumed.append(path)
        else:
            unknown.append(path + f"={raw_path}")

    config = model.get("config") if isinstance(model.get("config"), Mapping) else {}
    for key in sorted(config, key=str):
        path = f"model.config.{key}"
        key_text = str(key)
        alias_target = _CAPABILITY_PARAMETER_ALIASES.get(capability_id, {}).get(key_text)
        if key_text not in declared_keys and not (alias_target and alias_target in declared_keys):
            unknown.append(path)
            continue
        aliases.extend(_leaf_paths(config[key], path))
        # Canonical ``parameters.values`` is authoritative.  ``model.config``
        # is a compatibility mirror retained for older adapters; a stale mirror
        # is disclosed but must not override or block the canonical value.

    mission = canonical.get("mission") if isinstance(canonical.get("mission"), Mapping) else {}
    for key in sorted(mission, key=str):
        path = f"mission.{key}"
        if str(key) in _MISSION_KEYS:
            consumed.extend(_leaf_paths(mission[key], path))
        else:
            unknown.extend(_leaf_paths(mission[key], path))

    events = canonical.get("events") if isinstance(canonical.get("events"), Mapping) else {}
    for collection in ("faults", "degradations", "constraints"):
        for index, event in enumerate(events.get(collection) or []):
            if not isinstance(event, Mapping):
                continue
            effect = str(event.get("effect") or "")
            allowed = _EFFECT_PARAMETER_KEYS.get(effect, frozenset({"fmea"})) | frozenset({"scenario"})
            parameters = event.get("parameters") if isinstance(event.get("parameters"), Mapping) else {}
            for key in sorted(parameters, key=str):
                prefix = f"events.{collection}[{index}].parameters.{key}"
                leaves = _leaf_paths(parameters[key], prefix)
                if str(key) in allowed:
                    consumed.extend(leaves)
                else:
                    unknown.extend(leaves)

    for container in _OPAQUE_MODEL_CONTAINERS:
        value = model.get(container)
        if isinstance(value, Mapping) and value:
            opaque.extend(_leaf_paths(value, f"model.{container}"))

    # Classify every canonical leaf path.  Strict structural fields are consumed
    # by the planner/runtime; free-form metadata is deliberately retained but
    # ignored by physical execution.  Any remaining unclassified path is a hard
    # error so the audit is exhaustive rather than best-effort.
    all_paths = set(_leaf_paths(canonical, ""))
    classified = set(consumed) | set(aliases) | set(unknown) | set(conflicts) | set(opaque)
    structural_prefixes = (
        "schema_version", "task.", "simulation.", "outputs.", "assurance.",
        "provenance.", "model.capability_id", "model.target.", "model.template", "model.config",
        "parameters.profile", "parameters.overrides[",
    )
    event_core_tokens = (".id", ".event_type", ".target", ".effect", ".start_s", ".end_s", ".magnitude", ".scale", ".implementation", ".delivery", ".target_type", ".label")
    for path in sorted(all_paths - classified):
        if path.startswith("metadata.") or path == "metadata":
            ignored.append(path)
        elif path == "events.faults" or path == "events.degradations" or path == "events.constraints":
            consumed.append(path)
        elif path.startswith("events.") and path.endswith(".parameters"):
            consumed.append(path)
        elif path.startswith("events.") and any(token in path for token in event_core_tokens) and ".parameters." not in path:
            consumed.append(path)
        elif path.startswith(structural_prefixes) or path in {"mission", "parameters.values", "parameters.overrides", "provenance.fields", "provenance.assumptions", "provenance.pending_confirmations", *(f"model.{container}" for container in _OPAQUE_MODEL_CONTAINERS)}:
            consumed.append(path)
        elif path.startswith("model.config."):
            aliases.append(path)
        elif any(path.startswith(f"model.{container}.") for container in _OPAQUE_MODEL_CONTAINERS):
            opaque.append(path)
        else:
            unknown.append(path)

    status = "FAIL" if unknown or conflicts else "PASS_WITH_PROVENANCE_ADVISORY" if provenance_gaps or opaque else "PASS"
    return ParameterConsumptionAudit(
        capability_id=capability_id,
        status=status,
        consumed_paths=tuple(sorted(set(consumed))),
        alias_paths=tuple(sorted(set(aliases))),
        unknown_paths=tuple(sorted(set(unknown))),
        conflict_paths=tuple(sorted(set(conflicts))),
        opaque_forwarded_paths=tuple(sorted(set(opaque))),
        ignored_paths=tuple(sorted(set(ignored))),
        provenance_gaps=tuple(sorted(set(provenance_gaps))),
        details={
            "declared_parameter_count": len(declared_keys),
            "parameter_profile": profile,
            "canonical_leaf_count": len(all_paths),
            "classified_leaf_count": len(all_paths & (set(consumed) | set(aliases) | set(unknown) | set(conflicts) | set(opaque) | set(ignored))),
            "policy": "unknown paths and true canonical conflicts block planning; stale model.config mirrors are disclosed aliases and canonical values win",
            "shadowed_compatibility_aliases": [
                f"model.config.{key}" for key in sorted(config, key=str)
                if key in values and values[key] != config[key]
            ],
        },
    )


__all__ = [
    "PARAMETER_CONSUMPTION_SCHEMA_VERSION",
    "ParameterConsumptionAudit",
    "audit_parameter_consumption",
]
