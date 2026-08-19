"""Canonical TaskSpec v1 models and legacy migration utilities.

The simulation runtime historically consumed a flat ``TaskSpec 0.1`` mapping.
V21 introduces a single Pydantic-backed public contract without forcing every
existing Basilisk adapter to change at once.  Public tools operate on
``CanonicalTaskSpec``; the compiler converts it to a deterministic legacy-shaped
runtime mapping at the trusted boundary.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

CANONICAL_TASK_SPEC_VERSION = "1.0.0"
LEGACY_TASK_SPEC_VERSION = "0.1.0"
SUPPORTED_TASK_SPEC_VERSIONS = (CANONICAL_TASK_SPEC_VERSION, LEGACY_TASK_SPEC_VERSION)

TaskLevel = Literal["component", "subsystem", "orbit_environment", "whole_spacecraft", "campaign", "reference"]
ScenarioMode = Literal["nominal", "fault", "degradation", "mixed", "monte_carlo", "constraint"]
ParameterProfile = Literal["demo", "engineering_estimate", "ground_calibrated", "flight_correlated"]
ClaimLevel = Literal[
    "analysis_only",
    "engineering_estimate",
    "ground_calibrated",
    "flight_correlated",
]
FieldSource = Literal[
    "user_provided",
    "user_explicit",
    "form_default",
    "template_default",
    "agent_inferred",
    "resolver_derived",
    "legacy_migration",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)


class TaskIdentity(StrictModel):
    id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
    name: str | None = None
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_name(self) -> "TaskIdentity":
        if not self.name:
            object.__setattr__(self, "name", self.id)
        return self


class SolverSpec(FlexibleModel):
    method: Literal["euler", "rk4"] = "euler"
    step_s: float | None = Field(default=None, gt=0)
    rtol: float = Field(default=1e-9, ge=0)
    atol: float = Field(default=1e-12, ge=0)
    deterministic_seed: int | None = Field(default=None, ge=0)
    include_endpoint: bool = True


class SimulationSpec(StrictModel):
    level: TaskLevel
    subsystem: str | None = None
    duration_s: float = Field(gt=0)
    step_s: float | None = Field(default=None, gt=0)
    sample_s: float = Field(gt=0)
    random_seed: int | None = Field(default=None, ge=0)
    backend: Literal["python", "basilisk", "selective_unified_basilisk_assembly"] = "selective_unified_basilisk_assembly"
    time_base: Literal["simulation_seconds", "relative_seconds", "UTC", "JulianDate"] = "simulation_seconds"
    epoch_utc: str | None = None
    time_system: Literal["simulation_seconds", "relative_seconds", "UTC", "JulianDate"] = "UTC"
    solver: SolverSpec | None = None

    @model_validator(mode="after")
    def validate_grid(self) -> "SimulationSpec":
        if self.sample_s > self.duration_s:
            raise ValueError("sample_s must not exceed duration_s")
        if self.step_s is None:
            inferred = self.solver.step_s if self.solver and self.solver.step_s else self.sample_s
            object.__setattr__(self, "step_s", inferred)
        if self.step_s is not None and self.step_s > self.sample_s:
            raise ValueError("step_s must not exceed sample_s")
        if self.solver and self.solver.step_s is not None and self.solver.step_s > self.sample_s:
            raise ValueError("solver.step_s must not exceed sample_s")
        if self.level == "subsystem" and not self.subsystem:
            raise ValueError("subsystem is required when simulation.level='subsystem'")
        return self


class ParameterOverride(FlexibleModel):
    path: str = Field(min_length=1)
    value: Any
    unit: str | None = None
    source: FieldSource = "user_provided"


class ParameterSpec(StrictModel):
    profile: ParameterProfile = "demo"
    values: dict[str, Any] = Field(default_factory=dict)
    overrides: list[ParameterOverride] = Field(default_factory=list)
    registry_id: str | None = None
    registry_hash: str | None = None


class MissionSpec(FlexibleModel):
    """Mission intent retained across Agent, resolver, plan and Run Bundle.

    Extra mission fields remain allowed for backward compatibility, while
    ``required_couplings`` is a first-class validated causal contract.
    """

    template: str | None = None
    configuration_source: str | None = None
    required_couplings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_required_couplings(self) -> "MissionSpec":
        from .physical_couplings import KNOWN_PHYSICAL_COUPLING_IDS, normalize_coupling_ids
        normalized = list(normalize_coupling_ids([str(item) for item in self.required_couplings]))
        unknown = sorted(set(normalized) - KNOWN_PHYSICAL_COUPLING_IDS)
        if unknown:
            raise ValueError(f"unknown mission.required_couplings: {unknown}")
        object.__setattr__(self, "required_couplings", normalized)
        return self


class BaseEvent(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1)
    effect: str = Field(min_length=1)
    start_s: float = Field(default=0.0, ge=0)
    end_s: float | None = Field(default=None, gt=0)
    magnitude: float | None = Field(default=None, ge=0, le=1)
    scale: float | None = Field(default=None, ge=0)
    implementation: Literal["auto", "native", "proxy"] = "auto"
    delivery: Literal["modifier", "legacy_fault", "legacy_degradation", "runtime_parameter", "runtime_constraint"] = "modifier"
    target_type: str | None = None
    label: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "BaseEvent":
        if self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("event end_s must be greater than start_s")
        return self


class FaultEvent(BaseEvent):
    event_type: Literal["fault"] = "fault"


class DegradationEvent(BaseEvent):
    event_type: Literal["degradation"] = "degradation"


class ConstraintEvent(BaseEvent):
    """Operational state/constraint event, not a hardware fault.

    Examples include reaction-wheel speed saturation, actuator command
    saturation, eclipse entry and resource-limit activation.  These events may
    alter runtime limits or expose state transitions while leaving health-state
    classification unchanged.
    """

    event_type: Literal["constraint"] = "constraint"


_REACTION_WHEEL_SATURATION_EFFECTS = {
    "rw_speed_saturation",
    "adcs_rw_speed_saturation",
    "reaction_wheel_speed_limit",
    "adcs_reaction_wheel_speed_limit",
    "reaction_wheel_saturation",
}


class EventsSpec(StrictModel):
    faults: list[FaultEvent] = Field(default_factory=list)
    degradations: list[DegradationEvent] = Field(default_factory=list)
    constraints: list[ConstraintEvent] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reclassify_operational_constraints(cls, value: Any) -> Any:
        """Move legacy wheel-saturation entries out of the fault list.

        v0.5.3.5 exposed wheel speed saturation as a fault.  Preserve backward
        compatibility for saved TaskSpecs while normalizing the canonical
        representation to ``events.constraints``.
        """
        if not isinstance(value, Mapping):
            return value
        payload = copy.deepcopy(dict(value))
        faults = list(payload.get("faults") or [])
        constraints = list(payload.get("constraints") or [])
        kept: list[Any] = []
        for item in faults:
            effect = str(item.get("effect") or "") if isinstance(item, Mapping) else ""
            if effect in _REACTION_WHEEL_SATURATION_EFFECTS and isinstance(item, Mapping):
                converted = dict(item)
                converted["event_type"] = "constraint"
                converted["delivery"] = "runtime_constraint"
                converted["effect"] = (
                    "adcs_reaction_wheel_speed_limit"
                    if effect.startswith("adcs_")
                    else "reaction_wheel_speed_limit"
                )
                converted.setdefault("label", "反作用轮达到速度限制")
                constraints.append(converted)
            else:
                kept.append(item)
        payload["faults"] = kept
        payload["constraints"] = constraints
        return payload


class TelemetryStreamSpec(StrictModel):
    stream_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    sample_s: float = Field(gt=0)
    fields: list[str] = Field(min_length=1)
    format: Literal["csv", "jsonl"] = "csv"

    @model_validator(mode="after")
    def unique_fields(self) -> "TelemetryStreamSpec":
        object.__setattr__(self, "fields", list(dict.fromkeys(self.fields)))
        return self


class FmeaOutputSpec(StrictModel):
    enabled: bool = False
    formats: list[Literal["csv", "json"]] = Field(default_factory=lambda: ["csv", "json"])

    @model_validator(mode="after")
    def unique_formats(self) -> "FmeaOutputSpec":
        object.__setattr__(self, "formats", list(dict.fromkeys(self.formats)))
        return self


class OutputSpec(StrictModel):
    output_root: str = Field(min_length=1)
    trace_format: Literal["csv", "parquet", "jsonl"] = "csv"
    qoi: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    plots: list[str] = Field(default_factory=list)
    telemetry_streams: list[TelemetryStreamSpec] = Field(default_factory=list)
    fmea: FmeaOutputSpec = Field(default_factory=FmeaOutputSpec)
    include_summary: bool = True
    include_trace: bool = True
    include_labels: bool = True
    include_manifest: bool = True

    @model_validator(mode="after")
    def unique_lists(self) -> "OutputSpec":
        for field_name in ("qoi", "files", "plots"):
            values = list(dict.fromkeys(getattr(self, field_name)))
            object.__setattr__(self, field_name, values)
        stream_ids = [stream.stream_id for stream in self.telemetry_streams]
        if len(stream_ids) != len(set(stream_ids)):
            raise ValueError("telemetry stream_id values must be unique")
        return self


class AssuranceSpec(StrictModel):
    fidelity_level: str = "declared_by_capability"
    claim_level: ClaimLevel = "analysis_only"
    validation_profile: str = "default"
    parameter_profile: ParameterProfile = "demo"
    allow_proxy: bool = False


class TargetBinding(FlexibleModel):
    level: str | None = None
    name: str | None = None
    mode: ScenarioMode = "nominal"


class ModelBinding(StrictModel):
    capability_id: str | None = None
    target: TargetBinding | None = None
    spacecraft: dict[str, Any] = Field(default_factory=dict)
    orbit_environment: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    legacy_degradations: dict[str, Any] = Field(default_factory=dict)
    campaign: dict[str, Any] = Field(default_factory=dict)
    simulation_extra: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    template: str | None = None


class FieldProvenance(StrictModel):
    source: FieldSource
    evidence: str | None = None
    note: str | None = None


class Assumption(StrictModel):
    path: str
    value: Any = None
    reason: str
    status: Literal["accepted", "pending_confirmation", "rejected"] = "accepted"


class ProvenanceSpec(StrictModel):
    fields: dict[str, FieldProvenance] = Field(default_factory=dict)
    assumptions: list[Assumption] = Field(default_factory=list)
    pending_confirmations: list[str] = Field(default_factory=list)
    migrated_from_version: str | None = None


class CanonicalTaskSpec(StrictModel):
    schema_version: Literal[CANONICAL_TASK_SPEC_VERSION] = CANONICAL_TASK_SPEC_VERSION
    task: TaskIdentity
    simulation: SimulationSpec
    mission: MissionSpec = Field(default_factory=MissionSpec)
    parameters: ParameterSpec = Field(default_factory=ParameterSpec)
    events: EventsSpec = Field(default_factory=EventsSpec)
    outputs: OutputSpec
    assurance: AssuranceSpec = Field(default_factory=AssuranceSpec)
    model: ModelBinding = Field(default_factory=ModelBinding)
    provenance: ProvenanceSpec = Field(default_factory=ProvenanceSpec)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_events_with_duration(self) -> "CanonicalTaskSpec":
        if self.parameters.profile != self.assurance.parameter_profile:
            raise ValueError(
                "parameters.profile and assurance.parameter_profile must match"
            )
        duration = self.simulation.duration_s
        ids: set[str] = set()
        for event in [*self.events.faults, *self.events.degradations, *self.events.constraints]:
            if event.id in ids:
                raise ValueError(f"duplicate event id: {event.id}")
            ids.add(event.id)
            if event.start_s >= duration:
                raise ValueError(f"event {event.id} must start before simulation duration")
            if event.end_s is not None and event.end_s > duration:
                raise ValueError(f"event {event.id} ends after simulation duration")
        mode = self.model.target.mode if self.model.target else "nominal"
        if mode == "fault" and not self.events.faults:
            raise ValueError("target mode 'fault' requires at least one fault event")
        if mode == "degradation" and not (self.events.degradations or self.model.legacy_degradations):
            raise ValueError("target mode 'degradation' requires at least one degradation event")
        if mode == "constraint" and not self.events.constraints:
            raise ValueError("target mode 'constraint' requires at least one constraint event")
        integration_step = float(self.simulation.step_s or self.simulation.sample_s)
        for stream in self.outputs.telemetry_streams:
            if stream.sample_s < integration_step - 1e-12:
                raise ValueError(
                    f"telemetry stream {stream.stream_id} sample_s must be >= simulation.step_s"
                )
            ratio = stream.sample_s / integration_step
            if abs(ratio - round(ratio)) > 1e-9:
                raise ValueError(
                    f"telemetry stream {stream.stream_id} sample_s must be an integer multiple of simulation.step_s"
                )
        return self


class DraftTaskSpec(FlexibleModel):
    """Permissive Agent/form candidate before deterministic canonicalization."""

    schema_version: str | None = None
    task: dict[str, Any] | None = None
    simulation: dict[str, Any] | None = None
    mission: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] | None = None
    assurance: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class MigrationNotice:
    code: str
    path: str
    message: str
    severity: str = "info"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TaskSpecMigrationResult:
    source_version: str
    target_version: str
    canonical: dict[str, Any]
    notices: tuple[MigrationNotice, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_version": self.source_version,
            "target_version": self.target_version,
            "canonical": copy.deepcopy(self.canonical),
            "notices": [item.to_dict() for item in self.notices],
        }


def is_canonical_task_spec(spec: Mapping[str, Any]) -> bool:
    return spec.get("schema_version") == CANONICAL_TASK_SPEC_VERSION and isinstance(spec.get("task"), Mapping)


def _event_end(start_s: float, duration_s: Any) -> float | None:
    try:
        duration = float(duration_s)
    except Exception:
        return None
    if duration == -1:
        return None
    return start_s + max(0.0, duration)


def _fault_from_legacy(item: Mapping[str, Any], index: int, *, delivery: str) -> dict[str, Any]:
    start = float(item.get("onset_time_s", item.get("start_time_s", 0.0)) or 0.0)
    return {
        "id": str(item.get("fault_id") or item.get("modifier_id") or f"fault_{index:03d}"),
        "target": str(item.get("target") or item.get("target_path") or "unknown"),
        "effect": str((item.get("parameters") or {}).get("scenario") or item.get("fault_type") or item.get("modifier_type") or item.get("type") or "unknown"),
        "start_s": max(0.0, start),
        "end_s": _event_end(start, item.get("duration_s", -1.0)),
        "magnitude": item.get("magnitude", item.get("severity")),
        "scale": item.get("scale"),
        "implementation": str(item.get("implementation") or "auto"),
        "delivery": delivery,
        "target_type": item.get("target_type"),
        "label": item.get("label"),
        "parameters": dict(item.get("parameters") or {}),
    }


def _degradation_from_legacy(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    start = float(item.get("onset_time_s", item.get("start_time_s", 0.0)) or 0.0)
    return {
        "id": str(item.get("degradation_id") or item.get("modifier_id") or f"degradation_{index:03d}"),
        "target": str(item.get("target") or item.get("target_path") or "unknown"),
        "effect": str((item.get("parameters") or {}).get("scenario") or item.get("degradation_type") or item.get("modifier_type") or item.get("type") or "unknown"),
        "start_s": max(0.0, start),
        "end_s": _event_end(start, item.get("duration_s", -1.0)),
        "magnitude": item.get("severity"),
        "scale": item.get("scale"),
        "implementation": str(item.get("implementation") or "auto"),
        "delivery": "modifier",
        "target_type": item.get("target_type"),
        "label": item.get("label"),
        "parameters": dict(item.get("parameters") or {}),
    }


def _constraint_from_legacy(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    start = float(item.get("onset_time_s", item.get("start_time_s", 0.0)) or 0.0)
    return {
        "id": str(item.get("constraint_id") or item.get("modifier_id") or f"constraint_{index:03d}"),
        "target": str(item.get("target") or item.get("target_path") or "unknown"),
        "effect": str((item.get("parameters") or {}).get("scenario") or item.get("constraint_type") or item.get("modifier_type") or item.get("type") or "unknown"),
        "start_s": max(0.0, start),
        "end_s": _event_end(start, item.get("duration_s", -1.0)),
        "magnitude": item.get("severity", item.get("magnitude")),
        "scale": item.get("scale"),
        "implementation": str(item.get("implementation") or "auto"),
        "delivery": "runtime_constraint",
        "target_type": item.get("target_type"),
        "label": item.get("label"),
        "parameters": dict(item.get("parameters") or {}),
    }


def migrate_legacy_task_spec(spec: Mapping[str, Any]) -> TaskSpecMigrationResult:
    """Migrate a flat TaskSpec 0.1 mapping to CanonicalTaskSpec 1.0."""

    src = copy.deepcopy(dict(spec))
    source_version = str(src.get("schema_version") or LEGACY_TASK_SPEC_VERSION)
    task_type = str(src.get("task_type") or "component")
    target = dict(src.get("target") or {})
    level = str(target.get("level") or task_type)
    if level not in {"component", "subsystem", "orbit_environment", "whole_spacecraft", "campaign", "reference"}:
        level = task_type if task_type in {"component", "subsystem", "orbit_environment", "whole_spacecraft", "campaign", "reference"} else "component"
    sim = dict(src.get("simulation") or {})
    outputs = dict(src.get("outputs") or {})
    metadata = dict(src.get("metadata") or {})
    modifiers = dict(src.get("modifiers") or {})

    faults: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    for idx, item in enumerate(src.get("faults") or []):
        if not isinstance(item, Mapping):
            continue
        converted = _fault_from_legacy(item, idx, delivery="legacy_fault")
        if converted["effect"] in _REACTION_WHEEL_SATURATION_EFFECTS:
            canonical_effect = "adcs_reaction_wheel_speed_limit" if str(converted["effect"]).startswith("adcs_") else "reaction_wheel_speed_limit"
            constraints.append({**converted, "effect": canonical_effect, "event_type": "constraint", "delivery": "runtime_constraint"})
        else:
            faults.append(converted)
    offset = len(faults)
    for idx, item in enumerate(modifiers.get("faults") or []):
        if not isinstance(item, Mapping):
            continue
        converted = _fault_from_legacy(item, offset + idx, delivery="modifier")
        if converted["effect"] in _REACTION_WHEEL_SATURATION_EFFECTS:
            canonical_effect = "adcs_reaction_wheel_speed_limit" if str(converted["effect"]).startswith("adcs_") else "reaction_wheel_speed_limit"
            constraints.append({**converted, "effect": canonical_effect, "event_type": "constraint", "delivery": "runtime_constraint"})
        else:
            faults.append(converted)
    degradations = [
        _degradation_from_legacy(item, idx)
        for idx, item in enumerate(modifiers.get("degradations") or [])
        if isinstance(item, Mapping)
    ]
    constraints.extend(
        _constraint_from_legacy(item, len(constraints) + idx)
        for idx, item in enumerate(modifiers.get("constraints") or [])
        if isinstance(item, Mapping)
    )

    assurance_src = src.get("assurance") if isinstance(src.get("assurance"), Mapping) else {}
    parameters_src = src.get("parameters") if isinstance(src.get("parameters"), Mapping) else {}
    profile = str(
        assurance_src.get("parameter_profile")
        or metadata.get("parameter_profile")
        or parameters_src.get("profile")
        or "demo"
    )
    if profile not in {"demo", "engineering_estimate", "ground_calibrated", "flight_correlated"}:
        profile = "demo"
    claim_level = str(
        ((src.get("assurance") or {}).get("claim_level") if isinstance(src.get("assurance"), Mapping) else None)
        or metadata.get("claim_level")
        or "analysis_only"
    )
    if claim_level not in {"analysis_only", "engineering_estimate", "ground_calibrated", "flight_correlated"}:
        claim_level = "analysis_only"

    # Legacy inputs exist in two shapes:
    #   parameters: {key: value}
    #   parameters: {values: {key: value}, profile: ...}
    # Normalize both to the canonical flat values mapping.  Treating the
    # second form as an ordinary dictionary would create values.values.* and
    # silently prevent adapters from consuming user parameters.
    nested_values = parameters_src.get("values")
    if isinstance(nested_values, Mapping):
        legacy_parameter_values = dict(nested_values)
    else:
        legacy_parameter_values = dict(parameters_src)
        legacy_parameter_values.pop("profile", None)
        legacy_parameter_values.pop("overrides", None)
        legacy_parameter_values.pop("registry_id", None)
        legacy_parameter_values.pop("registry_hash", None)

    task_id = str(src.get("task_id") or "migrated_task")
    sample_s = float(sim.get("sample_s", 10.0) or 10.0)
    solver = dict(sim.get("solver") or {})
    step_s = solver.get("step_s", sample_s)
    mission = dict(src.get("mission") or {})
    spacecraft = dict(src.get("spacecraft") or {})
    if not mission and isinstance(spacecraft.get("mission"), Mapping):
        mission = dict(spacecraft.get("mission") or {})

    canonical_payload = {
        "schema_version": CANONICAL_TASK_SPEC_VERSION,
        "task": {
            "id": task_id,
            "name": str(metadata.get("task_name") or task_id),
            "description": str(src.get("description") or ""),
            "tags": list(src.get("tags") or []),
        },
        "simulation": {
            "level": level,
            "subsystem": str(target.get("name")) if level == "subsystem" and target.get("name") else None,
            "duration_s": float(sim.get("duration_s", 300.0) or 300.0),
            "step_s": float(step_s) if step_s is not None else sample_s,
            "sample_s": sample_s,
            "random_seed": sim.get("seed", sim.get("random_seed")),
            "backend": str(sim.get("backend") or "selective_unified_basilisk_assembly"),
            "time_base": str(sim.get("time_base") or "simulation_seconds"),
            "epoch_utc": sim.get("epoch_utc"),
            "time_system": str(sim.get("time_system") or "UTC"),
            "solver": solver or None,
        },
        "mission": mission,
        "parameters": {
            "profile": profile,
            "values": legacy_parameter_values,
            "overrides": [],
            "registry_id": metadata.get("parameter_registry_id"),
            "registry_hash": metadata.get("parameter_registry_hash"),
        },
        "events": {"faults": faults, "degradations": degradations, "constraints": constraints},
        "outputs": {
            "output_root": str(outputs.get("output_root") or "runs"),
            "trace_format": str(outputs.get("trace_format") or "csv"),
            "qoi": list(outputs.get("record_fields") or outputs.get("qoi") or []),
            "files": list(outputs.get("files") or []),
            "plots": list(outputs.get("plots") or []),
            "telemetry_streams": list(outputs.get("telemetry_streams") or []),
            "fmea": dict(outputs.get("fmea") or {}),
            "include_summary": bool(outputs.get("include_summary", True)),
            "include_trace": bool(outputs.get("include_trace", True)),
            "include_labels": bool(outputs.get("include_labels", True)),
            "include_manifest": bool(outputs.get("include_manifest", True)),
        },
        "assurance": {
            "fidelity_level": str(((src.get("assurance") or {}).get("fidelity_level") if isinstance(src.get("assurance"), Mapping) else None) or metadata.get("fidelity_level") or "declared_by_capability"),
            "claim_level": claim_level,
            "validation_profile": str(((src.get("assurance") or {}).get("validation_profile") if isinstance(src.get("assurance"), Mapping) else None) or metadata.get("validation_profile") or "default"),
            "parameter_profile": profile,
            "allow_proxy": bool(((src.get("assurance") or {}).get("allow_proxy") if isinstance(src.get("assurance"), Mapping) else False) or False),
        },
        "model": {
            "capability_id": src.get("capability_id"),
            "target": target or {"level": level, "name": None, "mode": "nominal"},
            "spacecraft": spacecraft,
            "orbit_environment": dict(src.get("orbit_environment") or {}),
            "config": legacy_parameter_values,
            "legacy_degradations": dict(src.get("degradations") or {}) if isinstance(src.get("degradations"), Mapping) else {},
            "campaign": dict(src.get("campaign") or {}),
            "simulation_extra": {k: v for k, v in sim.items() if k not in {"duration_s", "sample_s", "seed", "random_seed", "backend", "time_base", "epoch_utc", "time_system", "solver"}},
            "validation": dict(src.get("validation") or {}),
            "template": src.get("template"),
        },
        "provenance": {
            "fields": {
                "task.id": {"source": "legacy_migration", "evidence": "$.task_id"},
                "simulation.duration_s": {"source": "legacy_migration", "evidence": "$.simulation.duration_s"},
                "simulation.sample_s": {"source": "legacy_migration", "evidence": "$.simulation.sample_s"},
                "model.capability_id": {"source": "legacy_migration", "evidence": "$.capability_id"},
            },
            "assumptions": [],
            "pending_confirmations": [],
            "migrated_from_version": source_version,
        },
        "metadata": metadata,
    }
    canonical = CanonicalTaskSpec.model_validate(canonical_payload).model_dump(mode="json", exclude_none=True)
    notices = (
        MigrationNotice(
            code="TASKSPEC_LEGACY_MIGRATED",
            path="$",
            message=f"TaskSpec {source_version} was deterministically migrated to {CANONICAL_TASK_SPEC_VERSION}.",
        ),
    )
    return TaskSpecMigrationResult(source_version, CANONICAL_TASK_SPEC_VERSION, canonical, notices)


def canonicalize_task_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return a validated, deterministically serialized CanonicalTaskSpec mapping."""

    if is_canonical_task_spec(spec):
        return CanonicalTaskSpec.model_validate(dict(spec)).model_dump(mode="json", exclude_none=True)
    return migrate_legacy_task_spec(spec).canonical


def _runtime_event(event: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    start = float(event.get("start_s", 0.0) or 0.0)
    end = event.get("end_s")
    duration = -1.0 if end is None else max(0.0, float(end) - start)
    if kind == "fault":
        return {
            "fault_id": str(event.get("id")),
            "target": str(event.get("target")),
            "target_type": str(event.get("target_type") or "unknown"),
            "fault_type": str(event.get("effect")),
            "onset_time_s": start,
            "duration_s": duration,
            "magnitude": float(event.get("magnitude") if event.get("magnitude") is not None else 1.0),
            "parameters": dict(event.get("parameters") or {}),
            **({"label": event.get("label")} if event.get("label") else {}),
        }
    if kind == "constraint":
        return {
            "constraint_id": str(event.get("id")),
            "target": str(event.get("target")),
            "constraint_type": str(event.get("effect")),
            "onset_time_s": start,
            "duration_s": duration,
            **({"severity": event.get("magnitude")} if event.get("magnitude") is not None else {}),
            **({"scale": event.get("scale")} if event.get("scale") is not None else {}),
            "parameters": dict(event.get("parameters") or {}),
            **({"label": event.get("label")} if event.get("label") else {}),
        }
    return {
        "degradation_id": str(event.get("id")),
        "target": str(event.get("target")),
        "degradation_type": str(event.get("effect")),
        "onset_time_s": start,
        "duration_s": duration,
        **({"severity": event.get("magnitude")} if event.get("magnitude") is not None else {}),
        **({"scale": event.get("scale")} if event.get("scale") is not None else {}),
        "parameters": dict(event.get("parameters") or {}),
    }


def to_runtime_task_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Convert CanonicalTaskSpec to the flat mapping consumed by current adapters."""

    if not is_canonical_task_spec(spec):
        return copy.deepcopy(dict(spec))
    canonical = CanonicalTaskSpec.model_validate(dict(spec)).model_dump(mode="python", exclude_none=True)
    task = canonical["task"]
    simulation = canonical["simulation"]
    model = canonical.get("model", {})
    target = dict(model.get("target") or {})
    events = canonical.get("events", {})
    outputs = canonical["outputs"]
    parameters = canonical.get("parameters", {})
    assurance = canonical.get("assurance", {})

    runtime_sim = dict(model.get("simulation_extra") or {})
    runtime_sim.update({
        "duration_s": simulation["duration_s"],
        "sample_s": simulation["sample_s"],
        "backend": simulation.get("backend", "selective_unified_basilisk_assembly"),
        "time_base": simulation.get("time_base", "simulation_seconds"),
        "time_system": simulation.get("time_system", "UTC"),
    })
    if simulation.get("random_seed") is not None:
        runtime_sim["seed"] = simulation["random_seed"]
    if simulation.get("epoch_utc") is not None:
        runtime_sim["epoch_utc"] = simulation["epoch_utc"]
    solver = dict(simulation.get("solver") or {})
    solver.setdefault("step_s", simulation.get("step_s"))
    runtime_sim["solver"] = solver

    mode = target.get("mode") or (
        "mixed" if events.get("faults") and events.get("degradations") else
        "fault" if events.get("faults") else
        "degradation" if events.get("degradations") or model.get("legacy_degradations") else
        "nominal"
    )
    target.setdefault("level", simulation["level"])
    if simulation["level"] == "subsystem":
        target.setdefault("name", simulation.get("subsystem"))
    elif simulation["level"] == "orbit_environment":
        # Legacy orbit adapters use the historical ownership level "integrated".
        # Keep that mapping internal while the canonical/UI level remains explicit.
        target["level"] = "integrated"
        target.setdefault("name", "orbit_environment")
    target["mode"] = mode

    top_faults: list[dict[str, Any]] = []
    modifier_faults: list[dict[str, Any]] = []
    for event in events.get("faults", []):
        payload = _runtime_event(event, kind="fault")
        if event.get("delivery") == "legacy_fault":
            top_faults.append(payload)
        else:
            modifier_faults.append({
                "modifier_id": payload["fault_id"],
                "target": payload["target"],
                "fault_type": payload["fault_type"],
                "onset_time_s": payload["onset_time_s"],
                "duration_s": payload["duration_s"],
                "severity": payload["magnitude"],
                "parameters": payload["parameters"],
            })
    modifier_degradations = [_runtime_event(event, kind="degradation") for event in events.get("degradations", [])]
    modifier_constraints = [_runtime_event(event, kind="constraint") for event in events.get("constraints", [])]

    runtime_outputs = {
        "output_root": outputs["output_root"],
        "trace_format": outputs.get("trace_format", "csv"),
        "include_summary": outputs.get("include_summary", True),
        "include_trace": outputs.get("include_trace", True),
        "include_labels": outputs.get("include_labels", True),
        "include_manifest": outputs.get("include_manifest", True),
        "record_fields": list(outputs.get("qoi") or []),
        "telemetry_streams": [dict(item) for item in (outputs.get("telemetry_streams") or [])],
        "fmea": dict(outputs.get("fmea") or {}),
    }
    metadata = dict(canonical.get("metadata") or {})
    metadata.update({
        "canonical_task_spec_version": CANONICAL_TASK_SPEC_VERSION,
        "canonical_assurance": assurance,
        "canonical_provenance": canonical.get("provenance", {}),
        "task_name": task.get("name"),
        "parameter_profile": parameters.get("profile", "demo"),
        "claim_level": assurance.get("claim_level", "analysis_only"),
        "validation_profile": assurance.get("validation_profile", "default"),
    })

    runtime = {
        "schema_version": LEGACY_TASK_SPEC_VERSION,
        "task_id": task["id"],
        "task_type": simulation["level"],
        "description": task.get("description", ""),
        "tags": list(task.get("tags") or []),
        "mission": copy.deepcopy(canonical.get("mission") or {}),
        "simulation": runtime_sim,
        "target": target,
        "spacecraft": dict(model.get("spacecraft") or {}),
        "orbit_environment": dict(model.get("orbit_environment") or {}),
        "parameters": dict(parameters.get("values") or model.get("config") or {}),
        "degradations": dict(model.get("legacy_degradations") or {}),
        "faults": top_faults,
        "modifiers": {"faults": modifier_faults, "degradations": modifier_degradations, "constraints": modifier_constraints},
        "outputs": runtime_outputs,
        "assurance": copy.deepcopy(assurance),
        "validation": dict(model.get("validation") or {}),
        "metadata": metadata,
    }
    if model.get("capability_id"):
        runtime["capability_id"] = model["capability_id"]
    if model.get("campaign"):
        runtime["campaign"] = copy.deepcopy(model["campaign"])
    if model.get("template"):
        runtime["template"] = model["template"]
    return runtime


def canonical_task_spec_schema() -> dict[str, Any]:
    """Return the JSON Schema generated from the Pydantic source of truth."""

    schema = CanonicalTaskSpec.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/task-spec-v1.schema.json"
    schema["title"] = "Satellite Simulation Canonical TaskSpec v1.0"
    return schema


__all__ = [
    "CANONICAL_TASK_SPEC_VERSION",
    "LEGACY_TASK_SPEC_VERSION",
    "SUPPORTED_TASK_SPEC_VERSIONS",
    "DraftTaskSpec",
    "CanonicalTaskSpec",
    "MissionSpec",
    "ConstraintEvent",
    "TaskSpecMigrationResult",
    "MigrationNotice",
    "is_canonical_task_spec",
    "migrate_legacy_task_spec",
    "canonicalize_task_spec",
    "to_runtime_task_spec",
    "canonical_task_spec_schema",
]
