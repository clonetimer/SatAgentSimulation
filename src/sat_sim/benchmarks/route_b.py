"""HF-9 Route-B physics benchmark scenarios and tolerance envelopes.

HF-9 does not add a new physics model.  It turns the Route-B prototype models
from HF-3 through HF-8 into a repeatable regression suite with explicit QoI
(quantity-of-interest) envelopes.  Passing this suite is evidence that the
engineering prototype is stable and auditable; it is still not evidence of full
high-fidelity flight validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
import copy
import csv
import json
import math

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.orbit_environment_medium_fidelity import OrbitEnvironmentMediumFidelityAdapter
from sat_sim.adapters.subsystem_adcs_closed_loop_basic import AdcsClosedLoopBasicAdapter
from sat_sim.adapters.whole_spacecraft_power_thermal_orbit_coupled import PowerThermalOrbitCoupledAdapter
from sat_sim.adapters.whole_spacecraft_comm_payload_mission_coupled import CommPayloadMissionCoupledAdapter
from sat_sim.adapters.whole_spacecraft_maneuver_orbit_attitude import ManeuverOrbitAttitudeAdapter
from sat_sim.validation import evaluate_physical_validation

HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION = "hf9.physics_benchmark.v1"


class BenchmarkEnvelopeError(ValueError):
    """Raised when an HF-9 benchmark case definition is invalid."""


@dataclass(frozen=True)
class BenchmarkCase:
    """One HF-9 benchmark scenario.

    ``qoi_envelopes`` uses flat summary.qoi keys.  Supported envelope fields:
    ``min``, ``max``, ``equals``, ``abs_max`` and ``required``.
    """

    case_id: str
    title: str
    capability_id: str
    task_spec: Mapping[str, Any]
    qoi_envelopes: Mapping[str, Mapping[str, Any]]
    physical_validation_required: bool = True
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "description": self.description,
            "capability_id": self.capability_id,
            "tags": list(self.tags),
            "physical_validation_required": self.physical_validation_required,
            "qoi_envelopes": {k: dict(v) for k, v in self.qoi_envelopes.items()},
            "task_spec": copy.deepcopy(dict(self.task_spec)),
        }


ADAPTERS = {
    "orbit_environment.medium_fidelity.v1": OrbitEnvironmentMediumFidelityAdapter,
    "subsystem.adcs_closed_loop.basic.v1": AdcsClosedLoopBasicAdapter,
    "whole_spacecraft.power_thermal_orbit_coupled.v1": PowerThermalOrbitCoupledAdapter,
    "whole_spacecraft.comm_payload_mission_coupled.v1": CommPayloadMissionCoupledAdapter,
    "whole_spacecraft.maneuver_orbit_attitude.v1": ManeuverOrbitAttitudeAdapter,
}


def _base_orbit_environment() -> dict[str, Any]:
    return {
        "altitude_m": 500_000.0,
        "eccentricity": 0.001,
        "inclination_deg": 51.6,
        "raan_deg": 0.0,
        "arg_perigee_deg": 0.0,
        "true_anomaly_deg": 180.0,
        "use_j2_gravity": True,
        "enable_eclipse": True,
        "sun_model": "constant",
        "sun_vector_eci": [1.0, 0.0, 0.0],
        "ground_station": {
            "name": "equator_site",
            "latitude_deg": 0.0,
            "longitude_deg": 0.0,
            "min_elevation_deg": 5.0,
        },
    }


def _orbit_eclipse_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": "hf9_orbit_eclipse_benchmark",
        "task_type": "orbit_environment",
        "capability_id": "orbit_environment.medium_fidelity.v1",
        "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
        "simulation": {
            "duration_s": 5400.0,
            "sample_s": 120.0,
            "backend": "python",
            "epoch_utc": "2026-07-05T00:00:00Z",
            "time_system": "UTC",
            "solver": {"method": "rk4", "step_s": 120.0, "deterministic_seed": 0},
        },
        "orbit_environment": _base_orbit_environment(),
        "parameters": {"ground_station": _base_orbit_environment()["ground_station"]},
        "outputs": {"output_root": "datasets/hf9_orbit_eclipse_benchmark", "include_summary": True, "include_trace": True},
        "validation": {"checks": ["orbit_altitude_sanity", "energy_balance_proxy"], "strict": False},
    }


def _adcs_pointing_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": "hf9_adcs_pointing_benchmark",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_closed_loop.basic.v1",
        "target": {"level": "subsystem", "name": "adcs_closed_loop", "mode": "nominal"},
        "simulation": {
            "duration_s": 600.0,
            "sample_s": 10.0,
            "seed": 0,
            "backend": "python",
            "time_system": "UTC",
            "epoch_utc": "2026-07-05T00:00:00Z",
            "solver": {"method": "euler", "step_s": 0.25, "deterministic_seed": 0, "include_endpoint": True},
        },
        "parameters": {
            "inertia_kg_m2": [12.0, 10.0, 8.0],
            "initial_attitude_error_deg": 12.0,
            "initial_error_axis": [0.0, 0.0, 1.0],
            "initial_rate_deg_s": [0.0, 0.0, 0.0],
            "target_mode": "inertial",
            "target_reference_vector": [1.0, 0.0, 0.0],
            "control_kp_nm_per_rad": 0.10,
            "control_kd_nm_per_rad_s": 0.85,
            "pointing_requirement_deg": 1.0,
            "disturbance_torque_nm": [0.0, 0.0, 0.0],
            "num_reaction_wheels": 3,
            "wheel_inertia_kg_m2": 0.08,
            "max_wheel_torque_nm": 0.05,
            "max_wheel_speed_rad_s": 900.0,
            "initial_wheel_speed_rad_s": [60.0, 60.0, 60.0],
            "gyro_bias_deg_s": [0.0, 0.0, 0.0],
            "gyro_noise_std_deg_s": 0.0,
        },
        "outputs": {"output_root": "datasets/hf9_adcs_pointing_benchmark", "include_summary": True, "include_trace": True},
        "validation": {"checks": ["quaternion_norm", "pointing_convergence"], "strict": False},
    }


def _power_thermal_base_spec(task_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": task_id,
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.power_thermal_orbit_coupled.v1",
        "target": {"level": "whole_spacecraft", "name": "power_thermal_orbit_coupled", "mode": "nominal"},
        "simulation": {
            "duration_s": 5400.0,
            "sample_s": 60.0,
            "backend": "python",
            "epoch_utc": "2026-07-05T00:00:00Z",
            "time_system": "UTC",
            "solver": {"method": "rk4", "step_s": 60.0, "deterministic_seed": 42},
        },
        "orbit_environment": _base_orbit_environment(),
        "spacecraft": {
            "eps": {
                "battery_capacity_wh": 180.0,
                "initial_soc": 0.72,
                "solar_array_max_power_w": 150.0,
                "bus_load_power_w": 24.0,
                "payload_load_power_w": 32.0,
                "adcs_load_power_w": 8.0,
                "comm_load_power_w": 6.0,
                "eps_efficiency": 0.97,
            },
            "thermal": {
                "initial_bus_temp_c": 18.0,
                "initial_battery_temp_c": 16.0,
                "solar_heat_w": 50.0,
                "heater_power_w": 22.0,
                "heater_setpoint_c": 4.0,
                "radiator_area_m2": 0.35,
                "radiator_emissivity": 0.82,
            },
        },
        "outputs": {"output_root": f"datasets/{task_id}", "include_summary": True, "include_trace": True},
        "validation": {"checks": ["soc_bounds", "thermal_bounds", "orbit_altitude_sanity", "energy_balance_proxy"], "strict": False},
    }


def _battery_discharge_spec() -> dict[str, Any]:
    spec = _power_thermal_base_spec("hf9_battery_discharge_benchmark")
    spec["simulation"]["duration_s"] = 1800.0
    spec["spacecraft"]["eps"].update({
        "initial_soc": 0.80,
        "solar_array_max_power_w": 1.0,
        "bus_load_power_w": 40.0,
        "payload_load_power_w": 20.0,
        "adcs_load_power_w": 8.0,
        "comm_load_power_w": 0.0,
    })
    spec["spacecraft"]["thermal"].update({"solar_heat_w": 0.0})
    return spec


def _thermal_heater_spec() -> dict[str, Any]:
    spec = _power_thermal_base_spec("hf9_thermal_heater_benchmark")
    spec["simulation"]["duration_s"] = 3600.0
    spec["spacecraft"]["eps"].update({"initial_soc": 0.45, "solar_array_max_power_w": 80.0, "bus_load_power_w": 12.0, "payload_load_power_w": 0.0, "adcs_load_power_w": 4.0, "comm_load_power_w": 0.0})
    spec["spacecraft"]["thermal"].update({
        "initial_bus_temp_c": -12.0,
        "initial_battery_temp_c": -10.0,
        "solar_heat_w": 15.0,
        "heater_power_w": 35.0,
        "heater_setpoint_c": 4.0,
        "heater_deadband_c": 1.5,
        "radiator_area_m2": 0.20,
    })
    return spec


def _comm_downlink_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": "hf9_comm_downlink_benchmark",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.comm_payload_mission_coupled.v1",
        "target": {"level": "whole_spacecraft", "name": "comm_payload_mission_coupled", "mode": "nominal"},
        "simulation": {
            "duration_s": 5400.0,
            "sample_s": 60.0,
            "backend": "python",
            "epoch_utc": "2026-07-05T00:00:00Z",
            "time_system": "UTC",
            "solver": {"method": "rk4", "step_s": 60.0, "deterministic_seed": 42},
        },
        "orbit_environment": _base_orbit_environment(),
        "spacecraft": {
            "payload": {"payload_data_rate_bps": 2_000_000.0, "payload_power_w": 35.0},
            "comm_data": {"storage_capacity_bits": 8_000_000_000.0, "initial_stored_bits": 1_000_000_000.0, "downlink_rate_bps": 12_000_000.0, "transmitter_power_w": 18.0, "comm_idle_power_w": 4.0},
            "eps": {"battery_capacity_wh": 180.0, "initial_soc": 0.60, "solar_array_max_power_w": 120.0, "bus_power_w": 22.0, "eps_efficiency": 0.97},
        },
        "outputs": {"output_root": "datasets/hf9_comm_downlink_benchmark", "include_summary": True, "include_trace": True},
        "validation": {"checks": ["soc_bounds", "data_conservation", "orbit_altitude_sanity", "energy_balance_proxy"], "strict": False},
    }


def _propulsion_impulse_spec() -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": "hf9_propulsion_impulse_benchmark",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.maneuver_orbit_attitude.v1",
        "target": {"level": "whole_spacecraft", "name": "maneuver_orbit_attitude", "mode": "nominal"},
        "simulation": {"duration_s": 2400.0, "sample_s": 20.0, "epoch_utc": "2026-07-05T00:00:00Z", "solver": {"method": "rk4", "step_s": 20.0}},
        "orbit_environment": {**_base_orbit_environment(), "eccentricity": 0.0, "true_anomaly_deg": 0.0},
        "spacecraft": {"propulsion": {"enabled": True}, "adcs": {"dyn_step_s": 0.5, "fsw_step_s": 0.5}},
        "parameters": {
            "dry_mass_kg": 120.0,
            "initial_propellant_kg": 8.0,
            "thrust_n": 0.45,
            "specific_impulse_s": 220.0,
            "burn_start_s": 600.0,
            "burn_duration_s": 180.0,
            "thrust_efficiency": 0.96,
            "burn_direction": "prograde",
            "initial_pointing_error_deg": 0.5,
            "attitude_damping_rate_1_s": 0.0015,
            "disturbance_gain_deg_per_m_s": 0.22,
        },
        "outputs": {"output_root": "datasets/hf9_propulsion_impulse_benchmark", "include_summary": True, "include_trace": True},
        "validation": {"checks": ["fuel_monotonicity", "orbit_altitude_sanity", "pointing_convergence", "energy_balance_proxy"], "strict": False},
    }


def get_default_hf9_benchmark_cases() -> tuple[BenchmarkCase, ...]:
    """Return the canonical Route-B HF-9 benchmark suite."""

    return (
        BenchmarkCase(
            case_id="orbit_eclipse_hf3",
            title="HF-3 orbit eclipse and ground access benchmark",
            capability_id="orbit_environment.medium_fidelity.v1",
            task_spec=_orbit_eclipse_spec(),
            tags=("hf3", "orbit", "eclipse", "ground_access"),
            description="Medium-fidelity LEO orbit/environment benchmark with eclipse and access geometry.",
            qoi_envelopes={
                "orbit.altitude_min_m": {"min": 490_000.0, "max": 505_000.0, "required": True},
                "orbit.altitude_max_m": {"min": 495_000.0, "max": 515_000.0, "required": True},
                "orbit.speed_mean_m_s": {"min": 7_500.0, "max": 7_750.0, "required": True},
                "environment.eclipse_fraction": {"min": 0.20, "max": 0.50, "required": True},
                "ground.access_fraction": {"min": 0.01, "max": 0.20, "required": True},
            },
        ),
        BenchmarkCase(
            case_id="adcs_pointing_hf4",
            title="HF-4 ADCS pointing convergence benchmark",
            capability_id="subsystem.adcs_closed_loop.basic.v1",
            task_spec=_adcs_pointing_spec(),
            tags=("hf4", "adcs", "closed_loop", "pointing"),
            description="Basic closed-loop ADCS benchmark for quaternion normalization and pointing convergence.",
            qoi_envelopes={
                "adcs.initial_pointing_error_deg": {"min": 11.0, "max": 13.0, "required": True},
                "adcs.final_pointing_error_deg": {"min": 0.0, "max": 1.0, "required": True},
                "adcs.convergence_ratio": {"min": 0.0, "max": 0.10, "required": True},
                "adcs.quaternion_norm_max_error": {"abs_max": 1.0e-6, "required": True},
                "adcs.saturation_count": {"equals": 0, "required": True},
            },
        ),
        BenchmarkCase(
            case_id="battery_discharge_hf5",
            title="HF-5 battery discharge under shadow/no-solar benchmark",
            capability_id="whole_spacecraft.power_thermal_orbit_coupled.v1",
            task_spec=_battery_discharge_spec(),
            tags=("hf5", "eps", "battery", "discharge"),
            description="Power/thermal/orbit benchmark with no solar generation to check SOC decrease and bounds.",
            qoi_envelopes={
                "spacecraft.power.initial_soc": {"min": 0.79, "max": 0.81, "required": True},
                "spacecraft.power.final_soc": {"min": 0.60, "max": 0.80, "required": True},
                "spacecraft.power.min_soc": {"min": 0.60, "max": 0.80, "required": True},
                "spacecraft.power.load_energy_wh": {"min": 30.0, "max": 40.0, "required": True},
            },
        ),
        BenchmarkCase(
            case_id="thermal_heater_hf5",
            title="HF-5 thermal heater response benchmark",
            capability_id="whole_spacecraft.power_thermal_orbit_coupled.v1",
            task_spec=_thermal_heater_spec(),
            tags=("hf5", "thermal", "heater", "coupled"),
            description="Cold-start thermal benchmark to verify heater load and bounded bus/battery temperatures.",
            qoi_envelopes={
                "spacecraft.thermal.initial_bus_temp_c": {"min": -13.0, "max": -11.0, "required": True},
                "spacecraft.thermal.final_bus_temp_c": {"min": -20.0, "max": 20.0, "required": True},
                "spacecraft.thermal.min_bus_temp_c": {"min": -25.0, "max": 5.0, "required": True},
                "spacecraft.thermal.heater_energy_wh": {"min": 0.1, "max": 80.0, "required": True},
            },
        ),
        BenchmarkCase(
            case_id="comm_downlink_hf6",
            title="HF-6 communication pass downlink benchmark",
            capability_id="whole_spacecraft.comm_payload_mission_coupled.v1",
            task_spec=_comm_downlink_spec(),
            tags=("hf6", "comm", "payload", "downlink", "storage"),
            description="Mission coupling benchmark for payload data, storage, ground access, and downlink conservation.",
            qoi_envelopes={
                "mission.data.generated_bits": {"min": 1.0e10, "max": 1.2e10, "required": True},
                "mission.data.downlinked_bits": {"min": 5.0e9, "max": 7.0e9, "required": True},
                "mission.data.conservation_error_bits": {"abs_max": 1.0e-3, "required": True},
                "mission.comm.downlink_active_fraction": {"min": 0.05, "max": 0.20, "required": True},
                "mission.power.min_soc": {"min": 0.45, "max": 0.90, "required": True},
            },
        ),
        BenchmarkCase(
            case_id="propulsion_impulse_hf7",
            title="HF-7 finite burn impulse benchmark",
            capability_id="whole_spacecraft.maneuver_orbit_attitude.v1",
            task_spec=_propulsion_impulse_spec(),
            tags=("hf7", "propulsion", "delta_v", "attitude"),
            description="Finite-burn maneuver benchmark for delta-v proxy, fuel monotonicity, and attitude disturbance damping.",
            qoi_envelopes={
                "propulsion.total_delta_v_m_s": {"min": 0.4, "max": 0.8, "required": True},
                "propulsion.propellant_used_kg": {"min": 0.02, "max": 0.06, "required": True},
                "propulsion.final_propellant_kg": {"min": 7.90, "max": 8.00, "required": True},
                "propulsion.fuel_monotonic": {"equals": True, "required": True},
                "adcs.final_pointing_error_deg": {"min": 0.0, "max": 0.2, "required": True},
            },
        ),
    )


def _extract_qoi(summary: Mapping[str, Any]) -> dict[str, Any]:
    qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
    return dict(qoi)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _evaluate_one_envelope(case_id: str, qoi: Mapping[str, Any], field_name: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    required = bool(envelope.get("required", True))
    if field_name not in qoi:
        return {
            "case_id": case_id,
            "field": field_name,
            "status": "fail" if required else "warning",
            "message": "required QoI is missing" if required else "optional QoI is missing",
            "actual": None,
            "envelope": dict(envelope),
        }
    actual = qoi.get(field_name)
    if "equals" in envelope:
        expected = envelope.get("equals")
        ok = actual == expected
        return {
            "case_id": case_id,
            "field": field_name,
            "status": "pass" if ok else "fail",
            "message": "equals envelope satisfied" if ok else "equals envelope violated",
            "actual": actual,
            "envelope": dict(envelope),
        }
    number = _as_number(actual)
    if number is None:
        return {
            "case_id": case_id,
            "field": field_name,
            "status": "fail",
            "message": "QoI is not numeric for numeric envelope",
            "actual": actual,
            "envelope": dict(envelope),
        }
    messages: list[str] = []
    ok = True
    if "min" in envelope and number < float(envelope["min"]):
        ok = False
        messages.append(f"actual {number} < min {envelope['min']}")
    if "max" in envelope and number > float(envelope["max"]):
        ok = False
        messages.append(f"actual {number} > max {envelope['max']}")
    if "abs_max" in envelope and abs(number) > float(envelope["abs_max"]):
        ok = False
        messages.append(f"abs(actual) {abs(number)} > abs_max {envelope['abs_max']}")
    return {
        "case_id": case_id,
        "field": field_name,
        "status": "pass" if ok else "fail",
        "message": "envelope satisfied" if ok else "; ".join(messages),
        "actual": number,
        "envelope": dict(envelope),
    }


def evaluate_qoi_envelopes(case: BenchmarkCase, qoi: Mapping[str, Any]) -> dict[str, Any]:
    results = [_evaluate_one_envelope(case.case_id, qoi, field, envelope) for field, envelope in case.qoi_envelopes.items()]
    fail_count = sum(1 for item in results if item["status"] == "fail")
    warning_count = sum(1 for item in results if item["status"] == "warning")
    return {
        "status": "fail" if fail_count else "warning" if warning_count else "pass",
        "envelope_count": len(results),
        "fail_count": fail_count,
        "warning_count": warning_count,
        "results": results,
    }


def _adapter_for(capability_id: str):
    cls = ADAPTERS.get(capability_id)
    if cls is None:
        raise BenchmarkEnvelopeError(f"unsupported HF-9 benchmark capability: {capability_id}")
    return cls()


def _issue_to_dict(issue: Any) -> dict[str, Any]:
    return {
        "severity": getattr(issue, "severity", "unknown"),
        "path": getattr(issue, "path", ""),
        "message": getattr(issue, "message", str(issue)),
        "category": getattr(issue, "category", ""),
    }


def run_hf9_benchmark_case(case: BenchmarkCase) -> dict[str, Any]:
    """Run one HF-9 benchmark case and evaluate QoI envelopes."""

    adapter = _adapter_for(case.capability_id)
    spec = copy.deepcopy(dict(case.task_spec))
    issues = tuple(adapter.validate(spec))
    error_issues = [issue for issue in issues if getattr(issue, "severity", "") == "error"]
    if error_issues:
        return {
            "schema_version": HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION,
            "case_id": case.case_id,
            "title": case.title,
            "capability_id": case.capability_id,
            "status": "fail",
            "adapter_validation_status": "fail",
            "adapter_validation_issues": [_issue_to_dict(issue) for issue in issues],
            "qoi": {},
            "qoi_envelope_status": "not_run",
            "physical_validation_status": "not_run",
        }
    result: SimulationResult = adapter.run(spec)
    qoi = _extract_qoi(result.summary)
    qoi_eval = evaluate_qoi_envelopes(case, qoi)
    physical_validation = evaluate_physical_validation(
        result.trace_rows,
        summary=result.summary,
        checks=(spec.get("validation") or {}).get("checks") if isinstance(spec.get("validation"), Mapping) else None,
        strict=bool((spec.get("validation") or {}).get("strict", False)) if isinstance(spec.get("validation"), Mapping) else False,
    )
    physical_status = physical_validation["status"] if case.physical_validation_required else "not_required"
    failed = qoi_eval["status"] == "fail" or (case.physical_validation_required and physical_status == "fail")
    warning = qoi_eval["status"] == "warning" or (case.physical_validation_required and physical_status == "warning")
    status = "fail" if failed else "warning" if warning else "pass"
    return {
        "schema_version": HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION,
        "case_id": case.case_id,
        "title": case.title,
        "description": case.description,
        "capability_id": case.capability_id,
        "tags": list(case.tags),
        "status": status,
        "adapter_validation_status": "pass",
        "adapter_validation_issues": [_issue_to_dict(issue) for issue in issues],
        "trace_row_count": len(result.trace_rows),
        "summary_status": result.summary.get("status", "complete"),
        "qoi": qoi,
        "qoi_envelope_status": qoi_eval["status"],
        "qoi_envelope_evaluation": qoi_eval,
        "physical_validation_status": physical_status,
        "physical_validation": physical_validation,
        "can_claim_high_fidelity": False,
        "fidelity_level": result.summary.get("fidelity_level"),
    }


def _write_report_files(report_dir: Path, report: Mapping[str, Any], cases: Sequence[BenchmarkCase]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    scenarios = {
        "schema_version": HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION,
        "route_b_version": "B-6/HF-9",
        "cases": [case.to_dict() for case in cases],
    }
    (report_dir / "benchmark_scenarios.json").write_text(json.dumps(scenarios, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    with (report_dir / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "capability_id", "status", "qoi_envelope_status", "physical_validation_status", "trace_row_count"])
        writer.writeheader()
        for case_result in report.get("case_results", []):
            writer.writerow({key: case_result.get(key) for key in writer.fieldnames})
    with (report_dir / "tolerance_envelopes.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["case_id", "field", "status", "actual", "envelope", "message"])
        writer.writeheader()
        for case_result in report.get("case_results", []):
            eval_result = case_result.get("qoi_envelope_evaluation") if isinstance(case_result.get("qoi_envelope_evaluation"), Mapping) else {}
            for item in eval_result.get("results", []):
                writer.writerow({
                    "case_id": item.get("case_id"),
                    "field": item.get("field"),
                    "status": item.get("status"),
                    "actual": item.get("actual"),
                    "envelope": json.dumps(item.get("envelope", {}), ensure_ascii=False, sort_keys=True),
                    "message": item.get("message"),
                })


def build_hf9_readiness_matrix(*, benchmark_report: Mapping[str, Any] | None = None, capability_count: int | None = None, route_b_model_library_capability_count: int | None = None) -> dict[str, Any]:
    report = benchmark_report if isinstance(benchmark_report, Mapping) else None
    out: dict[str, Any] = {
        "schema_version": HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION,
        "route_b_version": "B-6/HF-9",
        "benchmark_status": report.get("status") if report else "not_run",
        "benchmark_case_count": report.get("case_count") if report else 0,
        "benchmark_pass_count": report.get("pass_count") if report else 0,
        "benchmark_fail_count": report.get("fail_count") if report else 0,
        "tolerance_envelopes_available": True,
        "physical_validation_gate_available": True,
        "route_b_release_status": "benchmark_passed_release_candidate" if report and report.get("status") == "pass" else "not_release_ready",
        "high_fidelity_ready_count": 0,
        "can_claim_package_high_fidelity": False,
        "reason_high_fidelity_still_blocked": (
            "HF-9 benchmark envelopes provide repeatable regression evidence, but they are not external validation against flight data, "
            "high-fidelity sensor calibration, full 6-DOF dynamics, or certified force/thermal/RF models."
        ),
        "known_limits_document": "docs/route_b_known_limitations_hf9.md",
        "release_checklist": "docs/route_b_release_checklist.md",
    }
    if capability_count is not None:
        out["capability_count"] = int(capability_count)
    if route_b_model_library_capability_count is not None:
        out["route_b_model_library_capability_count"] = int(route_b_model_library_capability_count)
    return out


def run_hf9_benchmark_suite(cases: Sequence[BenchmarkCase] | None = None, *, report_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the default HF-9 benchmark suite and optionally write reports."""

    selected = tuple(cases or get_default_hf9_benchmark_cases())
    case_results = [run_hf9_benchmark_case(case) for case in selected]
    fail_count = sum(1 for item in case_results if item["status"] == "fail")
    warning_count = sum(1 for item in case_results if item["status"] == "warning")
    pass_count = sum(1 for item in case_results if item["status"] == "pass")
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    report: dict[str, Any] = {
        "schema_version": HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION,
        "route_b_version": "B-6/HF-9",
        "status": status,
        "case_count": len(case_results),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "case_results": case_results,
        "tolerance_envelope_count": sum(len(case.qoi_envelopes) for case in selected),
        "physical_validation_gate_available": True,
        "can_claim_high_fidelity": False,
        "route_b_release_status": "benchmark_passed_release_candidate" if status == "pass" else "needs_attention",
        "known_physics_limits": [
            "Benchmarks are deterministic regression envelopes, not external validation against flight or lab data.",
            "HF-3 orbit/environment uses medium-fidelity approximations and compact eclipse/access geometry.",
            "HF-4 ADCS is a basic closed-loop proxy, not complete FSW or calibrated sensor/actuator physics.",
            "HF-5/HF-6/HF-7 coupled models are engineering prototypes with simplified coupling terms.",
        ],
    }
    report["readiness_matrix"] = build_hf9_readiness_matrix(benchmark_report=report)
    if report_dir is not None:
        _write_report_files(Path(report_dir), report, selected)
    return report


__all__ = [
    "HF9_PHYSICS_BENCHMARK_SCHEMA_VERSION",
    "BenchmarkCase",
    "BenchmarkEnvelopeError",
    "ADAPTERS",
    "evaluate_qoi_envelopes",
    "get_default_hf9_benchmark_cases",
    "run_hf9_benchmark_case",
    "run_hf9_benchmark_suite",
    "build_hf9_readiness_matrix",
]
