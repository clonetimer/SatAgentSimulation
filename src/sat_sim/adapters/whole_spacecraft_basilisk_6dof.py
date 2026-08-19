"""Adapter for ``whole_spacecraft.basilisk_6dof.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.spacecraft.basilisk_6dof import (
    BASILISK_6DOF_SCHEMA_VERSION,
    Basilisk6DofConfig,
    Basilisk6DofError,
    build_basilisk_6dof_blueprint,
    build_bsk_6dof1_payload,
    check_basilisk_6dof_availability,
    run_basilisk_6dof_if_available,
)
from sat_sim.task_validator import ValidationIssue


class SpacecraftBasilisk6DofAdapter:
    """BSK-6DOF-1 explicit Basilisk-native 6-DOF spacecraft graph adapter.

    This adapter never silently falls back to ``whole_spacecraft.orbit_adcs_fidelity.v1``.
    INT-1 remains the local orbit+ADCS integration gate and an explicit comparison
    baseline for later validation.
    """

    capability_id = "whole_spacecraft.basilisk_6dof.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "Basilisk 6-DOF capability requires task_type='whole_spacecraft'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "whole_spacecraft"}:
                issues.append(ValidationIssue("error", "$.target.level", "requires target.level='whole_spacecraft'", "capability"))
            if target.get("name") not in {None, "basilisk_6dof", "orbit_adcs", "spacecraft_6dof", "whole_spacecraft"}:
                issues.append(ValidationIssue("error", "$.target.name", "must target Basilisk 6-DOF / orbit+ADCS spacecraft graph", "capability"))
            if target.get("mode", "nominal") != "nominal":
                issues.append(ValidationIssue("error", "$.target.mode", "BSK-6DOF-1 supports nominal mode only", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        try:
            config = Basilisk6DofConfig.from_task_spec(spec)
        except Basilisk6DofError as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
            config = None
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
            config = None
        if config is not None:
            availability = check_basilisk_6dof_availability(config)
            if not availability.available:
                issues.append(ValidationIssue(
                    "error",
                    "$.capability_id",
                    "Basilisk-native 6-DOF execution requires optional Basilisk/bsk dependency; install the basilisk extra and rerun, or explicitly choose whole_spacecraft.orbit_adcs_fidelity.v1 as local integration baseline",
                    "basilisk_unavailable",
                ))
            else:
                issues.append(ValidationIssue(
                    "warning",
                    "$.capability_id",
                    "BSK-6DOF-1 prepares a Basilisk-native spacecraft graph; runtime integration and benchmark envelopes are BSK-6DOF-2 scope",
                    "bsk_6dof2_runtime_benchmark_pending",
                ))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = Basilisk6DofConfig.from_task_spec(spec)
        availability = check_basilisk_6dof_availability(config)
        blueprint = build_basilisk_6dof_blueprint(config)
        task_id = str(spec.get("task_id", "spacecraft_basilisk_6dof_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        if not availability.available:
            summary = {
                "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "unavailable",
                "physical_validation_status": "not_run_basilisk_missing",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "availability": availability.to_dict(),
                "fallback_policy": "no_implicit_fallback_use_whole_spacecraft.orbit_adcs_fidelity.v1_explicitly",
            }
            return SimulationResult(
                summary=summary,
                trace_rows=(),
                labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": "unavailable"}]},
                metadata={"bsk_6dof1": build_bsk_6dof1_payload(spec), "bsk_6dof2_integration_status": "not_run_basilisk_missing"},
            )
        try:
            summary, rows = run_basilisk_6dof_if_available(config)
        except Exception as exc:
            summary = {
                "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "configured_but_not_executed",
                "physical_validation_status": "not_run_bsk_6dof2_integration_benchmark_pending",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "runtime_error": str(exc),
                "blueprint": blueprint,
            }
            rows = ()
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "whole_spacecraft",
            "target_name": "basilisk_6dof",
            "can_claim_high_fidelity": False,
        })
        return SimulationResult(
            summary=summary,
            trace_rows=tuple(rows),
            labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": summary.get("basilisk_status")}]},
            metadata={"bsk_6dof1": {"config": config.to_dict(), "availability": availability.to_dict(), "blueprint": blueprint}},
        )

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.basilisk_6dof.v1 capability script.

This script requires Basilisk/bsk.  It will not silently use the INT-1 local
orbit+ADCS integration proxy; choose whole_spacecraft.orbit_adcs_fidelity.v1
explicitly for local fallback or comparison baselines.
"""

import json

from sat_sim.adapters.whole_spacecraft_basilisk_6dof import SpacecraftBasilisk6DofAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = SpacecraftBasilisk6DofAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(json.dumps({{"ok": False, "errors": [i.to_dict() for i in errors]}}, indent=2, ensure_ascii=False))
        return 2
    result = adapter.run(TASK_SPEC)
    print(json.dumps({{"ok": True, "summary": result.summary, "metadata": result.metadata}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(capability, Mapping) and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        return {"trace": [], "summary": []}


__all__ = ["SpacecraftBasilisk6DofAdapter"]
