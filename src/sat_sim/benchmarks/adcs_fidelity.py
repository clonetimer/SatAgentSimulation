"""ADCS-2 benchmark scenarios and tolerance envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from pathlib import Path
import csv
import json

from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter

ADCS2_BENCHMARK_SCHEMA_VERSION = "adcs2.adcs_validation_benchmark.v1"


@dataclass(frozen=True)
class ADCSQoIEnvelope:
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
class ADCSBenchmarkCase:
    case_id: str
    description: str
    task_spec: dict[str, Any]
    envelopes: tuple[ADCSQoIEnvelope, ...]


def _base_spec(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"duration_s": 600.0, "sample_s": 5.0, "seed": 0, "backend": "python", "epoch_utc": "2026-07-05T00:00:00Z", "solver": {"method": "euler", "step_s": 0.5}},
        "parameters": {
            "initial_attitude_error_deg": 12.0,
            "initial_rate_deg_s": [0.1, -0.05, 0.02],
            "target_mode": "nadir",
            "control_kp_nm_per_rad": 0.12,
            "control_kd_nm_per_rad_s": 0.9,
            "pointing_requirement_deg": 1.5,
            "environment_torques": {"gravity_gradient": True, "magnetic": True, "aerodynamic": False, "srp": False},
            "gyro_bias_deg_s": [0.005, -0.002, 0.001],
            "gyro_noise_std_deg_s": 0.001,
            "star_tracker_noise_deg": 0.002,
            "sun_sensor_noise_deg": 0.2,
        },
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv", "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "metadata": {"case_id": case_id, "benchmark": "ADCS-2"},
    }


def adcs_benchmark_cases() -> list[ADCSBenchmarkCase]:
    detumble = _base_spec("adcs2_detumble")
    detumble["parameters"].update({"target_mode": "detumble", "initial_attitude_error_deg": 3.0, "initial_rate_deg_s": [0.4, -0.2, 0.1]})
    nadir = _base_spec("adcs2_nadir_pointing")
    sun = _base_spec("adcs2_sun_pointing")
    sun["parameters"].update({"target_mode": "sun", "environment_torques": {"gravity_gradient": True, "magnetic": True, "aerodynamic": False, "srp": True}})
    dropout = _base_spec("adcs2_sensor_dropout_recovery")
    dropout["parameters"].update({"sensor_dropout": {"start_s": 200.0, "duration_s": 60.0}})
    saturation = _base_spec("adcs2_wheel_saturation_guard")
    saturation["parameters"].update({"max_wheel_speed_rad_s": 250.0, "initial_wheel_speed_rad_s": [20.0, 20.0, 20.0], "max_wheel_torque_nm": 0.06})
    return [
        ADCSBenchmarkCase("adcs2_detumble", "Detumble reduces angular-rate proxy while preserving quaternion norm", detumble, (
            ADCSQoIEnvelope("adcs.max_abs_rate_rad_s", 0.0, 0.02),
            ADCSQoIEnvelope("adcs.quaternion_norm_max_error", 0.0, 1.0e-9),
        )),
        ADCSBenchmarkCase("adcs2_nadir_pointing", "Nadir pointing convergence with environmental torque proxies", nadir, (
            ADCSQoIEnvelope("adcs.final_pointing_error_deg", 0.0, 2.0),
            ADCSQoIEnvelope("adcs.environment.total_torque_norm_nm", 1.0e-9, 1.0e-4),
            ADCSQoIEnvelope("adcs.quaternion_norm_max_error", 0.0, 1.0e-9),
        )),
        ADCSBenchmarkCase("adcs2_sun_pointing", "Sun pointing includes SRP torque proxy and convergence envelope", sun, (
            ADCSQoIEnvelope("adcs.final_pointing_error_deg", 0.0, 2.5),
            ADCSQoIEnvelope("adcs.environment.total_torque_norm_nm", 1.0e-9, 1.0e-4),
        )),
        ADCSBenchmarkCase("adcs2_sensor_dropout_recovery", "Sensor dropout window is traceable and does not break controller execution", dropout, (
            ADCSQoIEnvelope("adcs.sensor.dropout_count", 1.0, 100.0),
            ADCSQoIEnvelope("adcs.final_pointing_error_deg", 0.0, 3.0),
        )),
        ADCSBenchmarkCase("adcs2_wheel_saturation_guard", "Wheel momentum remains inside declared guard in a high-demand case", saturation, (
            ADCSQoIEnvelope("adcs.rw.max_abs_momentum_nms", 0.0, 25.0),
            ADCSQoIEnvelope("adcs.quaternion_norm_max_error", 0.0, 1.0e-9),
        )),
    ]


def run_adcs_fidelity_benchmark(report_dir: str | Path) -> dict[str, Any]:
    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    adapter = AdcsFidelityAdapter()
    case_results: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    for case in adcs_benchmark_cases():
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
        "schema_version": ADCS2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "ADCS-2",
        "status": "pass" if all(c["status"] == "pass" for c in case_results) else "fail",
        "case_count": len(case_results),
        "pass_count": sum(1 for c in case_results if c["status"] == "pass"),
        "fail_count": sum(1 for c in case_results if c["status"] != "pass"),
        "tolerance_envelope_count": len(envelope_rows),
        "case_results": case_results,
        "can_claim_high_fidelity": False,
        "validation_scope": "internal_regression_tolerance_envelopes_not_flight_data_validation",
    }
    (out / "adcs_fidelity_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    readiness = {
        "schema_version": ADCS2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "ADCS-1/ADCS-2",
        "adcs_fidelity_capability_count": 1,
        "benchmark_status": report["status"],
        "benchmark_case_count": report["case_count"],
        "tolerance_envelope_count": report["tolerance_envelope_count"],
        "high_fidelity_ready_count": 0,
        "can_claim_high_fidelity": False,
        "validation_scope": report["validation_scope"],
    }
    (out / "readiness_matrix.json").write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")
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


__all__ = ["ADCS2_BENCHMARK_SCHEMA_VERSION", "ADCSQoIEnvelope", "ADCSBenchmarkCase", "adcs_benchmark_cases", "run_adcs_fidelity_benchmark"]
