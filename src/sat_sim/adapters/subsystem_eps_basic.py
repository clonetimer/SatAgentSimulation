"""Explicit adapter for ``subsystem.eps.basic.v1``.

The adapter implements a deterministic EPS energy-balance simulation using the
project's battery, solar-panel and PDU model primitives. It intentionally avoids
``subsystems.eps.runner`` and other demo runner functions.

P6-B deepens the P6 EPS slice by making solar-array geometry/tracking and PDU
channel-level decisions explicit in the capability path.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapter_effects import effect_parameter, runtime_effects
from sat_sim.task_validator import ValidationIssue


class EpsBasicAdapter:
    """Production adapter for a basic EPS subsystem capability."""

    capability_id = "subsystem.eps.basic.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "EPS capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "subsystem" or target.get("name") != "eps":
            issues.append(ValidationIssue("error", "$.target", "EPS capability requires target.level='subsystem' and target.name='eps'", "capability"))
        mode = target.get("mode", "nominal")
        if mode not in {"nominal", "fault", "degradation", "mixed"}:
            issues.append(ValidationIssue("error", "$.target.mode", "EPS capability supports nominal/fault/degradation/mixed", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        duration = sim.get("duration_s")
        sample = sim.get("sample_s")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or float(duration) <= 0:
            issues.append(ValidationIssue("error", "$.simulation.duration_s", "must be a positive number", "range"))
        if isinstance(sample, bool) or not isinstance(sample, (int, float)) or float(sample) <= 0:
            issues.append(ValidationIssue("error", "$.simulation.sample_s", "must be a positive number", "range"))

        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        required = ("battery_capacity_wh", "initial_soc", "solar_array_max_power_w")
        for key in required:
            if key not in params:
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "is required by subsystem.eps.basic.v1", "capability_required"))
        positive_keys = ("battery_capacity_wh", "solar_array_max_power_w", "solar_panel_count")
        nonnegative_keys = (
            "bus_load_power_w", "payload_load_power_w", "adcs_load_power_w", "comm_load_power_w", "heater_load_power_w",
            "eclipse_duration_s", "pdu_bus_max_w", "solar_tracking_max_slew_rate_rad_s",
        )
        ratio_keys = (
            "initial_soc", "solar_array_efficiency", "solar_incidence_cos", "pdu_efficiency",
            "solar_deployment_fraction", "payload_min_soc", "comm_min_soc", "heater_min_soc", "adcs_min_soc", "load_shed_soc_threshold",
        )
        for key in positive_keys:
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in nonnegative_keys:
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        for key in ratio_keys:
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be within [0, 1]", "range"))
        for key in ("load_profile_w", "shadow_profile"):
            if key in params:
                value = params.get(key)
                if not isinstance(value, list) or not value:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-empty numeric list", "type"))
                elif any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "all values must be numeric", "type"))
        if "shadow_profile" in params and isinstance(params.get("shadow_profile"), list):
            for i, value in enumerate(params["shadow_profile"]):
                if isinstance(value, (int, float)) and not 0.0 <= float(value) <= 1.0:
                    issues.append(ValidationIssue("error", f"$.parameters.shadow_profile[{i}]", "must be within [0, 1]", "range"))
        for key in ("solar_initial_normal_b", "sun_vector_b"):
            if key in params and not self._is_vector3(params.get(key)):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a 3-element numeric vector", "type"))
        if "sun_vector_profile_b" in params:
            profile = params.get("sun_vector_profile_b")
            if not isinstance(profile, list) or not profile or any(not self._is_vector3(v) for v in profile):
                issues.append(ValidationIssue("error", "$.parameters.sun_vector_profile_b", "must be a non-empty list of 3-element numeric vectors", "type"))

        faults = self._runtime_fault_payloads(spec)
        supported = {
            "battery": {"open_circuit", "sudden_capacity_loss"},
            "solar_panel": {"solar_panel_failure", "solar_panel_degradation", "deployment_failure"},
        }
        for i, fault in enumerate(faults):
            if not isinstance(fault, Mapping):
                continue
            target_type = str(fault.get("target_type") or fault.get("target") or "")
            ftype = str(fault.get("fault_type") or "")
            if target_type not in supported:
                issues.append(ValidationIssue("error", f"$.faults[{i}].target_type", "must be 'battery' or 'solar_panel'", "capability_fault"))
            elif ftype not in supported[target_type]:
                issues.append(ValidationIssue("error", f"$.faults[{i}].fault_type", f"unsupported for {target_type}: {ftype!r}", "capability_fault"))
        for i, effect in enumerate(runtime_effects(spec, "degradation")):
            if effect.onset_time_s > 0.0:
                issues.append(ValidationIssue(
                    "error",
                    f"$.modifiers.degradations[{i}].onset_time_s",
                    "subsystem.eps.basic.v1 degradations are build-time effects and must start at 0 s",
                    "capability_degradation_timing",
                ))
            if effect.effect_id not in {
                "capacity_loss_pct", "internal_resistance_increase_pct",
                "efficiency_loss_pct", "radiation_damage_factor", "pdu_efficiency_loss_pct",
            }:
                issues.append(ValidationIssue("error", f"$.modifiers.degradations[{i}]", f"unsupported EPS degradation effect {effect.effect_id!r}", "capability_degradation"))
        if mode in {"degradation", "mixed"} and not self._eps_degradation_payload(spec):
            issues.append(ValidationIssue("warning", "$.degradations.eps", "EPS degradation mode has no eps degradation payload", "capability_degradation"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.battery.model import build_nominal_battery_config, initialize_battery, step_battery
        from components.battery.degradation import BatteryDegradation
        from components.solar_panel.degradation import SolarPanelDegradation
        from components.pdu.builder import apply_load_shedding, build_nominal_pdu_config
        from components.solar_panel.builder import (
            SolarPanelState,
            build_nominal_solar_panel_config,
            compute_solar_power,
            step_solar_tracking,
        )

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 1800.0))
        sample_s = float(sim.get("sample_s", 10.0))
        task_id = str(spec.get("task_id", "eps_basic_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"

        eps_deg = self._eps_degradation_payload(spec)
        batt_deg_payload = eps_deg.get("battery") if isinstance(eps_deg.get("battery"), Mapping) else {}
        solar_deg_payload = eps_deg.get("solar_panel") if isinstance(eps_deg.get("solar_panel"), Mapping) else {}
        pdu_eff_loss_pct = float(eps_deg.get("pdu_efficiency_loss_pct", 0.0) or 0.0)
        battery_deg = BatteryDegradation(**dict(batt_deg_payload)) if batt_deg_payload else None
        solar_deg = SolarPanelDegradation(**dict(solar_deg_payload)) if solar_deg_payload else None

        battery_config = build_nominal_battery_config(
            capacity_wh=float(params.get("battery_capacity_wh", 160.0)),
            initial_soc=float(params.get("initial_soc", 0.7)),
            charge_efficiency=float(params.get("battery_charge_efficiency", 0.98)),
            discharge_efficiency=float(params.get("battery_discharge_efficiency", 0.97)),
            min_soc=float(params.get("min_soc", 0.0)),
            max_soc=float(params.get("max_soc", 1.0)),
            degradation=battery_deg,
        )
        battery_state = initialize_battery(battery_config)
        nominal_capacity_wh = float(params.get("battery_capacity_wh", 160.0))
        panel_count = int(params.get("solar_panel_count", params.get("panel_count", 1)))
        deployment_fraction = self._clamp01(params.get("solar_deployment_fraction", params.get("deployment_fraction", 1.0)))
        solar_config = build_nominal_solar_panel_config(
            max_power_w=float(params.get("solar_array_max_power_w", 120.0)) * max(1, panel_count) * deployment_fraction,
            efficiency=float(params.get("solar_array_efficiency", 1.0)),
            max_slew_rate_rad_s=float(params.get("solar_tracking_max_slew_rate_rad_s", params.get("max_slew_rate_rad_s", 0.0))),
            degradation=solar_deg,
        )
        solar_state = SolarPanelState(self._unit3(params.get("solar_initial_normal_b", params.get("panel_normal_b", (1.0, 0.0, 0.0)))))
        solar_tracking = bool(params.get("enable_solar_tracking", params.get("enable_tracking", False)))
        nominal_solar_efficiency = max(1e-12, float(params.get("solar_array_efficiency", 1.0)))
        solar_degradation_ratio = max(0.0, min(1.0, float(solar_config.efficiency) / nominal_solar_efficiency))
        pdu_efficiency = max(0.0, min(1.0, float(params.get("pdu_efficiency", 0.97)) * (1.0 - pdu_eff_loss_pct / 100.0)))
        pdu_bus_max_w = max(0.0, float(params.get("pdu_bus_max_w", 90.0)))
        pdu_config = build_nominal_pdu_config(bus_max_w=pdu_bus_max_w, shed_order=tuple(str(x) for x in params.get("shed_order", ["payload", "comm", "heater", "adcs"])))
        faults = self._runtime_fault_payloads(spec)
        load_shed_events = 0
        overload_events = 0
        power_margin_values: list[float] = []
        solar_energy_wh = 0.0
        load_served_energy_wh = 0.0
        load_unserved_energy_wh = 0.0
        active_capacity_loss_ids: set[str] = set()
        rows: list[dict[str, Any]] = []

        def make_row(time_s: float, sample_index: int, solar_w: float, req_w: float, served_w: float, shadow: float, incidence: float, fault_multiplier: float, fault_active: bool, pdu: dict[str, Any]) -> dict[str, Any]:
            margin_w = solar_w * pdu_efficiency - served_w
            applied_battery_power_w = margin_w
            for fault in self._active_faults(faults, time_s):
                target_type = str(fault.get("target_type") or fault.get("target") or "")
                if target_type == "battery" and str(fault.get("fault_type") or "") == "open_circuit":
                    applied_battery_power_w = 0.0
                    break
            unserved_w = max(0.0, req_w - served_w)
            power_margin_values.append(margin_w)
            if fault_active:
                health = "fault_active"
            elif pdu["shed"] or pdu["overload_remaining"]:
                health = "load_shed"
            elif battery_deg or solar_deg or pdu_eff_loss_pct:
                health = "degraded"
            else:
                health = "nominal"
            row = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": sample_index,
                "target_level": "subsystem",
                "target_name": "eps",
                "mode": mode,
                "eps.battery.soc": battery_state.soc,
                "eps.battery.storage_wh": battery_state.storage_wh,
                "eps.battery.capacity_wh": nominal_capacity_wh,
                "eps.battery.effective_capacity_wh": battery_state.capacity_wh,
                "eps.battery.net_power_w": solar_w * pdu_efficiency - served_w,
                "eps.battery.applied_power_w": applied_battery_power_w,
                "eps.battery.effective_charge_efficiency": float(battery_config.charge_efficiency),
                "eps.battery.effective_discharge_efficiency": float(battery_config.discharge_efficiency),
                "eps.solar.array_power_w": solar_w,
                "eps.solar.generated_power_w": solar_w,
                "eps.solar.effective_power_ratio": max(0.0, min(1.0, solar_degradation_ratio * fault_multiplier)),
                "eps.solar.panel_count": panel_count,
                "eps.solar.incidence_cos": incidence,
                "eps.solar.shadow_factor": shadow,
                "eps.solar.fault_multiplier": fault_multiplier,
                "eps.solar.normal_b_x": solar_state.normal_b[0],
                "eps.solar.normal_b_y": solar_state.normal_b[1],
                "eps.solar.normal_b_z": solar_state.normal_b[2],
                "eps.loads.requested_power_w": req_w,
                "eps.loads.served_power_w": served_w,
                "eps.loads.unserved_power_w": unserved_w,
                "eps.power.margin_w": margin_w,
                "eps.pdu.efficiency": pdu_efficiency,
                "eps.pdu.effective_efficiency": pdu_efficiency,
                "eps.pdu.bus_max_w": pdu_bus_max_w,
                "eps.pdu.load_shed_active": bool(pdu["shed"] or pdu["overload_remaining"]),
                "eps.pdu.shed_loads": "|".join(pdu["shed"]) if pdu["shed"] else "none",
                "eps.pdu.shed_reason": pdu["reason"],
                "eps.pdu.overload_remaining": bool(pdu["overload_remaining"]),
                "environment.shadow_factor": shadow,
                "label.health_state": health,
                "label.fault_active": bool(fault_active),
            }
            for name in ("bus", "payload", "adcs", "comm", "heater"):
                row[f"eps.loads.requested.{name}_w"] = float(pdu["requested_channels"].get(name, 0.0))
                row[f"eps.loads.served.{name}_w"] = float(pdu["served_channels"].get(name, 0.0))
            return row

        t = 0.0
        sample_index = 0
        initial_shadow = self._shadow_factor(params, 0.0, 0)
        initial_solar, initial_incidence, initial_multiplier, solar_state = self._solar_power_details(
            compute_solar_power, step_solar_tracking, solar_config, solar_state, params, initial_shadow, faults, 0.0, 0, 0.0, update_tracking=False
        )
        initial_req, initial_channels = self._requested_load(params, 0)
        initial_pdu = self._pdu_decision(params, pdu_config, initial_req, initial_channels, battery_state.soc)
        rows.append(make_row(0.0, 0, initial_solar, initial_req, initial_pdu["served_w"], initial_shadow, initial_incidence, initial_multiplier, self._faults_active(faults, 0.0), initial_pdu))

        while t < duration_s - 1e-12:
            dt = min(sample_s, duration_s - t)
            shadow = self._shadow_factor(params, t, sample_index)
            solar_w, incidence, fault_multiplier, solar_state = self._solar_power_details(
                compute_solar_power, step_solar_tracking, solar_config, solar_state, params, shadow, faults, t, sample_index, dt, update_tracking=solar_tracking
            )
            active_faults = self._active_faults(faults, t)
            _fault_active = bool(active_faults)
            requested_w, channels = self._requested_load(params, sample_index)
            pdu = self._pdu_decision(params, pdu_config, requested_w, channels, battery_state.soc)
            served_w = float(pdu["served_w"])
            if pdu["shed"] or pdu["overload_remaining"]:
                load_shed_events += 1
            if pdu["overload_remaining"]:
                overload_events += 1
            net_battery_power_w = solar_w * pdu_efficiency - served_w
            applied_battery_power_w = net_battery_power_w
            for fault in active_faults:
                target_type = str(fault.get("target_type") or fault.get("target") or "")
                ftype = str(fault.get("fault_type", ""))
                fid = str(fault.get("fault_id") or ftype)
                mag = float(fault.get("magnitude", 0.0))
                if target_type == "battery" and ftype == "open_circuit":
                    applied_battery_power_w = 0.0
                elif target_type == "battery" and ftype == "sudden_capacity_loss" and fid not in active_capacity_loss_ids:
                    new_capacity = max(0.0, battery_state.capacity_wh * (1.0 - mag))
                    new_storage = min(battery_state.storage_wh, new_capacity)
                    battery_state = replace(
                        battery_state,
                        capacity_wh=new_capacity,
                        storage_wh=new_storage,
                        soc=(new_storage / new_capacity if new_capacity > 0 else 0.0),
                    )
                    active_capacity_loss_ids.add(fid)
            battery_state = step_battery(battery_state, battery_config, applied_battery_power_w, dt)
            solar_energy_wh += solar_w * dt / 3600.0
            load_served_energy_wh += served_w * dt / 3600.0
            load_unserved_energy_wh += max(0.0, requested_w - served_w) * dt / 3600.0
            t += dt
            sample_index += 1
            rows.append(make_row(t, sample_index, solar_w, requested_w, served_w, shadow, incidence, fault_multiplier, self._faults_active(faults, t), pdu))

        soc_values = [float(r["eps.battery.soc"]) for r in rows]
        shed_fraction = load_shed_events / max(1, len(rows) - 1)
        solar_values = [float(r["eps.solar.array_power_w"]) for r in rows]
        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "subsystem",
            "target_name": "eps",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "eps.battery.initial_soc": soc_values[0],
                "eps.battery.final_soc": soc_values[-1],
                "eps.battery.min_soc": min(soc_values),
                "eps.battery.max_soc": max(soc_values),
                "eps.battery.final_storage_wh": rows[-1]["eps.battery.storage_wh"],
                "eps.power.min_margin_w": min(power_margin_values) if power_margin_values else 0.0,
                "eps.power.load_shed_fraction": shed_fraction,
                "eps.solar.energy_generated_wh": solar_energy_wh,
                "eps.solar.min_array_power_w": min(solar_values),
                "eps.solar.max_array_power_w": max(solar_values),
                "eps.loads.energy_served_wh": load_served_energy_wh,
                "eps.loads.energy_unserved_wh": load_unserved_energy_wh,
                "eps.loads.unserved_fraction": load_unserved_energy_wh / max(1e-12, load_served_energy_wh + load_unserved_energy_wh),
            },
            "events": {
                "fault_count": len(faults),
                "degradation_count": self._degradation_count(eps_deg),
                "load_shed_events": load_shed_events,
                "pdu_overload_events": overload_events,
            },
            "trace_rows": len(rows),
        }
        labels = {
            "run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}],
            "fault_labels": [dict(f) for f in faults],
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.eps.basic.v1 capability script.

This deterministic script executes the explicit EpsBasicAdapter and does not
call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.subsystem_eps_basic import EpsBasicAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = EpsBasicAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "eps_basic_capability_output")))
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
        from sat_sim.outputs.standard_fields import EPS_BASIC_TRACE_SCHEMA
        return {"trace": EPS_BASIC_TRACE_SCHEMA}

    @staticmethod
    def _eps_degradation_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
        d = spec.get("degradations") if isinstance(spec.get("degradations"), Mapping) else {}
        eps = d.get("eps") if isinstance(d.get("eps"), Mapping) else {}
        out = dict(eps)
        for effect in runtime_effects(spec, "degradation"):
            if effect.effect_id == "capacity_loss_pct":
                out.setdefault("battery", {})["capacity_loss_pct"] = effect_parameter(effect, "value_pct", "capacity_loss_pct", default=30.0)
            elif effect.effect_id == "internal_resistance_increase_pct":
                out.setdefault("battery", {})["internal_resistance_increase_pct"] = effect_parameter(effect, "value_pct", "internal_resistance_increase_pct", default=30.0)
            elif effect.effect_id == "efficiency_loss_pct":
                out.setdefault("solar_panel", {})["efficiency_loss_pct"] = effect_parameter(effect, "value_pct", "efficiency_loss_pct", default=20.0)
            elif effect.effect_id == "radiation_damage_factor":
                out.setdefault("solar_panel", {})["radiation_damage_factor"] = effect_parameter(effect, "factor", "radiation_damage_factor", default=0.2)
            elif effect.effect_id == "pdu_efficiency_loss_pct":
                out["pdu_efficiency_loss_pct"] = effect_parameter(effect, "value_pct", "pdu_efficiency_loss_pct", default=10.0)
        return out

    @staticmethod
    def _runtime_fault_payloads(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        effects = runtime_effects(spec, "fault")
        if effects:
            out: list[dict[str, Any]] = []
            for effect in effects:
                target = effect.target.lower()
                target_type = "solar_panel" if "solar" in target else "battery" if "battery" in target else target.rsplit(".", 1)[-1]
                magnitude = effect.magnitude
                if magnitude is None:
                    magnitude = max(0.0, 1.0 - float(effect.scale)) if effect.scale is not None else 1.0
                out.append({
                    "fault_id": effect.effect_id,
                    "target": effect.target,
                    "target_type": target_type,
                    "fault_type": effect.effect_id,
                    "onset_time_s": effect.onset_time_s,
                    "duration_s": effect.duration_s,
                    "magnitude": max(0.0, min(1.0, float(magnitude))),
                    "parameters": dict(effect.parameters),
                })
            return out
        return [dict(f) for f in spec.get("faults", []) or [] if isinstance(f, Mapping)]

    @staticmethod
    def _degradation_count(eps_deg: Mapping[str, Any]) -> int:
        count = 0
        for _key, value in eps_deg.items():
            if isinstance(value, Mapping):
                if any(float(v or 0.0) != 0.0 for v in value.values() if isinstance(v, (int, float))):
                    count += 1
            elif isinstance(value, (int, float)) and float(value) != 0.0:
                count += 1
        return count

    @staticmethod
    def _active_faults(faults: Sequence[Mapping[str, Any]], time_s: float) -> list[Mapping[str, Any]]:
        active: list[Mapping[str, Any]] = []
        for fault in faults:
            onset = float(fault.get("onset_time_s", 0.0))
            duration = float(fault.get("duration_s", -1.0))
            end = float("inf") if duration < 0 else onset + duration
            if onset <= time_s < end:
                active.append(fault)
        return active

    @classmethod
    def _faults_active(cls, faults: Sequence[Mapping[str, Any]], time_s: float) -> bool:
        return bool(cls._active_faults(faults, time_s))

    @staticmethod
    def _shadow_factor(params: Mapping[str, Any], time_s: float, sample_index: int) -> float:
        if isinstance(params.get("shadow_profile"), list) and params["shadow_profile"]:
            profile = params["shadow_profile"]
            return max(0.0, min(1.0, float(profile[min(sample_index, len(profile) - 1)])))
        period = float(params.get("eclipse_period_s", 5400.0) or 5400.0)
        eclipse_duration = max(0.0, float(params.get("eclipse_duration_s", 0.0) or 0.0))
        start = float(params.get("eclipse_start_s", 0.0) or 0.0)
        if period <= 0.0 or eclipse_duration <= 0.0:
            return 1.0
        phase = (time_s - start) % period
        return 0.0 if 0.0 <= phase < min(eclipse_duration, period) else 1.0

    @classmethod
    def _solar_power_details(cls, compute_solar_power: Any, step_solar_tracking: Any, solar_config: Any, solar_state: Any, params: Mapping[str, Any], shadow: float, faults: Sequence[Mapping[str, Any]], time_s: float, sample_index: int, dt_s: float, *, update_tracking: bool) -> tuple[float, float, float, Any]:
        sun_b = cls._sun_vector(params, sample_index)
        if update_tracking and dt_s > 0.0:
            solar_state, base = step_solar_tracking(solar_state, solar_config, sun_b, shadow, dt_s)
        else:
            base = compute_solar_power(solar_config, solar_state.normal_b, sun_b, shadow)
        incidence = max(0.0, cls._dot(cls._unit3(solar_state.normal_b), cls._unit3(sun_b)))
        multiplier = 1.0
        for fault in cls._active_faults(faults, time_s):
            target_type = str(fault.get("target_type") or fault.get("target") or "")
            if target_type != "solar_panel":
                continue
            ftype = str(fault.get("fault_type") or "")
            mag = max(0.0, min(1.0, float(fault.get("magnitude", 0.0))))
            if ftype in {"solar_panel_failure", "solar_panel_degradation", "deployment_failure"}:
                multiplier *= (1.0 - mag)
        return max(0.0, base * multiplier), incidence, max(0.0, multiplier), solar_state

    @staticmethod
    def _requested_load(params: Mapping[str, Any], sample_index: int) -> tuple[float, dict[str, float]]:
        channels = {
            "bus": max(0.0, float(params.get("bus_load_power_w", 25.0))),
            "payload": max(0.0, float(params.get("payload_load_power_w", 30.0))),
            "adcs": max(0.0, float(params.get("adcs_load_power_w", 8.0))),
            "comm": max(0.0, float(params.get("comm_load_power_w", 6.0))),
            "heater": max(0.0, float(params.get("heater_load_power_w", 0.0))),
        }
        # P9-B needs deterministic cross-subsystem coupling where child
        # capabilities can contribute dynamic per-channel loads without replacing
        # the whole EPS load model.  The generic load_profile_w remains supported
        # as a total-power override for backwards compatibility.
        for name in ("bus", "payload", "adcs", "comm", "heater"):
            profile = params.get(f"{name}_dynamic_power_profile_w")
            if isinstance(profile, list) and profile:
                channels[name] += max(0.0, float(profile[min(sample_index, len(profile) - 1)]))
        base_total = sum(channels.values())
        if isinstance(params.get("load_profile_w"), list) and params["load_profile_w"]:
            profile = params["load_profile_w"]
            total = max(0.0, float(profile[min(sample_index, len(profile) - 1)]))
            if base_total > 1e-15:
                scale = total / base_total
                channels = {k: v * scale for k, v in channels.items()}
            else:
                channels["bus"] = total
            return total, channels
        return base_total, channels

    @staticmethod
    def _pdu_decision(params: Mapping[str, Any], pdu_config: Any, requested_w: float, channels: Mapping[str, float], soc: float) -> dict[str, Any]:
        from components.pdu.builder import apply_load_shedding

        enable = bool(params.get("enable_load_shedding", True))
        requested_channels = {name: max(0.0, float(channels.get(name, 0.0))) for name in ("bus", "payload", "adcs", "comm", "heater")}
        active = dict(requested_channels)
        shed: list[str] = []
        reasons: list[str] = []
        if enable:
            thresholds = {
                "payload": float(params.get("payload_min_soc", params.get("load_shed_soc_threshold", 0.15))),
                "comm": float(params.get("comm_min_soc", params.get("load_shed_soc_threshold", 0.15))),
                "heater": float(params.get("heater_min_soc", params.get("load_shed_soc_threshold", 0.15))),
                "adcs": float(params.get("adcs_min_soc", params.get("load_shed_soc_threshold", 0.15))),
            }
            for name, threshold in thresholds.items():
                if soc < threshold and active.get(name, 0.0) > 0.0:
                    active[name] = 0.0
                    shed.append(name)
                    reasons.append(f"{name}_low_soc")
            result = apply_load_shedding(active, pdu_config)
            for name in result.shed:
                if name not in shed:
                    shed.append(name)
                active[name] = 0.0
                reasons.append(f"{name}_bus_limit")
            overload_remaining = bool(result.overload_remaining)
        else:
            overload_remaining = sum(active.values()) > float(pdu_config.bus_max_w)
        served_total = sum(active.values())
        if served_total > float(pdu_config.bus_max_w) > 0.0:
            scale = float(pdu_config.bus_max_w) / served_total
            active = {name: value * scale for name, value in active.items()}
            served_total = sum(active.values())
            overload_remaining = True
            reasons.append("bus_power_scaled")
        served_channels = {name: float(active.get(name, 0.0)) for name in requested_channels}
        if not reasons:
            reasons = ["none"]
        return {
            "served_w": served_total,
            "shed": shed,
            "reason": "|".join(reasons),
            "overload_remaining": overload_remaining,
            "requested_channels": requested_channels,
            "served_channels": served_channels,
        }

    @staticmethod
    def _sun_vector(params: Mapping[str, Any], sample_index: int) -> tuple[float, float, float]:
        if isinstance(params.get("sun_vector_profile_b"), list) and params["sun_vector_profile_b"]:
            profile = params["sun_vector_profile_b"]
            return EpsBasicAdapter._unit3(profile[min(sample_index, len(profile) - 1)])
        if "sun_vector_b" in params:
            return EpsBasicAdapter._unit3(params.get("sun_vector_b"))
        incidence = max(0.0, min(1.0, float(params.get("solar_incidence_cos", 1.0))))
        return EpsBasicAdapter._unit3((incidence, math.sqrt(max(0.0, 1.0 - incidence * incidence)), 0.0))

    @staticmethod
    def _is_vector3(value: Any) -> bool:
        return isinstance(value, (list, tuple)) and len(value) == 3 and all((not isinstance(x, bool) and isinstance(x, (int, float))) for x in value)

    @staticmethod
    def _unit3(value: Any) -> tuple[float, float, float]:
        if not EpsBasicAdapter._is_vector3(value):
            return (1.0, 0.0, 0.0)
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
        n = math.sqrt(x * x + y * y + z * z)
        if n <= 1e-15:
            return (1.0, 0.0, 0.0)
        return (x / n, y / n, z / n)

    @staticmethod
    def _dot(a: Sequence[float], b: Sequence[float]) -> float:
        return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])

    @staticmethod
    def _clamp01(value: Any) -> float:
        return max(0.0, min(1.0, float(value)))


__all__ = ["EpsBasicAdapter"]
