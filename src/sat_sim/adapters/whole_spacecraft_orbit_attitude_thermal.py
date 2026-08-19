"""Adapter for ``whole_spacecraft.orbit_attitude_thermal.v1``.

THERM-1B keeps the thermal network model in the subsystem layer and places
cross-subsystem orbit/attitude/power coupling in this whole-spacecraft adapter.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.coupled.orbit_attitude_thermal import (
    ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION,
    build_whole_spacecraft_thermal_task_spec,
)
from sat_sim.task_validator import ValidationIssue
from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter


class WholeSpacecraftOrbitAttitudeThermalAdapter:
    """Orbit/attitude/power to reduced-order thermal network integration adapter."""

    capability_id = "whole_spacecraft.orbit_attitude_thermal.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "requires task_type='whole_spacecraft'", "capability"))
        if not isinstance(spec.get("spacecraft"), Mapping):
            issues.append(ValidationIssue("error", "$.spacecraft", "whole-spacecraft thermal coupling requires spacecraft metadata", "required"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        child = build_whole_spacecraft_thermal_task_spec(spec)
        for issue in ThermalReducedOrderAdapter().validate(child):
            path = issue.path if issue.path.startswith("$.thermal_child") else f"$.thermal_child{issue.path[1:]}"
            issues.append(ValidationIssue(issue.severity, path, issue.message, issue.code))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        child_spec = build_whole_spacecraft_thermal_task_spec(spec)
        child = ThermalReducedOrderAdapter().run(child_spec)
        summary = dict(child.summary)
        summary.update({
            "schema_version": "therm1b.whole_spacecraft_orbit_attitude_thermal.v1",
            "status": child.summary.get("status", "pass"),
            "capability_id": self.capability_id,
            "child_capability_id": "subsystem.thermal_reduced_order.v1",
            "backend_type": "orchestration_wrapper",
            "thermal_backend_type": child.summary.get("backend_type"),
            "coupling_schema_version": ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION,
            "qoi.spacecraft.thermal.final_internal_temp_c": child.summary.get("qoi.thermal.final_internal_temp_c"),
            "qoi.spacecraft.thermal.max_internal_temp_c": child.summary.get("qoi.thermal.max_internal_temp_c"),
            "qoi.spacecraft.thermal.min_internal_temp_c": child.summary.get("qoi.thermal.min_internal_temp_c"),
            "qoi.spacecraft.thermal.heater_energy_wh": child.summary.get("qoi.thermal.heater_energy_wh"),
            "qoi.spacecraft.thermal.heater_duty_cycle": child.summary.get("qoi.thermal.heater_duty_cycle"),
            "qoi.spacecraft.thermal.heater_saturation_count": child.summary.get("qoi.thermal.heater_saturation_count"),
            "qoi.spacecraft.thermal.heater_transition_count": child.summary.get("qoi.thermal.heater_transition_count"),
            "qoi.spacecraft.thermal.control_mode": child.summary.get("qoi.thermal.control_mode"),
            "can_claim_high_fidelity": False,
            "flight_validated": False,
            "thermal_desktop_equivalent": False,
        })
        rows = []
        for row in child.trace_rows:
            out = dict(row)
            out["capability_id"] = self.capability_id
            out["child_capability_id"] = "subsystem.thermal_reduced_order.v1"
            out["whole_spacecraft.task_id"] = spec.get("task_id", "whole_spacecraft_orbit_attitude_thermal")
            rows.append(out)
        labels = dict(child.labels)
        labels.update({"integration_layer": "whole_spacecraft", "thermal_child": "subsystem.thermal_reduced_order.v1"})
        metadata = {
            "schema_version": "therm1b.whole_spacecraft_orbit_attitude_thermal.v1",
            "adapter": self.__class__.__name__,
            "capability_id": self.capability_id,
            "child_task_spec": child_spec,
            "child_metadata": child.metadata,
            "boundary": {
                "backend_type": "orchestration_wrapper",
                "thermal_model_backend": "local_physics_proxy",
                "subsystem_model": "subsystem.thermal_reduced_order.v1",
                "flight_validated": False,
                "thermal_desktop_equivalent": False,
                "thermal_control_policy": child.summary.get("qoi.thermal.control_mode"),
            },
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated whole_spacecraft.orbit_attitude_thermal.v1 capability script.

This script composes orbit/attitude/power boundary conditions and delegates the
thermal solve to subsystem.thermal_reduced_order.v1. It is public-reference-
informed and not flight validated.
"""

import json
from sat_sim.adapters.whole_spacecraft_orbit_attitude_thermal import WholeSpacecraftOrbitAttitudeThermalAdapter

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = WholeSpacecraftOrbitAttitudeThermalAdapter()
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


__all__ = ["WholeSpacecraftOrbitAttitudeThermalAdapter"]
