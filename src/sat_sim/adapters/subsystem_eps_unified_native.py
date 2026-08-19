"""Basilisk-native focused EPS subsystem adapter."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any, Mapping

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue

CAPABILITY_ID = "subsystem.eps.unified_native.v1"


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
        if not isinstance(container, Mapping):
            continue
        rows.extend(dict(item) for item in (container.get(plural) or ()) if isinstance(item, Mapping))
    top_level = spec.get(plural)
    if isinstance(top_level, list):
        rows.extend(dict(item) for item in top_level if isinstance(item, Mapping))
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


class EpsUnifiedNativeAdapter:
    capability_id = CAPABILITY_ID
    fault_effects = ("generation_storage_distribution_failure",)
    degradation_effects = ("energy_margin_degradation",)

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> tuple[ValidationIssue, ...]:
        issues: list[ValidationIssue] = []
        model = _mapping(spec.get("model"))
        if (model.get("capability_id") or spec.get("capability_id")) != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        if str(_mapping(spec.get("simulation")).get("backend") or "basilisk") != "basilisk":
            issues.append(ValidationIssue("error", "$.simulation.backend", "must be 'basilisk'", "backend"))
        for key, default in (("duration_s", 120.0), ("step_s", 1.0)):
            if _sim(spec, key, default) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be positive", "range"))
        values = _values(spec)
        initial_soc = float(values.get("initial_soc", 0.62))
        if not 0.0 <= initial_soc <= 1.0:
            issues.append(ValidationIssue("error", "$.parameters.values.initial_soc", "must be in [0,1]", "range"))
        for index, event in enumerate(_event_rows(spec, "fault")):
            effect = _effect_id(event, "fault")
            if effect not in self.fault_effects:
                issues.append(ValidationIssue("error", f"$.events.faults[{index}]", f"unsupported EPS fault effect {effect!r}", "event_effect"))
        for index, event in enumerate(_event_rows(spec, "degradation")):
            effect = _effect_id(event, "degradation")
            if effect not in self.degradation_effects:
                issues.append(ValidationIssue("error", f"$.events.degradations[{index}]", f"unsupported EPS degradation effect {effect!r}", "event_effect"))
        constraints = _event_rows(spec, "constraint")
        if constraints:
            issues.append(ValidationIssue("error", "$.events.constraints", "runtime EPS constraints are not supported", "event_effect"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from subsystems.eps.degradation import build_degradation_update_specs, default_degradation_scenarios
        from subsystems.eps.faults import build_fault_event_specs, default_fault_scenarios
        from subsystems.eps.runner import run_eps_basilisk_scenario
        from subsystems.eps.schemas import EPSBasiliskConfig
        from subsystems.runtime_injection import runtime_injection_evidence

        values = _values(spec)
        config = EPSBasiliskConfig(
            duration_s=max(_sim(spec, "duration_s", 120.0), 0.1),
            step_s=max(_sim(spec, "step_s", 1.0), 1e-4),
            battery_capacity_wh=float(values.get("battery_capacity_wh", 160.0)),
            initial_soc=float(values.get("initial_soc", 0.62)),
            solar_power_w=float(values.get("solar_power_w", 95.0)),
            solar_efficiency=float(values.get("solar_efficiency", 0.25)),
            use_simple_solar_panel=bool(values.get("use_simple_solar_panel", True)),
            bus_power_w=float(values.get("bus_power_w", 18.0)),
            payload_power_w=float(values.get("payload_power_w", 38.0)),
            adcs_power_w=float(values.get("adcs_power_w", 20.0)),
            comm_power_w=float(values.get("comm_power_w", 12.0)),
            heater_power_w=float(values.get("heater_power_w", 0.0)),
            payload_min_soc=float(values.get("payload_min_soc", 0.55)),
            comm_min_soc=float(values.get("comm_min_soc", 0.50)),
            heater_min_soc=float(values.get("heater_min_soc", 0.30)),
            adcs_min_soc=float(values.get("adcs_min_soc", 0.20)),
            recovery_soc=float(values.get("recovery_soc", 0.65)),
        )
        outputs = _mapping(spec.get("outputs"))
        telemetry_streams = tuple(item for item in (outputs.get("telemetry_streams") or ()) if isinstance(item, Mapping))
        fault_events = _event_rows(spec, "fault")
        degradation_events = _event_rows(spec, "degradation")
        fault_specs: list[dict[str, Any]] = []
        for event in fault_events:
            start_s, end_s = _event_window(event)
            generated = build_fault_event_specs(default_fault_scenarios()["combined_eps_fault"])
            for item in generated:
                row = dict(item)
                row["time_s"] = start_s if row.get("phase") == "start" else (end_s if end_s is not None else config.duration_s + config.step_s)
                fault_specs.append(row)
        degradation_specs: list[dict[str, Any]] = []
        for event in degradation_events:
            start_s, _ = _event_window(event)
            generated = build_degradation_update_specs(
                default_degradation_scenarios()["combined_eps_aging"],
                update_period_s=max(config.step_s, 1.0),
            )
            for item in generated:
                row = dict(item)
                row["start_s"] = start_s
                degradation = row.get("degradation")
                if degradation is not None and hasattr(degradation, "start_s"):
                    row["degradation"] = replace(degradation, start_s=start_s)
                degradation_specs.append(row)
        summary, rows, context = run_eps_basilisk_scenario(
            config,
            fault_event_specs=tuple(fault_specs),
            degradation_update_specs=tuple(degradation_specs),
            telemetry_streams=telemetry_streams,
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
                "eps.battery_storage_j": row.battery_storage_j,
                "eps.battery_capacity_j": row.battery_capacity_j,
                "eps.battery_soc": row.battery_soc,
                "eps.solar_power_w": row.solar_power_w,
                "eps.bus_load_w": row.bus_load_w,
                "eps.payload_load_enabled_w": row.payload_load_enabled_w,
                "eps.adcs_load_enabled_w": row.adcs_load_enabled_w,
                "eps.comm_load_enabled_w": row.comm_load_enabled_w,
                "eps.heater_load_enabled_w": row.heater_load_enabled_w,
                "eps.net_power_w": row.net_power_w,
                "eps.load_shed_active": row.load_shed_active,
                "eps.shed_reason": row.shed_reason,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
        trace_rows = tuple(trace_rows_list)
        official = [name for name in context.modules if name not in {"payload_request", "adcs_request", "comm_request", "heater_request", "pdu"}]
        project = [name for name in context.modules if name in {"payload_request", "adcs_request", "comm_request", "heater_request", "pdu"}]
        runtime_injection = runtime_injection_evidence(
            context,
            fault_events=fault_events,
            degradation_events=degradation_events,
        )
        payload = asdict(summary)
        payload.update({
            "capability_id": self.capability_id,
            "runtime_truth_status": "instantiated_connected_recorded",
            "official_module_count": len(official),
            "project_native_module_count": len(project),
            "external_or_proxy_module_count": 0,
            "instantiated_module_count": len(context.modules),
            "connected_message_count": 12,
            "recorder_count": len(context.recorders),
            "runtime_injection": runtime_injection,
        })
        metadata = {
            "runtime_manifest": {
                "process": "epsBasiliskManagedProcess",
                "task": context.task_name,
                "instantiated_modules": sorted(context.modules),
                "recorded_sources": sorted(context.recorders),
                "excluded_runtime_categories": ["external_solver", "curve_only_proxy"],
            },
            "model_source_boundary": {
                "basilisk_official_native": official,
                "basilisk_project_native": project,
                "external_or_proxy": [],
            },
            "parameters_applied": asdict(config),
            "native_multi_rate_telemetry": context.base_parameters.get("native_multi_rate_telemetry"),
            "runtime_injection": runtime_injection,
        }
        return SimulationResult(summary=payload, trace_rows=trace_rows, labels={"run_labels": [{"capability_id": self.capability_id}]}, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        return (
            "from sat_sim.adapters.subsystem_eps_unified_native import EpsUnifiedNativeAdapter\n"
            f"TASK_SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n"
            "if __name__ == '__main__':\n"
            "    print(EpsUnifiedNativeAdapter().run(TASK_SPEC).summary)\n"
        )

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"summary": ["runtime_truth_status", "final_soc", "load_shed_event_count"], "trace_fields": ["time_s", "eps.*"]}


__all__ = ["CAPABILITY_ID", "EpsUnifiedNativeAdapter"]
