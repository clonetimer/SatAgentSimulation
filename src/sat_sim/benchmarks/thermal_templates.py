"""THERM-1B public-case-compatible thermal-template benchmark harness."""
from __future__ import annotations

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from sat_sim.adapters.subsystem_thermal_reduced_order import ThermalReducedOrderAdapter
from sat_sim.adapters.whole_spacecraft_orbit_attitude_thermal import WholeSpacecraftOrbitAttitudeThermalAdapter
from subsystems.thermal.templates import thermal_template_inventory

THERM1B_TEMPLATE_BENCHMARK_SCHEMA_VERSION = "therm1b.thermal_template_benchmark.v1"


@dataclass(frozen=True)
class ThermalTemplateEnvelope:
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
class ThermalTemplateBenchmarkCase:
    case_id: str
    description: str
    adapter_kind: str
    task_spec: dict[str, Any]
    template_id: str
    envelopes: tuple[ThermalTemplateEnvelope, ...]


def _subsystem_spec(case_id: str, template_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "target": {"level": "subsystem", "name": "thermal_reduced_order", "mode": "nominal"},
        "simulation": {"duration_s": 3600.0, "sample_s": 60.0},
        "orbit_environment": {"altitude_m": 520000.0, "eclipse_period_s": 5400.0, "eclipse_duration_s": 0.0},
        "parameters": {"template_id": template_id, "attitude": {"mode": "sun_pointing"}},
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv"},
        "metadata": {"case_id": case_id, "benchmark": "THERM-1B"},
    }


def _whole_spec(case_id: str, template_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.orbit_attitude_thermal.v1",
        "target": {"level": "whole_spacecraft", "name": "orbit_attitude_thermal", "mode": "nominal"},
        "simulation": {"duration_s": 5400.0, "sample_s": 60.0},
        "spacecraft": {"name": "therm1b_public_reference_demo", "eps": {"initial_soc": 0.75}},
        "orbit_environment": {"altitude_m": 520000.0, "eclipse_period_s": 5400.0, "eclipse_duration_s": 1800.0},
        "parameters": {
            "thermal": {"template_id": template_id, "heater_power_w": 8.0},
            "attitude": {"mode": "nadir"},
            "power": {"bus_load_power_w": 12.0, "payload_load_power_w": 6.0, "adcs_load_power_w": 3.0, "comm_load_power_w": 2.0},
        },
        "outputs": {"output_root": f"datasets/{case_id}", "trace_format": "csv"},
        "metadata": {"case_id": case_id, "benchmark": "THERM-1B"},
    }


def thermal_template_benchmark_cases() -> list[ThermalTemplateBenchmarkCase]:
    single = _subsystem_spec("therm1b_single_node_leo_baseline", "nasa_single_node_leo_baseline")
    single["parameters"]["internal_power_by_node_w"] = {"internal": 5.0}
    cube = _subsystem_spec("therm1b_cubesat_7node_eclipse_transition", "cubesat_7node_box_template")
    cube["simulation"]["duration_s"] = 7200.0
    cube["orbit_environment"]["eclipse_duration_s"] = 1800.0
    cube["parameters"]["template_overrides"] = {"environment": {"eclipse_duration_s": 1800.0, "eclipse_period_s": 5400.0}, "internal_power_by_node_w": {"internal": 14.0}}
    tumble = _subsystem_spec("therm1b_cubesat_7node_tumbling_exposure", "cubesat_7node_box_template")
    tumble["parameters"]["attitude"] = {"mode": "tumbling"}
    satmo = _subsystem_spec("therm1b_satmo_like_mars_flux_scaling", "satmo_like_multiplanet_box_template")
    satmo["parameters"]["template_overrides"] = {"environment": {"solar_flux_w_m2": 586.0, "albedo_flux_w_m2": 35.0, "earth_ir_flux_w_m2": 110.0}}
    whole = _whole_spec("therm1b_whole_spacecraft_orbit_attitude_thermal", "cubesat_7node_box_template")
    return [
        ThermalTemplateBenchmarkCase("therm1b_single_node_leo_baseline", "Single-node LEO thermal balance sanity template.", "subsystem", single, "nasa_single_node_leo_baseline", (ThermalTemplateEnvelope("node_count", 1.0, 1.0), ThermalTemplateEnvelope("qoi.thermal.max_node_temp_c", -120.0, 180.0))),
        ThermalTemplateBenchmarkCase("therm1b_cubesat_7node_eclipse_transition", "CubeSat 7-node eclipse transition sanity template.", "subsystem", cube, "cubesat_7node_box_template", (ThermalTemplateEnvelope("node_count", 7.0, 7.0), ThermalTemplateEnvelope("qoi.thermal.heater_energy_wh", 0.0, None), ThermalTemplateEnvelope("qoi.thermal.max_node_temp_c", -120.0, 180.0))),
        ThermalTemplateBenchmarkCase("therm1b_cubesat_7node_tumbling_exposure", "Attitude exposure map sanity case using tumbling mode.", "subsystem", tumble, "cubesat_7node_box_template", (ThermalTemplateEnvelope("node_count", 7.0, 7.0), ThermalTemplateEnvelope("qoi.thermal.max_energy_balance_residual_w", 0.0, 1e-8))),
        ThermalTemplateBenchmarkCase("therm1b_satmo_like_mars_flux_scaling", "SATMO-like multi-planet template with Mars-like flux scaling.", "subsystem", satmo, "satmo_like_multiplanet_box_template", (ThermalTemplateEnvelope("node_count", 7.0, 7.0), ThermalTemplateEnvelope("qoi.thermal.max_node_temp_c", -150.0, 160.0))),
        ThermalTemplateBenchmarkCase("therm1b_whole_spacecraft_orbit_attitude_thermal", "Whole-spacecraft orbit-attitude-power-to-thermal coupling sanity gate.", "whole_spacecraft", whole, "cubesat_7node_box_template", (ThermalTemplateEnvelope("qoi.spacecraft.thermal.heater_energy_wh", 0.0, None), ThermalTemplateEnvelope("qoi.spacecraft.thermal.max_internal_temp_c", -120.0, 180.0))),
    ]


def _run_case(case: ThermalTemplateBenchmarkCase) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], list[dict[str, Any]]]:
    adapter = ThermalReducedOrderAdapter() if case.adapter_kind == "subsystem" else WholeSpacecraftOrbitAttitudeThermalAdapter()
    issues = adapter.validate(case.task_spec)
    errors = [issue.to_dict() for issue in issues if issue.severity == "error"]
    if errors:
        return {"status": "fail", "errors": errors, "case_id": case.case_id}, tuple(), []
    result = adapter.run(case.task_spec)
    summary = dict(result.summary)
    envelope_results = [env.evaluate(summary) for env in case.envelopes]
    status = "pass" if summary.get("status") == "pass" and all(er["status"] in {"pass", "not_evaluated"} for er in envelope_results) else "fail"
    summary["case_status"] = status
    return summary, result.trace_rows, envelope_results


def run_thermal_template_benchmark(report_dir: str | Path = "reports/thermal_template_benchmark") -> dict[str, Any]:
    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    cases = thermal_template_benchmark_cases()
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
            "template_id": case.template_id,
            "summary": summary,
            "trace_rows": len(rows),
            "envelope_results": envelope_results,
        })
    report = {
        "schema_version": THERM1B_TEMPLATE_BENCHMARK_SCHEMA_VERSION,
        "status": "pass" if fail_count == 0 else "fail",
        "case_count": len(cases),
        "pass_count": len(cases) - fail_count,
        "fail_count": fail_count,
        "template_inventory": thermal_template_inventory(),
        "public_reference_informed": True,
        "flight_validated": False,
        "thermal_desktop_equivalent": False,
        "can_claim_high_fidelity": False,
        "case_results": case_results,
    }
    (report_path / "thermal_template_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_lines = [
        "# THERM-1B Thermal Template Benchmark Report",
        "",
        f"- status: `{report['status']}`",
        f"- case_count: `{report['case_count']}`",
        f"- template_count: `{report['template_inventory']['template_count']}`",
        f"- flight_validated: `{report['flight_validated']}`",
        "",
        "| Case | Template | Adapter | Status | Trace rows |",
        "|---|---|---|---:|---:|",
    ]
    for result in case_results:
        md_lines.append(f"| `{result['case_id']}` | `{result['template_id']}` | `{result['adapter_kind']}` | `{result['status']}` | {result['trace_rows']} |")
    (report_path / "thermal_template_benchmark_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return report


__all__ = ["THERM1B_TEMPLATE_BENCHMARK_SCHEMA_VERSION", "thermal_template_benchmark_cases", "run_thermal_template_benchmark"]
