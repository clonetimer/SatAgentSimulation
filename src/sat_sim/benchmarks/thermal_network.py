"""THERM-1 reduced-order thermal-network benchmark harness."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import csv
import json

from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter
from subsystems.thermal.network import ThermalNetworkConfig, propagate_thermal_network

THERM1_BENCHMARK_SCHEMA_VERSION = "therm1.thermal_network_benchmark.v1"


@dataclass(frozen=True)
class ThermalNetworkEnvelope:
    name: str
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def evaluate(self, summary: Mapping[str, Any]) -> dict[str, Any]:
        value = summary.get(self.name)
        try:
            numeric = float(value)
        except Exception:
            return {"name": self.name, "value": None, "minimum": self.minimum, "maximum": self.maximum, "status": "not_evaluated", "description": self.description}
        status = "pass"
        if self.minimum is not None and numeric < self.minimum:
            status = "fail"
        if self.maximum is not None and numeric > self.maximum:
            status = "fail"
        return {"name": self.name, "value": numeric, "minimum": self.minimum, "maximum": self.maximum, "status": status, "description": self.description}


@dataclass(frozen=True)
class ThermalNetworkBenchmarkCase:
    case_id: str
    description: str
    task_spec: dict[str, Any]
    public_case_tags: tuple[str, ...]
    envelopes: tuple[ThermalNetworkEnvelope, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "task_spec": self.task_spec,
            "public_case_tags": list(self.public_case_tags),
            "envelopes": [env.__dict__ for env in self.envelopes],
        }


def _base_spec(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "target": {"level": "subsystem", "name": "thermal_reduced_order", "mode": "nominal"},
        "simulation": {"duration_s": 5400.0, "sample_s": 60.0, "epoch_utc": "2026-07-06T00:00:00Z"},
        "orbit_environment": {"altitude_m": 520_000.0, "eclipse_period_s": 5400.0, "eclipse_duration_s": 0.0},
        "parameters": {
            "attitude": {"mode": "sun_pointing"},
            "initial_temp_c": 20.0,
            "internal_power_w": 12.0,
            "face_area_m2": 0.01,
            "face_heat_capacity_j_k": 900.0,
            "internal_heat_capacity_j_k": 5000.0,
            "conductance_internal_face_w_k": 0.45,
            "radiator_face": "-X",
            "radiator_multiplier": 1.8,
            "heater_power_w": 8.0,
            "heater_setpoint_c": 2.0,
            "heater_deadband_c": 2.0,
            "min_temp_c_by_node": {"internal": -20.0},
            "max_temp_c_by_node": {"internal": 65.0},
        },
        "metadata": {"case_id": case_id, "benchmark": "THERM-1", "validation_claim": "public_reference_informed_not_flight_validated"},
    }


def thermal_network_benchmark_cases() -> list[ThermalNetworkBenchmarkCase]:
    nominal = _base_spec("therm1_7node_nominal_sun_pointing")

    eclipse = _base_spec("therm1_eclipse_heater_recovery")
    eclipse["simulation"]["duration_s"] = 7200.0
    eclipse["orbit_environment"]["eclipse_duration_s"] = 2100.0
    eclipse["parameters"]["environment"] = {"eclipse_duration_s": 2100.0, "eclipse_period_s": 5400.0}
    eclipse["parameters"]["initial_temp_c"] = -5.0
    eclipse["parameters"]["heater_setpoint_c"] = 5.0
    eclipse["parameters"]["heater_power_w"] = 12.0

    radiator = _base_spec("therm1_radiator_area_sensitivity")
    radiator["parameters"]["radiator_multiplier"] = 3.0
    radiator["parameters"]["internal_power_w"] = 28.0
    radiator["parameters"]["max_temp_c_by_node"] = {"internal": 75.0}

    tumbling = _base_spec("therm1_tumbling_public_reference_sanity")
    tumbling["parameters"]["attitude"] = {"mode": "tumbling"}
    tumbling["parameters"]["internal_power_w"] = 8.0
    tumbling["parameters"]["radiator_multiplier"] = 1.2

    return [
        ThermalNetworkBenchmarkCase(
            "therm1_7node_nominal_sun_pointing",
            "Seven-node CubeSat-style reduced-order thermal network sanity case.",
            nominal,
            ("cubesat_thermal_power_toolbox_7node", "satmo_6node"),
            (
                ThermalNetworkEnvelope("node_count", 7.0, 7.0, "Default network should contain six faces plus internal node."),
                ThermalNetworkEnvelope("qoi.thermal.max_energy_balance_residual_w", 0.0, 1.0e-9, "Explicit Euler energy bookkeeping residual."),
                ThermalNetworkEnvelope("qoi.thermal.max_internal_temp_c", -80.0, 90.0, "Broad sanity envelope for non-flight default parameters."),
            ),
        ),
        ThermalNetworkBenchmarkCase(
            "therm1_eclipse_heater_recovery",
            "Eclipse case with heater activity and recovery after sunlight returns.",
            eclipse,
            ("nasa_preliminary_thermal_analysis", "single_node_baseline"),
            (
                ThermalNetworkEnvelope("qoi.thermal.heater_energy_wh", 0.001, None, "Heater should consume energy during cold eclipse case."),
                ThermalNetworkEnvelope("qoi.thermal.min_internal_temp_c", -80.0, 40.0, "Cold case should remain within broad sanity bounds."),
            ),
        ),
        ThermalNetworkBenchmarkCase(
            "therm1_radiator_area_sensitivity",
            "Higher radiator multiplier under larger internal power; exercises radiator energy rejection.",
            radiator,
            ("thermal_desktop_correlation_case_inspired", "3cat4_tvac_boundary"),
            (
                ThermalNetworkEnvelope("qoi.thermal.radiator_energy_wh", 0.001, None, "Radiator should reject non-zero heat."),
                ThermalNetworkEnvelope("qoi.thermal.max_internal_temp_c", -50.0, 120.0, "Broad sanity envelope for high power non-flight case."),
            ),
        ),
        ThermalNetworkBenchmarkCase(
            "therm1_tumbling_public_reference_sanity",
            "Tumbling exposure sanity case inspired by early CubeSat thermal trade studies.",
            tumbling,
            ("public_reference_informed", "tumbling_exposure_proxy"),
            (
                ThermalNetworkEnvelope("node_count", 7.0, 7.0, "Default network should stay seven-node."),
                ThermalNetworkEnvelope("qoi.thermal.max_node_temp_c", -80.0, 100.0, "Broad sanity envelope for public-reference-informed defaults."),
            ),
        ),
    ]


def run_thermal_network_benchmark(report_dir: str | Path = "reports/thermal_network_benchmark") -> dict[str, Any]:
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    adapter = ThermalReducedOrderAdapter()
    cases = thermal_network_benchmark_cases()
    case_results: list[dict[str, Any]] = []
    fail_count = 0
    for case in cases:
        issues = adapter.validate(case.task_spec)
        errors = [issue.to_dict() for issue in issues if issue.severity == "error"]
        if errors:
            summary = {"status": "fail", "errors": errors, "case_id": case.case_id}
            rows: tuple[dict[str, Any], ...] = ()
        else:
            result = adapter.run(case.task_spec)
            summary = dict(result.summary)
            rows = result.trace_rows
        envelope_results = [env.evaluate(summary) for env in case.envelopes]
        status = "pass" if summary.get("status") == "pass" and all(er["status"] in {"pass", "not_evaluated"} for er in envelope_results) else "fail"
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
            "summary": summary,
            "public_case_tags": list(case.public_case_tags),
            "envelope_results": envelope_results,
            "trace_rows": len(rows),
        })
    report = {
        "schema_version": THERM1_BENCHMARK_SCHEMA_VERSION,
        "status": "pass" if fail_count == 0 else "fail",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "benchmark_status": "internal_benchmark_passed" if fail_count == 0 else "internal_benchmark_failed",
        "case_count": len(cases),
        "pass_count": len(cases) - fail_count,
        "fail_count": fail_count,
        "public_reference_informed": True,
        "flight_validated": False,
        "can_claim_high_fidelity": False,
        "case_results": case_results,
    }
    (report_path / "thermal_network_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (report_path / "cases.json").write_text(json.dumps([case.to_dict() for case in cases], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_lines = [
        "# THERM-1 Thermal Network Benchmark Report",
        "",
        f"- status: `{report['status']}`",
        f"- case_count: `{report['case_count']}`",
        f"- public_reference_informed: `{report['public_reference_informed']}`",
        f"- flight_validated: `{report['flight_validated']}`",
        "",
        "| Case | Status | Trace rows | Public tags |",
        "|---|---:|---:|---|",
    ]
    for result in case_results:
        md_lines.append(f"| `{result['case_id']}` | `{result['status']}` | {result['trace_rows']} | {', '.join(result['public_case_tags'])} |")
    (report_path / "thermal_network_benchmark_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return report


__all__ = ["THERM1_BENCHMARK_SCHEMA_VERSION", "thermal_network_benchmark_cases", "run_thermal_network_benchmark"]
