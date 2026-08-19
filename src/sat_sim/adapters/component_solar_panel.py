"""Explicit adapter for ``component.solar_panel.v1``.

This adapter maps TaskSpec parameters directly to the Python solar-panel model
functions in ``components.solar_panel.builder``. It intentionally avoids the
``components.solar_panel.runner`` demo functions.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapter_effects import effect_parameter, runtime_effects
from sat_sim.task_validator import ValidationIssue


class SolarPanelAdapter:
    """Production adapter for the solar-panel component capability."""

    capability_id = "component.solar_panel.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "solar-panel capability requires task_type='component'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "component" or target.get("name") != "solar_panel":
            issues.append(ValidationIssue("error", "$.target", "solar-panel capability requires target.level='component' and target.name='solar_panel'", "capability"))
        mode = target.get("mode", "nominal")
        if mode not in {"nominal", "fault", "degradation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "solar-panel capability supports nominal/fault/degradation", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))

        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        if "max_power_w" not in params:
            issues.append(ValidationIssue("error", "$.parameters.max_power_w", "is required by component.solar_panel.v1", "capability_required"))
        positive = ("max_power_w", "panel_count")
        ratios = ("efficiency", "shadow_factor", "deployment_fraction")
        for key in positive:
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in ratios:
            if key in params:
                value = params.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be within [0, 1]", "range"))
        for key in ("initial_normal_b", "sun_vector_b"):
            if key in params and not self._is_vector3(params.get(key)):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a 3-element numeric vector", "type"))
        if "sun_vector_profile_b" in params:
            profile = params.get("sun_vector_profile_b")
            if not isinstance(profile, list) or not profile or any(not self._is_vector3(v) for v in profile):
                issues.append(ValidationIssue("error", "$.parameters.sun_vector_profile_b", "must be a non-empty list of 3-element numeric vectors", "type"))
        if "shadow_profile" in params:
            profile = params.get("shadow_profile")
            if not isinstance(profile, list) or not profile:
                issues.append(ValidationIssue("error", "$.parameters.shadow_profile", "must be a non-empty numeric list", "type"))
            elif any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0 for v in profile):
                issues.append(ValidationIssue("error", "$.parameters.shadow_profile", "all values must be numeric ratios in [0, 1]", "range"))

        faults = self._runtime_fault_payloads(spec)
        if mode == "fault":
            supported = {"solar_panel_failure", "solar_panel_degradation", "deployment_failure"}
            if not faults:
                issues.append(ValidationIssue("error", "$.faults", "fault mode requires at least one solar-panel fault", "capability_fault"))
            for i, fault in enumerate(faults):
                if not isinstance(fault, Mapping):
                    continue
                ftype = fault.get("fault_type")
                if ftype not in supported:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].fault_type", f"unsupported for {self.capability_id}: {ftype!r}", "capability_fault"))
                target_type = fault.get("target_type")
                if target_type not in {None, "solar_panel"}:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].target_type", "must be 'solar_panel'", "capability_fault"))
        if mode == "degradation" and not self._solar_degradation_payload(spec):
            issues.append(ValidationIssue("error", "$.degradations.eps.solar_panel", "solar-panel degradation payload is required", "capability_degradation"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.solar_panel.degradation import SolarPanelDegradation
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
        duration_s = float(sim.get("duration_s", 1200.0))
        sample_s = float(sim.get("sample_s", 60.0))
        task_id = str(spec.get("task_id", "solar_panel_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"

        panel_count = int(params.get("panel_count", 1))
        deployment_fraction = self._clamp01(params.get("deployment_fraction", 1.0))
        degradation_payload = self._solar_degradation_payload(spec)
        degradation = SolarPanelDegradation(**degradation_payload) if degradation_payload else None
        nominal_max_power_w = float(params.get("max_power_w", 120.0)) * max(1, panel_count) * deployment_fraction
        nominal_efficiency = float(params.get("efficiency", 1.0))
        config = build_nominal_solar_panel_config(
            max_power_w=nominal_max_power_w,
            efficiency=nominal_efficiency,
            max_slew_rate_rad_s=float(params.get("max_slew_rate_rad_s", params.get("solar_tracking_max_slew_rate_rad_s", 0.0))),
            degradation=degradation,
        )
        state = SolarPanelState(self._unit3(params.get("initial_normal_b", (1.0, 0.0, 0.0))))
        tracking = bool(params.get("enable_tracking", False))
        faults = self._runtime_fault_payloads(spec)

        rows: list[dict[str, Any]] = []
        power_values: list[float] = []
        incidence_values: list[float] = []
        eclipse_samples = 0
        fault_samples = 0
        energy_wh = 0.0

        def row_at(time_s: float, sample_index: int, power_w: float, shadow: float, sun_b: tuple[float, float, float], fault_multiplier: float, fault_active: bool) -> dict[str, Any]:
            incidence = max(0.0, self._dot(self._unit3(state.normal_b), self._unit3(sun_b)))
            health = "fault_active" if fault_active else ("degraded" if degradation else "nominal")
            power_values.append(power_w)
            incidence_values.append(incidence)
            return {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": sample_index,
                "target_level": "component",
                "target_name": "solar_panel",
                "mode": mode,
                "eps.solar.panel_power_w": power_w / max(1, panel_count),
                "eps.solar.array_power_w": power_w,
                "eps.solar.nominal_max_power_w": nominal_max_power_w,
                "eps.solar.max_power_w": config.max_power_w,
                "eps.solar.nominal_efficiency": nominal_efficiency,
                "eps.solar.efficiency": config.efficiency,
                "eps.solar.panel_count": panel_count,
                "eps.solar.deployment_fraction": deployment_fraction,
                "eps.solar.incidence_cos": incidence,
                "eps.solar.shadow_factor": shadow,
                "eps.solar.fault_multiplier": fault_multiplier,
                "eps.solar.normal_b_x": state.normal_b[0],
                "eps.solar.normal_b_y": state.normal_b[1],
                "eps.solar.normal_b_z": state.normal_b[2],
                "eps.solar.sun_vector_b_x": sun_b[0],
                "eps.solar.sun_vector_b_y": sun_b[1],
                "eps.solar.sun_vector_b_z": sun_b[2],
                "label.health_state": health,
                "label.fault_active": bool(fault_active),
            }

        t = 0.0
        sample_index = 0
        shadow = self._shadow_factor(params, t, sample_index)
        sun_b = self._sun_vector(params, sample_index)
        base_power = compute_solar_power(config, state.normal_b, sun_b, shadow)
        multiplier, fault_active = self._fault_multiplier(faults, t)
        rows.append(row_at(0.0, 0, base_power * multiplier, shadow, sun_b, multiplier, fault_active))

        while t < duration_s - 1e-12:
            dt = min(sample_s, duration_s - t)
            shadow = self._shadow_factor(params, t, sample_index)
            sun_b = self._sun_vector(params, sample_index)
            if tracking:
                state, base_power = step_solar_tracking(state, config, sun_b, shadow, dt)
            else:
                base_power = compute_solar_power(config, state.normal_b, sun_b, shadow)
            multiplier, fault_active = self._fault_multiplier(faults, t)
            power_w = max(0.0, base_power * multiplier)
            if shadow <= 1e-12:
                eclipse_samples += 1
            if fault_active:
                fault_samples += 1
            energy_wh += power_w * dt / 3600.0
            t += dt
            sample_index += 1
            rows.append(row_at(t, sample_index, power_w, shadow, sun_b, multiplier, self._faults_active(faults, t)))

        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": "solar_panel",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "eps.solar.initial_array_power_w": power_values[0],
                "eps.solar.final_array_power_w": power_values[-1],
                "eps.solar.min_array_power_w": min(power_values),
                "eps.solar.max_array_power_w": max(power_values),
                "eps.solar.energy_generated_wh": energy_wh,
                "eps.solar.mean_incidence_cos": sum(incidence_values) / max(1, len(incidence_values)),
                "eps.solar.eclipse_fraction": eclipse_samples / max(1, len(rows) - 1),
                "eps.solar.nominal_max_power_w": nominal_max_power_w,
                "eps.solar.effective_max_power_w": config.max_power_w,
                "eps.solar.nominal_efficiency": nominal_efficiency,
                "eps.solar.effective_efficiency": config.efficiency,
            },
            "events": {
                "fault_count": len(faults),
                "degradation_count": 1 if degradation else 0,
                "fault_active_samples": fault_samples,
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
"""Generated component.solar_panel.v1 capability script.

This deterministic script executes the explicit SolarPanelAdapter and does not
call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.component_solar_panel import SolarPanelAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = SolarPanelAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "solar_panel_capability_output")))
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
        from sat_sim.outputs.standard_fields import SOLAR_PANEL_TRACE_SCHEMA
        return {"trace": SOLAR_PANEL_TRACE_SCHEMA}

    @staticmethod
    def _solar_degradation_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
        d = spec.get("degradations") if isinstance(spec.get("degradations"), Mapping) else {}
        eps = d.get("eps") if isinstance(d.get("eps"), Mapping) else {}
        solar = eps.get("solar_panel") if isinstance(eps.get("solar_panel"), Mapping) else {}
        out = dict(solar)
        for effect in runtime_effects(spec, "degradation"):
            if effect.effect_id == "efficiency_loss_pct":
                out["efficiency_loss_pct"] = effect_parameter(effect, "value_pct", "efficiency_loss_pct", default=20.0)
            elif effect.effect_id == "radiation_damage_factor":
                out["radiation_damage_factor"] = effect_parameter(effect, "factor", "radiation_damage_factor", default=0.2)
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
                    "target_type": "solar_panel",
                    "fault_type": effect.effect_id,
                    "onset_time_s": effect.onset_time_s,
                    "duration_s": effect.duration_s,
                    "magnitude": max(0.0, min(1.0, float(magnitude))),
                    "parameters": dict(effect.parameters),
                })
            return out
        return [dict(f) for f in spec.get("faults", []) or [] if isinstance(f, Mapping)]

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

    @classmethod
    def _fault_multiplier(cls, faults: Sequence[Mapping[str, Any]], time_s: float) -> tuple[float, bool]:
        multiplier = 1.0
        active = cls._active_faults(faults, time_s)
        for fault in active:
            ftype = str(fault.get("fault_type") or "")
            mag = cls._clamp01(fault.get("magnitude", 0.0))
            if ftype in {"solar_panel_failure", "solar_panel_degradation", "deployment_failure"}:
                multiplier *= 1.0 - mag
        return max(0.0, multiplier), bool(active)

    @staticmethod
    def _shadow_factor(params: Mapping[str, Any], time_s: float, sample_index: int) -> float:
        if isinstance(params.get("shadow_profile"), list) and params["shadow_profile"]:
            profile = params["shadow_profile"]
            return SolarPanelAdapter._clamp01(profile[min(sample_index, len(profile) - 1)])
        return SolarPanelAdapter._clamp01(params.get("shadow_factor", 1.0))

    @staticmethod
    def _sun_vector(params: Mapping[str, Any], sample_index: int) -> tuple[float, float, float]:
        if isinstance(params.get("sun_vector_profile_b"), list) and params["sun_vector_profile_b"]:
            profile = params["sun_vector_profile_b"]
            return SolarPanelAdapter._unit3(profile[min(sample_index, len(profile) - 1)])
        return SolarPanelAdapter._unit3(params.get("sun_vector_b", (1.0, 0.0, 0.0)))

    @staticmethod
    def _is_vector3(value: Any) -> bool:
        return isinstance(value, (list, tuple)) and len(value) == 3 and all((not isinstance(x, bool) and isinstance(x, (int, float))) for x in value)

    @staticmethod
    def _clamp01(value: Any) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _unit3(value: Any) -> tuple[float, float, float]:
        if not SolarPanelAdapter._is_vector3(value):
            return (1.0, 0.0, 0.0)
        x, y, z = (float(value[0]), float(value[1]), float(value[2]))
        n = math.sqrt(x * x + y * y + z * z)
        if n <= 1e-15:
            return (1.0, 0.0, 0.0)
        return (x / n, y / n, z / n)

    @staticmethod
    def _dot(a: Sequence[float], b: Sequence[float]) -> float:
        return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


__all__ = ["SolarPanelAdapter"]
