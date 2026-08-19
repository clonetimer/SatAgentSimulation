"""Explicit adapter for ``component.reaction_wheel.v1``.

The adapter maps TaskSpec parameters directly to the reaction wheel reference
model classes in ``components.reaction_wheel``. It does not call
``components.reaction_wheel.runner`` demo functions.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue


class ReactionWheelAdapter:
    """Production adapter for the reaction wheel component capability."""

    capability_id = "component.reaction_wheel.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "component":
            issues.append(ValidationIssue("error", "$.task_type", "reaction wheel capability requires task_type='component'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "component" or target.get("name") != "reaction_wheel":
            issues.append(ValidationIssue("error", "$.target", "reaction wheel capability requires target.level='component' and target.name='reaction_wheel'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation", "constraint"}:
            issues.append(ValidationIssue("error", "$.target.mode", "reaction wheel capability supports nominal/fault/degradation/constraint", "capability"))

        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))

        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        num_wheels = params.get("num_wheels", 1)
        if isinstance(num_wheels, bool) or not isinstance(num_wheels, int) or not 1 <= int(num_wheels) <= 8:
            issues.append(ValidationIssue("error", "$.parameters.num_wheels", "must be an integer in [1, 8]", "range"))
            num_wheels = 1
        for key in ("wheel_inertia_kg_m2", "max_motor_torque_nm", "max_speed_rad_s"):
            if key in params and not self._numeric_or_numeric_sequence(params[key], positive=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number or numeric list", "type"))
        if "damping_nms" in params and not self._numeric_or_numeric_sequence(params["damping_nms"], nonnegative=True):
            issues.append(ValidationIssue("error", "$.parameters.damping_nms", "must be a non-negative number or numeric list", "type"))
        if "initial_wheel_speeds_rad_s" in params and not self._numeric_or_numeric_sequence(params["initial_wheel_speeds_rad_s"]):
            issues.append(ValidationIssue("error", "$.parameters.initial_wheel_speeds_rad_s", "must be numeric or numeric list", "type"))
        if "command_torque_nm" in params and not self._numeric_or_numeric_sequence(params["command_torque_nm"]):
            issues.append(ValidationIssue("error", "$.parameters.command_torque_nm", "must be numeric or numeric list", "type"))
        if "torque_profile_nm" in params:
            profile = params["torque_profile_nm"]
            if not isinstance(profile, list) or not profile:
                issues.append(ValidationIssue("error", "$.parameters.torque_profile_nm", "must be a non-empty list", "type"))
            elif any(not self._numeric_or_numeric_sequence(item) for item in profile):
                issues.append(ValidationIssue("error", "$.parameters.torque_profile_nm", "items must be numeric or numeric lists", "type"))

        faults = self._normalized_faults(spec)
        if mode == "fault":
            supported = {"rw_jamming", "rw_motor_failure"}
            if not faults:
                issues.append(ValidationIssue("error", "$.faults", "fault mode requires at least one reaction wheel fault", "capability_fault"))
            for i, fault in enumerate(faults):
                if not isinstance(fault, Mapping):
                    continue
                ftype = fault.get("fault_type")
                if ftype not in supported:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].fault_type", f"unsupported for {self.capability_id}: {ftype!r}", "capability_fault"))
                target_type = fault.get("target_type")
                if target_type not in {None, "reaction_wheel"}:
                    issues.append(ValidationIssue("error", f"$.faults[{i}].target_type", "must be 'reaction_wheel'", "capability_fault"))
        constraints = self._normalized_constraints(spec)
        for i, constraint in enumerate(constraints):
            ctype = str(constraint.get("constraint_type") or "")
            if ctype not in {"reaction_wheel_speed_limit", "rw_speed_saturation", "reaction_wheel_saturation"}:
                issues.append(ValidationIssue("error", f"$.modifiers.constraints[{i}].constraint_type", f"unsupported for {self.capability_id}: {ctype!r}", "capability_constraint"))
        if mode == "degradation" and not self._rw_degradation_payload(spec):
            issues.append(ValidationIssue("error", "$.degradations.adcs.reaction_wheel", "reaction wheel degradation payload is required", "capability_degradation"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from components.reaction_wheel.degradation import ReactionWheelDegradation
        from components.reaction_wheel.degradation import apply_reaction_wheel_degradation
        from components.reaction_wheel.model import clamp_motor_torque, momentum_norm_nms, rotational_energy_j, step_wheel_speed
        from components.reaction_wheel.schemas import ReactionWheelDynamicsConfig, ReactionWheelState

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 60.0))
        sample_s = float(sim.get("sample_s", 1.0))
        task_id = str(spec.get("task_id", "reaction_wheel_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"
        num_wheels = int(params.get("num_wheels", 1))

        base_config = ReactionWheelDynamicsConfig(
            num_wheels=num_wheels,
            wheel_inertia_kg_m2=self._expand_tuple(params.get("wheel_inertia_kg_m2", 0.1), num_wheels, 0.1),
            max_motor_torque_nm=self._expand_tuple(params.get("max_motor_torque_nm", 0.02), num_wheels, 0.02),
            max_speed_rad_s=self._expand_tuple(params.get("max_speed_rad_s", 1000.0), num_wheels, 1000.0),
            damping_nms=self._expand_tuple(params.get("damping_nms", 1.0e-5), num_wheels, 1.0e-5),
            wheel_axes_B=self._default_axes(num_wheels),
        )
        degradation_payload = self._rw_degradation_payload(spec)
        degradation = ReactionWheelDegradation(**degradation_payload) if degradation_payload else None
        config = apply_reaction_wheel_degradation(base_config, degradation) if degradation else base_config
        state = ReactionWheelState(self._expand_tuple(params.get("initial_wheel_speeds_rad_s", 100.0), num_wheels, 100.0))
        faults = self._normalized_faults(spec)
        constraints = self._normalized_constraints(spec)
        torque_profile = self._torque_profile(params, duration_s, sample_s, num_wheels)

        rows: list[dict[str, Any]] = []
        speed_limit_hit_count = 0

        def effective_config_and_torque(time_s: float, command: tuple[float, ...]) -> tuple[Any, tuple[float, ...], bool, bool]:
            eff = config
            torque = list(command)
            active_faults = self._active_faults(faults, time_s)
            active_constraints = self._active_faults(constraints, time_s)
            for fault in active_faults:
                idx = self._fault_wheel_index(fault, num_wheels)
                mag = max(0.0, min(1.0, float(fault.get("magnitude", 1.0))))
                ftype = str(fault.get("fault_type", ""))
                if ftype == "rw_jamming":
                    torque[idx] = 0.0
                elif ftype == "rw_motor_failure":
                    torques = list(eff.max_motor_torque_nm)
                    torques[idx] = max(0.0, torques[idx] * (1.0 - 0.8 * mag))
                    eff = replace(eff, max_motor_torque_nm=tuple(torques))
            for constraint in active_constraints:
                idx = self._fault_wheel_index(constraint, num_wheels)
                mag = max(0.0, min(1.0, float(constraint.get("magnitude", constraint.get("severity", 1.0)) or 0.0)))
                ctype = str(constraint.get("constraint_type") or constraint.get("modifier_type") or "")
                if ctype in {"reaction_wheel_speed_limit", "rw_speed_saturation", "reaction_wheel_saturation"}:
                    params = constraint.get("parameters") if isinstance(constraint.get("parameters"), Mapping) else {}
                    explicit = params.get("max_speed_rad_s")
                    speeds = list(eff.max_speed_rad_s)
                    speeds[idx] = max(0.0, float(explicit)) if explicit is not None else max(0.0, speeds[idx] * (1.0 - 0.5 * mag))
                    eff = replace(eff, max_speed_rad_s=tuple(speeds))
            applied = clamp_motor_torque(tuple(torque), eff)
            return eff, tuple(applied), bool(active_faults), bool(active_constraints)

        def make_row(time_s: float, sample_index: int, command: tuple[float, ...], applied: tuple[float, ...], eff: Any, fault_active: bool, constraint_active: bool) -> dict[str, Any]:
            max_abs_speed = max(abs(float(x)) for x in state.wheel_speeds_rad_s) if state.wheel_speeds_rad_s else 0.0
            row = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(float(time_s), 12),
                "sample_index": sample_index,
                "target_level": "component",
                "target_name": "reaction_wheel",
                "mode": mode,
                "adcs.reaction_wheel.num_wheels": num_wheels,
                "adcs.reaction_wheel.max_abs_speed_rad_s": max_abs_speed,
                "adcs.reaction_wheel.momentum_norm_nms": momentum_norm_nms(state, eff),
                "adcs.reaction_wheel.rotational_energy_j": rotational_energy_j(state, eff),
                "adcs.reaction_wheel.nominal_max_speed_rad_s_0": base_config.max_speed_rad_s[0],
                "adcs.reaction_wheel.effective_max_speed_rad_s_0": eff.max_speed_rad_s[0],
                "adcs.reaction_wheel.nominal_max_torque_nm_0": base_config.max_motor_torque_nm[0],
                "adcs.reaction_wheel.effective_max_torque_nm_0": eff.max_motor_torque_nm[0],
                "adcs.reaction_wheel.nominal_damping_nms_0": base_config.damping_nms[0],
                "adcs.reaction_wheel.damping_nms_0": eff.damping_nms[0],
                "label.health_state": "fault_active" if fault_active else ("degraded" if degradation else "nominal"),
                "label.fault_active": bool(fault_active),
                "label.degradation_active": bool(degradation),
                "label.constraint_active": bool(constraint_active),
            }
            for i in range(min(num_wheels, 4)):
                row[f"adcs.reaction_wheel.speed_rad_s_{i}"] = state.wheel_speeds_rad_s[i]
                row[f"adcs.reaction_wheel.command_torque_nm_{i}"] = command[i]
                row[f"adcs.reaction_wheel.applied_torque_nm_{i}"] = applied[i]
            return row

        initial_command = torque_profile[0] if torque_profile else tuple(0.0 for _ in range(num_wheels))
        initial_eff, initial_applied, initial_fault_active, initial_constraint_active = effective_config_and_torque(0.0, initial_command)
        rows.append(make_row(0.0, 0, initial_command, initial_applied, initial_eff, initial_fault_active, initial_constraint_active))

        t = 0.0
        sample_index = 0
        while t < duration_s - 1e-12:
            dt = min(sample_s, duration_s - t)
            command = torque_profile[min(sample_index, len(torque_profile) - 1)] if torque_profile else tuple(0.0 for _ in range(num_wheels))
            eff, applied, fault_active, constraint_active = effective_config_and_torque(t, command)
            # A jamming fault clamps the wheel speed to zero while active.
            if fault_active:
                speeds = list(state.wheel_speeds_rad_s)
                for fault in self._active_faults(faults, t):
                    if str(fault.get("fault_type")) == "rw_jamming":
                        speeds[self._fault_wheel_index(fault, num_wheels)] = 0.0
                state = ReactionWheelState(tuple(speeds))
            state = step_wheel_speed(state, applied, eff, dt)
            # Enforce the configured physical speed limit.  Reaching the limit is
            # an operational constraint activation, not a hardware fault.
            state = ReactionWheelState(tuple(
                max(-eff.max_speed_rad_s[i], min(eff.max_speed_rad_s[i], state.wheel_speeds_rad_s[i]))
                for i in range(num_wheels)
            ))
            if any(abs(state.wheel_speeds_rad_s[i]) >= 0.999 * max(eff.max_speed_rad_s[i], 1e-15) for i in range(num_wheels)):
                speed_limit_hit_count += 1
            t += dt
            sample_index += 1
            next_command = torque_profile[min(sample_index, len(torque_profile) - 1)] if torque_profile else command
            next_eff, next_applied, next_fault_active, next_constraint_active = effective_config_and_torque(t, next_command)
            rows.append(make_row(t, sample_index, next_command, next_applied, next_eff, next_fault_active, next_constraint_active))

        speed0 = [float(row.get("adcs.reaction_wheel.speed_rad_s_0", 0.0)) for row in rows]
        constraint_active_sample_count = sum(1 for row in rows if row.get("label.constraint_active") is True)
        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "component",
            "target_name": "reaction_wheel",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "adcs.reaction_wheel.initial_speed_rad_s_0": speed0[0],
                "adcs.reaction_wheel.final_speed_rad_s_0": speed0[-1],
                "adcs.reaction_wheel.min_speed_rad_s_0": min(speed0),
                "adcs.reaction_wheel.max_speed_rad_s_0": max(speed0),
                "adcs.reaction_wheel.max_abs_speed_rad_s": max(float(r["adcs.reaction_wheel.max_abs_speed_rad_s"]) for r in rows),
                "adcs.reaction_wheel.final_momentum_norm_nms": rows[-1]["adcs.reaction_wheel.momentum_norm_nms"],
                "adcs.reaction_wheel.final_rotational_energy_j": rows[-1]["adcs.reaction_wheel.rotational_energy_j"],
                "adcs.reaction_wheel.nominal_max_torque_nm_0": rows[-1]["adcs.reaction_wheel.nominal_max_torque_nm_0"],
                "adcs.reaction_wheel.effective_max_torque_nm_0": rows[-1]["adcs.reaction_wheel.effective_max_torque_nm_0"],
                "adcs.reaction_wheel.nominal_max_speed_rad_s_0": rows[-1]["adcs.reaction_wheel.nominal_max_speed_rad_s_0"],
                "adcs.reaction_wheel.effective_max_speed_rad_s_0": rows[-1]["adcs.reaction_wheel.effective_max_speed_rad_s_0"],
                "adcs.reaction_wheel.nominal_damping_nms_0": rows[-1]["adcs.reaction_wheel.nominal_damping_nms_0"],
                "adcs.reaction_wheel.effective_damping_nms_0": rows[-1]["adcs.reaction_wheel.damping_nms_0"],
            },
            "events": {
                "fault_count": len(faults),
                "degradation_count": 1 if degradation else 0,
                "constraint_count": len(constraints),
                "constraint_active_sample_count": constraint_active_sample_count,
                "speed_limit_hit_sample_count": speed_limit_hit_count,
            },
            "trace_rows": len(rows),
        }
        labels = {
            "fault_labels": self._fault_label_rows(task_id, case_id, faults),
            "constraint_labels": self._constraint_label_rows(task_id, case_id, constraints),
            "run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}],
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id, "modifiers_applied_by_adapter": True})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated component.reaction_wheel.v1 capability script.

This deterministic script executes the explicit ReactionWheelAdapter and does
not call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.component_reaction_wheel import ReactionWheelAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = ReactionWheelAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "reaction_wheel_capability_output")))
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
        from sat_sim.outputs.standard_fields import REACTION_WHEEL_TRACE_SCHEMA
        return {"trace": REACTION_WHEEL_TRACE_SCHEMA}

    @staticmethod
    def _normalized_faults(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Accept legacy top-level faults and canonical modifier-delivered faults.

        The workbench stores canonical events under ``modifiers.faults``.  The
        component adapter owns the physical response, so it normalizes those
        events before validation and propagation instead of leaving them to the
        generic post-processing modifier layer.
        """
        out = [dict(item) for item in (spec.get("faults") or []) if isinstance(item, Mapping)]
        modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
        for item in modifiers.get("faults", []) or []:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("fault_id", payload.get("modifier_id"))
            payload.setdefault("fault_type", payload.get("type") or payload.get("effect"))
            payload.setdefault("magnitude", payload.get("severity", 1.0))
            payload.setdefault("target_type", "reaction_wheel")
            out.append(payload)
        return out

    @staticmethod
    def _normalized_constraints(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
        modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
        out: list[dict[str, Any]] = []
        for item in modifiers.get("constraints", []) or []:
            if not isinstance(item, Mapping):
                continue
            payload = dict(item)
            payload.setdefault("constraint_id", payload.get("modifier_id"))
            payload.setdefault("constraint_type", payload.get("type") or payload.get("effect"))
            payload.setdefault("magnitude", payload.get("severity", 1.0))
            payload.setdefault("target_type", "reaction_wheel")
            out.append(payload)
        return out

    @staticmethod
    def _rw_degradation_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
        d = spec.get("degradations") if isinstance(spec.get("degradations"), Mapping) else {}
        adcs = d.get("adcs") if isinstance(d.get("adcs"), Mapping) else {}
        rw = adcs.get("reaction_wheel") if isinstance(adcs.get("reaction_wheel"), Mapping) else {}
        payload = dict(rw)
        modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
        for item in modifiers.get("degradations", []) or []:
            if not isinstance(item, Mapping):
                continue
            effect = str(item.get("degradation_type") or item.get("modifier_type") or item.get("effect") or "")
            severity = max(0.0, float(item.get("severity", item.get("scale", 1.0)) or 0.0))
            if effect == "friction_increase_pct":
                payload["friction_increase_pct"] = max(float(payload.get("friction_increase_pct", 0.0)), min(100.0, 100.0 * severity))
            elif effect == "bearing_wear_factor":
                payload["bearing_wear_factor"] = max(float(payload.get("bearing_wear_factor", 0.0)), min(1.0, severity))
        return payload

    @staticmethod
    def _numeric_or_numeric_sequence(value: Any, *, positive: bool = False, nonnegative: bool = False) -> bool:
        values: list[Any]
        if isinstance(value, list):
            values = value
        else:
            values = [value]
        if not values:
            return False
        for item in values:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                return False
            if positive and float(item) <= 0:
                return False
            if nonnegative and float(item) < 0:
                return False
        return True

    @staticmethod
    def _expand_tuple(value: Any, count: int, default: float) -> tuple[float, ...]:
        if isinstance(value, list):
            raw = [float(x) for x in value]
        elif isinstance(value, tuple):
            raw = [float(x) for x in value]
        else:
            raw = [float(value)] if value is not None else [float(default)]
        if not raw:
            raw = [float(default)]
        if len(raw) >= count:
            return tuple(raw[:count])
        return tuple(raw + [raw[-1]] * (count - len(raw)))

    @staticmethod
    def _default_axes(count: int) -> tuple[tuple[float, float, float], ...]:
        base = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.577350269, 0.577350269, 0.577350269)]
        while len(base) < count:
            base.append((1.0, 0.0, 0.0))
        return tuple(base[:count])

    @classmethod
    def _vector(cls, value: Any, count: int, default: float) -> tuple[float, ...]:
        return cls._expand_tuple(value, count, default)

    @classmethod
    def _torque_profile(cls, params: Mapping[str, Any], duration_s: float, sample_s: float, count: int) -> list[tuple[float, ...]]:
        n_steps = max(1, int(math.ceil(duration_s / sample_s)))
        raw_profile = params.get("torque_profile_nm")
        if isinstance(raw_profile, list) and raw_profile:
            profile = [cls._vector(item, count, 0.0) for item in raw_profile]
        else:
            profile = [cls._vector(params.get("command_torque_nm", 0.01), count, 0.01)]
        if len(profile) >= n_steps:
            return profile[:n_steps]
        return profile + [profile[-1]] * (n_steps - len(profile))

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

    @staticmethod
    def _fault_wheel_index(fault: Mapping[str, Any], count: int) -> int:
        text = str(fault.get("target") or fault.get("target_id") or "rw_0")
        idx = 0
        for token in reversed(text.replace("-", "_").split("_")):
            if token.isdigit():
                idx = int(token)
                break
        return max(0, min(count - 1, idx))

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
                "target": fault.get("target", "rw_0"),
                "target_type": fault.get("target_type", "reaction_wheel"),
                "event_type": "fault",
                "fault_type": fault.get("fault_type"),
                "degradation_type": None,
                "onset_time_s": onset,
                "end_time_s": None if duration == -1.0 else onset + duration,
                "magnitude": fault.get("magnitude"),
                "label": fault.get("label") or f"adcs.reaction_wheel.{fault.get('fault_type')}",
            })
        return rows

    @staticmethod
    def _constraint_label_rows(task_id: str, case_id: str, constraints: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in constraints:
            onset = float(item.get("onset_time_s", 0.0))
            duration = float(item.get("duration_s", -1.0))
            rows.append({
                "event_id": item.get("constraint_id") or f"constraint_{len(rows)}",
                "task_id": task_id,
                "case_id": case_id,
                "target": item.get("target", "rw_0"),
                "target_type": item.get("target_type", "reaction_wheel"),
                "event_type": "constraint",
                "fault_type": None,
                "degradation_type": None,
                "constraint_type": item.get("constraint_type"),
                "onset_time_s": onset,
                "end_time_s": None if duration == -1.0 else onset + duration,
                "magnitude": item.get("magnitude"),
                "label": item.get("label") or "反作用轮达到速度限制",
            })
        return rows


__all__ = ["ReactionWheelAdapter"]
