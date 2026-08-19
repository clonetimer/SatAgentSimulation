"""Adapter for ``whole_spacecraft.maneuver_orbit_attitude.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.coupled import (
    HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
    PropulsionOrbitAttitudeCoupledConfig,
    PropulsionOrbitAttitudeCoupledError,
    build_hf7_propulsion_orbit_attitude_payload,
    propagate_propulsion_orbit_attitude_coupled,
    summarize_propulsion_orbit_attitude_coupled,
)
from sat_sim.task_validator import ValidationIssue
from sat_sim.validation import evaluate_physical_validation, build_hf8_physical_validation_payload


class ManeuverOrbitAttitudeAdapter:
    """HF-7 Route-B maneuver/orbit/attitude coupled adapter."""

    capability_id = "whole_spacecraft.maneuver_orbit_attitude.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "HF-7 capability requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target.get("level") != "whole_spacecraft" or target.get("name") != "maneuver_orbit_attitude":
            issues.append(ValidationIssue("error", "$.target", "HF-7 requires target.level='whole_spacecraft' and target.name='maneuver_orbit_attitude'", "capability"))
        if str(target.get("mode") or "nominal") not in {"nominal", "mixed", "degradation", "fault"}:
            issues.append(ValidationIssue("error", "$.target.mode", "HF-7 supports nominal/mixed/degradation/fault modes", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        for key in ("dry_mass_kg", "initial_propellant_kg", "propellant_mass_kg", "specific_impulse_s", "isp_s"):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float)) or float(params[key]) <= 0.0):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a positive number", "range"))
        for key in ("thrust_n", "burn_start_s", "burn_duration_s", "initial_pointing_error_deg"):
            if key in params and (isinstance(params[key], bool) or not isinstance(params[key], (int, float)) or float(params[key]) < 0.0):
                issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be a non-negative number", "range"))
        if "thrust_efficiency" in params and (isinstance(params["thrust_efficiency"], bool) or not isinstance(params["thrust_efficiency"], (int, float)) or not 0.0 <= float(params["thrust_efficiency"]) <= 1.0):
            issues.append(ValidationIssue("error", "$.parameters.thrust_efficiency", "must be within [0, 1]", "range"))
        if not any(issue.severity == "error" for issue in issues):
            try:
                PropulsionOrbitAttitudeCoupledConfig.from_task_spec(spec)
            except PropulsionOrbitAttitudeCoupledError as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "physics_envelope"))
            except Exception as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = PropulsionOrbitAttitudeCoupledConfig.from_task_spec(spec)
        rows = propagate_propulsion_orbit_attitude_coupled(config)
        task_id = str(spec.get("task_id", "maneuver_orbit_attitude_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        for row in rows:
            row["task_id"] = task_id
            row["case_id"] = case_id
            row["mode"] = mode
        summary = summarize_propulsion_orbit_attitude_coupled(rows, config)
        validation_cfg = spec.get("validation") if isinstance(spec.get("validation"), Mapping) else {}
        physical_validation = evaluate_physical_validation(
            rows,
            summary=summary,
            checks=validation_cfg.get("checks") if isinstance(validation_cfg.get("checks"), list) else None,
            strict=bool(validation_cfg.get("strict", False)),
        )
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "mode": mode,
            "target_level": "whole_spacecraft",
            "target_name": "maneuver_orbit_attitude",
            "trace_rows": len(rows),
            "physical_validation_status": physical_validation["status"],
            "physical_validation_issue_count": physical_validation["issue_count"],
            "physical_validation": physical_validation,
        })
        metadata = {
            "capability_id": self.capability_id,
            "hf7_propulsion_orbit_attitude": build_hf7_propulsion_orbit_attitude_payload(spec),
            "hf8_physical_validation": build_hf8_physical_validation_payload(spec),
            "physical_validation": physical_validation,
            "schema_version": HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id, "fidelity_level": "medium"}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.maneuver_orbit_attitude.v1 capability script."""

import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_maneuver_orbit_attitude import ManeuverOrbitAttitudeAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = ManeuverOrbitAttitudeAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "maneuver_orbit_attitude_output")))
    dataset = write_task_dataset(output_root=output_root, compiled=compiled, task_spec=TASK_SPEC, summary=result.summary, trace_rows=result.trace_rows, status="complete")
    print(json.dumps({{"ok": True, "summary": result.summary, "dataset": dataset.to_dict()}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(capability, Mapping) and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        return {"trace": [], "summary": []}


__all__ = ["ManeuverOrbitAttitudeAdapter"]
