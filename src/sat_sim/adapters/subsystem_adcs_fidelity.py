"""Adapter for ``subsystem.adcs_fidelity.v1``."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adcs import (
    ADCS_FIDELITY_SCHEMA_VERSION,
    ADCSFidelityConfig,
    ADCSRuntimeEffects,
    build_adcs1_fidelity_payload,
    propagate_adcs_fidelity,
    summarize_adcs_fidelity,
    augment_trace_rows,
)
from sat_sim.task_validator import ValidationIssue


class AdcsFidelityAdapter:
    """ADCS-1 medium-fidelity/proxy ADCS adapter."""

    capability_id = "subsystem.adcs_fidelity.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "ADCS fidelity requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target and target.get("name") not in {None, "adcs", "adcs_fidelity", "adcs_closed_loop"}:
            issues.append(ValidationIssue("error", "$.target.name", "must target ADCS", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        try:
            ADCSFidelityConfig.from_task_spec(spec)
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = ADCSFidelityConfig.from_task_spec(spec)
        faults, degradations, constraints = self._normalized_events(spec)
        effect_resolver = self._effect_resolver(faults, degradations, constraints, config)
        samples = propagate_adcs_fidelity(config, effect_resolver=effect_resolver)
        summary = summarize_adcs_fidelity(samples, config)
        task_id = str(spec.get("task_id", "adcs_fidelity_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        rows_basic = tuple(
            sample.to_trace_row(
                task_id=task_id,
                case_id=case_id,
                capability_id=self.capability_id,
                pointing_requirement_deg=config.closed_loop.pointing_requirement_deg,
            )
            for sample in samples
        )
        rows_augmented = augment_trace_rows(rows_basic, config)
        rows = tuple(self._augment_event_evidence(row, config, effect_resolver(float(row.get("time_s", 0.0)))) for row in rows_augmented)
        active_sample_count = sum(1 for row in rows if row.get("label.fault_active") or row.get("label.degradation_active") or row.get("label.constraint_active"))
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "subsystem",
            "target_name": "adcs_fidelity",
            "status": "complete",
            "trace_rows": len(rows),
            "events": {
                "fault_count": len(faults),
                "degradation_count": len(degradations),
                "constraint_count": len(constraints),
                "active_sample_count": active_sample_count,
                "applied_by_adapter": True,
            },
        })
        adapter_metadata = {
            "capability_id": self.capability_id,
            "adcs1_fidelity": build_adcs1_fidelity_payload(spec),
            "schema_version": ADCS_FIDELITY_SCHEMA_VERSION,
        }
        mode = "mixed" if faults and degradations else "fault" if faults else "degradation" if degradations else "nominal"
        labels = {
            "run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "fidelity_level": "medium", "mode": mode}],
            "event_labels": [
                {
                    "effect": item.get("fault_type") or item.get("degradation_type") or item.get("constraint_type"),
                    "event_type": "fault" if item.get("fault_type") else ("degradation" if item.get("degradation_type") else "constraint"),
                    "target": item.get("target"),
                    "start_s": item.get("onset_time_s", 0.0),
                }
                for item in [*faults, *degradations, *constraints]
            ],
        }
        return SimulationResult(summary=summary, trace_rows=rows, labels=labels, metadata={**adapter_metadata, "modifiers_applied_by_adapter": True})

    @staticmethod
    def _normalized_events(spec: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        faults = [dict(item) for item in (spec.get("faults") or []) if isinstance(item, Mapping)]
        modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
        for item in modifiers.get("faults", []) or []:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("fault_type", payload.get("effect") or payload.get("type"))
            payload.setdefault("magnitude", payload.get("severity", 1.0))
            payload.setdefault("onset_time_s", payload.get("start_s", 0.0))
            faults.append(payload)
        degradations: list[dict[str, Any]] = []
        for item in modifiers.get("degradations", []) or []:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("degradation_type", payload.get("effect") or payload.get("type"))
            payload.setdefault("severity", payload.get("scale", 1.0))
            payload.setdefault("onset_time_s", payload.get("start_s", 0.0))
            degradations.append(payload)
        constraints: list[dict[str, Any]] = []
        for item in modifiers.get("constraints", []) or []:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("constraint_type", payload.get("effect") or payload.get("type"))
            payload.setdefault("severity", payload.get("magnitude", 1.0))
            payload.setdefault("onset_time_s", payload.get("start_s", 0.0))
            constraints.append(payload)
        return faults, degradations, constraints

    @staticmethod
    def _event_active(item: Mapping[str, Any], time_s: float) -> bool:
        onset = float(item.get("onset_time_s", item.get("start_s", 0.0)) or 0.0)
        duration = float(item.get("duration_s", -1.0) or -1.0)
        return time_s >= onset and (duration < 0.0 or time_s < onset + duration)

    @staticmethod
    def _axis_index(item: Mapping[str, Any], wheel_count: int = 3) -> int:
        params = item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}
        if isinstance(params.get("wheel_index"), int):
            return max(0, min(max(0, wheel_count - 1), int(params["wheel_index"])))
        text = str(item.get("target") or item.get("target_id") or "0")
        match = re.search(r"(\d+)$", text)
        return max(0, min(max(0, wheel_count - 1), int(match.group(1)) if match else 0))

    @classmethod
    def _effect_resolver(
        cls,
        faults: Sequence[Mapping[str, Any]],
        degradations: Sequence[Mapping[str, Any]],
        constraints: Sequence[Mapping[str, Any]],
        config: ADCSFidelityConfig,
    ):
        wheel_count = config.closed_loop.reaction_wheels.num_wheels
        nominal_max_speed = config.closed_loop.reaction_wheels.max_speed_rad_s

        def resolve(time_s: float) -> ADCSRuntimeEffects:
            torque_scale = [1.0] * wheel_count
            speed_scale = [1.0] * wheel_count
            bias_add = [0.0, 0.0, 0.0]
            drag = [0.0] * wheel_count
            noise_scale = 1.0
            jammed: set[int] = set()
            active: list[str] = []
            for item in [*faults, *degradations, *constraints]:
                if not cls._event_active(item, time_s):
                    continue
                effect = str(item.get("fault_type") or item.get("degradation_type") or item.get("constraint_type") or item.get("effect") or "")
                legacy_severity = max(0.0, min(1.0, float(item.get("magnitude", item.get("severity", 1.0)) or 0.0)))
                idx = cls._axis_index(item, wheel_count)
                params = item.get("parameters") if isinstance(item.get("parameters"), Mapping) else {}
                active.append(effect)
                if effect in {"adcs_rw_jamming", "rw_jamming"}:
                    # Binary fault: presence in the active time window is the
                    # complete physical instruction. Generic magnitude is ignored.
                    jammed.add(idx)
                    torque_scale[idx] = 0.0
                elif effect in {"adcs_reaction_wheel_speed_limit", "adcs_rw_speed_saturation", "reaction_wheel_speed_limit", "rw_speed_saturation"}:
                    if params.get("max_speed_rad_s") is not None:
                        ratio = float(params["max_speed_rad_s"]) / nominal_max_speed
                    elif params.get("limit_ratio") is not None:
                        ratio = float(params["limit_ratio"])
                    else:
                        ratio = max(0.05, 1.0 - 0.9 * legacy_severity)
                    speed_scale[idx] = min(speed_scale[idx], max(0.01, min(1.0, ratio)))
                elif effect in {"adcs_rw_motor_failure", "rw_motor_failure"}:
                    # Binary motor failure. Partial loss belongs to a degradation
                    # effect and is not encoded through a generic 0..1 magnitude.
                    torque_scale[idx] = 0.0
                elif effect == "adcs_rw_torque_authority_loss":
                    # Controlled degradation: retain a bounded fraction of the
                    # nominal actuator torque.  This must remain distinct from
                    # the binary motor-failure event to preserve label meaning.
                    requested = params.get("torque_scale", params.get("remaining_torque_ratio"))
                    if requested is None:
                        requested = max(0.05, 1.0 - 0.8 * legacy_severity)
                    ratio = max(0.0, min(1.0, float(requested)))
                    torque_scale[idx] = min(torque_scale[idx], ratio)
                elif effect in {"adcs_gyro_bias_step", "gyro_bias_step"}:
                    requested = params.get("bias_deg_s", [0.1, 0.0, 0.0])
                    if isinstance(requested, (list, tuple)) and len(requested) == 3:
                        bias_add[:] = [math.radians(float(v)) for v in requested]
                    else:
                        bias_add[min(idx, 2)] += math.radians(float(requested))
                elif effect in {"adcs_gyro_noise_increase", "gyro_noise_increase"}:
                    multiplier = float(params.get("noise_multiplier", 1.0 + 19.0 * legacy_severity))
                    noise_scale = max(noise_scale, max(1.0, multiplier))
                elif effect == "adcs_rw_friction_increase":
                    drag[idx] = max(drag[idx], max(0.0, float(params.get("drag_torque_nm", params.get("drag_nms", 0.002)))))
            return ADCSRuntimeEffects(
                axis_torque_scale=tuple(torque_scale), max_speed_scale=tuple(speed_scale),
                gyro_bias_add_rad_s=tuple(bias_add), gyro_noise_scale=noise_scale,
                wheel_drag_nms=tuple(drag), jammed_axes=tuple(sorted(jammed)), active_effects=tuple(active),
            )
        return resolve

    @staticmethod
    def _augment_event_evidence(row: Mapping[str, Any], config: ADCSFidelityConfig, effects: ADCSRuntimeEffects) -> dict[str, Any]:
        out = dict(row)
        out["label.fault_active"] = any(effect in {"adcs_rw_jamming", "rw_jamming", "adcs_rw_motor_failure", "rw_motor_failure", "adcs_gyro_bias_step", "gyro_bias_step"} for effect in effects.active_effects)
        out["label.degradation_active"] = any(effect in {"adcs_gyro_noise_increase", "gyro_noise_increase", "adcs_rw_friction_increase", "adcs_rw_torque_authority_loss"} for effect in effects.active_effects)
        out["label.constraint_active"] = any(effect in {"adcs_reaction_wheel_speed_limit", "adcs_rw_speed_saturation", "reaction_wheel_speed_limit", "rw_speed_saturation"} for effect in effects.active_effects)
        out["adcs.event.active_count"] = len(effects.active_effects)
        out["adcs.event.active_effects"] = ",".join(effects.active_effects)
        for i in range(config.closed_loop.reaction_wheels.num_wheels):
            out[f"adcs.rw.effective_max_torque_nm_{i}"] = config.closed_loop.reaction_wheels.max_torque_nm * effects.axis_torque_scale[i]
            out[f"adcs.rw.effective_max_speed_rad_s_{i}"] = config.closed_loop.reaction_wheels.max_speed_rad_s * effects.max_speed_scale[i]
            # ``effective_drag_nms`` is retained as a compatibility alias.
            # The local proxy applies this value as a constant opposing torque,
            # so the canonical observable is explicitly named in N*m.
            out[f"adcs.rw.effective_friction_torque_nm_{i}"] = effects.wheel_drag_nms[i]
            out[f"adcs.rw.effective_drag_nms_{i}"] = effects.wheel_drag_nms[i]
        out["adcs.sensor.effective_gyro_noise_scale"] = effects.gyro_noise_scale
        return out

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False)
        return f'''#!/usr/bin/env python3
"""Generated ADCS fidelity simulation script."""
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter

TASK_SPEC = {payload}

if __name__ == "__main__":
    result = AdcsFidelityAdapter().run(TASK_SPEC)
    print(result.to_json())
'''
