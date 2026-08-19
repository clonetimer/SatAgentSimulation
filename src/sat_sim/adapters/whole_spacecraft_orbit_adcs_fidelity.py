"""Adapter for ``whole_spacecraft.orbit_adcs_fidelity.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.coupled import (
    INT1_ORBIT_ADCS_SCHEMA_VERSION,
    OrbitAdcsIntegrationConfig,
    OrbitAdcsIntegrationError,
    build_int1_orbit_adcs_payload,
    propagate_orbit_adcs_fidelity,
    summarize_orbit_adcs_fidelity,
)
from sat_sim.task_validator import ValidationIssue


class OrbitAdcsFidelityAdapter:
    """INT-1 orbit + ADCS integration adapter."""

    capability_id = "whole_spacecraft.orbit_adcs_fidelity.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "INT-1 orbit+ADCS integration requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "whole_spacecraft"} or target.get("name") not in {None, "orbit_adcs"}:
                issues.append(ValidationIssue("error", "$.target", "requires target.level='whole_spacecraft' and target.name='orbit_adcs'", "capability"))
            if target.get("mode", "nominal") != "nominal":
                issues.append(ValidationIssue("error", "$.target.mode", "INT-1 supports nominal mode only", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        adcs_params = params.get("adcs") if isinstance(params.get("adcs"), Mapping) else params
        mode = str(adcs_params.get("target_mode") or params.get("target_mode") or "nadir").lower()
        if mode not in {"nadir", "sun", "detumble", "inertial"}:
            issues.append(ValidationIssue("error", "$.parameters.adcs.target_mode", "must be one of nadir/sun/detumble/inertial", "enum"))
        if not any(issue.severity == "error" for issue in issues):
            try:
                OrbitAdcsIntegrationConfig.from_task_spec(spec)
            except OrbitAdcsIntegrationError as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
            except Exception as exc:
                issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = OrbitAdcsIntegrationConfig.from_task_spec(spec)
        rows = propagate_orbit_adcs_fidelity(config)
        summary = summarize_orbit_adcs_fidelity(rows, config)
        task_id = str(spec.get("task_id", "orbit_adcs_fidelity_task"))
        case_id = str((spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}).get("case_id", "case_000"))
        fixed_rows = []
        for row in rows:
            r = dict(row)
            r["task_id"] = task_id
            r["case_id"] = case_id
            fixed_rows.append(r)
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "whole_spacecraft",
            "target_name": "orbit_adcs",
            "trace_rows": len(fixed_rows),
            "schema_version": INT1_ORBIT_ADCS_SCHEMA_VERSION,
        })
        metadata = {
            "capability_id": self.capability_id,
            "int1_orbit_adcs_integration": build_int1_orbit_adcs_payload(spec),
            "schema_version": INT1_ORBIT_ADCS_SCHEMA_VERSION,
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": "nominal", "capability_id": self.capability_id, "fidelity_level": "medium"}]}
        return SimulationResult(summary=summary, trace_rows=tuple(fixed_rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.orbit_adcs_fidelity.v1 capability script."""

import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_orbit_adcs_fidelity import OrbitAdcsFidelityAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = OrbitAdcsFidelityAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "orbit_adcs_fidelity_output")))
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


__all__ = ["OrbitAdcsFidelityAdapter"]
