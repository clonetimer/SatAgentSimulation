"""TaskSpec compiler.

The compiler turns a user/Agent-facing TaskSpec into a deterministic, runner-
agnostic execution plan.  The default compiler output is a plain dataclass made
of JSON-serializable dictionaries, so it can be inspected without importing
Basilisk or the whole-spacecraft runner.

Concrete runner dataclasses are created lazily by :mod:`sat_sim.task_runner`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass, replace
from typing import Any, Mapping

from .task_spec import TaskSpecError, spec_sha256
from .task_models import CANONICAL_TASK_SPEC_VERSION, is_canonical_task_spec, to_runtime_task_spec
from .task_validator import validate_task_spec
from .catalog import runner_module_for


@dataclass(frozen=True)
class CompiledTask:
    """Deterministic execution plan derived from a TaskSpec."""

    schema_version: str
    task_id: str
    task_type: str
    mode: str
    backend: str
    runner: str
    spec_hash: str
    simulation: dict[str, Any]
    outputs: dict[str, Any]
    run_config: dict[str, Any] = field(default_factory=dict)
    structure_config: dict[str, Any] = field(default_factory=dict)
    orbit_environment_config: dict[str, Any] = field(default_factory=dict)
    faults: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    degradations: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _get(mapping: Mapping[str, Any], key: str, default: Any) -> Any:
    value = mapping.get(key, default)
    return default if value is None else value


def _target_mode(spec: Mapping[str, Any]) -> str:
    target = _mapping(spec.get("target"))
    if target.get("mode"):
        return str(target["mode"])
    modifiers = _mapping(spec.get("modifiers"))
    has_faults = bool(spec.get("faults") or modifiers.get("faults"))
    has_degradations = bool(spec.get("degradations") or modifiers.get("degradations"))
    if has_faults and has_degradations:
        return "mixed"
    if has_faults:
        return "fault"
    if has_degradations:
        return "degradation"
    return "nominal"




def _capability_id(spec: Mapping[str, Any]) -> str | None:
    value = spec.get("capability_id")
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _compile_capability_task(spec: Mapping[str, Any], *, task_type: str) -> CompiledTask:
    """Compile a capability-centric TaskSpec into an explicit adapter plan."""

    from .capability_registry import get_capability
    from .capability_composition import composition_metadata, contract_interface_summary
    from .source_native import source_binding_payload

    cid = _capability_id(spec)
    if not cid:
        raise TaskSpecError("capability_id is required for capability compilation")
    contract = get_capability(cid)
    target = _mapping(spec.get("target"))
    base = _base_compiled(spec, runner=contract.adapter_class_path)
    sim = _mapping(spec.get("simulation"))
    interface = contract_interface_summary(contract.data)
    source_binding = source_binding_payload(contract.data)
    from .modifiers import modifiers_task_spec_payload
    modifier_payload = modifiers_task_spec_payload(spec)
    metadata = {
        **base.pop("metadata", {}),
        "capability_id": cid,
        "capability_trust_level": contract.trust_level,
        "adapter": contract.adapter_class_path,
        "target": {
            "level": str(target.get("level") or contract.target_level or task_type),
            "name": str(target.get("name") or contract.target_name),
            "mode": _target_mode(spec),
        },
        "parameters": _copy_mapping(spec.get("parameters")),
        "capability_contract": {
            "capability_id": cid,
            "level": contract.target_level,
            "target": contract.target_name,
            "trust_level": contract.trust_level,
            "dependencies": interface["dependencies"],
            "consumes": interface["consumes"],
            "produces": interface["produces"],
            "time_grid": interface["time_grid"],
            "source_binding": source_binding,
        },
        "source_binding": source_binding,
        "model_asset_execution": dict(contract.data.get("execution") or {}) if isinstance(contract.data.get("execution"), Mapping) else {},
        "model_projection": dict(contract.data.get("model_projection") or {}) if isinstance(contract.data.get("model_projection"), Mapping) else {},
        "composition": composition_metadata(cid, contract.data, task_spec=spec),
        "modifiers": modifier_payload,
        "applied_modifiers": modifier_payload,
    }


    if cid == "orbit_environment.orbit_fidelity.v1":
        try:
            from .orbit import build_orb1_orbit_fidelity_payload, build_orb1_force_model_payload

            metadata["orb1_orbit_fidelity"] = build_orb1_orbit_fidelity_payload(spec)
            metadata["orb1_force_models"] = build_orb1_force_model_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["orb1_orbit_fidelity"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "orbit_environment.medium_fidelity.v1":
        try:
            from .orbit import build_hf3_orbit_environment_payload

            metadata["hf3_orbit_environment"] = build_hf3_orbit_environment_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["hf3_orbit_environment"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "subsystem.adcs_closed_loop.basic.v1":
        try:
            from .adcs import build_hf4_adcs_closed_loop_payload

            metadata["hf4_adcs_closed_loop"] = build_hf4_adcs_closed_loop_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["hf4_adcs_closed_loop"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "subsystem.adcs_fidelity.v1":
        try:
            from .adcs import build_adcs1_fidelity_payload

            metadata["adcs1_fidelity"] = build_adcs1_fidelity_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["adcs1_fidelity"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "whole_spacecraft.power_thermal_orbit_coupled.v1":
        try:
            from .coupled import build_hf5_power_thermal_orbit_payload

            metadata["hf5_power_thermal_orbit"] = build_hf5_power_thermal_orbit_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["hf5_power_thermal_orbit"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "whole_spacecraft.comm_payload_mission_coupled.v1":
        try:
            from .coupled import build_hf6_comm_payload_mission_payload

            metadata["hf6_comm_payload_mission"] = build_hf6_comm_payload_mission_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["hf6_comm_payload_mission"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "whole_spacecraft.maneuver_orbit_attitude.v1":
        try:
            from .coupled import build_hf7_propulsion_orbit_attitude_payload

            metadata["hf7_propulsion_orbit_attitude"] = build_hf7_propulsion_orbit_attitude_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["hf7_propulsion_orbit_attitude"] = {"error": str(exc), "status": "metadata_build_failed"}

    if cid == "whole_spacecraft.orbit_adcs_fidelity.v1":
        try:
            from .coupled import build_int1_orbit_adcs_payload

            metadata["int1_orbit_adcs_integration"] = build_int1_orbit_adcs_payload(spec)
        except Exception as exc:  # pragma: no cover - defensive metadata path
            metadata["int1_orbit_adcs_integration"] = {"error": str(exc), "status": "metadata_build_failed"}

    try:
        from .validation import build_hf8_physical_validation_payload

        metadata.setdefault("hf8_physical_validation", build_hf8_physical_validation_payload(spec))
    except Exception as exc:  # pragma: no cover - defensive metadata path
        metadata.setdefault("hf8_physical_validation", {"error": str(exc), "status": "metadata_build_failed"})

    modern_modifiers = _mapping(spec.get("modifiers"))
    faults = tuple(dict(item) for item in (modern_modifiers.get("faults") or spec.get("faults", []) or []) if isinstance(item, Mapping))
    labels = {
        "mode": base["mode"],
        "target_level": metadata["target"]["level"],
        "target_name": metadata["target"]["name"],
        "capability_id": cid,
        "fault_labels": [f.get("label") or f.get("fault_id") for f in faults],
        "degradation_present": bool(spec.get("degradations") or _mapping(spec.get("modifiers")).get("degradations")),
        "constraint_present": bool(spec.get("constraints") or _mapping(spec.get("modifiers")).get("constraints")),
        "modifier_present": bool(modifier_payload.get("count")),
    }
    degradations_payload = spec.get("degradations")
    if not isinstance(degradations_payload, Mapping):
        degradations_payload = {}
    return CompiledTask(
        **base,
        run_config={
            "duration_s": float(_get(sim, "duration_s", 300.0)),
            "sample_s": float(_get(sim, "sample_s", 10.0)),
        },
        faults=faults,
        degradations=_copy_mapping(degradations_payload),
        labels=labels,
        metadata=metadata,
    )

def _base_compiled(spec: Mapping[str, Any], runner: str) -> dict[str, Any]:
    sim = _copy_mapping(spec.get("simulation"))
    outputs = _copy_mapping(spec.get("outputs"))
    metadata = _copy_mapping(spec.get("metadata"))
    # HF-1/HF-2 foundation metadata is recorded for every compiled task, but it
    # must never turn a basic capability into a high-fidelity claim.  Keep this
    # best-effort so legacy tests that intentionally compile partial specs do not
    # fail for metadata-only reasons.
    try:
        from .fidelity.foundations import build_hf1_hf2_foundation_payload

        metadata.setdefault("hf_foundation", build_hf1_hf2_foundation_payload(spec))
    except Exception as exc:  # pragma: no cover - defensive metadata path
        metadata.setdefault("hf_foundation_error", str(exc))
    return {
        "schema_version": str(spec.get("schema_version", "")),
        "task_id": str(spec.get("task_id", "")),
        "task_type": str(spec.get("task_type", "")),
        "mode": _target_mode(spec),
        "backend": str(sim.get("backend", "python")),
        "runner": runner,
        "spec_hash": spec_sha256(spec),
        "simulation": sim,
        "outputs": outputs,
        "metadata": metadata,
    }


def compile_whole_spacecraft_task(spec: Mapping[str, Any]) -> CompiledTask:
    """Compile a whole-spacecraft TaskSpec into a pure execution plan."""

    base = _base_compiled(spec, runner="whole_spacecraft.runner")
    sim = _mapping(spec.get("simulation"))
    sc = _mapping(spec.get("spacecraft"))
    adcs = _mapping(sc.get("adcs"))
    eps = _mapping(sc.get("eps"))
    comm = _mapping(sc.get("comm_data"))
    thermal = _mapping(sc.get("thermal"))
    propulsion = _mapping(sc.get("propulsion"))
    orbit = _mapping(spec.get("orbit_environment"))

    structure_config = {
        "adcs_dyn_step_s": float(_get(adcs, "dyn_step_s", 0.2)),
        "adcs_fsw_step_s": float(_get(adcs, "fsw_step_s", 0.2)),
        "battery_capacity_wh": float(_get(eps, "battery_capacity_wh", 160.0)),
        "initial_soc": float(_get(eps, "initial_soc", 0.62)),
        "solar_power_w": float(_get(eps, "solar_power_w", 95.0)),
        "payload_power_w": float(_get(eps, "payload_power_w", 38.0)),
        "bus_power_w": float(_get(eps, "bus_power_w", 18.0)),
        "instrument_baud_bps": float(_get(comm, "instrument_baud_bps", 2.5e6)),
        "storage_capacity_bits": float(_get(comm, "storage_capacity_bits", 6.0e9)),
        "storage_initial_bits": float(_get(comm, "storage_initial_bits", 0.0)),
        "transmitter_baud_bps": float(_get(comm, "transmitter_baud_bps", 1.5e6)),
        "thermal_step_s": float(_get(thermal, "step_s", 10.0)),
        "thermal_heat_power_w": float(_get(thermal, "heat_power_w", 30.0)),
        "thermal_use_network": bool(_get(thermal, "use_network", True)),
        "propulsion_enabled": bool(_get(propulsion, "enabled", True)),
        "orb_env_step_s": float(_get(orbit, "step_s", 1.0)),
        "orb_env_sun_model": str(_get(orbit, "sun_model", "spice")),
        "orb_env_sun_vector_n": tuple(float(x) for x in _get(orbit, "sun_vector_n", (1.0, 0.0, 0.0))),
        "orb_env_magnetic_field_model": str(_get(orbit, "magnetic_field_model", "dipole")),
        "orb_env_use_j2_gravity": bool(_get(orbit, "use_j2_gravity", True)),
        "orb_env_enable_eclipse": bool(_get(orbit, "enable_eclipse", False)),
        "orb_env_spice_data_path": orbit.get("spice_data_path"),
        "orb_env_wmm_data_path": orbit.get("wmm_data_path"),
    }
    run_config = {
        "duration_s": float(_get(sim, "duration_s", 300.0)),
        "sample_s": float(_get(sim, "sample_s", 10.0)),
    }
    mission = _mapping(sc.get("mission"))
    if mission:
        if "access_window_s" in mission:
            run_config["access_window_s"] = float(mission["access_window_s"])
        if "access_period_s" in mission:
            run_config["access_period_s"] = float(mission["access_period_s"])
        if "max_pointing_error_deg" in mission:
            run_config["max_pointing_error_deg"] = float(mission["max_pointing_error_deg"])

    from .modifiers import modifiers_task_spec_payload

    modern_modifiers = _mapping(spec.get("modifiers"))
    faults = tuple(dict(item) for item in (modern_modifiers.get("faults") or spec.get("faults", []) or []) if isinstance(item, Mapping))
    modifier_payload = modifiers_task_spec_payload(spec)
    labels = {
        "mode": base["mode"],
        "fault_labels": [f.get("label") or f.get("fault_id") for f in faults],
        "degradation_present": bool(spec.get("degradations") or _mapping(spec.get("modifiers")).get("degradations")),
        "constraint_present": bool(spec.get("constraints") or _mapping(spec.get("modifiers")).get("constraints")),
        "modifier_present": bool(modifier_payload.get("count")),
    }
    degradations_payload = spec.get("degradations")
    if not isinstance(degradations_payload, Mapping):
        degradations_payload = {}
    return CompiledTask(
        **base,
        run_config=run_config,
        structure_config=structure_config,
        faults=faults,
        degradations=_copy_mapping(degradations_payload),
        labels=labels,
    )


def compile_orbit_environment_task(spec: Mapping[str, Any]) -> CompiledTask:
    """Compile an orbit/environment TaskSpec into a pure execution plan."""

    if _capability_id(spec):
        return _compile_capability_task(spec, task_type="orbit_environment")

    base = _base_compiled(spec, runner="integrated.orbit_environment.model")
    sim = _mapping(spec.get("simulation"))
    orbit = _mapping(spec.get("orbit_environment"))
    dt_s = float(_get(orbit, "step_s", _get(sim, "sample_s", 30.0)))
    cfg = {
        "duration_s": float(_get(sim, "duration_s", 6000.0)),
        "dt_s": dt_s,
        "sun_vector_n": tuple(float(x) for x in _get(orbit, "sun_vector_n", (1.0, 0.0, 0.0))),
    }
    # Optional fields accepted by the Python environment config.  The v0.1 schema
    # only exposes a subset, but this keeps the compiler forward-compatible.
    for key in (
        "earth_radius_m", "earth_mu_m3_s2", "earth_rotation_rad_s",
        "altitude_m", "inclination_deg", "raan_deg", "arg_lat0_deg",
        "magnetic_dipole_axis_n", "magnetic_equator_strength_t",
    ):
        if key in orbit:
            cfg[key] = orbit[key]
    return CompiledTask(
        **base,
        orbit_environment_config=cfg,
        labels={"mode": base["mode"]},
    )



def _compile_generic_target_task(spec: Mapping[str, Any], *, task_type: str) -> CompiledTask:
    """Compile a component/subsystem TaskSpec into a generic runner plan."""

    target = _mapping(spec.get("target"))
    name = str(target.get("name") or "").strip()
    if not name:
        raise TaskSpecError(f"{task_type} TaskSpec requires target.name")
    try:
        runner = runner_module_for(task_type, name)
    except Exception as exc:
        raise TaskSpecError(str(exc)) from exc

    base = _base_compiled(spec, runner=runner)
    sim = _mapping(spec.get("simulation"))
    metadata = {
        **base.pop("metadata", {}),
        "target": {
            "level": str(target.get("level") or task_type),
            "name": name,
            "mode": _target_mode(spec),
        },
        "parameters": _copy_mapping(spec.get("parameters")),
    }
    from .modifiers import modifiers_task_spec_payload

    modern_modifiers = _mapping(spec.get("modifiers"))
    faults = tuple(dict(item) for item in (modern_modifiers.get("faults") or spec.get("faults", []) or []) if isinstance(item, Mapping))
    modifier_payload = modifiers_task_spec_payload(spec)
    labels = {
        "mode": base["mode"],
        "target_level": task_type,
        "target_name": name,
        "fault_labels": [f.get("label") or f.get("fault_id") for f in faults],
        "degradation_present": bool(spec.get("degradations") or _mapping(spec.get("modifiers")).get("degradations")),
        "constraint_present": bool(spec.get("constraints") or _mapping(spec.get("modifiers")).get("constraints")),
        "modifier_present": bool(modifier_payload.get("count")),
    }
    degradations_payload = spec.get("degradations")
    if not isinstance(degradations_payload, Mapping):
        degradations_payload = {}
    return CompiledTask(
        **base,
        run_config={
            "duration_s": float(_get(sim, "duration_s", 300.0)),
            "sample_s": float(_get(sim, "sample_s", 10.0)),
        },
        faults=faults,
        degradations=_copy_mapping(degradations_payload),
        labels=labels,
        metadata=metadata,
    )


def compile_component_task(spec: Mapping[str, Any]) -> CompiledTask:
    """Compile a component-level TaskSpec."""

    if _capability_id(spec):
        return _compile_capability_task(spec, task_type="component")
    return _compile_generic_target_task(spec, task_type="component")


def compile_subsystem_task(spec: Mapping[str, Any]) -> CompiledTask:
    """Compile a subsystem-level TaskSpec."""

    if _capability_id(spec):
        return _compile_capability_task(spec, task_type="subsystem")
    return _compile_generic_target_task(spec, task_type="subsystem")

def compile_campaign_task(spec: Mapping[str, Any]) -> CompiledTask:
    """Compile a campaign TaskSpec into a pure campaign execution plan shell."""

    from .campaign import expand_campaign_spec

    base = _base_compiled(spec, runner="sat_sim.campaign")
    plan = expand_campaign_spec(spec)
    metadata = {**base.pop("metadata", {}), "campaign_plan": {"case_count": plan.case_count, "sampling": plan.sampling}}
    return CompiledTask(
        **base,
        labels={"mode": base["mode"], "sampling": plan.sampling, "case_count": plan.case_count},
        metadata=metadata,
    )


def compile_task_spec(spec: Mapping[str, Any], *, validate: bool = True) -> CompiledTask:
    """Compile legacy or Canonical TaskSpec input into a deterministic plan."""

    source_spec = dict(spec)
    source_is_canonical = is_canonical_task_spec(source_spec)
    if validate:
        result = validate_task_spec(source_spec)
        result.raise_for_errors()
    runtime_spec = to_runtime_task_spec(source_spec)
    task_type = runtime_spec.get("task_type")
    compiled: CompiledTask
    if _capability_id(runtime_spec) and task_type in {"component", "subsystem", "orbit_environment", "whole_spacecraft", "reference"}:
        compiled = _compile_capability_task(runtime_spec, task_type=str(task_type))
    elif task_type == "component":
        compiled = compile_component_task(runtime_spec)
    elif task_type == "subsystem":
        compiled = compile_subsystem_task(runtime_spec)
    elif task_type == "whole_spacecraft":
        compiled = compile_whole_spacecraft_task(runtime_spec)
    elif task_type == "orbit_environment":
        compiled = compile_orbit_environment_task(runtime_spec)
    elif task_type == "campaign":
        compiled = compile_campaign_task(runtime_spec)
    else:
        raise TaskSpecError(
            "compiler supports task levels component/subsystem/whole_spacecraft/orbit_environment/campaign/reference, "
            f"got {task_type!r}"
        )

    if source_is_canonical:
        metadata = dict(compiled.metadata)
        metadata.setdefault("runtime_task_spec_hash", compiled.spec_hash)
        metadata["canonical_task_spec_version"] = CANONICAL_TASK_SPEC_VERSION
        return replace(
            compiled,
            schema_version=CANONICAL_TASK_SPEC_VERSION,
            spec_hash=spec_sha256(source_spec),
            metadata=metadata,
        )
    return compiled


def dataclass_to_dict(value: Any) -> Any:
    """Recursively convert dataclasses/tuples to JSON-friendly values."""

    if is_dataclass(value):
        return {k: dataclass_to_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): dataclass_to_dict(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [dataclass_to_dict(v) for v in value]
    return value


__all__ = [
    "CompiledTask",
    "compile_task_spec",
    "compile_component_task",
    "compile_subsystem_task",
    "compile_whole_spacecraft_task",
    "compile_orbit_environment_task",
    "compile_campaign_task",
    "dataclass_to_dict",
]
