"""Explicit adapter for ``subsystem.adcs.basic_rw_pointing.v1``.

P8-A intentionally implements a low-order, deterministic ADCS pointing model
backed by reaction-wheel actuation.  It is an Agent-facing capability boundary,
not a full FSW stack and not a Basilisk message graph.  The adapter does not call
legacy demo runners.
"""
from __future__ import annotations

import json
import math
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue


class AdcsBasicRwPointingAdapter:
    """Production adapter for a minimal reaction-wheel ADCS pointing capability."""

    capability_id = "subsystem.adcs.basic_rw_pointing.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "ADCS pointing capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "subsystem" or target.get("name") not in {"adcs", "adcs_basic"}:
            issues.append(ValidationIssue("error", "$.target", "ADCS pointing capability requires target.level='subsystem' and target.name='adcs'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "saturation"}:
            issues.append(ValidationIssue("error", "$.target.mode", "ADCS basic pointing supports nominal/saturation modes", "capability"))

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
            "spacecraft_inertia_kg_m2",
            "wheel_inertia_kg_m2",
            "max_rw_torque_nm",
            "max_wheel_speed_rad_s",
            "control_kp_nm_per_rad",
            "pointing_requirement_deg",
        }
        nonnegative = {
            "control_kd_nm_per_rad_s",
            "initial_pointing_error_deg",
            "initial_rate_deg_s",
            "disturbance_torque_nm",
            "rw_idle_power_w",
            "rw_power_per_torque_w",
        }
        for key in sorted(positive):
            if key in params and not self._is_number(params[key], positive=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in sorted(nonnegative):
            if key in params and not self._is_number(params[key], nonnegative=True):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        if "num_reaction_wheels" in params:
            value = params["num_reaction_wheels"]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= int(value) <= 8:
                issues.append(ValidationIssue("error", "$.parameters.num_reaction_wheels", "must be an integer in [1, 8]", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}

        duration_s = float(sim.get("duration_s", 600.0))
        sample_s = float(sim.get("sample_s", 5.0))
        task_id = str(spec.get("task_id", "adcs_basic_rw_pointing_task"))
        case_id = str(metadata.get("case_id", "case_000"))

        inertia = float(params.get("spacecraft_inertia_kg_m2", 12.0))
        num_wheels = int(params.get("num_reaction_wheels", 3))
        wheel_inertia = float(params.get("wheel_inertia_kg_m2", 0.08))
        max_torque = float(params.get("max_rw_torque_nm", 0.03))
        max_speed = float(params.get("max_wheel_speed_rad_s", 900.0))
        kp = float(params.get("control_kp_nm_per_rad", 0.08))
        kd = float(params.get("control_kd_nm_per_rad_s", 0.55))
        pointing_requirement_deg = float(params.get("pointing_requirement_deg", 1.0))
        disturbance = float(params.get("disturbance_torque_nm", 0.0))
        idle_power_w = float(params.get("rw_idle_power_w", 2.0))
        power_per_torque_w = float(params.get("rw_power_per_torque_w", 80.0))

        error_rad = math.radians(float(params.get("initial_pointing_error_deg", 8.0)))
        rate_rad_s = math.radians(float(params.get("initial_rate_deg_s", 0.0)))
        wheel_speed = float(params.get("initial_wheel_speed_rad_s", 80.0))

        rows: list[dict[str, Any]] = []
        max_abs_error_deg = abs(math.degrees(error_rad))
        max_abs_rate_rad_s = abs(rate_rad_s)
        saturation_count = 0
        settled_time_s: float | None = None
        initial_error_deg = abs(math.degrees(error_rad))
        initial_wheel_speed = wheel_speed
        torque_limit = max_torque * max(1, num_wheels)

        n_samples = max(1, int(math.floor(duration_s / sample_s)) + 1)
        for i in range(n_samples):
            time_s = min(float(i) * sample_s, duration_s)
            command_torque = -kp * error_rad - kd * rate_rad_s
            applied_torque = max(-torque_limit, min(torque_limit, command_torque))
            torque_saturated = abs(command_torque - applied_torque) > 1.0e-12
            wheel_torque_each = -applied_torque / max(1, num_wheels)
            rw_power_w = idle_power_w * num_wheels + power_per_torque_w * abs(applied_torque)

            error_deg = math.degrees(error_rad)
            abs_error_deg = abs(error_deg)
            max_abs_error_deg = max(max_abs_error_deg, abs_error_deg)
            max_abs_rate_rad_s = max(max_abs_rate_rad_s, abs(rate_rad_s))
            speed_rpm = wheel_speed * 60.0 / (2.0 * math.pi)
            wheel_speed_saturated = abs(wheel_speed) >= max_speed - 1.0e-9
            saturated = bool(torque_saturated or wheel_speed_saturated)
            if saturated:
                saturation_count += 1
            if settled_time_s is None and abs_error_deg <= pointing_requirement_deg:
                settled_time_s = time_s
            pointing_state = "acquired" if abs_error_deg <= pointing_requirement_deg else "converging"
            health_state = "saturated" if saturated else ("nominal" if pointing_state == "acquired" else "degraded")

            row = {
                "task_id": task_id,
                "case_id": case_id,
                "time_s": round(time_s, 12),
                "sample_index": i,
                "target_level": "subsystem",
                "target_name": "adcs",
                "mode": mode,
                "adcs.attitude.error_deg": error_deg,
                "adcs.pointing.error_deg": abs_error_deg,
                "adcs.rate.omega_bn_b_rad_s_0": rate_rad_s,
                "adcs.control.command_torque_nm": command_torque,
                "adcs.control.applied_torque_nm": applied_torque,
                "adcs.control.disturbance_torque_nm": disturbance,
                "adcs.rw.num_wheels": num_wheels,
                "adcs.rw.torque_nm_0": wheel_torque_each,
                "adcs.rw.speed_rad_s_0": wheel_speed,
                "adcs.rw.speed_rpm_0": speed_rpm,
                "adcs.rw.max_abs_speed_rad_s": abs(wheel_speed),
                "adcs.rw.saturation_flag": saturated,
                "adcs.power.rw_power_w": rw_power_w,
                "label.pointing_state": pointing_state,
                "label.health_state": health_state,
                "label.rw_saturation": saturated,
            }
            rows.append(row)

            if i == n_samples - 1:
                break
            dt = min(sample_s, max(0.0, duration_s - time_s))
            if dt <= 0:
                break
            accel = (applied_torque + disturbance) / inertia
            rate_rad_s += accel * dt
            error_rad += rate_rad_s * dt
            wheel_speed += (wheel_torque_each / wheel_inertia) * dt
            if wheel_speed > max_speed:
                wheel_speed = max_speed
            elif wheel_speed < -max_speed:
                wheel_speed = -max_speed

        final = rows[-1]
        final_error_deg = float(final["adcs.pointing.error_deg"])
        final_wheel_speed = float(final["adcs.rw.speed_rad_s_0"])
        final_state = str(final["label.pointing_state"])
        health_state = "saturated" if saturation_count else ("nominal" if final_state == "acquired" else "degraded")
        summary = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "task_id": task_id,
            "status": "complete",
            "trace_rows": len(rows),
            "mode": mode,
            "qoi.adcs.initial_pointing_error_deg": initial_error_deg,
            "qoi.adcs.final_pointing_error_deg": final_error_deg,
            "qoi.adcs.max_pointing_error_deg": max_abs_error_deg,
            "qoi.adcs.max_rate_rad_s": max_abs_rate_rad_s,
            "qoi.adcs.settled_time_s": settled_time_s,
            "qoi.adcs.pointing_requirement_deg": pointing_requirement_deg,
            "qoi.adcs.initial_wheel_speed_rad_s_0": initial_wheel_speed,
            "qoi.adcs.final_wheel_speed_rad_s_0": final_wheel_speed,
            "qoi.adcs.saturation_count": saturation_count,
            "label.health_state": health_state,
            "label.pointing_state": final_state,
            "label.rw_saturation": bool(saturation_count),
        }
        labels = {
            "health_state": health_state,
            "pointing_state": final_state,
            "rw_saturation": bool(saturation_count),
            "pointing_requirement_met": final_error_deg <= pointing_requirement_deg,
        }
        metadata_out = {
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "model_assumptions": [
                "single-axis linearized pointing error dynamics",
                "reaction-wheel torque is mapped to one equivalent control axis",
                "no sensor noise, actuator delay, flexible modes, or full FSW state machine",
            ],
            "parameters": {
                "spacecraft_inertia_kg_m2": inertia,
                "num_reaction_wheels": num_wheels,
                "max_rw_torque_nm": max_torque,
                "max_wheel_speed_rad_s": max_speed,
                "control_kp_nm_per_rad": kp,
                "control_kd_nm_per_rad_s": kd,
            },
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata_out)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.adcs.basic_rw_pointing.v1 capability script.

This deterministic script executes the explicit AdcsBasicRwPointingAdapter and
does not call legacy demo runner functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.subsystem_adcs_basic_rw_pointing import AdcsBasicRwPointingAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = AdcsBasicRwPointingAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "adcs_basic_rw_pointing_output")))
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
        from sat_sim.outputs.standard_fields import ADCS_BASIC_RW_POINTING_TRACE_SCHEMA
        return {"trace": ADCS_BASIC_RW_POINTING_TRACE_SCHEMA}

    @staticmethod
    def _is_number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        v = float(value)
        if positive and v <= 0:
            return False
        if nonnegative and v < 0:
            return False
        return True
