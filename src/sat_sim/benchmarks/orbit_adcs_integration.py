"""INT-1 orbit + ADCS integration benchmark suite."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from sat_sim.adapters.whole_spacecraft_orbit_adcs_fidelity import OrbitAdcsFidelityAdapter
from sat_sim.coupled import build_int1_orbit_adcs_payload

INT1_BENCHMARK_SCHEMA_VERSION = "int1.orbit_adcs_benchmark.v1"


@dataclass(frozen=True)
class ToleranceEnvelope:
    qoi: str
    min_value: float | None = None
    max_value: float | None = None

    def evaluate(self, value: Any) -> dict[str, Any]:
        try:
            v = float(value)
        except Exception:
            return {"qoi": self.qoi, "status": "fail", "value": value, "reason": "not_numeric"}
        ok = True
        if self.min_value is not None and v < self.min_value:
            ok = False
        if self.max_value is not None and v > self.max_value:
            ok = False
        return {"qoi": self.qoi, "status": "pass" if ok else "fail", "value": v, "min": self.min_value, "max": self.max_value}


def _base_spec(case_id: str, *, target_mode: str = "nadir", initial_error: float = 10.0) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
        "target": {"level": "whole_spacecraft", "name": "orbit_adcs", "mode": "nominal"},
        "simulation": {
            "duration_s": 600.0,
            "sample_s": 10.0,
            "backend": "python",
            "epoch_utc": "2026-07-05T00:00:00Z",
            "solver": {"method": "rk4", "step_s": 10.0, "include_endpoint": True},
        },
        "spacecraft": {"adcs": {"dyn_step_s": 0.2, "fsw_step_s": 0.2}},
        "orbit_environment": {
            "altitude_m": 520000.0,
            "eccentricity": 0.001,
            "inclination_deg": 97.5,
            "raan_deg": 10.0,
            "true_anomaly_deg": 0.0,
            "enable_eclipse": True,
            "sun_model": "analytic",
            "magnetic_field_model": "dipole",
            "force_models": {"j2": True, "drag": True, "srp": True},
            "atmosphere": {"enabled": True, "reference_altitude_m": 400000.0, "reference_density_kg_m3": 4.0e-12, "scale_height_m": 60000.0},
        },
        "parameters": {
            "spacecraft": {"mass_kg": 120.0, "drag_area_m2": 1.2, "drag_coefficient": 2.2, "srp_area_m2": 1.0, "reflectivity_coefficient": 1.3},
            "integration": {"pointing_target": target_mode, "frame_contract": "ECI_to_LVLH_or_sun_target_metadata", "coupling_policy": "trace_level_time_aligned_proxy"},
            "adcs": {
                "target_mode": target_mode,
                "initial_attitude_error_deg": initial_error,
                "initial_rate_deg_s": [0.08, -0.04, 0.02],
                "control_kp_nm_per_rad": 0.12,
                "control_kd_nm_per_rad_s": 0.9,
                "pointing_requirement_deg": 2.0,
                "environment_torques": {"gravity_gradient": True, "magnetic": True, "aerodynamic": True, "srp": True},
                "gyro_bias_deg_s": [0.005, -0.002, 0.001],
                "gyro_noise_std_deg_s": 0.001,
                "star_tracker_noise_deg": 0.002,
                "sun_sensor_noise_deg": 0.2,
            },
        },
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_manifest": True},
        "metadata": {"case_id": case_id, "route_version": "INT-1"},
    }


def benchmark_cases() -> tuple[dict[str, Any], ...]:
    return (
        {"case_id": "int1_nadir_frame_alignment", "spec": _base_spec("int1_nadir_frame_alignment", target_mode="nadir", initial_error=10.0)},
        {"case_id": "int1_sun_pointing_context", "spec": _base_spec("int1_sun_pointing_context", target_mode="sun", initial_error=8.0)},
        {"case_id": "int1_detumble_context", "spec": _base_spec("int1_detumble_context", target_mode="detumble", initial_error=4.0)},
    )


ENVELOPES: tuple[ToleranceEnvelope, ...] = (
    ToleranceEnvelope("qoi.integration.time_alignment_max_error_s", min_value=0.0, max_value=1.0e-9),
    ToleranceEnvelope("qoi.integration.frame_consistency_pass", min_value=1.0, max_value=1.0),
    ToleranceEnvelope("qoi.orbit.altitude_min_m", min_value=100000.0, max_value=3000000.0),
    ToleranceEnvelope("qoi.adcs.quaternion_norm_max_error", min_value=0.0, max_value=1.0e-6),
    ToleranceEnvelope("qoi.adcs.max_pointing_error_deg", min_value=0.0, max_value=90.0),
)


def _qoi(summary: Mapping[str, Any], dotted: str) -> Any:
    q = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
    key = dotted[4:] if dotted.startswith("qoi.") else dotted
    if key in q:
        return q[key]
    if dotted in q:
        return q[dotted]
    value: Any = summary
    for part in dotted.split("."):
        if isinstance(value, Mapping) and part in value:
            value = value[part]
        else:
            return None
    return value


def run_int1_benchmarks() -> dict[str, Any]:
    adapter = OrbitAdcsFidelityAdapter()
    case_results: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    for case in benchmark_cases():
        spec = case["spec"]
        result = adapter.run(spec)
        summary = result.summary
        case_status = "pass"
        checks = []
        for envelope in ENVELOPES:
            ev = envelope.evaluate(_qoi(summary, envelope.qoi))
            ev["case_id"] = case["case_id"]
            checks.append(ev)
            envelope_rows.append(ev)
            if ev["status"] != "pass":
                case_status = "fail"
        if summary.get("status") == "fail":
            case_status = "fail"
        case_results.append({
            "case_id": case["case_id"],
            "status": case_status,
            "summary_status": summary.get("status"),
            "target_mode": summary.get("target_mode"),
            "trace_rows": summary.get("trace_rows"),
            "checks": checks,
        })
    fail_count = sum(1 for c in case_results if c["status"] != "pass")
    return {
        "schema_version": INT1_BENCHMARK_SCHEMA_VERSION,
        "route_version": "INT-1",
        "status": "pass" if fail_count == 0 else "fail",
        "case_count": len(case_results),
        "pass_count": len(case_results) - fail_count,
        "fail_count": fail_count,
        "tolerance_envelope_count": len(envelope_rows),
        "case_results": case_results,
        "tolerance_envelopes": envelope_rows,
        "readiness_matrix": {
            "schema_version": INT1_BENCHMARK_SCHEMA_VERSION,
            "route_version": "INT-1",
            "orbit_adcs_integration_gate_available": True,
            "benchmark_status": "pass" if fail_count == 0 else "fail",
            "can_claim_package_high_fidelity": False,
            "high_fidelity_ready_count": 0,
        },
    }


def write_int1_benchmark_reports(output_dir: str | Path = "reports/orbit_adcs_integration_benchmark") -> dict[str, Any]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = run_int1_benchmarks()
    (out / "orbit_adcs_integration_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "readiness_matrix.json").write_text(json.dumps(report["readiness_matrix"], indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "status", "summary_status", "target_mode", "trace_rows"])
        writer.writeheader()
        for row in report["case_results"]:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})
    with (out / "tolerance_envelopes.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "qoi", "status", "value", "min", "max"])
        writer.writeheader()
        for row in report["tolerance_envelopes"]:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})
    example_payload = build_int1_orbit_adcs_payload(benchmark_cases()[0]["spec"])
    (out / "int1_payload_example.json").write_text(json.dumps(example_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


__all__ = ["INT1_BENCHMARK_SCHEMA_VERSION", "benchmark_cases", "run_int1_benchmarks", "write_int1_benchmark_reports"]
