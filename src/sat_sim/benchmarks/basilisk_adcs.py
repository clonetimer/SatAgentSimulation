"""BSK-ADCS-2 Basilisk-native ADCS/FSW benchmark harness.

This harness mirrors the BSK-ORB-2 boundary discipline:

* ``basilisk_adcs_benchmark_passed`` means a Basilisk runtime case actually
  executed through the Basilisk-native ADCS/FSW adapter.
* ``benchmark_config_prepared`` means auditable benchmark cases, module
  blueprints, and local-proxy comparison baselines exist, but Basilisk is not
  installed in the current environment.
* ``configured_not_executed`` means Basilisk is importable, but the current
  adapter still stops at the configuration/blueprint boundary.

Local ADCS proxy results are explicit comparison baselines only.  They are never
implicit fallback for ``subsystem.adcs_basilisk_fsw.v1`` and never count as a
Basilisk benchmark pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import csv
import json

from sat_sim.adapters.subsystem_adcs_basilisk_fsw import AdcsBasiliskFswAdapter
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.adcs.basilisk_fsw import (
    BasiliskAdcsFswConfig,
    build_basilisk_adcs_fsw_blueprint,
    check_basilisk_adcs_availability,
)

BSK_ADCS2_BENCHMARK_SCHEMA_VERSION = "bsk_adcs2.basilisk_adcs_validation_benchmark.v1"


@dataclass(frozen=True)
class BasiliskAdcsBenchmarkEnvelope:
    """Simple QoI tolerance envelope for cases that truly execute."""

    name: str
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def evaluate(self, value: Any) -> dict[str, Any]:
        status = "pass"
        numeric_value: float | None
        try:
            numeric_value = float(value)
        except Exception:
            numeric_value = None
            status = "not_evaluated"
        if numeric_value is not None:
            if self.minimum is not None and numeric_value < self.minimum:
                status = "fail"
            if self.maximum is not None and numeric_value > self.maximum:
                status = "fail"
        return {
            "name": self.name,
            "value": numeric_value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "status": status,
            "description": self.description,
        }


@dataclass(frozen=True)
class BasiliskAdcsBenchmarkCase:
    """A BSK-ADCS-2 benchmark case and its optional local-proxy comparator."""

    case_id: str
    description: str
    fsw_scope: str
    task_spec: dict[str, Any]
    expected_basilisk_modules: tuple[str, ...]
    local_proxy_comparable: bool
    envelopes: tuple[BasiliskAdcsBenchmarkEnvelope, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "fsw_scope": self.fsw_scope,
            "task_spec": self.task_spec,
            "expected_basilisk_modules": list(self.expected_basilisk_modules),
            "local_proxy_comparable": self.local_proxy_comparable,
            "envelopes": [env.__dict__ for env in self.envelopes],
        }


def _base_basilisk_adcs_spec(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_basilisk_fsw.v1",
        "target": {"level": "subsystem", "name": "adcs_fsw", "mode": "nominal"},
        "simulation": {
            "duration_s": 600.0,
            "sample_s": 5.0,
            "epoch_utc": "2026-07-06T00:00:00Z",
            "solver": {"step_s": 0.5},
        },
        "parameters": {
            "target_mode": "nadir",
            "fsw_step_s": 1.0,
            "inertia_kg_m2": [12.0, 10.0, 8.0],
            "initial_attitude_error_deg": 12.0,
            "initial_rate_deg_s": [0.10, -0.05, 0.02],
            "mode_manager": {"enabled_modes": ["standby", "inertial3d", "sun_safe", "nadir", "velocity", "detumble"]},
            "controller": {"control_law": "mrp_feedback", "K": 3.5, "P": 30.0, "Ki": 0.0, "max_control_torque_nm": 0.05},
            "reaction_wheels": {"geometry": "pyramid", "num_wheels": 4, "wheel_inertia_kg_m2": 0.08, "max_torque_nm": 0.05, "max_speed_rad_s": 6000.0},
            "estimator": {"type": "simple_nav"},
            "sensors": {"enabled": ["imu", "star_tracker", "css"], "star_tracker_noise_deg": 0.002, "sun_sensor_noise_deg": 0.2},
        },
        "outputs": {"output_root": f"reports/basilisk_adcs_benchmark/{case_id}"},
        "metadata": {"case_id": case_id, "benchmark": "BSK-ADCS-2"},
    }


def basilisk_adcs_benchmark_cases() -> list[BasiliskAdcsBenchmarkCase]:
    """Return deterministic BSK-ADCS-2 benchmark cases."""

    detumble = _base_basilisk_adcs_spec("bsk_adcs2_detumble")
    detumble["simulation"]["duration_s"] = 900.0
    detumble["parameters"].update({
        "target_mode": "detumble",
        "initial_attitude_error_deg": 3.0,
        "initial_rate_deg_s": [0.4, -0.2, 0.1],
        "controller": {"control_law": "rate_damping", "K": 2.5, "P": 20.0, "Ki": 0.0, "max_control_torque_nm": 0.06},
        "sensors": {"enabled": ["imu"], "star_tracker_noise_deg": 0.002, "sun_sensor_noise_deg": 0.2},
    })

    inertial = _base_basilisk_adcs_spec("bsk_adcs2_inertial_pointing")
    inertial["parameters"].update({"target_mode": "inertial3d", "controller": {"control_law": "mrp_feedback", "K": 3.8, "P": 32.0, "Ki": 0.0, "max_control_torque_nm": 0.05}})

    sun_safe = _base_basilisk_adcs_spec("bsk_adcs2_sun_safe_css_estimator")
    sun_safe["parameters"].update({
        "target_mode": "sun_safe",
        "controller": {"control_law": "mrp_pd", "K": 3.0, "P": 24.0, "Ki": 0.0, "max_control_torque_nm": 0.04},
        "estimator": {"type": "css_est"},
        "sensors": {"enabled": ["imu", "css", "sun_sensor"], "star_tracker_noise_deg": 0.002, "sun_sensor_noise_deg": 0.25},
    })

    hill = _base_basilisk_adcs_spec("bsk_adcs2_hill_nadir_pointing")
    hill["parameters"].update({"target_mode": "hill", "estimator": {"type": "simple_nav"}})

    rw = _base_basilisk_adcs_spec("bsk_adcs2_rw_momentum_management")
    rw["simulation"]["duration_s"] = 1200.0
    rw["parameters"].update({
        "target_mode": "nadir",
        "controller": {"control_law": "mrp_steering", "K": 4.0, "P": 35.0, "Ki": 0.0, "max_control_torque_nm": 0.08},
        "reaction_wheels": {"geometry": "pyramid", "num_wheels": 4, "wheel_inertia_kg_m2": 0.08, "max_torque_nm": 0.08, "max_speed_rad_s": 2500.0},
    })

    dropout = _base_basilisk_adcs_spec("bsk_adcs2_sensor_dropout_estimator_boundary")
    dropout["parameters"].update({
        "target_mode": "sun_pointing",
        "estimator": {"type": "sunline_ukf"},
        "sensors": {"enabled": ["imu", "star_tracker", "css", "tam"], "star_tracker_noise_deg": 0.003, "sun_sensor_noise_deg": 0.3, "dropout": {"start_s": 200.0, "duration_s": 60.0}},
        "sensor_dropout": {"start_s": 200.0, "duration_s": 60.0},
    })

    return [
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_detumble",
            description="Basilisk-native detumble FSW configuration and optional runtime benchmark.",
            fsw_scope="detumble_rate_damping",
            task_spec=detumble,
            expected_basilisk_modules=("fswAlgorithms.inertial3D", "fswAlgorithms.mrpFeedback", "fswAlgorithms.rwMotorTorque"),
            local_proxy_comparable=True,
            envelopes=(
                BasiliskAdcsBenchmarkEnvelope("qoi.adcs.max_abs_rate_rad_s", 0.0, 0.03, "Executed detumble rate envelope"),
                BasiliskAdcsBenchmarkEnvelope("qoi.adcs.quaternion_norm_max_error", 0.0, 1.0e-6, "Quaternion normalization sanity"),
            ),
        ),
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_inertial_pointing",
            description="Inertial pointing with MRP feedback and simple navigation configuration.",
            fsw_scope="inertial_pointing_mrp_feedback",
            task_spec=inertial,
            expected_basilisk_modules=("fswAlgorithms.inertial3D", "fswAlgorithms.mrpFeedback", "simulation.simpleNav"),
            local_proxy_comparable=True,
            envelopes=(BasiliskAdcsBenchmarkEnvelope("qoi.adcs.final_pointing_error_deg", 0.0, 5.0, "Executed BSK-RUN-5 pointing-error smoke sanity; not flight accuracy validation"),),
        ),
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_sun_safe_css_estimator",
            description="Sun-safe/sun-pointing configuration with CSS estimator boundary.",
            fsw_scope="sun_safe_css_estimator",
            task_spec=sun_safe,
            expected_basilisk_modules=("fswAlgorithms.sunSafePoint", "fswAlgorithms.mrpPD", "fswAlgorithms.cssWlsEst", "fswAlgorithms.cssComm"),
            local_proxy_comparable=True,
            envelopes=(BasiliskAdcsBenchmarkEnvelope("qoi.adcs.final_pointing_error_deg", 0.0, 3.0, "Executed sun-safe pointing sanity"),),
        ),
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_hill_nadir_pointing",
            description="Hill/nadir pointing configuration for later orbit-coupled ADCS integration.",
            fsw_scope="hill_nadir_pointing",
            task_spec=hill,
            expected_basilisk_modules=("fswAlgorithms.hillPoint", "fswAlgorithms.mrpFeedback", "fswAlgorithms.rwConfigData"),
            local_proxy_comparable=True,
            envelopes=(BasiliskAdcsBenchmarkEnvelope("qoi.adcs.final_pointing_error_deg", 0.0, 5.0, "Executed nadir/hill pointing sanity when that scope is supported"),),
        ),
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_rw_momentum_management",
            description="Reaction-wheel momentum and steering-control configuration coverage.",
            fsw_scope="reaction_wheel_momentum",
            task_spec=rw,
            expected_basilisk_modules=("fswAlgorithms.mrpSteering", "fswAlgorithms.rateServoFullNonlinear", "fswAlgorithms.rwMotorTorque", "reactionWheelStateEffector"),
            local_proxy_comparable=True,
            envelopes=(BasiliskAdcsBenchmarkEnvelope("qoi.adcs.rw.max_abs_momentum_nms", 0.0, 40.0, "Executed wheel momentum sanity"),),
        ),
        BasiliskAdcsBenchmarkCase(
            case_id="bsk_adcs2_sensor_dropout_estimator_boundary",
            description="Sensor dropout and estimator boundary case; dropout handling remains a benchmark boundary, not FDIR.",
            fsw_scope="sensor_dropout_estimator_boundary",
            task_spec=dropout,
            expected_basilisk_modules=("fswAlgorithms.sunlineUKF", "fswAlgorithms.stComm", "fswAlgorithms.tamComm"),
            local_proxy_comparable=True,
            envelopes=(BasiliskAdcsBenchmarkEnvelope("qoi.adcs.sensor.dropout_count", 1.0, 100.0, "Executed dropout trace sanity"),),
        ),
    ]


def _nested_qoi(summary: Mapping[str, Any], qoi_name: str) -> Any:
    if qoi_name.startswith("qoi."):
        key = qoi_name[len("qoi."):]
        qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
        return qoi.get(key)
    return summary.get(qoi_name)


def _local_mode(native_mode: str) -> str:
    aliases = {
        "inertial3d": "inertial",
        "inertial": "inertial",
        "sun_pointing": "sun",
        "sun_safe": "sun",
        "nadir": "nadir",
        "hill": "nadir",
        "velocity": "inertial",
        "detumble": "detumble",
    }
    return aliases.get(str(native_mode).strip().lower(), "inertial")


def _local_proxy_spec_for(case: BasiliskAdcsBenchmarkCase) -> dict[str, Any] | None:
    """Convert a comparable Basilisk ADCS case into an explicit local-proxy baseline."""

    if not case.local_proxy_comparable:
        return None
    native = json.loads(json.dumps(case.task_spec))
    params = native.get("parameters", {}) if isinstance(native.get("parameters"), Mapping) else {}
    sim = native.get("simulation", {}) if isinstance(native.get("simulation"), Mapping) else {}
    solver = sim.get("solver", {}) if isinstance(sim.get("solver"), Mapping) else {}
    controller = params.get("controller", {}) if isinstance(params.get("controller"), Mapping) else {}
    rw = params.get("reaction_wheels", {}) if isinstance(params.get("reaction_wheels"), Mapping) else {}
    sensors = params.get("sensors", {}) if isinstance(params.get("sensors"), Mapping) else {}
    native_mode = str(params.get("target_mode", "nadir")).strip().lower().replace("-", "_")
    dropout = params.get("sensor_dropout") if isinstance(params.get("sensor_dropout"), Mapping) else sensors.get("dropout") if isinstance(sensors.get("dropout"), Mapping) else None

    local_params: dict[str, Any] = {
        "target_mode": _local_mode(native_mode),
        "initial_attitude_error_deg": params.get("initial_attitude_error_deg", 12.0),
        "initial_rate_deg_s": params.get("initial_rate_deg_s", [0.1, -0.05, 0.02]),
        "inertia_kg_m2": params.get("inertia_kg_m2", [12.0, 10.0, 8.0]),
        "control_kp_nm_per_rad": min(float(controller.get("K", 3.5)) * 0.035, 0.20),
        "control_kd_nm_per_rad_s": min(float(controller.get("P", 30.0)) * 0.03, 1.20),
        "pointing_requirement_deg": 2.0,
        "environment_torques": {"gravity_gradient": True, "magnetic": True, "aerodynamic": False, "srp": native_mode in {"sun_safe", "sun_pointing"}},
        "gyro_bias_deg_s": params.get("gyro_bias_deg_s", [0.005, -0.002, 0.001]),
        "gyro_noise_std_deg_s": sensors.get("gyro_noise_std_deg_s", 0.001),
        "star_tracker_noise_deg": sensors.get("star_tracker_noise_deg", 0.002),
        "sun_sensor_noise_deg": sensors.get("sun_sensor_noise_deg", 0.2),
        "num_wheels": min(3, int(rw.get("num_wheels", 3))),
        "wheel_inertia_kg_m2": rw.get("wheel_inertia_kg_m2", 0.08),
        "max_wheel_torque_nm": rw.get("max_torque_nm", 0.05),
        "max_wheel_speed_rad_s": min(float(rw.get("max_speed_rad_s", 900.0)), 900.0),
        "initial_wheel_speed_rad_s": [40.0, 40.0, 40.0],
    }
    if dropout is not None:
        local_params["sensor_dropout"] = {"start_s": dropout.get("start_s", 200.0), "duration_s": dropout.get("duration_s", 60.0)}

    return {
        "schema_version": native.get("schema_version", "0.1.0"),
        "task_id": case.case_id + "_local_proxy_baseline",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {
            "duration_s": sim.get("duration_s", 600.0),
            "sample_s": sim.get("sample_s", 5.0),
            "seed": 0,
            "backend": "python",
            "epoch_utc": sim.get("epoch_utc", "2026-07-06T00:00:00Z"),
            "solver": {"method": "euler", "step_s": min(float(solver.get("step_s", 0.5)), 0.5)},
        },
        "parameters": local_params,
        "outputs": {"output_root": f"reports/basilisk_adcs_benchmark/{case.case_id}_local_proxy_baseline", "include_summary": True, "include_trace": True},
        "metadata": {"case_id": case.case_id + "_local_proxy_baseline", "benchmark": "BSK-ADCS-2-local-proxy-comparison"},
    }


def _run_local_proxy_comparison(case: BasiliskAdcsBenchmarkCase) -> dict[str, Any]:
    local_spec = _local_proxy_spec_for(case)
    if local_spec is None:
        return {"case_id": case.case_id, "status": "not_applicable", "reason": "no local proxy equivalent for requested Basilisk FSW surface"}
    adapter = AdcsFidelityAdapter()
    issues = adapter.validate(local_spec)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        return {
            "case_id": case.case_id,
            "status": "validation_error",
            "capability_id": "subsystem.adcs_fidelity.v1",
            "error_count": len(errors),
            "errors": [issue.to_dict() for issue in errors],
        }
    result = adapter.run(local_spec)
    qoi = result.summary.get("qoi", {}) if isinstance(result.summary.get("qoi"), Mapping) else {}
    return {
        "case_id": case.case_id,
        "status": "computed",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "fallback_policy": "explicit_comparison_only_not_implicit_fallback",
        "qoi": qoi,
        "trace_rows": result.summary.get("trace_rows"),
        "physical_validation_status": result.summary.get("physical_validation_status", result.summary.get("status")),
    }


def _case_status(*, availability_available: bool, errors: list[Any], result_summary: Mapping[str, Any]) -> str:
    if errors:
        return "validation_error"
    basilisk_status = str(result_summary.get("basilisk_status") or "unknown")
    if not availability_available and basilisk_status == "unavailable":
        return "benchmark_config_prepared"
    if basilisk_status == "executed":
        return "basilisk_adcs_benchmark_passed"
    if basilisk_status == "configured_but_not_executed":
        return "configured_not_executed"
    return basilisk_status


def run_basilisk_adcs_benchmark(report_dir: str | Path) -> dict[str, Any]:
    """Run the BSK-ADCS-2 benchmark harness and write JSON/CSV artifacts."""

    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    adapter = AdcsBasiliskFswAdapter()
    case_results: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    availability_seen: dict[str, Any] | None = None

    for case in basilisk_adcs_benchmark_cases():
        config = BasiliskAdcsFswConfig.from_task_spec(case.task_spec)
        availability = check_basilisk_adcs_availability(config)
        availability_seen = availability.to_dict()
        blueprint = build_basilisk_adcs_fsw_blueprint(config)
        issues = adapter.validate(case.task_spec)
        errors = [issue for issue in issues if issue.severity == "error" and issue.code not in {"basilisk_unavailable"}]
        result = adapter.run(case.task_spec)
        summary = result.summary
        status = _case_status(availability_available=availability.available, errors=errors, result_summary=summary)

        env_evaluations: list[dict[str, Any]] = []
        if status == "basilisk_adcs_benchmark_passed":
            for env in case.envelopes:
                row = env.evaluate(_nested_qoi(summary, env.name))
                row["case_id"] = case.case_id
                env_evaluations.append(row)
                envelope_rows.append(row)
            if env_evaluations and any(row["status"] == "fail" for row in env_evaluations):
                status = "tolerance_fail"

        configured_modules = list(blueprint.get("processes", {}).get("fsw_process", {}).get("modules", []))
        # Include dynamics modules in the expected-module check because reaction
        # wheel state effectors live outside the FSW task list.
        configured_modules.extend(blueprint.get("processes", {}).get("dynamics_process", {}).get("modules", []))
        missing_expected_modules = sorted(set(case.expected_basilisk_modules) - set(configured_modules))
        local_comparison = _run_local_proxy_comparison(case)
        local_rows.append(local_comparison)
        case_results.append({
            "case_id": case.case_id,
            "description": case.description,
            "fsw_scope": case.fsw_scope,
            "status": status,
            "basilisk_status": summary.get("basilisk_status"),
            "physical_validation_status": summary.get("physical_validation_status"),
            "can_claim_high_fidelity": False,
            "error_count": len(errors),
            "non_basilisk_errors": [issue.to_dict() for issue in errors],
            "expected_basilisk_modules": list(case.expected_basilisk_modules),
            "missing_expected_modules": missing_expected_modules,
            "configured_modules": configured_modules,
            "execution_scope": blueprint.get("execution_scope"),
            "blueprint": blueprint,
            "summary_qoi": summary.get("qoi", {}),
            "envelopes": env_evaluations,
            "local_proxy_comparison": local_comparison,
        })

    runtime_pass_count = sum(1 for row in case_results if row["status"] == "basilisk_adcs_benchmark_passed")
    prepared_count = sum(1 for row in case_results if row["status"] == "benchmark_config_prepared")
    configured_not_executed_count = sum(1 for row in case_results if row["status"] == "configured_not_executed")
    fail_count = sum(1 for row in case_results if row["status"] in {"validation_error", "tolerance_fail"})
    availability_available = bool((availability_seen or {}).get("available"))
    if availability_available and runtime_pass_count == len(case_results):
        basilisk_adcs_benchmark_status = "basilisk_runtime_passed"
    elif availability_available and runtime_pass_count > 0:
        basilisk_adcs_benchmark_status = "partial_basilisk_runtime_coverage"
    elif availability_available and configured_not_executed_count:
        basilisk_adcs_benchmark_status = "configured_not_executed_runtime_scope_pending"
    else:
        basilisk_adcs_benchmark_status = "not_run_basilisk_missing"

    report = {
        "schema_version": BSK_ADCS2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "BSK-ADCS-2",
        "status": "pass" if fail_count == 0 and all(not row["missing_expected_modules"] for row in case_results) else "fail",
        "basilisk_adcs_benchmark_status": basilisk_adcs_benchmark_status,
        "availability": availability_seen or check_basilisk_adcs_availability().to_dict(),
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "fail_count": fail_count,
        "local_proxy_comparison_count": sum(1 for row in local_rows if row.get("status") == "computed"),
        "case_results": case_results,
        "local_proxy_comparisons": local_rows,
        "tolerance_envelope_count": len(envelope_rows),
        "can_claim_high_fidelity": False,
        "validation_scope": "basilisk_native_adcs_benchmark_harness_and_configuration_blueprints; runtime pass requires installed Basilisk and is not flight validation",
        "fallback_policy": "local ADCS proxy comparisons are explicit baselines, never implicit fallback for subsystem.adcs_basilisk_fsw.v1",
    }

    (out / "basilisk_adcs_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "description", "fsw_scope", "status", "basilisk_status", "physical_validation_status", "error_count", "execution_scope"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in case_results:
            writer.writerow({key: row.get(key) for key in fieldnames})
    with (out / "local_proxy_comparison.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "status", "capability_id", "fallback_policy", "trace_rows", "physical_validation_status", "reason", "error_count"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in local_rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    with (out / "tolerance_envelopes.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "name", "value", "minimum", "maximum", "status", "description"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in envelope_rows:
            writer.writerow({key: row.get(key) for key in fieldnames})

    readiness = {
        "schema_version": "bsk_adcs2.basilisk_adcs_readiness_matrix.v1",
        "status": report["status"],
        "basilisk_adcs_benchmark_status": basilisk_adcs_benchmark_status,
        "basilisk_runtime_passed": basilisk_adcs_benchmark_status == "basilisk_runtime_passed",
        "benchmark_config_prepared": prepared_count == len(case_results) and not availability_available,
        "can_claim_high_fidelity": False,
        "claim_guardrail": "Do not mark basilisk_adcs_benchmark_passed or high_fidelity_ready without actual Basilisk runtime artifacts and external/reference validation.",
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "local_proxy_comparison_count": report["local_proxy_comparison_count"],
    }
    (out / "readiness_matrix.json").write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


__all__ = [
    "BSK_ADCS2_BENCHMARK_SCHEMA_VERSION",
    "BasiliskAdcsBenchmarkEnvelope",
    "BasiliskAdcsBenchmarkCase",
    "basilisk_adcs_benchmark_cases",
    "run_basilisk_adcs_benchmark",
]
