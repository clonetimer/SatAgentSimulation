"""Explicit adapter for ``orbit_environment.leo_simple.v1``.

This adapter maps TaskSpec simulation/orbit_environment fields directly to the
support-data-free reference environment model. It does not call demo harness
functions.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue


class OrbitEnvironmentLeoSimpleAdapter:
    """Production adapter for deterministic simple LEO environment profiles."""

    capability_id = "orbit_environment.leo_simple.v1"

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "orbit_environment":
            issues.append(ValidationIssue("error", "$.task_type", "orbit environment capability requires task_type='orbit_environment'", "capability"))
        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        if target:
            if target.get("level") not in {None, "integrated"} or target.get("name") not in {None, "orbit_environment"}:
                issues.append(ValidationIssue("error", "$.target", "orbit environment capability requires target.level='integrated' and target.name='orbit_environment'", "capability"))
            if target.get("mode", "nominal") != "nominal":
                issues.append(ValidationIssue("error", "$.target.mode", "orbit environment capability currently supports nominal only", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        for key in ("duration_s", "sample_s"):
            value = sim.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        orbit = self._merged_orbit_payload(spec)
        if "altitude_m" in orbit:
            altitude = orbit.get("altitude_m")
            if isinstance(altitude, bool) or not isinstance(altitude, (int, float)) or not 100_000.0 <= float(altitude) <= 2_000_000.0:
                issues.append(ValidationIssue("error", "$.orbit_environment.altitude_m", "must be within [100000, 2000000] m", "range"))
        if "inclination_deg" in orbit:
            inc = orbit.get("inclination_deg")
            if isinstance(inc, bool) or not isinstance(inc, (int, float)) or not 0.0 <= float(inc) <= 180.0:
                issues.append(ValidationIssue("error", "$.orbit_environment.inclination_deg", "must be within [0, 180] deg", "range"))
        for key in ("sun_vector_n", "magnetic_dipole_axis_n"):
            if key in orbit and not self._valid_vector3(orbit[key]):
                issues.append(ValidationIssue("error", f"$.orbit_environment.{key}", "must be a non-zero numeric vector3", "type"))
        gs = orbit.get("ground_station")
        if gs is not None and not isinstance(gs, Mapping):
            issues.append(ValidationIssue("error", "$.parameters.ground_station", "must be an object", "type"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        from integrated.orbit_environment.model import generate_environment_profile, summarize_profile
        from integrated.orbit_environment.schemas import GroundStationConfig, OrbitEnvironmentConfig

        target = spec.get("target") if isinstance(spec.get("target"), Mapping) else {}
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        orbit = self._merged_orbit_payload(spec)
        duration_s = float(sim.get("duration_s", 6000.0))
        sample_s = float(sim.get("sample_s", orbit.get("step_s", 30.0)))
        task_id = str(spec.get("task_id", "orbit_environment_task"))
        case_id = str((spec.get("metadata") or {}).get("case_id", "case_000")) if isinstance(spec.get("metadata"), Mapping) else "case_000"
        mode = str(target.get("mode") or "nominal")

        gs_payload = orbit.get("ground_station") if isinstance(orbit.get("ground_station"), Mapping) else {}
        cfg_kwargs: dict[str, Any] = {"duration_s": duration_s, "dt_s": sample_s}
        for key in (
            "earth_radius_m",
            "earth_mu_m3_s2",
            "earth_rotation_rad_s",
            "altitude_m",
            "inclination_deg",
            "raan_deg",
            "arg_lat0_deg",
            "sun_vector_n",
            "magnetic_dipole_axis_n",
            "magnetic_equator_strength_t",
        ):
            if key in orbit:
                cfg_kwargs[key] = tuple(orbit[key]) if key.endswith("_n") else orbit[key]
        if gs_payload:
            cfg_kwargs["ground_station"] = GroundStationConfig(**dict(gs_payload))
        cfg = OrbitEnvironmentConfig(**cfg_kwargs)
        profile = generate_environment_profile(cfg)
        summary_obj = summarize_profile(profile)

        rows: list[dict[str, Any]] = []
        for idx, sample in enumerate(profile.samples):
            rows.append({
                "task_id": task_id,
                "case_id": case_id,
                "time_s": sample.time_s,
                "sample_index": idx,
                "target_level": "integrated",
                "target_name": "orbit_environment",
                "mode": mode,
                "orbit.r_bn_n_m_x": sample.r_bn_n_m[0],
                "orbit.r_bn_n_m_y": sample.r_bn_n_m[1],
                "orbit.r_bn_n_m_z": sample.r_bn_n_m[2],
                "orbit.v_bn_n_m_s_x": sample.v_bn_n_m_s[0],
                "orbit.v_bn_n_m_s_y": sample.v_bn_n_m_s[1],
                "orbit.v_bn_n_m_s_z": sample.v_bn_n_m_s[2],
                "orbit.radius_m": sample.orbit_radius_m,
                "environment.sun_vector_n_x": sample.sun_vector_n[0],
                "environment.sun_vector_n_y": sample.sun_vector_n[1],
                "environment.sun_vector_n_z": sample.sun_vector_n[2],
                "environment.shadow_factor": sample.shadow_factor,
                "environment.eclipse_flag": bool(sample.shadow_factor <= 0.0),
                "environment.magnetic_field_n_t_x": sample.magnetic_field_n_t[0],
                "environment.magnetic_field_n_t_y": sample.magnetic_field_n_t[1],
                "environment.magnetic_field_n_t_z": sample.magnetic_field_n_t[2],
                "environment.magnetic_field_norm_t": sample.magnetic_field_norm_t,
                "ground.range_m": sample.ground_range_m,
                "ground.elevation_deg": sample.ground_elevation_deg,
                "ground.has_access": sample.ground_has_access,
            })

        summary = {
            "task_id": task_id,
            "case_id": case_id,
            "status": "complete",
            "duration_s": duration_s,
            "sample_s": sample_s,
            "target_level": "integrated",
            "target_name": "orbit_environment",
            "capability_id": self.capability_id,
            "mode": mode,
            "qoi": {
                "orbit.radius_mean_m": summary_obj.orbit_radius_mean_m,
                "orbit.radius_span_m": summary_obj.orbit_radius_span_m,
                "environment.eclipse_fraction": summary_obj.eclipse_fraction,
                "environment.shadow_min": summary_obj.shadow_min,
                "environment.shadow_max": summary_obj.shadow_max,
                "environment.magnetic_field_norm_min_t": summary_obj.magnetic_field_norm_min_t,
                "environment.magnetic_field_norm_max_t": summary_obj.magnetic_field_norm_max_t,
                "ground.access_fraction": summary_obj.access_fraction,
                "ground.access_windows": summary_obj.access_windows,
            },
            "events": {"fault_count": 0, "degradation_count": 0},
            "trace_rows": len(rows),
        }
        labels = {"run_labels": [{"task_id": task_id, "mode": mode, "capability_id": self.capability_id}]}
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata={"capability_id": self.capability_id})

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated orbit_environment.leo_simple.v1 capability script.

This deterministic script executes the explicit OrbitEnvironmentLeoSimpleAdapter
and does not call legacy demo harness functions.
"""

import json
from pathlib import Path

from sat_sim.adapters.orbit_environment_leo_simple import OrbitEnvironmentLeoSimpleAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = OrbitEnvironmentLeoSimpleAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{i.path}}: {{i.message}}" for i in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "orbit_environment_capability_output")))
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
        if capability and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        from sat_sim.outputs.standard_fields import ORBIT_ENVIRONMENT_TRACE_SCHEMA
        return {"trace": ORBIT_ENVIRONMENT_TRACE_SCHEMA}

    @staticmethod
    def _valid_vector3(value: Any) -> bool:
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return False
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
            return False
        return sum(float(x) ** 2 for x in value) > 0.0

    @staticmethod
    def _merged_orbit_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        merged = dict(orbit)
        merged.update(dict(params))
        return merged


__all__ = ["OrbitEnvironmentLeoSimpleAdapter"]
