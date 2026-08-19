"""THERM-1C thermal control policy benchmark harness."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter
from sat_sim.adapters.whole_spacecraft_orbit_attitude_thermal import WholeSpacecraftOrbitAttitudeThermalAdapter

THERM1C_CONTROL_BENCHMARK_SCHEMA_VERSION = "therm1c.thermal_control_benchmark.v1"


@dataclass(frozen=True)
class ThermalControlEnvelope:
    name: str
    minimum: float | None = None
    maximum: float | None = None
    expected: Any | None = None
    description: str = ""

    def evaluate(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        value = summary.get(self.name)
        if self.expected is not None:
            status = "pass" if value == self.expected else "fail"
            return {"name": self.name, "value": value, "expected": self.expected, "status": status, "description": self.description}
        try:
            numeric = float(value)
        except Exception:
            return {"name": self.name, "value": value, "minimum": self.minimum, "maximum": self.maximum, "status": "not_evaluated", "description": self.description}
        status = "pass"
        if self.minimum is not None and numeric < self.minimum:
            status = "fail"
        if self.maximum is not None and numeric > self.maximum:
            status = "fail"
        return {"name": self.name, "value": numeric, "minimum": self.minimum, "maximum": self.maximum, "status": status, "description": self.description}


@dataclass(frozen=True)
class ThermalControlBenchmarkCase:
    case_id: str
    description: str
    adapter_kind: str
    task_spec: dict[str, Any]
    control_mode: str
    envelopes: tuple[ThermalControlEnvelope, ...]


def _subsystem_spec(case_id: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "target": {"level": "subsystem", "name": "thermal_reduced_order", "mode": "nominal"},
        "simulation": {"duration_s": 2400.0, "sample_s": 60.0},
        "orbit_environment": {"altitude_m": 520000.0, "eclipse_period_s": 5400.0, "eclipse_duration_s": 2400.0},
        "parameters": {
            "template_id": "cubesat_7node_box_template",
            "template_overrides": {
                "nodes": {"internal": {"initial_temp_c": -8.0}},
                "environment": {"eclipse_duration_s": 2400.0, "eclipse_period_s": 5400.0},
                "internal_power_by_node_w": {"internal": 2.0},
            },
            "heater_control": {"mode": mode, "node": "internal", "max_power_w": 10.0, "setpoint_c": 2.0, "deadband_c": 2.0},
        },
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv"},
        "metadata": {"case_id": case_id, "benchmark": "THERM-1C"},
    }


def _whole_spec(case_id: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.orbit_attitude_thermal.v1",
        "target": {"level": "whole_spacecraft", "name": "orbit_attitude_thermal", "mode": "nominal"},
        "simulation": {"duration_s": 2400.0, "sample_s": 60.0},
        "spacecraft": {"name": "therm1c_control_demo"},
        "orbit_environment": {"altitude_m": 520000.0, "eclipse_period_s": 5400.0, "eclipse_duration_s": 1800.0},
        "parameters": {
            "thermal": {
                "template_id": "cubesat_7node_box_template",
                "template_overrides": {"nodes": {"internal": {"initial_temp_c": -6.0}}},
                "heater_control": {"mode": mode, "node": "internal", "max_power_w": 10.0, "setpoint_c": 2.0, "deadband_c": 2.0, "kp_w_per_c": 1.2},
            },
            "attitude": {"mode": "nadir"},
            "power": {"bus_load_power_w": 5.0, "payload_load_power_w": 2.0},
        },
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv"},
        "metadata": {"case_id": case_id, "benchmark": "THERM-1C"},
    }


def thermal_control_benchmark_cases() -> list[ThermalControlBenchmarkCase]:
    threshold = _subsystem_spec("therm1c_threshold_legacy_guard", "threshold")
    hysteresis = _subsystem_spec("therm1c_hysteresis_thermostat_guard", "hysteresis")
    pid = _subsystem_spec("therm1c_pid_bounded_power_guard", "pid")
    pid["parameters"]["heater_control"].update({"kp_w_per_c": 1.3, "ki_w_per_c_s": 0.002, "integral_limit_c_s": 5000.0})
    saturation = _subsystem_spec("therm1c_pid_saturation_guard", "pid")
    saturation["parameters"]["heater_control"].update({"kp_w_per_c": 50.0, "ki_w_per_c_s": 0.0, "kd_w_s_per_c": 0.0})
    whole = _whole_spec("therm1c_whole_spacecraft_pid_control", "pid")
    return [
        ThermalControlBenchmarkCase("therm1c_threshold_legacy_guard", "Legacy threshold heater policy remains deterministic and backward compatible.", "subsystem", threshold, "threshold", (ThermalControlEnvelope("qoi.thermal.control_mode", expected="threshold"), ThermalControlEnvelope("qoi.thermal.heater_energy_wh", 0.0, None))),
        ThermalControlBenchmarkCase("therm1c_hysteresis_thermostat_guard", "Stateful thermostat hysteresis policy reports transitions and duty cycle.", "subsystem", hysteresis, "hysteresis", (ThermalControlEnvelope("qoi.thermal.control_mode", expected="hysteresis"), ThermalControlEnvelope("qoi.thermal.heater_duty_cycle", 0.0, 1.0))),
        ThermalControlBenchmarkCase("therm1c_pid_bounded_power_guard", "PID heater command remains bounded and exposes PID trace terms.", "subsystem", pid, "pid", (ThermalControlEnvelope("qoi.thermal.control_mode", expected="pid"), ThermalControlEnvelope("qoi.thermal.heater_duty_cycle", 0.0, 1.0), ThermalControlEnvelope("qoi.thermal.heater_saturation_count", 0.0, 9999.0))),
        ThermalControlBenchmarkCase("therm1c_pid_saturation_guard", "Aggressive PID case triggers explicit saturation trace without exceeding max power.", "subsystem", saturation, "pid", (ThermalControlEnvelope("qoi.thermal.control_mode", expected="pid"), ThermalControlEnvelope("qoi.thermal.heater_saturation_count", 1.0, None))),
        ThermalControlBenchmarkCase("therm1c_whole_spacecraft_pid_control", "Whole-spacecraft orbit-attitude-thermal wrapper propagates child PID control metadata.", "whole_spacecraft", whole, "pid", (ThermalControlEnvelope("qoi.spacecraft.thermal.control_mode", expected="pid"), ThermalControlEnvelope("qoi.spacecraft.thermal.heater_energy_wh", 0.0, None))),
    ]


def _run_case(case: ThermalControlBenchmarkCase) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], list[dict[str, Any]]]:
    adapter = ThermalReducedOrderAdapter() if case.adapter_kind == "subsystem" else WholeSpacecraftOrbitAttitudeThermalAdapter()
    issues = adapter.validate(case.task_spec)
    errors = [issue.to_dict() for issue in issues if issue.severity == "error"]
    if errors:
        return {"status": "fail", "case_id": case.case_id, "errors": errors}, tuple(), []
    result = adapter.run(case.task_spec)
    summary = dict(result.summary)
    envelope_results = [env.evaluate(summary) for env in case.envelopes]
    status = "pass" if summary.get("status") == "pass" and all(er["status"] in {"pass", "not_evaluated"} for er in envelope_results) else "fail"
    # Extra trace-level control guardrails.
    if case.control_mode == "pid":
        powers = [float(row.get("thermal.heater.power_w", 0.0)) for row in result.trace_rows]
        max_allowed = float(case.task_spec.get("parameters", {}).get("heater_control", {}).get("max_power_w", 10.0)) if case.adapter_kind == "subsystem" else 10.0
        if powers and (min(powers) < -1.0e-12 or max(powers) > max_allowed + 1.0e-12):
            status = "fail"
    summary["case_status"] = status
    return summary, result.trace_rows, envelope_results


def run_thermal_control_benchmark(report_dir: str | Path = "reports/thermal_control_benchmark") -> dict[str, Any]:
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    cases = thermal_control_benchmark_cases()
    case_results: list[dict[str, Any]] = []
    fail_count = 0
    for case in cases:
        summary, rows, envelope_results = _run_case(case)
        status = "pass" if summary.get("case_status", summary.get("status")) == "pass" else "fail"
        if status != "pass":
            fail_count += 1
        case_dir = report_path / case.case_id
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if rows:
            with (case_dir / "trace.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=sorted({k for row in rows for k in row.keys()}))
                writer.writeheader()
                writer.writerows(rows)
        case_results.append({
            "case_id": case.case_id,
            "description": case.description,
            "status": status,
            "adapter_kind": case.adapter_kind,
            "control_mode": case.control_mode,
            "summary": summary,
            "trace_rows": len(rows),
            "envelope_results": envelope_results,
        })
    report = {
        "schema_version": THERM1C_CONTROL_BENCHMARK_SCHEMA_VERSION,
        "status": "pass" if fail_count == 0 else "fail",
        "case_count": len(cases),
        "pass_count": len(cases) - fail_count,
        "fail_count": fail_count,
        "supported_control_modes": ["threshold", "hysteresis", "pid"],
        "control_policy_status": "deterministic_early_design_not_flight_software",
        "flight_validated": False,
        "thermal_desktop_equivalent": False,
        "can_claim_high_fidelity": False,
        "case_results": case_results,
    }
    (report_path / "thermal_control_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_lines = [
        "# THERM-1C Thermal Control Benchmark Report",
        "",
        f"- status: `{report['status']}`",
        f"- case_count: `{report['case_count']}`",
        f"- supported_control_modes: `{', '.join(report['supported_control_modes'])}`",
        f"- flight_validated: `{report['flight_validated']}`",
        "",
        "| Case | Mode | Adapter | Status | Trace rows |",
        "|---|---|---|---:|---:|",
    ]
    for result in case_results:
        md_lines.append(f"| `{result['case_id']}` | `{result['control_mode']}` | `{result['adapter_kind']}` | `{result['status']}` | {result['trace_rows']} |")
    (report_path / "thermal_control_benchmark_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    (report_path / "cases.json").write_text(json.dumps({"schema_version": THERM1C_CONTROL_BENCHMARK_SCHEMA_VERSION, "case_ids": [case.case_id for case in cases]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


__all__ = ["THERM1C_CONTROL_BENCHMARK_SCHEMA_VERSION", "thermal_control_benchmark_cases", "run_thermal_control_benchmark"]
