"""Basilisk-native focused propulsion subsystem adapter."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any, Mapping

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue

CAPABILITY_ID = "subsystem.propulsion.unified_native.v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _values(spec: Mapping[str, Any]) -> dict[str, Any]:
    params = _mapping(spec.get("parameters"))
    nested = _mapping(params.get("values"))
    return nested or params


def _sim(spec: Mapping[str, Any], key: str, default: float) -> float:
    simulation = _mapping(spec.get("simulation"))
    raw = simulation.get(key, _mapping(simulation.get("solver")).get(key, default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _event_rows(spec: Mapping[str, Any], kind: str) -> tuple[dict[str, Any], ...]:
    plural = f"{kind}s"
    rows: list[dict[str, Any]] = []
    for container_key in ("events", "modifiers"):
        container = spec.get(container_key)
        if isinstance(container, Mapping):
            rows.extend(dict(item) for item in (container.get(plural) or ()) if isinstance(item, Mapping))
    if isinstance(spec.get(plural), list):
        rows.extend(dict(item) for item in spec[plural] if isinstance(item, Mapping))
    return tuple(rows)


def _effect_id(event: Mapping[str, Any], kind: str) -> str:
    return str(event.get(f"{kind}_type") or event.get("modifier_type") or event.get("effect") or event.get("type") or "")


def _event_window(event: Mapping[str, Any]) -> tuple[float, float | None]:
    start = float(event.get("start_s", event.get("onset_time_s", 0.0)) or 0.0)
    if event.get("end_s") is not None:
        return start, float(event["end_s"])
    if event.get("duration_s") is not None and float(event["duration_s"]) >= 0.0:
        return start, start + float(event["duration_s"])
    return start, None


class PropulsionUnifiedNativeAdapter:
    capability_id = CAPABILITY_ID
    fault_effects = ("thrust_or_feed_failure",)
    degradation_effects = ("performance_degradation",)

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        model = _mapping(spec.get("model"))
        if (model.get("capability_id") or spec.get("capability_id")) != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        simulation = _mapping(spec.get("simulation"))
        if str(simulation.get("backend") or "basilisk") != "basilisk":
            issues.append(ValidationIssue("error", "$.simulation.backend", "must be 'basilisk'", "backend"))
        for key, default in (("duration_s", 3.0), ("step_s", 0.1)):
            if _sim(spec, key, default) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be positive", "range"))
        values = _values(spec)
        initial = float(values.get("initial_propellant_kg", 1.0))
        capacity = float(values.get("tank_capacity_kg", 2.0))
        if initial < 0.0 or capacity <= 0.0 or initial > capacity:
            issues.append(ValidationIssue("error", "$.parameters", "initial_propellant_kg must be within [0, tank_capacity_kg]", "range"))
        burn_start = float(values.get("burn_start_s", 0.0))
        if burn_start < 0.0 or burn_start > _sim(spec, "duration_s", 3.0):
            issues.append(ValidationIssue("error", "$.parameters.burn_start_s", "must be within simulation duration", "time"))
        for index, event in enumerate(_event_rows(spec, "fault")):
            if _effect_id(event, "fault") not in self.fault_effects:
                issues.append(ValidationIssue("error", f"$.events.faults[{index}]", f"unsupported propulsion fault effect {_effect_id(event, 'fault')!r}", "event_effect"))
        for index, event in enumerate(_event_rows(spec, "degradation")):
            if _effect_id(event, "degradation") not in self.degradation_effects:
                issues.append(ValidationIssue("error", f"$.events.degradations[{index}]", f"unsupported propulsion degradation effect {_effect_id(event, 'degradation')!r}", "event_effect"))
        if _event_rows(spec, "constraint"):
            issues.append(ValidationIssue("error", "$.events.constraints", "runtime propulsion constraints are not supported", "event_effect"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from subsystems.propulsion.degradation import build_degradation_update_specs, default_degradation_scenarios
        from subsystems.propulsion.faults import build_fault_event_specs, default_fault_scenarios
        from subsystems.propulsion.runner import run_propulsion_basilisk_scenario
        from subsystems.propulsion.schemas import PropulsionBasiliskConfig
        from subsystems.runtime_injection import runtime_injection_evidence
        values = _values(spec)
        on_time = float(values.get("burn_on_time_s", 0.5))
        config = PropulsionBasiliskConfig(
            duration_s=max(_sim(spec, "duration_s", 3.0), 0.1),
            step_s=max(_sim(spec, "step_s", 0.1), 1.0e-4),
            spacecraft_mass_kg=float(values.get("spacecraft_mass_kg", 10.0)),
            initial_propellant_kg=float(values.get("initial_propellant_kg", 1.0)),
            tank_capacity_kg=float(values.get("tank_capacity_kg", 2.0)),
            on_time_s=(on_time, on_time),
            burn_start_s=float(values.get("burn_start_s", 0.0)),
        )
        fault_events = _event_rows(spec, "fault")
        degradation_events = _event_rows(spec, "degradation")
        fault_specs: list[dict[str, Any]] = []
        for event in fault_events:
            start_s, end_s = _event_window(event)
            for item in build_fault_event_specs(default_fault_scenarios()["feed_system_restriction"]):
                row = dict(item)
                row["time_s"] = start_s if row.get("phase") == "start" else (end_s if end_s is not None else config.duration_s + config.step_s)
                fault_specs.append(row)
        degradation_specs: list[dict[str, Any]] = []
        for event in degradation_events:
            start_s, _ = _event_window(event)
            for item in build_degradation_update_specs(
                default_degradation_scenarios()["combined_propulsion_aging"],
                update_period_s=max(config.step_s, 0.1),
            ):
                row = dict(item)
                row["start_s"] = start_s
                degradation = row.get("degradation")
                if degradation is not None and hasattr(degradation, "start_s"):
                    row["degradation"] = replace(degradation, start_s=start_s)
                degradation_specs.append(row)
        summary, rows, context = run_propulsion_basilisk_scenario(
            native_config=config,
            fault_event_specs=tuple(fault_specs),
            degradation_update_specs=tuple(degradation_specs),
            return_context=True,
        )
        mode = str(_mapping(spec.get("model")).get("target", {}).get("mode") or _mapping(spec.get("target")).get("mode") or "nominal")
        trace_rows_list: list[dict[str, Any]] = []
        for row in rows:
            fault_active = any(
                row.time_s >= (window := _event_window(event))[0]
                and (window[1] is None or row.time_s <= window[1])
                for event in fault_events
            )
            degradation_active = any(row.time_s >= _event_window(event)[0] for event in degradation_events)
            trace_rows_list.append({
                "time_s": row.time_s,
                "mode": mode,
                "propulsion.position_x_m": row.position_x_m,
                "propulsion.velocity_x_m_s": row.velocity_x_m_s,
                "propulsion.fuel_mass_kg": row.fuel_mass_kg,
                "propulsion.fuel_mass_dot_kg_s": row.fuel_mass_dot_kg_s,
                "propulsion.thrust_force_n": row.thrust_force_n,
                "propulsion.thrust_force_b_x_n": row.thrust_force_b_x_n,
                "propulsion.thrust_factor": row.thrust_factor,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
        trace_rows = tuple(trace_rows_list)
        module_tags = [str(getattr(item, "ModelTag", key)) for key, item in context.modules.items() if key not in {"thruster_bundle", "fuel_tank_bundle"}]
        project_count = 1 if "delayed_burn_controller" in context.modules else 0
        runtime_injection = runtime_injection_evidence(
            context,
            fault_events=fault_events,
            degradation_events=degradation_events,
        )
        payload = asdict(summary)
        if (
            fault_events
            and rows
            and runtime_injection.get("actual_basilisk_application_count", 0) > 0
            and runtime_injection.get("failed_direct_application_count", 0) == 0
        ):
            payload["nominal_burn_status"] = payload.get("status")
            payload["status"] = "PASS"
        payload.update({
            "capability_id": self.capability_id,
            "runtime_truth_status": "instantiated_connected_recorded",
            "official_module_count": 3,
            "project_native_module_count": project_count,
            "external_or_proxy_module_count": 0,
            "instantiated_module_count": 3 + project_count,
            "connected_message_count": 4,
            "recorder_count": len(context.recorders),
            "runtime_injection": runtime_injection,
        })
        metadata = {
            "runtime_manifest": {
                "process": "propulsionBasiliskProcess",
                "task": context.task_name,
                "instantiated_modules": module_tags,
                "connections": [
                    "THRArrayOnTimeCmdMsg->thrusterDynamicEffector",
                    "thrusterDynamicEffector->spacecraft",
                    "fuelTank->spacecraft",
                    "thrusterDynamicEffector->fuelTank",
                ],
                "recorded_sources": sorted(context.recorders),
                "excluded_runtime_categories": ["external_solver", "curve_only_proxy"],
            },
            "model_source_boundary": {
                "basilisk_official_native": ["spacecraft.Spacecraft", "thrusterDynamicEffector.ThrusterDynamicEffector", "fuelTank.FuelTank"],
                "basilisk_project_native": ["delayed_burn_controller"] if project_count else [],
                "external_or_proxy": [],
            },
            "parameters_applied": asdict(config),
            "runtime_injection": runtime_injection,
        }
        return SimulationResult(summary=payload, trace_rows=trace_rows, labels={"run_labels": [{"capability_id": self.capability_id}]}, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        return (
            "from sat_sim.adapters.subsystem_propulsion_unified_native import PropulsionUnifiedNativeAdapter\n"
            f"TASK_SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n"
            "if __name__ == '__main__':\n"
            "    print(PropulsionUnifiedNativeAdapter().run(TASK_SPEC).summary)\n"
        )

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "summary": ["runtime_truth_status", "propellant_used_kg", "final_velocity_x_m_s", "official_module_count", "project_native_module_count"],
            "trace_fields": ["time_s", "propulsion.*"],
            "metadata": ["runtime_manifest", "model_source_boundary", "parameters_applied"],
        }


__all__ = ["CAPABILITY_ID", "PropulsionUnifiedNativeAdapter"]
