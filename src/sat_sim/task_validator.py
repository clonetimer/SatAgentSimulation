"""TaskSpec validation.

The validator has two layers:
1. JSON Schema validation when ``jsonschema`` is installed.
2. Built-in semantic checks for units, time windows, fault windows, and basic
   physics guardrails.

It intentionally avoids importing Basilisk or runner modules.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from .task_spec import TASK_SPEC_VERSION, TaskSpecError, as_number, load_task_spec
from .task_models import (
    CANONICAL_TASK_SPEC_VERSION,
    LEGACY_TASK_SPEC_VERSION,
    canonical_task_spec_schema,
    canonicalize_task_spec,
    is_canonical_task_spec,
    to_runtime_task_spec,
)
from .catalog import known_target_names

KNOWN_TASK_TYPES = {"component", "subsystem", "orbit_environment", "whole_spacecraft", "campaign", "reference"}
KNOWN_TARGET_MODES = {"nominal", "degradation", "fault", "mixed", "monte_carlo", "constraint"}
KNOWN_TRACE_FORMATS = {"csv", "parquet", "jsonl"}
KNOWN_BACKENDS = {"python", "basilisk", "selective_unified_basilisk_assembly"}
KNOWN_CAMPAIGN_SAMPLING = {"manual", "grid", "random", "lhs"}
KNOWN_RANDOM_DISTRIBUTIONS = {"uniform", "normal", "choice", "randint"}
KNOWN_FAULT_TYPES = {
    "accel_bias_drift", "accel_noise_increase", "accuracy_loss", "bias_drift",
    "data_corruption", "deployment_failure", "eclipse_blindness",
    "field_of_view_obstruction", "fov_obstruction", "fuel_leak",
    "gyro_bias_drift", "gyro_noise_increase", "heater_failure",
    "heater_overheating", "heater_stuck", "hysteresis", "ignition_failure",
    "intermittent", "link_loss", "noise_increase", "nozzle_blockage",
    "open_circuit", "overpressure", "pressure_loss", "rapid_leak",
    "rw_jamming", "rw_motor_failure", "adcs_rw_jamming", "adcs_rw_motor_failure",
    "gyro_bias_step", "payload_instrument_off", "comm_data_downlink_link_loss",
    "eps_battery_capacity_loss", "propulsion_thruster_ignition_failure",
    "scale_factor_error", "signal_loss", "solar_panel_degradation",
    "solar_panel_failure", "stuck_at_last", "stuck_at_zero",
    "sudden_capacity_loss", "thermal_runaway", "valve_stuck",
}
KNOWN_CONSTRAINT_TYPES = {
    "reaction_wheel_speed_limit", "adcs_reaction_wheel_speed_limit",
    "rw_speed_saturation", "adcs_rw_speed_saturation", "reaction_wheel_saturation",
    "control_torque_saturation", "control_torque_limit",
    "momentum_unloading", "power_safe_mode_threshold",
}
PERCENT_PATHS = (
    "degradations.eps.battery.capacity_loss_pct",
    "degradations.eps.battery.internal_resistance_increase_pct",
    "degradations.eps.battery.self_discharge_rate_increase_pct",
    "degradations.eps.solar_panel.efficiency_loss_pct",
    "degradations.eps.pdu_efficiency_loss_pct",
    "degradations.adcs.reaction_wheel.friction_increase_pct",
    "degradations.propulsion.thruster.thrust_loss_pct",
    "degradations.propulsion.thruster.isp_loss_pct",
    "degradations.propulsion.fuel_tank.fuel_leak_pct",
    "degradations.propulsion.fuel_tank.pressure_loss_pct",
    "degradations.thermal.heater.efficiency_loss_pct",
    "degradations.thermal.heater.response_time_increase_pct",
    "degradations.thermal.radiator.efficiency_loss_pct",
    "degradations.thermal.radiator.emissivity_degradation_pct",
)
NONNEGATIVE_PATHS = (
    "spacecraft.eps.solar_power_w",
    "spacecraft.eps.payload_power_w",
    "spacecraft.eps.bus_power_w",
    "spacecraft.comm_data.instrument_baud_bps",
    "spacecraft.comm_data.storage_capacity_bits",
    "spacecraft.comm_data.storage_initial_bits",
    "spacecraft.comm_data.transmitter_baud_bps",
    "spacecraft.thermal.heat_power_w",
    "orbit_environment.step_s",
    "degradations.eps.solar_panel.radiation_damage_factor",
    "degradations.adcs.reaction_wheel.bearing_wear_factor",
    "degradations.adcs.sensor.noise_increase_pct",
    "degradations.adcs.sensor.bias_drift_factor",
    "degradations.propulsion.thruster.fuel_leak_rate",
)
POSITIVE_PATHS = (
    "spacecraft.eps.battery_capacity_wh",
    "spacecraft.adcs.dyn_step_s",
    "spacecraft.adcs.fsw_step_s",
    "spacecraft.thermal.step_s",
)


@dataclass(frozen=True)
class ValidationIssue:
    """One validation issue."""

    severity: str
    path: str
    message: str
    code: str = "validation"

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    """Validation result with errors and warnings."""

    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }

    def raise_for_errors(self) -> None:
        if self.errors:
            joined = "; ".join(f"{issue.path}: {issue.message}" for issue in self.errors[:8])
            raise TaskSpecError(joined)


def _issue(severity: str, path: str, message: str, code: str = "validation") -> ValidationIssue:
    return ValidationIssue(severity=severity, path=path, message=message, code=code)


def _schema_path(name: str) -> Path | None:
    try:
        ref = resources.files("sat_sim.schemas").joinpath(name)
        with resources.as_file(ref) as path:
            if path.exists():
                return Path(path)
    except Exception as exc:
        record_runtime_diagnostic(
            code='PACKAGED_TASK_SCHEMA_LOOKUP_FAILED',
            category=DiagnosticCategory.REGISTRY_SCHEMA_FAILURE,
            location='src/sat_sim/task_validator.py:_schema_path:01',
            exception=exc,
            strict=None,
        )
    # Editable-source fallback.
    candidate = Path(__file__).resolve().parents[2] / "schemas" / name
    return candidate if candidate.exists() else None


def load_task_schema() -> dict[str, Any] | None:
    """Return the Pydantic-generated Canonical TaskSpec v1 JSON Schema.

    The packaged JSON file is generated from the same model for external tools;
    runtime validation uses the model-derived schema directly to prevent drift.
    """

    return canonical_task_spec_schema()


def _json_schema_issues(spec: Mapping[str, Any]) -> list[ValidationIssue]:
    schema = load_task_schema()
    if schema is None:
        return [_issue("warning", "$schema", "TaskSpec JSON Schema file not found; semantic checks only", "schema_missing")]
    try:
        from jsonschema import Draft202012Validator  # type: ignore
    except Exception:  # pragma: no cover - environment dependent
        return [_issue("warning", "$schema", "jsonschema is not installed; semantic checks only", "jsonschema_missing")]

    validator = Draft202012Validator(schema)
    issues: list[ValidationIssue] = []
    for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.path)):
        path = "$" + "".join(f".{part}" if isinstance(part, str) else f"[{part}]" for part in err.path)
        issues.append(_issue("error", path, err.message, "schema"))
    return issues


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(spec: Mapping[str, Any], path: str, default: float | None = None) -> float | None:
    try:
        return as_number(spec, path, default)
    except TaskSpecError as exc:
        raise TaskSpecError(str(exc)) from exc


def _has_non_empty_mapping(spec: Mapping[str, Any], key: str) -> bool:
    value = spec.get(key)
    return isinstance(value, Mapping) and any(v not in ({}, [], None) for v in value.values())



def _modifier_issues(spec: Mapping[str, Any], duration_s: float | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    modifiers = spec.get("modifiers")
    if modifiers in (None, {}):
        return issues
    if not isinstance(modifiers, Mapping):
        return [_issue("error", "$.modifiers", "must be an object", "type")]
    for kind, type_key in (("faults", "fault_type"), ("degradations", "degradation_type"), ("constraints", "constraint_type")):
        items = modifiers.get(kind, []) or []
        if not isinstance(items, list):
            issues.append(_issue("error", f"$.modifiers.{kind}", "must be a list", "type"))
            continue
        for i, item in enumerate(items):
            path = f"$.modifiers.{kind}[{i}]"
            if not isinstance(item, Mapping):
                issues.append(_issue("error", path, "must be an object", "type"))
                continue
            target = item.get("target") or item.get("target_path")
            if not isinstance(target, str) or not target.strip():
                issues.append(_issue("error", f"{path}.target", "is required", "required"))
            event_type_value = item.get(type_key)
            if not isinstance(event_type_value, str) or not event_type_value.strip():
                issues.append(_issue("error", f"{path}.{type_key}", "is required", "required"))
            elif kind == "constraints" and event_type_value not in KNOWN_CONSTRAINT_TYPES:
                issues.append(_issue("warning", f"{path}.{type_key}", f"unregistered operational constraint: {event_type_value!r}; capability validation will decide support", "constraint_registry"))
            elif kind == "faults" and event_type_value in KNOWN_CONSTRAINT_TYPES:
                issues.append(_issue("error", f"{path}.{type_key}", "operational constraint must be placed under modifiers.constraints, not modifiers.faults", "event_classification"))
            onset = item.get("onset_time_s", item.get("start_time_s", 0.0))
            if isinstance(onset, bool) or not isinstance(onset, (int, float)) or onset < 0:
                issues.append(_issue("error", f"{path}.onset_time_s", "must be non-negative", "range"))
            elif duration_s is not None and onset > duration_s:
                issues.append(_issue("error", f"{path}.onset_time_s", "must be within simulation duration", "time"))
            duration = item.get("duration_s", -1.0)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or (duration <= 0 and duration != -1):
                issues.append(_issue("error", f"{path}.duration_s", "must be positive or -1 for persistent modifier", "range"))
            elif isinstance(onset, (int, float)) and duration != -1 and duration_s is not None and onset + duration > duration_s + 1e-9:
                issues.append(_issue("error", path, "modifier window exceeds simulation duration", "time"))
            if "severity" in item:
                severity = item.get("severity")
                if isinstance(severity, bool) or not isinstance(severity, (int, float)) or not 0 <= float(severity) <= 1:
                    issues.append(_issue("error", f"{path}.severity", "must be within [0, 1]", "range"))
            if "scale" in item:
                scale = item.get("scale")
                if isinstance(scale, bool) or not isinstance(scale, (int, float)) or float(scale) < 0:
                    issues.append(_issue("error", f"{path}.scale", "must be non-negative", "range"))
    return issues


def _campaign_issues(spec: Mapping[str, Any]) -> list[ValidationIssue]:
    """Validate campaign-specific contract without importing campaign runtime."""

    issues: list[ValidationIssue] = []
    if spec.get("task_type") != "campaign":
        return issues
    campaign = _mapping(spec.get("campaign"))
    if not campaign:
        return [_issue("error", "$.campaign", "campaign tasks require a campaign object", "required")]

    sampling = campaign.get("sampling", "manual")
    if sampling not in KNOWN_CAMPAIGN_SAMPLING:
        issues.append(_issue("error", "$.campaign.sampling", f"unsupported sampling: {sampling!r}", "enum"))

    base_spec = campaign.get("base_spec")
    if not isinstance(base_spec, Mapping):
        issues.append(_issue("error", "$.campaign.base_spec", "is required and must be a child TaskSpec object", "required"))
    else:
        child_type = base_spec.get("task_type")
        if child_type == "campaign":
            issues.append(_issue("error", "$.campaign.base_spec.task_type", "nested campaign base_spec is not supported", "campaign"))
        elif child_type not in {"orbit_environment", "whole_spacecraft", "component", "subsystem"}:
            issues.append(_issue("error", "$.campaign.base_spec.task_type", f"unsupported child task_type: {child_type!r}", "enum"))
        else:
            # Reuse semantic checks for the child spec and remap paths.  JSON Schema
            # validation is intentionally not applied recursively here to keep the
            # top-level schema simple and avoid self-referential schema complexity.
            for child_issue in _semantic_issues(base_spec):
                if child_issue.severity == "error":
                    path = child_issue.path.replace("$", "$.campaign.base_spec", 1)
                    issues.append(_issue(child_issue.severity, path, child_issue.message, child_issue.code))

    count = campaign.get("count")
    if count is not None and (not isinstance(count, int) or count <= 0):
        issues.append(_issue("error", "$.campaign.count", "must be a positive integer when provided", "range"))
    max_cases = campaign.get("max_cases")
    if max_cases is not None and (not isinstance(max_cases, int) or max_cases <= 0):
        issues.append(_issue("error", "$.campaign.max_cases", "must be a positive integer when provided", "range"))
    parallelism = campaign.get("parallelism")
    if parallelism is not None and (not isinstance(parallelism, int) or parallelism <= 0):
        issues.append(_issue("error", "$.campaign.parallelism", "must be a positive integer when provided", "range"))

    cases = campaign.get("cases") or []
    if sampling == "manual":
        if not isinstance(cases, list) or not cases:
            issues.append(_issue("error", "$.campaign.cases", "manual sampling requires at least one case", "campaign"))
    if cases and isinstance(cases, list):
        seen: set[str] = set()
        for i, case in enumerate(cases):
            path = f"$.campaign.cases[{i}]"
            if not isinstance(case, Mapping):
                issues.append(_issue("error", path, "must be an object", "type"))
                continue
            case_id = case.get("case_id")
            if case_id is not None:
                if not isinstance(case_id, str) or not case_id.strip():
                    issues.append(_issue("error", f"{path}.case_id", "must be a non-empty string", "type"))
                elif case_id in seen:
                    issues.append(_issue("error", f"{path}.case_id", "duplicate case_id", "duplicate"))
                else:
                    seen.add(case_id)
            overrides = case.get("overrides", case.get("patch", {}))
            if overrides is not None and not isinstance(overrides, Mapping):
                issues.append(_issue("error", f"{path}.overrides", "must be an object of path:value overrides", "type"))

    sweeps = campaign.get("parameter_sweeps") or []
    if sampling == "grid":
        if not isinstance(sweeps, list) or not sweeps:
            issues.append(_issue("error", "$.campaign.parameter_sweeps", "grid sampling requires at least one parameter sweep", "campaign"))
    if sweeps and isinstance(sweeps, list):
        grid_count = 1
        for i, sweep in enumerate(sweeps):
            path = f"$.campaign.parameter_sweeps[{i}]"
            if not isinstance(sweep, Mapping):
                issues.append(_issue("error", path, "must be an object", "type"))
                continue
            if not isinstance(sweep.get("path"), str) or not str(sweep.get("path", "")).strip():
                issues.append(_issue("error", f"{path}.path", "is required", "required"))
            values = sweep.get("values")
            if not isinstance(values, list) or not values:
                issues.append(_issue("error", f"{path}.values", "must be a non-empty list", "required"))
            else:
                grid_count *= len(values)
        if sampling == "grid" and max_cases is None and grid_count > 500:
            issues.append(_issue("warning", "$.campaign.parameter_sweeps", f"grid expands to {grid_count} cases; consider max_cases", "campaign"))

    randomizations = campaign.get("randomizations") or []
    if sampling in {"random", "lhs"}:
        if count is None:
            issues.append(_issue("error", "$.campaign.count", "random/lhs sampling requires count", "campaign"))
        if not isinstance(randomizations, list) or not randomizations:
            issues.append(_issue("error", "$.campaign.randomizations", "random/lhs sampling requires randomizations", "campaign"))
    if randomizations and isinstance(randomizations, list):
        for i, item in enumerate(randomizations):
            path = f"$.campaign.randomizations[{i}]"
            if not isinstance(item, Mapping):
                issues.append(_issue("error", path, "must be an object", "type"))
                continue
            if not isinstance(item.get("path"), str) or not str(item.get("path", "")).strip():
                issues.append(_issue("error", f"{path}.path", "is required", "required"))
            distribution = item.get("distribution", "uniform")
            if distribution not in KNOWN_RANDOM_DISTRIBUTIONS:
                issues.append(_issue("error", f"{path}.distribution", f"unsupported distribution: {distribution!r}", "enum"))
            if distribution == "choice":
                choices = item.get("choices")
                if not isinstance(choices, list) or not choices:
                    issues.append(_issue("error", f"{path}.choices", "choice distribution requires non-empty choices", "required"))
            elif distribution in {"uniform", "randint"}:
                if "min" not in item or "max" not in item:
                    issues.append(_issue("error", path, f"{distribution} distribution requires min and max", "required"))
                elif item.get("min") > item.get("max"):
                    issues.append(_issue("error", path, "min must be <= max", "range"))
            elif distribution == "normal":
                if "mean" not in item or "std" not in item:
                    issues.append(_issue("error", path, "normal distribution requires mean and std", "required"))
                elif isinstance(item.get("std"), (int, float)) and item.get("std") < 0:
                    issues.append(_issue("error", f"{path}.std", "must be non-negative", "range"))

    return issues

def _semantic_issues(spec: Mapping[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if spec.get("schema_version") != TASK_SPEC_VERSION:
        issues.append(_issue("error", "$.schema_version", f"must be {TASK_SPEC_VERSION!r}", "version"))
    task_id = spec.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        issues.append(_issue("error", "$.task_id", "task_id is required", "required"))
    task_type = spec.get("task_type")
    if task_type not in KNOWN_TASK_TYPES:
        issues.append(_issue("error", "$.task_type", f"unsupported task_type: {task_type!r}", "enum"))

    target = _mapping(spec.get("target"))
    mode = target.get("mode")
    if mode is not None and mode not in KNOWN_TARGET_MODES:
        issues.append(_issue("error", "$.target.mode", f"unsupported mode: {mode!r}", "enum"))

    model = _mapping(spec.get("model"))
    capability_id = spec.get("capability_id") or model.get("capability_id")
    if task_type in {"component", "subsystem"}:
        if not target:
            issues.append(_issue("error", "$.target", f"{task_type} tasks require target.level/name/mode", "required"))
        level = target.get("level")
        name = target.get("name")
        if level is not None and level != task_type:
            issues.append(_issue("error", "$.target.level", f"must be {task_type!r} for task_type={task_type!r}", "target"))
        if not isinstance(name, str) or not name.strip():
            issues.append(_issue("error", "$.target.name", f"{task_type} tasks require target.name", "required"))
        elif not capability_id:
            # Generic runner tasks are restricted by the static component/subsystem
            # catalog. Capability tasks may define specialized target names such as
            # adcs_fsw or thermal_reduced_order; their adapter contract is authoritative.
            known = set(known_target_names(task_type))
            if known and name not in known:
                preview = ", ".join(sorted(known)[:16])
                suffix = "..." if len(known) > 16 else ""
                issues.append(_issue("error", "$.target.name", f"unknown {task_type} target {name!r}; known: {preview}{suffix}", "target"))

    sim = _mapping(spec.get("simulation"))
    if not sim:
        issues.append(_issue("error", "$.simulation", "simulation object is required", "required"))
    duration_s = _num({"simulation": sim}, "simulation.duration_s")
    sample_s = _num({"simulation": sim}, "simulation.sample_s")
    if duration_s is None or duration_s <= 0:
        issues.append(_issue("error", "$.simulation.duration_s", "must be positive", "range"))
    if sample_s is None or sample_s <= 0:
        issues.append(_issue("error", "$.simulation.sample_s", "must be positive", "range"))
    if duration_s and sample_s:
        if sample_s > duration_s:
            issues.append(_issue("error", "$.simulation.sample_s", "must not exceed duration_s", "time"))
        ratio = duration_s / sample_s
        if abs(round(ratio) - ratio) > 1e-9:
            issues.append(_issue("warning", "$.simulation", "duration_s is not an integer multiple of sample_s", "time_grid"))
    backend = sim.get("backend")
    if backend is not None and backend not in KNOWN_BACKENDS:
        issues.append(_issue("error", "$.simulation.backend", f"unsupported backend: {backend!r}", "enum"))

    sc = _mapping(spec.get("spacecraft"))
    if task_type == "whole_spacecraft" and not sc:
        issues.append(_issue("error", "$.spacecraft", "whole_spacecraft tasks require a spacecraft object", "required"))
    initial_soc = _num(spec, "spacecraft.eps.initial_soc", None)
    if initial_soc is not None and not 0.0 <= initial_soc <= 1.0:
        issues.append(_issue("error", "$.spacecraft.eps.initial_soc", "must be within [0, 1]", "range"))
    for path in POSITIVE_PATHS:
        value = _num(spec, path, None)
        if value is not None and value <= 0:
            issues.append(_issue("error", f"$.{path}", "must be positive", "range"))
    for path in NONNEGATIVE_PATHS:
        value = _num(spec, path, None)
        if value is not None and value < 0:
            issues.append(_issue("error", f"$.{path}", "must be non-negative", "range"))
    for path in PERCENT_PATHS:
        value = _num(spec, path, None)
        if value is not None and not 0.0 <= value <= 100.0:
            issues.append(_issue("error", f"$.{path}", "must be within [0, 100]", "range"))

    faults = spec.get("faults", [])
    if faults is None:
        faults = []
    if not isinstance(faults, list):
        issues.append(_issue("error", "$.faults", "must be a list", "type"))
        faults = []
    seen_fault_ids: set[str] = set()
    for i, fault in enumerate(faults):
        path = f"$.faults[{i}]"
        if not isinstance(fault, Mapping):
            issues.append(_issue("error", path, "must be an object", "type"))
            continue
        fid = fault.get("fault_id")
        if not isinstance(fid, str) or not fid.strip():
            issues.append(_issue("error", f"{path}.fault_id", "is required", "required"))
        elif fid in seen_fault_ids:
            issues.append(_issue("error", f"{path}.fault_id", "duplicate fault_id", "duplicate"))
        else:
            seen_fault_ids.add(fid)
        ftype = fault.get("fault_type")
        if ftype not in KNOWN_FAULT_TYPES:
            issues.append(_issue("error", f"{path}.fault_type", f"unknown fault_type: {ftype!r}", "enum"))
        onset = fault.get("onset_time_s")
        duration = fault.get("duration_s")
        magnitude = fault.get("magnitude")
        if isinstance(onset, bool) or not isinstance(onset, (int, float)) or onset < 0:
            issues.append(_issue("error", f"{path}.onset_time_s", "must be non-negative", "range"))
        elif duration_s is not None and onset > duration_s:
            issues.append(_issue("error", f"{path}.onset_time_s", "must be within simulation duration", "time"))
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or (duration <= 0 and duration != -1):
            issues.append(_issue("error", f"{path}.duration_s", "must be positive or -1 for persistent faults", "range"))
        elif isinstance(onset, (int, float)) and duration != -1 and duration_s is not None and onset + duration > duration_s + 1e-9:
            issues.append(_issue("error", path, "fault window exceeds simulation duration", "time"))
        if isinstance(magnitude, bool) or not isinstance(magnitude, (int, float)) or not 0 <= float(magnitude) <= 1:
            issues.append(_issue("error", f"{path}.magnitude", "must be within [0, 1]", "range"))

    modifier_faults = _mapping(spec.get("modifiers")).get("faults") if isinstance(spec.get("modifiers"), Mapping) else []
    modifier_degradations = _mapping(spec.get("modifiers")).get("degradations") if isinstance(spec.get("modifiers"), Mapping) else []
    modifier_constraints = _mapping(spec.get("modifiers")).get("constraints") if isinstance(spec.get("modifiers"), Mapping) else []
    if mode == "fault" and len(faults) == 0 and not modifier_faults:
        issues.append(_issue("error", "$.faults", "target.mode=fault requires at least one fault or modifiers.faults", "mode_contract"))
    if mode == "degradation" and not _has_non_empty_mapping(spec, "degradations") and not modifier_degradations:
        issues.append(_issue("error", "$.degradations", "target.mode=degradation requires degradations or modifiers.degradations", "mode_contract"))
    if mode == "constraint" and not spec.get("constraints") and not modifier_constraints:
        issues.append(_issue("error", "$.constraints", "target.mode=constraint requires constraints or modifiers.constraints", "mode_contract"))
    if mode == "nominal" and (faults or _has_non_empty_mapping(spec, "degradations") or modifier_faults or modifier_degradations):
        issues.append(_issue("warning", "$.target.mode", "nominal mode should not include fault or degradation modifiers; operational constraints may remain active", "mode_contract"))
    issues.extend(_modifier_issues(spec, duration_s))

    outputs = spec.get("outputs")
    if not isinstance(outputs, Mapping):
        issues.append(_issue("error", "$.outputs", "outputs object is required", "required"))
    else:
        if not outputs.get("output_root"):
            issues.append(_issue("error", "$.outputs.output_root", "is required", "required"))
        trace_format = outputs.get("trace_format")
        if trace_format not in KNOWN_TRACE_FORMATS:
            issues.append(_issue("error", "$.outputs.trace_format", "must be csv/parquet/jsonl", "enum"))
        elif trace_format in {"parquet", "jsonl"}:
            issues.append(_issue("warning", "$.outputs.trace_format", "writer is optimized for csv; parquet/jsonl may require optional code paths", "writer_support"))

    orbit = _mapping(spec.get("orbit_environment"))
    if task_type == "orbit_environment" and not orbit:
        issues.append(_issue("error", "$.orbit_environment", "orbit_environment tasks require an orbit_environment object", "required"))
    sun_vec = orbit.get("sun_vector_n")
    if sun_vec is not None:
        if not (isinstance(sun_vec, list) and len(sun_vec) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in sun_vec)):
            issues.append(_issue("error", "$.orbit_environment.sun_vector_n", "must be a 3-element numeric list", "type"))
        else:
            norm = math.sqrt(sum(float(x) ** 2 for x in sun_vec))
            if norm <= 0.0:
                issues.append(_issue("error", "$.orbit_environment.sun_vector_n", "must be non-zero", "range"))
            elif abs(norm - 1.0) > 1e-6:
                issues.append(_issue("warning", "$.orbit_environment.sun_vector_n", "will be normalized by the compiler/runner", "unit_vector"))

    issues.extend(_campaign_issues(spec))

    return issues


def _pydantic_issues(exc: Exception) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for item in errors():
            loc = item.get("loc", ())
            path = "$" + "".join(f".{part}" if isinstance(part, str) else f"[{part}]" for part in loc)
            issues.append(_issue("error", path, str(item.get("msg", "invalid value")), "TASKSPEC_MODEL_INVALID"))
    if not issues:
        issues.append(_issue("error", "$", str(exc), "TASKSPEC_MODEL_INVALID"))
    return issues


def validate_task_spec(spec: Mapping[str, Any], *, use_json_schema: bool = True) -> ValidationResult:
    """Validate legacy or canonical TaskSpec input through one canonical model.

    Legacy 0.1 documents are migrated in memory, validated as CanonicalTaskSpec
    1.0, and then converted to the current adapter runtime shape for established
    semantic and capability-specific checks.
    """

    issues: list[ValidationIssue] = []
    source_is_canonical = is_canonical_task_spec(spec)
    try:
        canonical = canonicalize_task_spec(spec)
    except Exception as exc:
        return ValidationResult(tuple(_pydantic_issues(exc)))

    if use_json_schema:
        issues.extend(_json_schema_issues(canonical))

    runtime_spec = to_runtime_task_spec(canonical)
    try:
        issues.extend(_semantic_issues(runtime_spec))
        if runtime_spec.get("task_type") != "campaign":
            from .coupling_requirements import supported_couplings_for_capability, validate_declared_required_couplings
            model = runtime_spec.get("model") if isinstance(runtime_spec.get("model"), Mapping) else {}
            capability_id = runtime_spec.get("capability_id") or model.get("capability_id")
            supported_couplings = supported_couplings_for_capability(str(capability_id) if capability_id else None)
            for row in validate_declared_required_couplings(dict(runtime_spec), supported_couplings=supported_couplings):
                issues.append(_issue("error", row["path"], row["message"], row["code"]))
            try:
                from .capability_registry import validate_spec_against_capability

                issues.extend(validate_spec_against_capability(runtime_spec))
                if capability_id:
                    from .capability_registry import get_capability
                    capability_contract = get_capability(str(capability_id))
                    implementation = capability_contract.data.get("implementation") if isinstance(capability_contract.data.get("implementation"), Mapping) else {}
                    native_high_rate = bool(implementation.get("native_multi_rate_telemetry", False))
                    simulation = canonical.get("simulation") if isinstance(canonical.get("simulation"), Mapping) else {}
                    outputs = canonical.get("outputs") if isinstance(canonical.get("outputs"), Mapping) else {}
                    base_sample = float(simulation.get("sample_s") or 0.0)
                    for index, stream in enumerate(outputs.get("telemetry_streams") or []):
                        if not isinstance(stream, Mapping):
                            continue
                        stream_sample = float(stream.get("sample_s") or 0.0)
                        if stream_sample < base_sample - 1.0e-12 and not native_high_rate:
                            issues.append(_issue(
                                "error", f"$.outputs.telemetry_streams[{index}].sample_s",
                                "sampling faster than simulation.sample_s requires a capability with native Basilisk recorder groups",
                                "NATIVE_HIGH_RATE_TELEMETRY_UNSUPPORTED",
                            ))
                from .parameter_consumption import audit_parameter_consumption

                parameter_audit = audit_parameter_consumption(canonical)
                for path in parameter_audit.unknown_paths:
                    issues.append(_issue(
                        "error", path,
                        "parameter path is not declared by the selected capability and would not be consumed",
                        "PARAMETER_PATH_UNCONSUMED",
                    ))
                for path in parameter_audit.conflict_paths:
                    issues.append(_issue(
                        "error", path,
                        "parameter is declared through conflicting canonical and compatibility aliases",
                        "PARAMETER_ALIAS_CONFLICT",
                    ))
                if parameter_audit.provenance_gaps:
                    issues.append(_issue(
                        "warning", "$.provenance.fields",
                        f"{len(parameter_audit.provenance_gaps)} consumed parameter paths lack explicit provenance",
                        "PARAMETER_PROVENANCE_INCOMPLETE",
                    ))
            except Exception as exc:
                issues.append(_issue("error", "$.model.capability_id" if source_is_canonical else "$.capability_id", str(exc), "CAPABILITY_VALIDATION_FAILED"))
    except TaskSpecError as exc:
        issues.append(_issue("error", "$", str(exc), "TASKSPEC_SEMANTIC_INVALID"))

    if not source_is_canonical:
        issues.append(_issue(
            "warning",
            "$.schema_version",
            f"Legacy TaskSpec {spec.get('schema_version', LEGACY_TASK_SPEC_VERSION)!r} accepted through deterministic migration to {CANONICAL_TASK_SPEC_VERSION}.",
            "TASKSPEC_LEGACY_MIGRATED",
        ))
    return ValidationResult(tuple(issues))

def validate_task_spec_file(path: str | Path, *, use_json_schema: bool = True) -> ValidationResult:
    """Load and validate a TaskSpec file."""

    doc = load_task_spec(path)
    return validate_task_spec(doc.data, use_json_schema=use_json_schema)


def format_issues(issues: Iterable[ValidationIssue]) -> str:
    """Human-readable validation issue list."""

    lines = []
    for issue in issues:
        lines.append(f"[{issue.severity}] {issue.path} {issue.code}: {issue.message}")
    return "\n".join(lines)


__all__ = [
    "KNOWN_TASK_TYPES",
    "KNOWN_TARGET_MODES",
    "KNOWN_TRACE_FORMATS",
    "KNOWN_CAMPAIGN_SAMPLING",
    "KNOWN_RANDOM_DISTRIBUTIONS",
    "KNOWN_FAULT_TYPES",
    "KNOWN_CONSTRAINT_TYPES",
    "ValidationIssue",
    "ValidationResult",
    "load_task_schema",
    "validate_task_spec",
    "validate_task_spec_file",
    "format_issues",
]
