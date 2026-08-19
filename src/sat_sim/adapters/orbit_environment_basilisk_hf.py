"""Adapter for ``orbit_environment.basilisk_hf.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.orbit.basilisk_hf import (
    BASILISK_ORBIT_HF_SCHEMA_VERSION,
    BasiliskOrbitHfConfig,
    BasiliskOrbitHfError,
    BasiliskOrbitHfRuntimeUnavailable,
    build_basilisk_orbit_blueprint,
    build_bsk_orb1_payload,
    check_basilisk_availability,
    run_basilisk_orbit_hf_if_available,
)
from sat_sim.task_validator import ValidationIssue


class OrbitEnvironmentBasiliskHfAdapter:
    """BSK-ORB-1/2 Basilisk-native orbit adapter.

    The adapter is explicit-Basilisk only.  It never silently falls back to the
    existing local ORB-1/ORB-2 numerical proxy; users must select that local
    capability directly for fallback or comparison runs.
    """

    capability_id = "orbit_environment.basilisk_hf.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "orbit_environment":
            issues.append(ValidationIssue("error", "$.task_type", "Basilisk orbit HF capability requires task_type='orbit_environment'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "integrated"} or target.get("name") not in {None, "orbit_environment"}:
                issues.append(ValidationIssue("error", "$.target", "requires target.level='integrated' and target.name='orbit_environment'", "capability"))
            if target.get("mode", "nominal") != "nominal":
                issues.append(ValidationIssue("error", "$.target.mode", "Basilisk orbit HF supports nominal mode only in BSK-ORB-1", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        try:
            config = BasiliskOrbitHfConfig.from_task_spec(spec)
        except BasiliskOrbitHfError as exc:
            issues.append(ValidationIssue("error", "$.orbit_environment", str(exc), "physics_envelope"))
            config = None
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.orbit_environment", str(exc), "capability"))
            config = None
        if config is not None:
            availability = check_basilisk_availability(config)
            if not availability.available:
                issues.append(ValidationIssue(
                    "error",
                    "$.capability_id",
                    "Basilisk-native orbit execution requires optional Basilisk/bsk dependency; install the basilisk extra and rerun, or explicitly choose orbit_environment.orbit_fidelity.v1 as local proxy baseline",
                    "basilisk_unavailable",
                ))
            elif config.force_models.drag_enabled or config.force_models.srp_enabled:
                issues.append(ValidationIssue(
                    "warning",
                    "$.orbit_environment.force_models",
                    "BSK-RUN-4 drag/SRP execution is a runtime smoke only; it is not flight validation or external truth correlation",
                    "bsk_run4_drag_srp_runtime_smoke_only",
                ))
            elif config.force_models.spice_ephemeris:
                issues.append(ValidationIssue(
                    "warning",
                    "$.orbit_environment.force_models",
                    "BSK-RUN-3 SPICE/third-body runtime requires real SPICE kernels; missing kernels must be reported as unavailable, not passed",
                    "bsk_run3_spice_kernel_required",
                ))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = BasiliskOrbitHfConfig.from_task_spec(spec)
        availability = check_basilisk_availability(config)
        blueprint = build_basilisk_orbit_blueprint(config)
        task_id = str(spec.get("task_id", "orbit_environment_basilisk_hf_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        if not availability.available:
            summary = {
                "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "unavailable",
                "physical_validation_status": "not_run_basilisk_missing",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "unavailable_reason": "Basilisk is not available",
                "unavailable_details": availability.to_dict(),
                "events": {"fault_count": 0, "degradation_count": 0},
                "availability": availability.to_dict(),
                "fallback_policy": "no_implicit_fallback_use_orbit_environment.orbit_fidelity.v1_explicitly",
            }
            return SimulationResult(
                summary=summary,
                trace_rows=(),
                labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": "unavailable"}]},
                metadata={"bsk_orb1": build_bsk_orb1_payload(spec), "bsk_orb2_benchmark_status": "not_run_basilisk_missing"},
            )
        try:
            summary, rows = run_basilisk_orbit_hf_if_available(config)
        except BasiliskOrbitHfRuntimeUnavailable as exc:
            summary = {
                "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "unavailable",
                "physical_validation_status": "not_run_runtime_unavailable",
                "can_claim_high_fidelity": False,
                "trace_rows": 0,
                "unavailable_reason": exc.reason,
                "unavailable_details": exc.details,
                "blueprint": blueprint,
            }
            rows = ()
        except Exception as exc:
            summary = {
                "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
                "task_id": task_id,
                "case_id": case_id,
                "capability_id": self.capability_id,
                "backend_type": "basilisk_native",
                "basilisk_required": True,
                "basilisk_status": "configured_but_not_executed",
                "physical_validation_status": "not_run_bsk_runtime_scope_limit",
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
            "target_level": "integrated",
            "target_name": "orbit_environment",
            "can_claim_high_fidelity": False,
        })
        return SimulationResult(
            summary=summary,
            trace_rows=tuple(rows),
            labels={"run_labels": [{"task_id": task_id, "capability_id": self.capability_id, "backend_type": "basilisk_native", "status": summary.get("basilisk_status")}]},
            metadata={"bsk_orb1": {"config": config.to_dict(), "availability": availability.to_dict(), "blueprint": blueprint}},
        )

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated orbit_environment.basilisk_hf.v1 capability script.

This script requires Basilisk/bsk.  It will not silently use the local orbit
proxy; choose orbit_environment.orbit_fidelity.v1 explicitly for local fallback
or comparison baselines.
"""

import json

from sat_sim.adapters.orbit_environment_basilisk_hf import OrbitEnvironmentBasiliskHfAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = OrbitEnvironmentBasiliskHfAdapter()
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


__all__ = ["OrbitEnvironmentBasiliskHfAdapter"]
