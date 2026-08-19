"""Explicit adapter for ``subsystem.thermal.basic_lumped.v1``.

P9-A implements a deterministic low-order lumped thermal capability.  It is an
Agent-facing capability boundary, not a high-fidelity thermal network, finite
element model, or Basilisk message graph.  The adapter does not call legacy demo
runner functions.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapter_effects import effect_parameter, runtime_effects
from sat_sim.task_validator import ValidationIssue


class ThermalBasicLumpedAdapter:
    """Production adapter for a minimal bus/battery lumped thermal capability."""

    capability_id = "subsystem.thermal.basic_lumped.v1"
    STEFAN_BOLTZMANN = 5.670374419e-8

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "thermal capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "subsystem" or target.get("name") not in {"thermal", "thermal_basic"}:
            issues.append(ValidationIssue("error", "$.target", "thermal capability requires target.level='subsystem' and target.name='thermal'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "thermal basic supports nominal/fault/degradation modes", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        if isinstance(sim.get("duration_s"), (int, float)) and isinstance(sim.get("sample_s"), (int, float)):
            if float(sim["sample_s"]) > float(sim["duration_s"]):
                issues.append(ValidationIssue("warning", "$.simulation.sample_s", "sample_s is larger than duration_s; trace will contain one sample", "range"))

        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        positive = {
            "bus_thermal_capacity_j_k",
            "battery_thermal_capacity_j_k",
            "radiator_area_m2",
            "radiator_emissivity",
            "thermal_conductance_bus_battery_w_k",
        }
        nonnegative = {
            "internal_power_w",
            "battery_internal_power_w",
            "solar_heat_w",
            "heater_power_w",
            "heater_deadband_c",
        }
        for key in sorted(positive):
            if key in params and not self._is_number(params[key], positive=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in sorted(nonnegative):
            if key in params and not self._is_number(params[key], nonnegative=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        for key in ("initial_bus_temp_c", "initial_battery_temp_c", "sink_temp_c", "heater_setpoint_c", "bus_min_temp_c", "bus_max_temp_c", "battery_min_temp_c", "battery_max_temp_c"):
            if key in params and not self._is_number(params[key]):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a number", "range"))
        for key in ("shadow_factor", "radiator_degradation_factor"):
            if key in params and not self._is_number(params[key], min_value=0.0, max_value=1.0):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be in [0, 1]", "range"))
        for key in ("shadow_profile", "internal_power_profile_w"):
            value = params.get(key)
            if key in params and not self._is_number_list(value):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be an array of numbers", "type"))
        for i, effect in enumerate(runtime_effects(spec, "degradation")):
            if effect.effect_id != "radiator_degradation_factor":
                issues.append(ValidationIssue("error", f"$.modifiers.degradations[{i}]", f"unsupported thermal degradation effect {effect.effect_id!r}", "capability_degradation"))
            if effect.onset_time_s > 0.0:
                issues.append(ValidationIssue(
                    "error",
                    f"$.modifiers.degradations[{i}].onset_time_s",
                    "subsystem.thermal.basic_lumped.v1 degradation is a build-time effect and must start at 0 s",
                    "capability_degradation_timing",
                ))
        for i, effect in enumerate(runtime_effects(spec, "fault")):
            if effect.effect_id != "heating_or_rejection_failure":
                issues.append(ValidationIssue("error", f"$.modifiers.faults[{i}]", f"unsupported thermal fault effect {effect.effect_id!r}", "capability_fault"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}

        duration_s = float(sim.get("duration_s", 3600.0))
        sample_s = float(sim.get("sample_s", 60.0))
        task_id = str(spec.get("task_id", "thermal_basic_lumped_task"))
        case_id = str(metadata.get("case_id", "case_000"))

        bus_temp_c = float(params.get("initial_bus_temp_c", 20.0))
        battery_temp_c = float(params.get("initial_battery_temp_c", 18.0))
        bus_capacity = float(params.get("bus_thermal_capacity_j_k", 18000.0))
        battery_capacity = float(params.get("battery_thermal_capacity_j_k", 9000.0))
        internal_power_default = float(params.get("internal_power_w", 28.0))
        battery_internal_power = float(params.get("battery_internal_power_w", 3.0))
        solar_heat_w = float(params.get("solar_heat_w", 45.0))
        shadow_factor_default = float(params.get("shadow_factor", 1.0))
        sink_temp_c = float(params.get("sink_temp_c", -35.0))
        radiator_area = float(params.get("radiator_area_m2", 0.35))
        emissivity = float(params.get("radiator_emissivity", 0.82))
        radiator_deg = float(params.get("radiator_degradation_factor", 1.0))
        for effect in runtime_effects(spec, "degradation"):
            if effect.effect_id == "radiator_degradation_factor":
                radiator_deg = effect_parameter(effect, "factor", "degradation_factor", default=0.8)
        radiator_deg = max(0.0, min(1.0, radiator_deg))
        faults = runtime_effects(spec, "fault")
        conductance = float(params.get("thermal_conductance_bus_battery_w_k", 0.45))
        heater_power = float(params.get("heater_power_w", 20.0))
        heater_setpoint = float(params.get("heater_setpoint_c", 5.0))
        heater_deadband = float(params.get("heater_deadband_c", 2.0))
        bus_min = float(params.get("bus_min_temp_c", -10.0))
        bus_max = float(params.get("bus_max_temp_c", 45.0))
        battery_min = float(params.get("battery_min_temp_c", 0.0))
        battery_max = float(params.get("battery_max_temp_c", 40.0))

        shadow_profile = self._number_list(params.get("shadow_profile"))
        internal_power_profile = self._number_list(params.get("internal_power_profile_w"))
        eclipse_start = float(params.get("eclipse_start_s", 0.0))
        eclipse_duration = max(0.0, float(params.get("eclipse_duration_s", 0.0)))
        eclipse_period = max(1.0, float(params.get("eclipse_period_s", 5400.0)))

        n_samples = max(1, int(math.floor(duration_s / sample_s)) + 1)
        rows: list[dict[str, Any]] = []
        heater_energy_wh = 0.0
        radiator_energy_wh = 0.0
        max_radiator_w = 0.0
        hot_count = 0
        cold_count = 0
        bus_min_seen = bus_temp_c
        bus_max_seen = bus_temp_c
        batt_min_seen = battery_temp_c
        batt_max_seen = battery_temp_c

        for i in range(n_samples):
            time_s = min(float(i) * sample_s, duration_s)
            shadow_factor = self._profile_value(shadow_profile, i, shadow_factor_default)
            if not shadow_profile and eclipse_duration > 0.0:
                phase = (time_s - eclipse_start) % eclipse_period
                if 0.0 <= phase < eclipse_duration:
                    shadow_factor = 0.0
            shadow_factor = max(0.0, min(1.0, float(shadow_factor)))
            internal_power = max(0.0, self._profile_value(internal_power_profile, i, internal_power_default))
            solar_input = max(0.0, solar_heat_w * shadow_factor)
            fault_active = any(effect.active_at(time_s) for effect in faults)
            effective_radiator_deg = 0.0 if fault_active else radiator_deg
            heater_on = bus_temp_c <= heater_setpoint - heater_deadband and not fault_active
            heater_output = heater_power if heater_on else 0.0
            radiator_heat = self._radiator_heat_w(bus_temp_c, sink_temp_c, radiator_area, emissivity, effective_radiator_deg)
            conduction_bus_to_batt = conductance * (bus_temp_c - battery_temp_c)

            bus_hot = bus_temp_c > bus_max
            bus_cold = bus_temp_c < bus_min
            battery_hot = battery_temp_c > battery_max
            battery_cold = battery_temp_c < battery_min
            hot_flag = bool(bus_hot or battery_hot)
            cold_flag = bool(bus_cold or battery_cold)
            if hot_flag:
                hot_count += 1
            if cold_flag:
                cold_count += 1
            if hot_flag:
                thermal_state = "hot"
            elif cold_flag:
                thermal_state = "cold"
            else:
                thermal_state = "nominal"
            degradation_active = radiator_deg < 1.0 - 1e-12
            health_state = "fault" if fault_active else ("nominal" if thermal_state == "nominal" and not degradation_active else "degraded")

            bus_min_seen = min(bus_min_seen, bus_temp_c)
            bus_max_seen = max(bus_max_seen, bus_temp_c)
            batt_min_seen = min(batt_min_seen, battery_temp_c)
            batt_max_seen = max(batt_max_seen, battery_temp_c)
            max_radiator_w = max(max_radiator_w, radiator_heat)

            rows.append({
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(time_s, 12),
                "sample_index": i,
                "target_level": "subsystem",
                "target_name": "thermal",
                "mode": mode,
                "thermal.node.bus_temp_c": bus_temp_c,
                "thermal.node.battery_temp_c": battery_temp_c,
                "thermal.heat.internal_power_w": internal_power,
                "thermal.heat.battery_internal_power_w": battery_internal_power,
                "thermal.heat.solar_input_w": solar_input,
                "thermal.heater.power_w": heater_output,
                "thermal.radiator.heat_reject_w": radiator_heat,
                "thermal.radiator.rejected_heat_w": radiator_heat,
                "thermal.radiator.area_m2": radiator_area,
                "thermal.radiator.degradation_factor": effective_radiator_deg,
                "thermal.coupling.bus_battery_heat_w": conduction_bus_to_batt,
                "environment.shadow_factor": shadow_factor,
                "environment.eclipse_flag": shadow_factor <= 1.0e-9,
                "label.thermal_state": thermal_state,
                "label.thermal_hot_flag": hot_flag,
                "label.thermal_cold_flag": cold_flag,
                "label.fault_active": fault_active,
                "label.degradation_active": degradation_active,
                "label.health_state": health_state,
            })

            if i == n_samples - 1:
                break
            dt = min(sample_s, max(0.0, duration_s - time_s))
            if dt <= 0.0:
                break
            heater_energy_wh += heater_output * dt / 3600.0
            radiator_energy_wh += radiator_heat * dt / 3600.0
            bus_heat_w = internal_power + solar_input + heater_output - radiator_heat - conduction_bus_to_batt
            batt_heat_w = battery_internal_power + conduction_bus_to_batt
            bus_temp_c += bus_heat_w * dt / bus_capacity
            battery_temp_c += batt_heat_w * dt / battery_capacity

        final = rows[-1]
        final_state = str(final["label.thermal_state"])
        health_state = "nominal" if hot_count == 0 and cold_count == 0 and radiator_deg >= 1.0 - 1e-12 else "degraded"
        summary = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "task_id": task_id,
            "status": "complete",
            "trace_rows": len(rows),
            "mode": mode,
            "qoi.thermal.initial_bus_temp_c": float(rows[0]["thermal.node.bus_temp_c"]),
            "qoi.thermal.final_bus_temp_c": float(final["thermal.node.bus_temp_c"]),
            "qoi.thermal.min_bus_temp_c": bus_min_seen,
            "qoi.thermal.max_bus_temp_c": bus_max_seen,
            "qoi.thermal.initial_battery_temp_c": float(rows[0]["thermal.node.battery_temp_c"]),
            "qoi.thermal.final_battery_temp_c": float(final["thermal.node.battery_temp_c"]),
            "qoi.thermal.min_battery_temp_c": batt_min_seen,
            "qoi.thermal.max_battery_temp_c": batt_max_seen,
            "qoi.thermal.heater_energy_wh": heater_energy_wh,
            "qoi.thermal.radiator_energy_wh": radiator_energy_wh,
            "qoi.thermal.max_radiator_heat_reject_w": max_radiator_w,
            "qoi.thermal.hot_count": hot_count,
            "qoi.thermal.cold_count": cold_count,
            "label.health_state": health_state,
            "label.thermal_state": final_state,
        }
        labels = {
            "health_state": health_state,
            "thermal_state": final_state,
            "thermal_hot": bool(hot_count),
            "thermal_cold": bool(cold_count),
        }
        metadata_out = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "model_assumptions": [
                "two lumped nodes: spacecraft bus and battery",
                "solar heat input is a scalar profile controlled by shadow_factor/eclipse fields",
                "radiator heat rejection uses a simple Stefan-Boltzmann surface model",
                "no finite-element thermal network, attitude-dependent surface geometry, or full heater controller state machine",
            ],
            "parameters": {
                "bus_thermal_capacity_j_k": bus_capacity,
                "battery_thermal_capacity_j_k": battery_capacity,
                "internal_power_w": internal_power_default,
                "solar_heat_w": solar_heat_w,
                "radiator_area_m2": radiator_area,
                "heater_setpoint_c": heater_setpoint,
            },
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata_out)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.thermal.basic_lumped.v1 capability script.

This deterministic script executes the explicit ThermalBasicLumpedAdapter and
does not call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.subsystem_thermal_basic_lumped import ThermalBasicLumpedAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = ThermalBasicLumpedAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "thermal_basic_lumped_output")))
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
        from sat_sim.outputs.standard_fields import THERMAL_BASIC_LUMPED_TRACE_SCHEMA
        return {"trace": THERMAL_BASIC_LUMPED_TRACE_SCHEMA}

    @staticmethod
    def _is_number(value: Any, *, positive: bool = False, nonnegative: bool = False, min_value: float | None = None, max_value: float | None = None) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        v = float(value)
        if positive and v <= 0:
            return False
        if nonnegative and v < 0:
            return False
        if min_value is not None and v < min_value:
            return False
        if max_value is not None and v > max_value:
            return False
        return True

    @classmethod
    def _is_number_list(cls, value: Any) -> bool:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            return False
        return all(cls._is_number(item) for item in value)

    @classmethod
    def _number_list(cls, value: Any) -> list[float]:
        if not cls._is_number_list(value):
            return []
        return [float(item) for item in value]

    @staticmethod
    def _profile_value(profile: Sequence[float], index: int, default: float) -> float:
        if not profile:
            return float(default)
        if index < len(profile):
            return float(profile[index])
        return float(profile[-1])

    @classmethod
    def _radiator_heat_w(cls, bus_temp_c: float, sink_temp_c: float, area_m2: float, emissivity: float, degradation_factor: float) -> float:
        area = max(0.0, float(area_m2))
        emissivity = max(0.0, min(1.0, float(emissivity)))
        degradation = max(0.0, min(1.0, float(degradation_factor)))
        hot_k = max(1.0, float(bus_temp_c) + 273.15)
        sink_k = max(1.0, float(sink_temp_c) + 273.15)
        delta = max(0.0, hot_k ** 4 - sink_k ** 4)
        return cls.STEFAN_BOLTZMANN * emissivity * area * degradation * delta
