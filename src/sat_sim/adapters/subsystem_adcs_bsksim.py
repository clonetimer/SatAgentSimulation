"""Adapter for ``subsystem.adcs_bsksim.v1``."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.bsk_engine.adcs_scenario import ADCS_BSKSIM_CAPABILITY_ID, ADCSBSKSimScenario, adcs_config_from_task_spec
from sat_sim.task_validator import ValidationIssue


class AdcsBSKSimAdapter:
    """ADCS BSKSim-style migration bridge."""

    capability_id = ADCS_BSKSIM_CAPABILITY_ID

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
        cid = model.get("capability_id") or spec.get("capability_id")
        if cid != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        task_type = spec.get("task_type")
        if task_type not in {None, "subsystem"}:
            issues.append(ValidationIssue("error", "$.task_type", "ADCS BSKSim capability requires task_type='subsystem'", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        step_s = sim.get("step_s", solver.get("step_s"))
        for value, path in (
            (sim.get("duration_s"), "$.simulation.duration_s"),
            (step_s, "$.simulation.solver.step_s" if "step_s" not in sim else "$.simulation.step_s"),
            (sim.get("sample_s"), "$.simulation.sample_s"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", path, "must be a positive number", "range"))
        if isinstance(step_s, (int, float)) and isinstance(sim.get("sample_s"), (int, float)) and float(sim["sample_s"]) < float(step_s):
            issues.append(ValidationIssue("error", "$.simulation.sample_s", "must be >= step_s", "range"))
        projected = dict(spec)
        projected["capability_id"] = "subsystem.adcs_fidelity.v1"
        model = dict(model)
        model["capability_id"] = "subsystem.adcs_fidelity.v1"
        projected["model"] = model
        issues.extend(AdcsFidelityAdapter().validate(projected, capability))
        return tuple(i for i in issues if "subsystem.adcs_fidelity.v1" not in i.message)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        scenario = ADCSBSKSimScenario(adcs_config_from_task_spec(spec))
        return scenario.run(spec)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False)
        return f'''#!/usr/bin/env python3\n"""Generated ADCS BSKSim-style migration-bridge script."""\nfrom sat_sim.adapters.subsystem_adcs_bsksim import AdcsBSKSimAdapter\n\nTASK_SPEC = {payload}\n\nif __name__ == "__main__":\n    result = AdcsBSKSimAdapter().run(TASK_SPEC)\n    print(result.to_dict()["summary"])\n'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "summary": ["status", "engine", "bsk_process_count", "bsk_module_count", "migration_stage"],
            "trace_fields": ["time_s", "adcs.attitude.pointing_error_deg", "adcs.rw.speed_rad_s_*", "adcs.control.applied_torque_nm_*", "label.*"],
            "metadata": ["execution_plan", "model_source_boundary"],
        }


__all__ = ["AdcsBSKSimAdapter"]
