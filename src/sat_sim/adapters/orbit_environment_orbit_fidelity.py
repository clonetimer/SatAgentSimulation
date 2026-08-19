"""Adapter for ``orbit_environment.orbit_fidelity.v1``."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.orbit.numerical import (
    ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION,
    OrbitFidelityConfig,
    build_orb1_orbit_fidelity_payload,
    propagate_orbit_fidelity,
    summarize_orbit_fidelity,
)
from sat_sim.orbit.force_models import OrbitForceModelError, build_orb1_force_model_payload
from sat_sim.task_validator import ValidationIssue
from sat_sim.time_systems import build_time_grid


class OrbitEnvironmentOrbitFidelityAdapter:
    """ORB-1 numerical orbit-fidelity adapter."""

    capability_id = "orbit_environment.orbit_fidelity.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "orbit_environment":
            issues.append(ValidationIssue("error", "$.task_type", "orbit fidelity capability requires task_type='orbit_environment'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "integrated"} or target.get("name") not in {None, "orbit_environment"}:
                issues.append(ValidationIssue("error", "$.target", "requires target.level='integrated' and target.name='orbit_environment'", "capability"))
            if target.get("mode", "nominal") != "nominal":
                issues.append(ValidationIssue("error", "$.target.mode", "ORB-1 orbit fidelity supports nominal mode only", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        if solver and solver.get("method") not in {None, "euler", "rk4"}:
            issues.append(ValidationIssue("error", "$.simulation.solver.method", "must be 'euler' or 'rk4'", "enum"))
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        if "altitude_m" in orbit:
            altitude = orbit.get("altitude_m")
            if isinstance(altitude, bool) or not isinstance(altitude, (int, float)) or not 120_000.0 <= float(altitude) <= 2_500_000.0:
                issues.append(ValidationIssue("error", "$.orbit_environment.altitude_m", "must be within [120000, 2500000] m for ORB-1 LEO envelope", "range"))
        if "eccentricity" in orbit:
            ecc = orbit.get("eccentricity")
            if isinstance(ecc, bool) or not isinstance(ecc, (int, float)) or not 0.0 <= float(ecc) < 0.2:
                issues.append(ValidationIssue("error", "$.orbit_environment.eccentricity", "must be within [0, 0.2)", "range"))
        # Hard boundary: ORB-1 does not support flight-grade/truth force models.
        force = orbit.get("force_models") if isinstance(orbit.get("force_models"), Mapping) else {}
        unsupported_enabled = [
            key for key in ("third_body", "spice_ephemeris", "truth_ephemeris", "calibrated_tolerances", "nrlmsise00")
            if bool(force.get(key, False))
        ]
        if unsupported_enabled:
            issues.append(ValidationIssue("error", "$.orbit_environment.force_models", f"unsupported ORB-1 force models requested: {unsupported_enabled}", "unsupported"))
        if not any(i.severity == "error" for i in issues):
            try:
                OrbitFidelityConfig.from_task_spec(spec)
            except OrbitForceModelError as exc:
                issues.append(ValidationIssue("error", "$.orbit_environment", str(exc), "physics_envelope"))
            except Exception as exc:
                issues.append(ValidationIssue("error", "$.orbit_environment", str(exc), "capability"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        config = OrbitFidelityConfig.from_task_spec(spec)
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        solver = config.solver
        grid = build_time_grid(
            duration_s=float(sim.get("duration_s", solver.duration_s if solver else 300.0)),
            sample_s=float(sim.get("sample_s", solver.step_s if solver else 10.0)),
            epoch_utc=str(sim.get("epoch_utc") or config.epoch_utc),
            include_endpoint=bool((sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}).get("include_endpoint", True)),
        )
        samples = propagate_orbit_fidelity(config, grid)
        summary = summarize_orbit_fidelity(samples, config)
        task_id = str(spec.get("task_id", "orbit_environment_orbit_fidelity_task"))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        mode = str(target.get("mode") or "nominal")
        rows = tuple(
            sample.to_trace_row(
                task_id=task_id,
                case_id=case_id,
                capability_id=self.capability_id,
                mode=mode,
                earth_radius_m=config.force_model.earth_radius_m,
            )
            for sample in samples
        )
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "integrated",
            "target_name": "orbit_environment",
            "trace_rows": len(rows),
            "events": {"fault_count": 0, "degradation_count": 0},
            "physical_validation_status": "pass" if summary["qoi"]["orbit.altitude_min_m"] > 100_000.0 else "fail",
        })
        adapter_metadata = {
            "capability_id": self.capability_id,
            "orb1_orbit_fidelity": build_orb1_orbit_fidelity_payload(spec),
            "orb1_force_models": build_orb1_force_model_payload(spec),
            "schema_version": ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION,
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id, "fidelity_level": "medium"}]}
        return SimulationResult(summary=summary, trace_rows=rows, labels=labels, metadata=adapter_metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated orbit_environment.orbit_fidelity.v1 capability script."""

import json
from pathlib import Path

from sat_sim.adapters.orbit_environment_orbit_fidelity import OrbitEnvironmentOrbitFidelityAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = OrbitEnvironmentOrbitFidelityAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "orbit_environment_orbit_fidelity_output")))
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


__all__ = ["OrbitEnvironmentOrbitFidelityAdapter"]
