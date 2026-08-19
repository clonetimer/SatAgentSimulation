"""Adapter for ``subsystem.adcs_basilisk_fsw.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.adcs.basilisk_fsw import (
    BASILISK_ADCS_FSW_SCHEMA_VERSION,
    BasiliskAdcsFswConfig,
    BasiliskAdcsFswError,
    build_basilisk_adcs_fsw_blueprint,
    build_bsk_adcs1_payload,
    check_basilisk_adcs_availability,
    run_basilisk_adcs_fsw_if_available,
)
from sat_sim.task_validator import ValidationIssue


class AdcsBasiliskFswAdapter:
    """BSK-ADCS-1 explicit Basilisk-native ADCS/FSW adapter.

    This adapter never silently falls back to ``subsystem.adcs_fidelity.v1``.
    The local ADCS proxy remains useful for ordinary Agent usage, smoke tests,
    and explicit comparison baselines.
    """

    capability_id = "subsystem.adcs_basilisk_fsw.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "subsystem":
            issues.append(ValidationIssue("error", "$.task_type", "Basilisk ADCS/FSW capability requires task_type='subsystem'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "subsystem"}:
                issues.append(ValidationIssue("error", "$.target.level", "requires target.level='subsystem'", "capability"))
            if target.get("name") not in {None, "adcs", "adcs_fsw", "attitude_control"}:
                issues.append(ValidationIssue("error", "$.target.name", "must target ADCS/FSW", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        try:
            config = BasiliskAdcsFswConfig.from_task_spec(spec)
        except BasiliskAdcsFswError as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
            config = None
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
            config = None
        if config is not None:
            if config.mode.fault_management_requested:
                issues.append(ValidationIssue(
                    "error",
                    "$.parameters.fault_management",
                    "BSK-ADCS-1 does not implement task-specific fault management/FDIR; configure guidance/control/estimator modes only or add a later FDIR capability",
                    "fdir_not_implemented",
                ))
            availability = check_basilisk_adcs_availability(config)
            if not availability.available:
                issues.append(ValidationIssue(
                    "error",
                    "$.capability_id",
                    "Basilisk-native ADCS/FSW execution requires optional Basilisk/bsk dependency; install the basilisk extra and rerun, or explicitly choose subsystem.adcs_fidelity.v1 as local proxy baseline",
                    "basilisk_unavailable",
                ))
            else:
                issues.append(ValidationIssue(
                    "warning",
                    "$.capability_id",
                    "BSK-ADCS-1 prepares a Basilisk-native ADCS/FSW configuration surface; runtime validation is BSK-ADCS-2 scope",
                    "bsk_adcs2_runtime_benchmark_pending",
                ))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = BasiliskAdcsFswConfig.from_task_spec(spec)
        availability = check_basilisk_adcs_availability(config)
        blueprint = build_basilisk_adcs_fsw_blueprint(config)
        task_id = str(spec.get("task_id", "adcs_basilisk_fsw_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        if not availability.available:
            summary = {
                "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "unavailable",
                "physical_validation_status": "not_run_basilisk_missing",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "events": {"fault_count": 0, "degradation_count": 0},
                "availability": availability.to_dict(),
                "fallback_policy": "no_implicit_fallback_use_subsystem.adcs_fidelity.v1_explicitly",
            }
            return SimulationResult(
                summary=summary,
                trace_rows=(),
                labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": "unavailable"}]},
                metadata={"bsk_adcs1": build_bsk_adcs1_payload(spec), "bsk_adcs2_benchmark_status": "not_run_basilisk_missing"},
            )
        try:
            summary, rows = run_basilisk_adcs_fsw_if_available(config)
        except Exception as exc:
            summary = {
                "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "configured_but_not_executed",
                "physical_validation_status": "not_run_bsk_adcs2_runtime_benchmark_pending",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "runtime_error": str(exc),
                "availability": availability.to_dict(),
                "fallback_policy": "no_implicit_fallback_use_subsystem.adcs_fidelity.v1_explicitly",
                "blueprint": blueprint,
            }
            rows = ()
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "subsystem",
            "target_name": "adcs_fsw",
            "can_claim_high_fidelity": False,
        })
        return SimulationResult(
            summary=summary,
            trace_rows=tuple(rows),
            labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": summary.get("basilisk_status")}]},
            metadata={"bsk_adcs1": {"config": config.to_dict(), "availability": availability.to_dict(), "blueprint": blueprint}},
        )

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated subsystem.adcs_basilisk_fsw.v1 capability script.

This script requires Basilisk/bsk.  It will not silently use the local ADCS
proxy; choose subsystem.adcs_fidelity.v1 explicitly for local fallback or
comparison baselines.
"""

import json

from sat_sim.adapters.subsystem_adcs_basilisk_fsw import AdcsBasiliskFswAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = AdcsBasiliskFswAdapter()
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


__all__ = ["AdcsBasiliskFswAdapter"]
