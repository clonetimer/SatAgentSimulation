"""Adapter for the v0.5.4.0 BSKSim-style foundation scenario."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.bsk_engine.scenario_factory import FOUNDATION_CAPABILITY_ID, create_scenario
from sat_sim.task_validator import ValidationIssue


class WholeSpacecraftBSKSimFoundationAdapter:
    """Run the initial project-owned BSKSim-style scenario.

    This adapter is deliberately scoped: it establishes the Process/Task/Dynamics/FSW
    separation and runs a Basilisk timeline, but it does not yet claim complete
    migration of all native ADCS or whole-spacecraft modules.
    """

    capability_id = FOUNDATION_CAPABILITY_ID

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
        cid = model.get("capability_id") or spec.get("capability_id")
        if cid != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        step_s = sim.get("step_s", solver.get("step_s"))
        for key, value, path in (
            ("duration_s", sim.get("duration_s"), "$.simulation.duration_s"),
            ("step_s", step_s, "$.simulation.solver.step_s" if "step_s" not in sim else "$.simulation.step_s"),
            ("sample_s", sim.get("sample_s"), "$.simulation.sample_s"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", path, "must be a positive number", "range"))
        if isinstance(step_s, (int, float)) and isinstance(sim.get("sample_s"), (int, float)) and float(sim["sample_s"]) < float(step_s):
            issues.append(ValidationIssue("error", "$.simulation.sample_s", "must be >= step_s", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        scenario = create_scenario(spec)
        result = scenario.run()
        summary = dict(result.summary)
        summary["bsk_engine_schema"] = result.execution_plan.schema_version
        summary["execution_plan_available"] = True
        summary["status"] = "complete" if result.status == "SUCCEEDED" else "unavailable"
        rows = tuple(dict(row) for row in result.trace_rows)
        fault_environment = result.metadata.get("fault_environment", {}) if isinstance(result.metadata, Mapping) else {}
        labels = {
            "run_labels": [{
                "capability_id": self.capability_id,
                "engine": "basilisk_native_foundation",
                "mode": summary.get("mode_request", "standby"),
            }],
            "event_labels": [e.to_dict() for e in result.execution_plan.events],
            "fault_environment": fault_environment,
        }
        metadata = {
            "bsk_engine": result.metadata,
            "runtime_manifest": result.metadata.get("runtime_manifest", {}) if isinstance(result.metadata, Mapping) else {},
            "fault_environment": fault_environment,
            "execution_plan": result.execution_plan.to_dict(),
            "modifiers_applied_by_adapter": True,
            "model_source_boundary": {
                "uses_basilisk_timeline": bool(summary.get("basilisk_timeline_executed")),
                "uses_basilisk_native_modules": bool(summary.get("instantiated_module_count", 0)),
                "native_module_migration": "foundation_orbit_attitude_native_modules",
                "claim": "Native Basilisk orbit-attitude foundation with project-owned BSKSim-style orchestration; not the complete official BSKSim template suite",
            },
        }
        return SimulationResult(summary=summary, trace_rows=rows, labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        spec_json = json.dumps(spec, ensure_ascii=False, indent=2)
        return f'''# Auto-generated BSKSim-style foundation scenario runner.\nfrom sat_sim.adapters.whole_spacecraft_bsksim_foundation import WholeSpacecraftBSKSimFoundationAdapter\n\nTASK_SPEC = {spec_json}\n\nif __name__ == "__main__":\n    result = WholeSpacecraftBSKSimFoundationAdapter().run(TASK_SPEC)\n    print(result.summary)\n'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "summary": ["status", "basilisk_timeline_executed", "process_count", "task_count", "module_count", "connection_count"],
            "trace_fields": ["time_s", "orbit.theta_rad", "attitude.pointing_error_deg", "fsw.mode", "label.fault_active", "label.degradation_active", "label.constraint_active"],
            "metadata": ["execution_plan", "model_source_boundary"],
        }
