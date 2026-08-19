"""Adapter for ``subsystem.adcs_closed_loop.basic.v1``.

HF-4 adds a local Route-B model-library implementation of basic closed-loop ADCS.
It is runnable and source-bound, but it remains below engineering high-fidelity
ADCS/FSW validation.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adcs import (
    HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION,
    ADCSClosedLoopConfig,
    ADCSClosedLoopError,
    build_hf4_adcs_closed_loop_payload,
    propagate_adcs_closed_loop,
    summarize_adcs_closed_loop,
)
from sat_sim.task_validator import ValidationIssue


class AdcsClosedLoopBasicAdapter:
    """HF-4 basic closed-loop ADCS capability adapter."""

    capability_id = "subsystem.adcs_closed_loop.basic.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "ADCS closed-loop capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "subsystem"} or target.get("name") not in {None, "adcs", "adcs_closed_loop"}:
                issues.append(ValidationIssue("error", "$.target", "requires target.level='subsystem' and target.name='adcs_closed_loop' or 'adcs'", "capability"))
            if target.get("mode", "nominal") not in {"nominal"}:
                issues.append(ValidationIssue("error", "$.target.mode", "HF-4 ADCS closed-loop basic supports nominal mode only", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        if solver and solver.get("method") not in {None, "euler", "rk4"}:
            issues.append(ValidationIssue("error", "$.simulation.solver.method", "must be 'euler' or 'rk4'", "enum"))
        if isinstance(solver.get("step_s"), (int, float)) and isinstance(sim.get("sample_s"), (int, float)):
            if float(solver["step_s"]) > float(sim["sample_s"]):
                issues.append(ValidationIssue("error", "$.simulation.solver.step_s", "must not exceed simulation.sample_s for HF-4 internal integration", "range"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        for key in (
            "spacecraft_inertia_kg_m2", "wheel_inertia_kg_m2", "max_wheel_torque_nm", "max_rw_torque_nm",
            "max_wheel_speed_rad_s", "control_kp_nm_per_rad", "control_kd_nm_per_rad_s", "pointing_requirement_deg",
        ):
            if key in params:
                value = params[key]
                if isinstance(value, bool) or not isinstance(value, (int, float, list, tuple)):
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be numeric or vector where supported", "range"))
                elif isinstance(value, (int, float)) and float(value) <= 0:
                    issues.append(ValidationIssue("error", f"$.parameters.{key}", "must be positive", "range"))
        if "target_mode" in params and str(params.get("target_mode")).lower() not in {"inertial", "nadir", "sun", "detumble"}:
            issues.append(ValidationIssue("error", "$.parameters.target_mode", "must be inertial/nadir/sun/detumble", "enum"))
        if "num_reaction_wheels" in params:
            value = params["num_reaction_wheels"]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8:
                issues.append(ValidationIssue("error", "$.parameters.num_reaction_wheels", "must be an integer in [1, 8]", "range"))
        if not any(i.severity == "error" for i in issues):
            try:
                ADCSClosedLoopConfig.from_task_spec(spec)
            except ADCSClosedLoopError as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "physics_envelope"))
            except Exception as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = ADCSClosedLoopConfig.from_task_spec(spec)
        samples = propagate_adcs_closed_loop(config)
        summary = summarize_adcs_closed_loop(samples, config)
        task_id = str(spec.get("task_id", "adcs_closed_loop_basic_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        rows = tuple(
            sample.to_trace_row(
                task_id=task_id,
                case_id=case_id,
                capability_id=self.capability_id,
                pointing_requirement_deg=config.pointing_requirement_deg,
            )
            for sample in samples
        )
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "subsystem",
            "target_name": "adcs_closed_loop",
            "status": "complete",
            "trace_rows": len(rows),
            "events": {"fault_count": 0, "degradation_count": 0},
        })
        adapter_metadata = {
            "capability_id": self.capability_id,
            "hf4_adcs_closed_loop": build_hf4_adcs_closed_loop_payload(spec),
            "schema_version": HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION,
        }
        labels = {"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "fidelity_level": "basic", "mode": "nominal"}]}
        return SimulationResult(summary=summary, trace_rows=rows, labels=labels, metadata=adapter_metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.adcs_closed_loop.basic.v1 capability script."""

import json
from pathlib import Path

from sat_sim.adapters.subsystem_adcs_closed_loop_basic import AdcsClosedLoopBasicAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = AdcsClosedLoopBasicAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "adcs_closed_loop_basic_output")))
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
        if isinstance(capability, Mapping) and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        return {"trace": [], "summary": []}


__all__ = ["AdcsClosedLoopBasicAdapter"]
