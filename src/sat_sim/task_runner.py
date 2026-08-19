"""Execution bridge from CompiledTask to current simulation runners."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import importlib
import inspect
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dataset_writer import DatasetWriteResult, write_task_dataset
from .task_compiler import CompiledTask, dataclass_to_dict
from .task_spec import TaskSpecError
from .task_models import to_runtime_task_spec


@dataclass(frozen=True)
class TaskRunResult:
    """Result of executing a compiled task."""

    compiled: CompiledTask
    summary: dict[str, Any]
    trace_rows: tuple[dict[str, Any], ...]
    dataset: DatasetWriteResult | None = None
    runtime_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiled": self.compiled.to_dict(),
            "summary": self.summary,
            "trace_rows": list(self.trace_rows),
            "dataset": self.dataset.to_dict() if self.dataset else None,
            "runtime_metadata": self.runtime_metadata or {},
        }


def _enum_value(enum_cls: Any, value: str) -> Any | None:
    try:
        for item in enum_cls:
            if getattr(item, "value", None) == value or getattr(item, "name", None) == value:
                return item
    except Exception:
        return None
    return None


def _fault_type_for_spec(fault: Mapping[str, Any]) -> Any:
    value = str(fault.get("fault_type", ""))
    target_type = str(fault.get("target_type", "unknown"))
    try:
        from components.battery.faults import BatteryFaultType
        from components.comm.faults import CommFaultType
        from components.fuel_tank.faults import FuelTankFaultType
        from components.heater.faults import HeaterFaultType
        from components.reaction_wheel.faults import RWFaultType
        from components.sensor.faults import SensorFaultType
        from components.solar_panel.faults import SolarPanelFaultType
        from components.thruster.faults import ThrusterFaultType
        enum_by_target = {
            "battery": BatteryFaultType,
            "comm": CommFaultType,
            "fuel_tank": FuelTankFaultType,
            "heater": HeaterFaultType,
            "reaction_wheel": RWFaultType,
            "rw": RWFaultType,
            "sensor": SensorFaultType,
            "solar_panel": SolarPanelFaultType,
            "thruster": ThrusterFaultType,
        }
        enum_cls = enum_by_target.get(target_type)
        if enum_cls is not None:
            matched = _enum_value(enum_cls, value)
            if matched is not None:
                return matched
        # Fallback by globally unique values used in current code.
        for enum_cls in (BatteryFaultType, RWFaultType, ThrusterFaultType, SolarPanelFaultType, FuelTankFaultType, HeaterFaultType, CommFaultType, SensorFaultType):
            matched = _enum_value(enum_cls, value)
            if matched is not None:
                return matched
    except Exception as exc:
        record_runtime_diagnostic(
            code='RUNNER_INTROSPECTION_FALLBACK',
            category=DiagnosticCategory.INTROSPECTION_FALLBACK,
            location='src/sat_sim/task_runner.py:_fault_type_for_spec:01',
            exception=exc,
            strict=False,
        )
    return value


def _build_fault_specs(compiled: CompiledTask) -> list[Any]:
    from components.fault_spec import FaultSpec

    specs = []
    for fault in compiled.faults:
        specs.append(
            FaultSpec(
                fault_type=_fault_type_for_spec(fault),
                onset_time_s=float(fault["onset_time_s"]),
                duration_s=float(fault["duration_s"]),
                magnitude=float(fault["magnitude"]),
                target_id=str(fault.get("target") or fault.get("target_id") or ""),
            )
        )
    return specs


def _build_degradations(compiled: CompiledTask) -> dict[str, Any]:
    d = compiled.degradations or {}
    out: dict[str, Any] = {"eps": None, "adcs": None, "propulsion": None, "thermal": None}
    if not d:
        return out

    from components.battery.degradation import BatteryDegradation
    from components.fuel_tank.degradation import FuelTankDegradation
    from components.heater.degradation import HeaterDegradation
    from components.radiator.degradation import RadiatorDegradation
    from components.reaction_wheel.degradation import ReactionWheelDegradation
    from components.sensor.degradation import SensorDegradation
    from components.solar_panel.degradation import SolarPanelDegradation
    from components.thruster.degradation import ThrusterDegradation
    from subsystems.adcs.degradation import ADCSDegradation
    from subsystems.eps.degradation import EPSDegradation
    from subsystems.propulsion.degradation import PropulsionDegradation
    from subsystems.thermal.degradation import ThermalDegradation

    eps = d.get("eps") if isinstance(d.get("eps"), Mapping) else None
    if eps:
        out["eps"] = EPSDegradation(
            battery_degradation=BatteryDegradation(**dict(eps.get("battery") or {})),
            solar_panel_degradation=SolarPanelDegradation(**dict(eps.get("solar_panel") or {})),
            pdu_efficiency_loss_pct=float(eps.get("pdu_efficiency_loss_pct", 0.0)),
        )
    adcs = d.get("adcs") if isinstance(d.get("adcs"), Mapping) else None
    if adcs:
        out["adcs"] = ADCSDegradation(
            rw_degradation=ReactionWheelDegradation(**dict(adcs.get("reaction_wheel") or {})),
            sensor_degradation=SensorDegradation(**dict(adcs.get("sensor") or {})),
        )
    propulsion = d.get("propulsion") if isinstance(d.get("propulsion"), Mapping) else None
    if propulsion:
        out["propulsion"] = PropulsionDegradation(
            thruster_degradation=ThrusterDegradation(**dict(propulsion.get("thruster") or {})),
            fuel_tank_degradation=FuelTankDegradation(**dict(propulsion.get("fuel_tank") or {})),
        )
    thermal = d.get("thermal") if isinstance(d.get("thermal"), Mapping) else None
    if thermal:
        out["thermal"] = ThermalDegradation(
            heater_degradation=HeaterDegradation(**dict(thermal.get("heater") or {})),
            radiator_degradation=RadiatorDegradation(**dict(thermal.get("radiator") or {})),
        )
    return out


def instantiate_whole_spacecraft_run_config(compiled: CompiledTask) -> Any:
    """Instantiate current whole-spacecraft dataclasses from a CompiledTask."""

    try:
        from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"whole-spacecraft schemas are unavailable: {exc}") from exc

    structure = WholeSpacecraftConfig(**compiled.structure_config)
    degradations = _build_degradations(compiled)
    fault_specs = _build_fault_specs(compiled)
    structure = replace(
        structure,
        eps_degradation=degradations["eps"],
        adcs_degradation=degradations["adcs"],
        propulsion_degradation=degradations["propulsion"],
        thermal_degradation=degradations["thermal"],
        fault_specs=fault_specs,
    )
    return WholeSpacecraftRunConfig(**compiled.run_config, structure=structure)


def _run_whole_spacecraft(compiled: CompiledTask) -> tuple[Any, tuple[Any, ...]]:
    try:
        from Basilisk.utilities import SimulationBaseClass  # noqa: F401
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "whole_spacecraft execution requires Basilisk/bsk to be installed; "
            "validation and compilation remain available without Basilisk"
        ) from exc
    try:
        from whole_spacecraft.runner import run_whole_spacecraft_native_case
    except Exception as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(f"whole-spacecraft runner is unavailable: {exc}") from exc
    cfg = instantiate_whole_spacecraft_run_config(compiled)
    return run_whole_spacecraft_native_case(cfg)


def _run_orbit_environment(compiled: CompiledTask) -> tuple[Any, tuple[dict[str, Any], ...]]:
    from integrated.orbit_environment.model import generate_environment_profile, summarize_profile
    from integrated.orbit_environment.schemas import OrbitEnvironmentConfig

    cfg = OrbitEnvironmentConfig(**compiled.orbit_environment_config)
    profile = generate_environment_profile(cfg)
    summary = summarize_profile(profile)
    rows: list[dict[str, Any]] = []
    for sample in profile.samples:
        rows.append(
            {
                "time_s": sample.time_s,
                "r_x_m": sample.r_bn_n_m[0],
                "r_y_m": sample.r_bn_n_m[1],
                "r_z_m": sample.r_bn_n_m[2],
                "v_x_m_s": sample.v_bn_n_m_s[0],
                "v_y_m_s": sample.v_bn_n_m_s[1],
                "v_z_m_s": sample.v_bn_n_m_s[2],
                "orbit_radius_m": sample.orbit_radius_m,
                "sun_x": sample.sun_vector_n[0],
                "sun_y": sample.sun_vector_n[1],
                "sun_z": sample.sun_vector_n[2],
                "shadow_factor": sample.shadow_factor,
                "mag_x_t": sample.magnetic_field_n_t[0],
                "mag_y_t": sample.magnetic_field_n_t[1],
                "mag_z_t": sample.magnetic_field_n_t[2],
                "mag_norm_t": sample.magnetic_field_norm_t,
                "ground_range_m": sample.ground_range_m,
                "ground_elevation_deg": sample.ground_elevation_deg,
                "ground_has_access": sample.ground_has_access,
            }
        )
    return summary, tuple(rows)



def _target_metadata(compiled: CompiledTask) -> Mapping[str, Any]:
    meta = compiled.metadata.get("target") if isinstance(compiled.metadata, Mapping) else None
    return meta if isinstance(meta, Mapping) else {}


def _component_degradation_payload(compiled: CompiledTask, name: str) -> dict[str, Any]:
    d = compiled.degradations or {}
    if not isinstance(d, Mapping):
        return {}
    mapping = {
        "battery": ("eps", "battery"),
        "solar_panel": ("eps", "solar_panel"),
        "reaction_wheel": ("adcs", "reaction_wheel"),
        "thruster": ("propulsion", "thruster"),
        "fuel_tank": ("propulsion", "fuel_tank"),
        "heater": ("thermal", "heater"),
        "radiator": ("thermal", "radiator"),
    }
    group_key = mapping.get(name)
    if not group_key:
        return {}
    group = d.get(group_key[0])
    if not isinstance(group, Mapping):
        return {}
    payload = group.get(group_key[1])
    return dict(payload) if isinstance(payload, Mapping) else {}


def _generic_degradation_for(compiled: CompiledTask) -> Any:
    target = _target_metadata(compiled)
    task_type = str(target.get("level") or compiled.task_type)
    name = str(target.get("name") or "")
    _d = compiled.degradations or {}

    if task_type == "subsystem":
        subsystem_degradations = _build_degradations(compiled)
        if name in subsystem_degradations and subsystem_degradations[name] is not None:
            return subsystem_degradations[name]
        # Some runner names are implementation-level variants of ADCS.
        if name.startswith("adcs") and subsystem_degradations.get("adcs") is not None:
            return subsystem_degradations["adcs"]

    payload = _component_degradation_payload(compiled, name)
    if not payload:
        raise TaskSpecError(f"no degradation payload available for {task_type} target {name!r}")

    from components import degradation as deg

    cls_by_component = {
        "battery": "BatteryDegradation",
        "solar_panel": "SolarPanelDegradation",
        "reaction_wheel": "ReactionWheelDegradation",
        "thruster": "ThrusterDegradation",
        "fuel_tank": "FuelTankDegradation",
        "heater": "HeaterDegradation",
        "radiator": "RadiatorDegradation",
    }
    cls_name = cls_by_component.get(name)
    if cls_name is None:
        raise TaskSpecError(f"degradation mode is not mapped for {task_type} target {name!r}")
    cls = getattr(deg, cls_name)
    return cls(**payload)


def _call_runner_function(func: Any, *preferred_args: Any) -> Any:
    """Call a runner function with args only when its signature expects them."""

    try:
        sig = inspect.signature(func)
        positional = [
            p for p in sig.parameters.values()
            if p.kind in {p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD}
            and p.default is p.empty
        ]
        if len(positional) == 0:
            return func()
    except Exception as exc:
        record_runtime_diagnostic(
            code='RUNNER_INTROSPECTION_FALLBACK',
            category=DiagnosticCategory.INTROSPECTION_FALLBACK,
            location='src/sat_sim/task_runner.py:_call_runner_function:01',
            exception=exc,
            strict=False,
        )
    return func(*preferred_args)


def _flatten_scalar_row(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in data.items():
        out_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            row.update(_flatten_scalar_row(value, out_key))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            row[out_key] = value
    return row


def _is_sequence_value(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _generic_summary_and_rows(result: Any) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    data = dataclass_to_dict(result)
    if not isinstance(data, Mapping):
        return {"value": data}, tuple()
    data = dict(data)
    time_s = data.get("time_s")
    rows: list[dict[str, Any]] = []
    if _is_sequence_value(time_s):
        n = len(time_s)
        for i in range(n):
            row: dict[str, Any] = {"time_s": time_s[i]}
            for key, value in data.items():
                if key == "time_s":
                    continue
                if _is_sequence_value(value) and len(value) == n:
                    item = value[i]
                    if _is_sequence_value(item):
                        for j, component in enumerate(item):
                            row[f"{key}_{j}"] = component
                    elif isinstance(item, Mapping):
                        row.update({f"{key}.{k}": v for k, v in _flatten_scalar_row(item).items()})
                    else:
                        row[str(key)] = item
            rows.append(row)
    else:
        scalar_row = _flatten_scalar_row(data)
        rows = [scalar_row] if scalar_row else []

    summary: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sequence_value(value) and key != "faults":
            # Keep summary compact; vector-like outputs belong in trace_rows.
            summary[f"{key}_count"] = len(value)
        else:
            summary[str(key)] = value
    summary.setdefault("status", "complete")
    summary["trace_rows"] = len(rows)
    return summary, tuple(rows)




def run_registered_capability_adapter(compiled: CompiledTask, task_spec: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any]]:
    """Run an explicit capability adapter instead of a legacy demo runner."""

    cid = compiled.metadata.get("capability_id") if isinstance(compiled.metadata, Mapping) else None
    if not isinstance(cid, str) or not cid:
        raise TaskSpecError("compiled task does not include metadata.capability_id")
    from .capability_registry import get_adapter_for_capability, get_capability

    contract = get_capability(cid)
    adapter = get_adapter_for_capability(cid)
    validation_issues = tuple(adapter.validate(task_spec, contract.data))
    validation_errors = [item for item in validation_issues if str(getattr(item, "severity", "")).lower() == "error"]
    if validation_errors:
        details = "; ".join(
            f"{getattr(item, 'path', '$')}: {getattr(item, 'message', str(item))}"
            for item in validation_errors[:8]
        )
        raise TaskSpecError(f"registered capability adapter validation failed: {details}")
    result = adapter.run(task_spec, contract.data)
    # Most adapters still use the conservative post-processing modifier layer.
    # V37-B composite whole-spacecraft execution owns physical fault/degradation
    # mapping itself and marks that ownership explicitly to prevent double use.
    if not bool(result.metadata.get("modifiers_applied_by_adapter")):
        from .modifiers import apply_modifiers_to_result
        result = apply_modifiers_to_result(result, task_spec)
    # Preserve adapter labels in the summary so dataset consumers can inspect
    # standard event labels even though the legacy DatasetWriter writes labels
    # from CompiledTask for backward compatibility.
    summary = dict(result.summary)
    # Always persist a normalized event-registration payload.  Adapters that
    # implement physical effects themselves intentionally skip the generic
    # row post-processor, but validation still requires auditable evidence
    # that each fault, degradation or operational constraint was registered.
    from .modifiers import modifiers_task_spec_payload
    modifier_payload = modifiers_task_spec_payload(task_spec)
    if modifier_payload.get("count", 0):
        active_sample_count = sum(
            1 for row in result.trace_rows
            if row.get("label.modifier_active") is True
            or row.get("label.fault_active") is True
            or row.get("label.degradation_active") is True
            or row.get("label.constraint_active") is True
        )
        modifier_payload["active_sample_count"] = active_sample_count
        events_summary = summary.setdefault("events", {})
        if isinstance(events_summary, dict):
            events_summary["applied_modifiers"] = modifier_payload
        summary["modifier_count"] = modifier_payload["count"]
        summary["fault_modifier_count"] = modifier_payload["fault_count"]
        summary["degradation_modifier_count"] = modifier_payload["degradation_count"]
        summary["constraint_modifier_count"] = modifier_payload["constraint_count"]
    if result.labels:
        summary.setdefault("adapter_labels", result.labels)
    runtime_metadata = dict(result.metadata)
    summary_metadata = {
        key: value for key, value in runtime_metadata.items()
        if key != "native_multi_rate_telemetry"
    }
    if summary_metadata:
        summary.setdefault("adapter_metadata", summary_metadata)
    return summary, tuple(dict(row) for row in result.trace_rows), runtime_metadata

def _run_generic_module(compiled: CompiledTask) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Run a generic component/subsystem runner module."""

    try:
        module = importlib.import_module(compiled.runner)
    except Exception as exc:  # pragma: no cover - import failures are environment-specific
        raise RuntimeError(f"runner module {compiled.runner!r} is unavailable: {exc}") from exc

    mode = compiled.mode
    if mode == "nominal":
        func = getattr(module, "run_nominal_case", None)
        if func is None:
            raise TaskSpecError(f"{compiled.runner} does not expose run_nominal_case")
        result = _call_runner_function(func)
    elif mode == "fault":
        func = getattr(module, "run_fault_case", None)
        if func is None:
            raise TaskSpecError(f"{compiled.runner} does not expose run_fault_case")
        result = _call_runner_function(func, _build_fault_specs(compiled))
    elif mode == "degradation":
        func = getattr(module, "run_degradation_case", None)
        if func is None:
            raise TaskSpecError(f"{compiled.runner} does not expose run_degradation_case")
        result = _call_runner_function(func, _generic_degradation_for(compiled))
    else:
        raise TaskSpecError(
            f"generic component/subsystem runner supports nominal/fault/degradation modes only, got {mode!r}"
        )
    return _generic_summary_and_rows(result)

def run_compiled_task(
    compiled: CompiledTask,
    *,
    task_spec: Mapping[str, Any] | None = None,
    output_root: str | Path | None = None,
    write_dataset: bool = True,
) -> TaskRunResult:
    """Execute a compiled task and optionally write a dataset directory."""

    canonical_task_spec = task_spec
    runtime_task_spec = to_runtime_task_spec(task_spec) if task_spec is not None else None

    if compiled.task_type == "campaign":
        if runtime_task_spec is None:
            raise TaskSpecError("task_spec is required when running a campaign")
        from .campaign import run_campaign_spec

        campaign_result = run_campaign_spec(
            runtime_task_spec,
            output_root=output_root or compiled.outputs.get("output_root"),
            continue_on_error=True,
            dry_run=False,
        )
        return TaskRunResult(
            compiled=compiled,
            summary=campaign_result.summary,
            trace_rows=campaign_result.case_rows,
            dataset=DatasetWriteResult(
                output_root=campaign_result.output_root,
                files=campaign_result.files,
                manifest=campaign_result.manifest,
            ),
        )

    runtime_metadata: dict[str, Any] = {}
    if isinstance(compiled.metadata, Mapping) and compiled.metadata.get("capability_id"):
        if runtime_task_spec is None:
            raise TaskSpecError("task_spec is required when running a capability adapter")
        summary_obj, rows_obj, runtime_metadata = run_registered_capability_adapter(compiled, runtime_task_spec)
    elif compiled.task_type in {"component", "subsystem"}:
        summary_obj, rows_obj = _run_generic_module(compiled)
    elif compiled.task_type == "whole_spacecraft":
        summary_obj, rows_obj = _run_whole_spacecraft(compiled)
    elif compiled.task_type == "orbit_environment":
        summary_obj, rows_obj = _run_orbit_environment(compiled)
    else:
        raise TaskSpecError(f"runner for task_type={compiled.task_type!r} is not implemented in P3")

    summary = dataclass_to_dict(summary_obj)
    if not isinstance(summary, Mapping):
        summary = {"value": summary}
    row_dicts = tuple(dataclass_to_dict(row) for row in rows_obj)
    row_dicts = tuple(dict(row) if isinstance(row, Mapping) else {"value": row} for row in row_dicts)

    dataset = None
    if write_dataset:
        if canonical_task_spec is None:
            raise TaskSpecError("task_spec is required when write_dataset=True")
        root = Path(output_root or compiled.outputs.get("output_root") or compiled.task_id)
        dataset = write_task_dataset(
            output_root=root,
            compiled=compiled,
            task_spec=canonical_task_spec,
            summary=summary,
            trace_rows=row_dicts,
            status="complete",
            runtime_metadata=runtime_metadata,
        )
    return TaskRunResult(
        compiled=compiled, summary=dict(summary), trace_rows=row_dicts, dataset=dataset,
        runtime_metadata=runtime_metadata,
    )


__all__ = [
    "TaskRunResult",
    "instantiate_whole_spacecraft_run_config",
    "run_registered_capability_adapter",
    "run_compiled_task",
]
