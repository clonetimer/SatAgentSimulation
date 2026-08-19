"""ORB-2 orbit fidelity benchmark scenarios and tolerance envelopes."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Callable
import csv
import json
from pathlib import Path

from sat_sim.adapters.orbit_environment_orbit_fidelity import OrbitEnvironmentOrbitFidelityAdapter
from sat_sim.orbit.numerical import OrbitFidelityConfig, propagate_orbit_fidelity, summarize_orbit_fidelity
from sat_sim.time_systems import build_time_grid

ORB2_BENCHMARK_SCHEMA_VERSION = "orb2.orbit_validation_benchmark.v1"


@dataclass(frozen=True)
class OrbitQoIEnvelope:
    name: str
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def evaluate(self, value: float) -> dict[str, Any]:
        status = "pass"
        if self.minimum is not None and value < self.minimum:
            status = "fail"
        if self.maximum is not None and value > self.maximum:
            status = "fail"
        return {"name": self.name, "value": value, "minimum": self.minimum, "maximum": self.maximum, "status": status, "description": self.description}


@dataclass(frozen=True)
class OrbitBenchmarkCase:
    case_id: str
    description: str
    task_spec: dict[str, Any]
    envelopes: tuple[OrbitQoIEnvelope, ...]


def _base_spec(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "orbit_environment",
        "capability_id": "orbit_environment.orbit_fidelity.v1",
        "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
        "simulation": {"duration_s": 5400.0, "sample_s": 60.0, "seed": 0, "backend": "python", "epoch_utc": "2026-07-05T00:00:00Z", "solver": {"method": "rk4", "step_s": 60.0, "include_endpoint": True}},
        "orbit_environment": {"altitude_m": 520_000.0, "eccentricity": 0.001, "inclination_deg": 97.5, "raan_deg": 0.0, "true_anomaly_deg": 0.0, "enable_eclipse": True, "force_models": {"j2": True, "drag": False, "srp": False}},
        "parameters": {"spacecraft": {"mass_kg": 120.0, "drag_area_m2": 1.2, "drag_coefficient": 2.2, "srp_area_m2": 1.0, "reflectivity_coefficient": 1.3}},
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "metadata": {"case_id": case_id, "benchmark": "ORB-2"},
    }


def orbit_benchmark_cases() -> list[OrbitBenchmarkCase]:
    circ = _base_spec("orb2_circular_leo")
    j2 = _base_spec("orb2_j2_acceleration")
    drag = _base_spec("orb2_drag_decay")
    drag["simulation"]["duration_s"] = 10_800.0
    drag["orbit_environment"]["altitude_m"] = 260_000.0
    drag["orbit_environment"]["force_models"] = {"j2": True, "drag": True, "srp": False}
    drag["orbit_environment"]["atmosphere"] = {"enabled": True, "reference_altitude_m": 260_000.0, "reference_density_kg_m3": 2.0e-10, "scale_height_m": 50_000.0}
    srp = _base_spec("orb2_srp_proxy")
    srp["orbit_environment"]["force_models"] = {"j2": True, "drag": False, "srp": True}
    srp["parameters"]["spacecraft"]["srp_area_m2"] = 3.0
    access = _base_spec("orb2_ground_access")
    access["orbit_environment"]["ground_station"] = {"name": "test_site", "latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 0.0, "min_elevation_deg": 0.0}
    return [
        OrbitBenchmarkCase("orb2_circular_leo", "LEO radius/speed sanity with numerical central+J2 model", circ, (
            OrbitQoIEnvelope("orbit.altitude_min_m", 450_000.0, 590_000.0),
            OrbitQoIEnvelope("orbit.altitude_span_m", 0.0, 80_000.0),
            OrbitQoIEnvelope("orbit.speed_mean_m_s", 7200.0, 8000.0),
        )),
        OrbitBenchmarkCase("orb2_j2_acceleration", "J2 acceleration contribution is non-zero and traceable", j2, (
            OrbitQoIEnvelope("orbit.force.j2_norm_mean_m_s2", 1.0e-3, 2.0e-2),
            OrbitQoIEnvelope("orbit.force.drag_norm_mean_m_s2", 0.0, 1.0e-12),
        )),
        OrbitBenchmarkCase("orb2_drag_decay", "Dense-proxy drag scenario produces negative altitude/energy trend", drag, (
            OrbitQoIEnvelope("orbit.force.drag_norm_mean_m_s2", 1.0e-7, 1.0e-3),
            OrbitQoIEnvelope("orbit.altitude_delta_m", -1.0e9, -0.01),
            OrbitQoIEnvelope("orbit.specific_energy_delta_j_kg", -1.0e9, -0.01),
        )),
        OrbitBenchmarkCase("orb2_srp_proxy", "SRP proxy is enabled and traceable", srp, (
            OrbitQoIEnvelope("orbit.force.srp_norm_mean_m_s2", 1.0e-9, 1.0e-6),
        )),
        OrbitBenchmarkCase("orb2_ground_access", "Ground access geometry remains serialized with orbit-fidelity traces", access, (
            OrbitQoIEnvelope("orbit.altitude_min_m", 450_000.0, 590_000.0),
        )),
    ]


def run_orbit_fidelity_benchmark(report_dir: str | Path) -> dict[str, Any]:
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    adapter = OrbitEnvironmentOrbitFidelityAdapter()
    case_results: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    for case in orbit_benchmark_cases():
        issues = adapter.validate(case.task_spec)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            summary = {"status": "validation_error", "qoi": {}}
        else:
            result = adapter.run(case.task_spec)
            summary = result.summary
        qoi = summary.get("qoi", {}) if isinstance(summary.get("qoi"), dict) else {}
        rows = []
        for env in case.envelopes:
            value = float(qoi.get(env.name, 0.0))
            row = env.evaluate(value)
            row["case_id"] = case.case_id
            rows.append(row)
            envelope_rows.append(row)
        status = "pass" if rows and all(row["status"] == "pass" for row in rows) and not errors else "fail"
        case_results.append({"case_id": case.case_id, "description": case.description, "status": status, "qoi": qoi, "envelopes": rows, "error_count": len(errors)})
    report = {
        "schema_version": ORB2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "ORB-2",
        "status": "pass" if all(c["status"] == "pass" for c in case_results) else "fail",
        "case_count": len(case_results),
        "pass_count": sum(1 for c in case_results if c["status"] == "pass"),
        "fail_count": sum(1 for c in case_results if c["status"] != "pass"),
        "tolerance_envelope_count": len(envelope_rows),
        "case_results": case_results,
        "can_claim_high_fidelity": False,
        "validation_scope": "internal_regression_tolerance_envelopes_not_external_truth_validation",
    }
    (out / "orbit_fidelity_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "description", "status", "error_count"])
        writer.writeheader()
        for c in case_results:
            writer.writerow({k: c[k] for k in ["case_id", "description", "status", "error_count"]})
    with (out / "tolerance_envelopes.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "name", "value", "minimum", "maximum", "status", "description"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in envelope_rows:
            writer.writerow({k: row.get(k) for k in fieldnames})
    return report


__all__ = ["ORB2_BENCHMARK_SCHEMA_VERSION", "OrbitQoIEnvelope", "OrbitBenchmarkCase", "orbit_benchmark_cases", "run_orbit_fidelity_benchmark"]
