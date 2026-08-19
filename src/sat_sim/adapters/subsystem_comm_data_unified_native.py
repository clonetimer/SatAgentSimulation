"""Basilisk-native focused communication/data subsystem adapter."""
from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Any, Mapping

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue

CAPABILITY_ID = "subsystem.comm_data.unified_native.v1"


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


class CommDataUnifiedNativeAdapter:
    capability_id = CAPABILITY_ID
    fault_effects = ("link_or_storage_failure",)
    degradation_effects = ("margin_or_throughput_degradation",)

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
        for index, event in enumerate(_event_rows(spec, "fault")):
            if _effect_id(event, "fault") not in self.fault_effects:
                issues.append(ValidationIssue("error", f"$.events.faults[{index}]", f"unsupported Comm/Data fault effect {_effect_id(event, 'fault')!r}", "event_effect"))
        for index, event in enumerate(_event_rows(spec, "degradation")):
            if _effect_id(event, "degradation") not in self.degradation_effects:
                issues.append(ValidationIssue("error", f"$.events.degradations[{index}]", f"unsupported Comm/Data degradation effect {_effect_id(event, 'degradation')!r}", "event_effect"))
        if _event_rows(spec, "constraint"):
            issues.append(ValidationIssue("error", "$.events.constraints", "runtime Comm/Data constraints are not supported", "event_effect"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from subsystems.comm_data.degradation import build_degradation_update_specs, default_degradation_scenarios
        from subsystems.comm_data.faults import build_fault_event_specs, default_fault_scenarios
        from subsystems.comm_data.runner import run_comm_data_basilisk_scenario
        from subsystems.comm_data.schemas import CommDataBasiliskConfig
        from subsystems.runtime_injection import runtime_injection_evidence

        values = _values(spec)
        config = CommDataBasiliskConfig(
            duration_s=max(_sim(spec, "duration_s", 120.0), 0.1),
            step_s=max(_sim(spec, "step_s", 1.0), 1e-4),
            instrument_baud_bps=float(values.get("instrument_baud_bps", 2.5e6)),
            storage_capacity_bits=float(values.get("storage_capacity_bits", 6.0e9)),
            transmitter_baud_bps=float(values.get("transmitter_baud_bps", 1.5e6)),
            data_name=str(values.get("data_name", "payload_science")),
            initial_storage_bits=float(values.get("initial_storage_bits", 0.0)),
            native_storage_drain_enabled=bool(values.get("native_storage_drain_enabled", True)),
        )
        outputs = _mapping(spec.get("outputs"))
        telemetry_streams = tuple(item for item in (outputs.get("telemetry_streams") or ()) if isinstance(item, Mapping))
        fault_events = _event_rows(spec, "fault")
        degradation_events = _event_rows(spec, "degradation")
        fault_specs: list[dict[str, Any]] = []
        for event in fault_events:
            start_s, end_s = _event_window(event)
            for item in build_fault_event_specs(default_fault_scenarios()["transmitter_power_loss"]):
                row = dict(item)
                row["time_s"] = start_s if row.get("phase") == "start" else (end_s if end_s is not None else config.duration_s + config.step_s)
                fault_specs.append(row)
        degradation_specs: list[dict[str, Any]] = []
        for event in degradation_events:
            start_s, _ = _event_window(event)
            generated = build_degradation_update_specs(
                default_degradation_scenarios()["rf_path_aging"],
                update_period_s=max(config.step_s, 1.0),
            )
            for item in generated:
                if str(item.get("component")) != "transmitter":
                    continue
                row = dict(item)
                row["start_s"] = start_s
                degradation = row.get("degradation")
                if degradation is not None and hasattr(degradation, "start_s"):
                    row["degradation"] = replace(degradation, start_s=start_s)
                degradation_specs.append(row)
        summary, rows, context = run_comm_data_basilisk_scenario(
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
                "comm_data.instrument_baud_bps": row.instrument_baud_bps,
                "comm_data.storage_level_bits": row.storage_level_bits,
                "comm_data.storage_capacity_bits": row.storage_capacity_bits,
                "comm_data.transmitter_baud_bps": row.transmitter_baud_bps,
                "comm_data.native_storage_drain_enabled": row.native_storage_drain_enabled,
                "comm_data.transmitter_storage_node_baud_bps": row.transmitter_storage_node_baud_bps,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": "fault" if fault_active else ("degraded" if degradation_active else "nominal"),
            })
        trace_rows = tuple(trace_rows_list)
        runtime_injection = runtime_injection_evidence(
            context,
            fault_events=fault_events,
            degradation_events=degradation_events,
        )
        payload = asdict(summary)
        payload.update({
            "capability_id": self.capability_id,
            "runtime_truth_status": "instantiated_connected_recorded",
            "official_module_count": 3,
            "project_native_module_count": 0,
            "external_or_proxy_module_count": 0,
            "instantiated_module_count": 3,
            "connected_message_count": 4,
            "recorder_count": len(context.recorders),
            "runtime_injection": runtime_injection,
        })
        metadata = {
            "runtime_manifest": {
                "process": "commDataBasiliskProcess",
                "task": context.task_name,
                "instantiated_modules": sorted(context.modules),
                "recorded_sources": sorted(context.recorders),
                "excluded_runtime_categories": ["external_solver", "curve_only_proxy"],
            },
            "model_source_boundary": {
                "basilisk_official_native": sorted(context.modules),
                "basilisk_project_native": [],
                "external_or_proxy": [],
            },
            "parameters_applied": asdict(config),
            "native_multi_rate_telemetry": context.base_parameters.get("native_multi_rate_telemetry"),
            "runtime_injection": runtime_injection,
        }
        return SimulationResult(summary=payload, trace_rows=trace_rows, labels={"run_labels": [{"capability_id": self.capability_id}]}, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        return (
            "from sat_sim.adapters.subsystem_comm_data_unified_native import CommDataUnifiedNativeAdapter\n"
            f"TASK_SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n"
            "if __name__ == '__main__':\n"
            "    print(CommDataUnifiedNativeAdapter().run(TASK_SPEC).summary)\n"
        )

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {"summary": ["runtime_truth_status", "final_storage_bits", "estimated_native_downlinked_bits"], "trace_fields": ["time_s", "comm_data.*"]}


__all__ = ["CAPABILITY_ID", "CommDataUnifiedNativeAdapter"]
