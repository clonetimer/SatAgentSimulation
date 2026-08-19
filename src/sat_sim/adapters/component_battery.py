"""Explicit adapter for ``component.battery.v1``.

This adapter maps TaskSpec parameters directly to the battery model classes in
``components.battery``. It intentionally does not call ``components.battery.runner``.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapter_effects import effect_parameter, runtime_effects
from sat_sim.task_validator import ValidationIssue


class BatteryAdapter:
    """Production adapter for the battery component capability."""

    capability_id = "component.battery.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "battery capability requires task_type='component'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "component" or target.get("name") != "battery":
            issues.append(ValidationIssue("error", "$.target", "battery capability requires target.level='component' and target.name='battery'", "capability"))
        mode = target.get("mode", "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "battery capability supports nominal/fault/degradation", "capability"))

        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        for key in ("capacity_wh", "initial_soc"):
            if key not in params:
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "is required by component.battery.v1", "capability_required"))
        cap = params.get("capacity_wh")
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0:
            issues.append(ValidationIssue("error", "$.parameters.capacity_wh", "must be a positive number", "range"))
        soc = params.get("initial_soc")
        if isinstance(soc, bool) or not isinstance(soc, (int, float)) or not 0.0 <= float(soc) <= 1.0:
            issues.append(ValidationIssue("error", "$.parameters.initial_soc", "must be within [0, 1]", "range"))
        for key in ("charge_efficiency", "discharge_efficiency", "min_soc", "max_soc"):
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be within [0, 1]", "range"))
        if "power_profile_w" in params:
            profile = params.get("power_profile_w")
            if not isinstance(profile, list) or not profile:
                issues.append(ValidationIssue("error", "$.parameters.power_profile_w", "must be a non-empty numeric list", "type"))
            elif any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in profile):
                issues.append(ValidationIssue("error", "$.parameters.power_profile_w", "all values must be numeric", "type"))
        elif "net_power_w" in params:
            value = params.get("net_power_w")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(ValidationIssue("error", "$.parameters.net_power_w", "must be numeric", "type"))
        else:
            issues.append(ValidationIssue("warning", "$.parameters.net_power_w", "not provided; default -20 W discharge profile will be used", "default"))

        faults = self._runtime_fault_payloads(spec)
        if mode == "fault":
            supported = {"open_circuit", "sudden_capacity_loss"}
            for i, fault in enumerate(faults):
                if not isinstance(fault, Mapping):
                    continue
                ftype = fault.get("fault_type")
                if ftype not in supported:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].fault_type", f"unsupported for {self.capability_id}: {ftype!r}", "capability_fault"))
                target_type = fault.get("target_type")
                if target_type not in {None, "battery"}:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].target_type", "must be 'battery'", "capability_fault"))
        if mode == "degradation":
            battery_deg = self._battery_degradation_payload(spec)
            if not battery_deg:
                issues.append(ValidationIssue("error", "$.degradations.eps.battery", "battery degradation payload is required", "capability_degradation"))
            else:
                supported = {"capacity_loss_pct", "internal_resistance_increase_pct"}
                unknown = sorted(set(battery_deg) - supported - {"self_discharge_rate_increase_pct"})
                for key in unknown:
                    issues.append(ValidationIssue("error", f"$.degradations.eps.battery.{key}", "unsupported battery degradation field", "capability_degradation"))
                if float(battery_deg.get("self_discharge_rate_increase_pct", 0.0) or 0.0) != 0.0:
                    issues.append(ValidationIssue(
                        "error",
                        "$.degradations.eps.battery.self_discharge_rate_increase_pct",
                        "self-discharge dynamics are out of scope for component.battery.v1; model extension is required",
                        "MODEL_EXTENSION_REQUIRED",
                    ))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.battery.model import build_nominal_battery_config, initialize_battery, step_battery
        from components.battery.degradation import BatteryDegradation

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 300.0))
        sample_s = float(sim.get("sample_s", 10.0))
        task_id = str(spec.get("task_id", "battery_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"

        degradation_payload = self._battery_degradation_payload(spec)
        degradation = BatteryDegradation(**degradation_payload) if degradation_payload else None
        config = build_nominal_battery_config(
            capacity_wh=float(params.get("capacity_wh", 160.0)),
            initial_soc=float(params.get("initial_soc", 0.62)),
            charge_efficiency=float(params.get("charge_efficiency", 0.98)),
            discharge_efficiency=float(params.get("discharge_efficiency", 0.97)),
            min_soc=float(params.get("min_soc", 0.0)),
            max_soc=float(params.get("max_soc", 1.0)),
            degradation=degradation,
        )
        state = initialize_battery(config)
        nominal_capacity_wh = float(params.get("capacity_wh", 160.0))
        faults = self._runtime_fault_payloads(spec)
        profile = self._power_profile(params, duration_s, sample_s)

        rows: list[dict[str, Any]] = []
        energy_throughput_wh = 0.0
        active_capacity_loss_ids: set[str] = set()

        def row(time_s: float, sample_index: int, net_power_w: float, applied_power_w: float, fault_active: bool) -> dict[str, Any]:
            return {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": sample_index,
                "target_level": "component",
                "target_name": "battery",
                "mode": mode,
                "eps.battery.soc": state.soc,
                "eps.battery.storage_wh": state.storage_wh,
                "eps.battery.capacity_wh": nominal_capacity_wh,
                "eps.battery.effective_capacity_wh": state.capacity_wh,
                "eps.battery.net_power_w": net_power_w,
                "eps.battery.applied_power_w": applied_power_w,
                "eps.battery.shunt_dissipated_wh": state.shunt_dissipated_wh,
                "eps.battery.nominal_charge_efficiency": float(params.get("charge_efficiency", 0.98)),
                "eps.battery.effective_charge_efficiency": config.charge_efficiency,
                "eps.battery.nominal_discharge_efficiency": float(params.get("discharge_efficiency", 0.97)),
                "eps.battery.effective_discharge_efficiency": config.discharge_efficiency,
                "label.health_state": "fault_active" if fault_active else ("degraded" if degradation else "nominal"),
                "label.fault_active": bool(fault_active),
            }

        rows.append(row(0.0, 0, profile[0] if profile else 0.0, profile[0] if profile else 0.0, self._faults_active(faults, 0.0)))
        t = 0.0
        sample_index = 0
        while t < duration_s - 1e-12:
            dt = min(sample_s, duration_s - t)
            net_power_w = profile[min(sample_index, len(profile) - 1)] if profile else 0.0
            active = self._active_faults(faults, t)
            _fault_active = bool(active)
            applied_power_w = net_power_w
            for fault in active:
                ftype = str(fault.get("fault_type", ""))
                fid = str(fault.get("fault_id") or ftype)
                mag = float(fault.get("magnitude", 0.0))
                if ftype == "open_circuit":
                    # Isolated battery: no charge or discharge can flow while active.
                    applied_power_w = 0.0
                elif ftype == "sudden_capacity_loss" and fid not in active_capacity_loss_ids:
                    new_capacity = max(0.0, state.capacity_wh * (1.0 - mag))
                    state = replace(
                        state,
                        capacity_wh=new_capacity,
                        storage_wh=min(state.storage_wh, new_capacity),
                        soc=(min(state.storage_wh, new_capacity) / new_capacity if new_capacity > 0 else 0.0),
                    )
                    active_capacity_loss_ids.add(fid)
            state = step_battery(state, config, applied_power_w, dt)
            energy_throughput_wh += abs(applied_power_w) * dt / 3600.0
            t += dt
            sample_index += 1
            rows.append(row(t, sample_index, net_power_w, applied_power_w, self._faults_active(faults, t)))

        soc_values = [float(r["eps.battery.soc"]) for r in rows]
        fault_count = len(faults)
        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": "battery",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "eps.battery.initial_soc": soc_values[0],
                "eps.battery.final_soc": soc_values[-1],
                "eps.battery.min_soc": min(soc_values),
                "eps.battery.max_soc": max(soc_values),
                "eps.battery.final_storage_wh": rows[-1]["eps.battery.storage_wh"],
                "eps.battery.energy_throughput_wh": energy_throughput_wh,
                "eps.battery.effective_capacity_wh": rows[-1]["eps.battery.effective_capacity_wh"],
                "eps.battery.nominal_charge_efficiency": rows[-1]["eps.battery.nominal_charge_efficiency"],
                "eps.battery.effective_charge_efficiency": rows[-1]["eps.battery.effective_charge_efficiency"],
                "eps.battery.nominal_discharge_efficiency": rows[-1]["eps.battery.nominal_discharge_efficiency"],
                "eps.battery.effective_discharge_efficiency": rows[-1]["eps.battery.effective_discharge_efficiency"],
            },
            "events": {
                "fault_count": fault_count,
                "degradation_count": 1 if degradation else 0,
            },
            "trace_rows": len(rows),
        }
        labels = {"fault_labels": self._fault_label_rows(task_id, case_id, faults), "run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated component.battery.v1 capability script.

This script is generated from a validated TaskSpec. It executes the explicit
BatteryAdapter, which calls components.battery model classes and does not use
legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.component_battery import BatteryAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = BatteryAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "battery_capability_output")))
    dataset = write_task_dataset(
        output_root=output_root,
        compiled=compiled,
        task_spec=TASK_SPEC,
        summary=result.summary,
        trace_rows=result.trace_rows,
        status="complete",
    )
    print(json.dumps({{"ok": True, "summary": result.summary, "dataset": dataset.to_dict()}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if capability and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        from sat_sim.outputs.standard_fields import BATTERY_TRACE_SCHEMA
        return {"trace": BATTERY_TRACE_SCHEMA}

    @staticmethod
    def _battery_degradation_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
        d = spec.get("degradations") if isinstance(spec.get("degradations"), Mapping) else {}
        eps = d.get("eps") if isinstance(d.get("eps"), Mapping) else {}
        batt = eps.get("battery") if isinstance(eps.get("battery"), Mapping) else {}
        out = dict(batt)
        for effect in runtime_effects(spec, "degradation"):
            if effect.effect_id == "capacity_loss_pct":
                out["capacity_loss_pct"] = effect_parameter(effect, "value_pct", "capacity_loss_pct", default=30.0)
            elif effect.effect_id == "internal_resistance_increase_pct":
                out["internal_resistance_increase_pct"] = effect_parameter(effect, "value_pct", "internal_resistance_increase_pct", default=30.0)
        return out

    @staticmethod
    def _runtime_fault_payloads(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        effects = runtime_effects(spec, "fault")
        if effects:
            out: list[dict[str, Any]] = []
            for effect in effects:
                magnitude = effect.magnitude if effect.magnitude is not None else (1.0 - effect.scale if effect.scale is not None else 1.0)
                out.append({
                    "fault_id": effect.effect_id,
                    "target": effect.target,
                    "target_type": "battery",
                    "fault_type": effect.effect_id,
                    "onset_time_s": effect.onset_time_s,
                    "duration_s": effect.duration_s,
                    "magnitude": max(0.0, min(1.0, float(magnitude))),
                    "parameters": dict(effect.parameters),
                })
            return out
        return [dict(f) for f in spec.get("faults", []) or [] if isinstance(f, Mapping)]

    @staticmethod
    def _power_profile(params: Mapping[str, Any], duration_s: float, sample_s: float) -> list[float]:
        n_steps = max(1, int(math.ceil(duration_s / sample_s)))
        if isinstance(params.get("power_profile_w"), list) and params["power_profile_w"]:
            raw = [float(x) for x in params["power_profile_w"]]
        else:
            raw = [float(params.get("net_power_w", -20.0))]
        if len(raw) >= n_steps:
            return raw[:n_steps]
        return raw + [raw[-1]] * (n_steps - len(raw))

    @staticmethod
    def _fault_active(fault: Mapping[str, Any], time_s: float) -> bool:
        onset = float(fault.get("onset_time_s", 0.0))
        duration = float(fault.get("duration_s", -1.0))
        if time_s < onset:
            return False
        if duration == -1.0:
            return True
        return time_s < onset + duration

    @classmethod
    def _active_faults(cls, faults: Sequence[Mapping[str, Any]], time_s: float) -> list[Mapping[str, Any]]:
        return [fault for fault in faults if cls._fault_active(fault, time_s)]

    @classmethod
    def _faults_active(cls, faults: Sequence[Mapping[str, Any]], time_s: float) -> bool:
        return any(cls._fault_active(fault, time_s) for fault in faults)

    @staticmethod
    def _fault_label_rows(task_id: str, case_id: str, faults: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fault in faults:
            onset = float(fault.get("onset_time_s", 0.0))
            duration = float(fault.get("duration_s", -1.0))
            rows.append({
                "event_id": fault.get("fault_id") or f"fault_{len(rows)}",
                "task_id": task_id,
                "case_id": case_id,
                "target": fault.get("target", "battery"),
                "target_type": fault.get("target_type", "battery"),
                "event_type": "fault",
                "fault_type": fault.get("fault_type"),
                "degradation_type": None,
                "onset_time_s": onset,
                "end_time_s": None if duration == -1.0 else onset + duration,
                "magnitude": fault.get("magnitude"),
                "label": fault.get("label") or f"eps.battery.{fault.get('fault_type')}",
            })
        return rows


__all__ = ["BatteryAdapter"]
