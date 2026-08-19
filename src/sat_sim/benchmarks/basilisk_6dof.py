"""BSK-6DOF-2 Basilisk-native orbit+ADCS integration benchmark harness.

The BSK-6DOF-2 harness is intentionally conservative.  It proves that the
repository now has auditable integration-gate cases for
``whole_spacecraft.basilisk_6dof.v1`` and that each case wires orbit, ADCS/FSW,
spacecraft hub, effectors, and frame/time/unit metadata consistently.  It does
not treat the INT-1 local integration path as a Basilisk runtime substitute.

Outcome vocabulary:

* ``basilisk_6dof_runtime_passed``: a native Basilisk 6-DOF case actually ran
  and passed its runtime tolerance envelopes.
* ``benchmark_config_prepared``: benchmark cases, graph blueprints, metadata
  gates, and explicit local comparison baselines exist, but Basilisk is missing.
* ``configured_not_executed``: Basilisk is importable, but the current adapter
  has not yet executed the full native spacecraft graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import csv
import json

from sat_sim.adapters.whole_spacecraft_basilisk_6dof import SpacecraftBasilisk6DofAdapter
from sat_sim.adapters.whole_spacecraft_orbit_adcs_fidelity import OrbitAdcsFidelityAdapter
from sat_sim.spacecraft.basilisk_6dof import (
    BASILISK_6DOF_SCHEMA_VERSION,
    Basilisk6DofConfig,
    build_basilisk_6dof_blueprint,
    check_basilisk_6dof_availability,
)

BSK_6DOF2_BENCHMARK_SCHEMA_VERSION = "bsk_6dof2.basilisk_6dof_integration_benchmark.v1"


@dataclass(frozen=True)
class Basilisk6DofIntegrationEnvelope:
    """QoI envelope evaluated only for true Basilisk runtime executions."""

    name: str
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def evaluate(self, value: Any) -> dict[str, Any]:
        try:
            numeric_value = float(value)
        except Exception:
            return {
                "name": self.name,
                "value": None,
                "minimum": self.minimum,
                "maximum": self.maximum,
                "status": "not_evaluated",
                "description": self.description,
            }
        status = "pass"
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
class Basilisk6DofIntegrationCase:
    """A native 6-DOF integration-gate case and explicit local baseline."""

    case_id: str
    description: str
    integration_scope: str
    task_spec: dict[str, Any]
    expected_graph_edges: tuple[str, ...]
    expected_metadata_fields: tuple[str, ...]
    expected_modules: tuple[str, ...]
    local_proxy_comparable: bool = True
    envelopes: tuple[Basilisk6DofIntegrationEnvelope, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "integration_scope": self.integration_scope,
            "task_spec": self.task_spec,
            "expected_graph_edges": list(self.expected_graph_edges),
            "expected_metadata_fields": list(self.expected_metadata_fields),
            "expected_modules": list(self.expected_modules),
            "local_proxy_comparable": self.local_proxy_comparable,
            "envelopes": [env.__dict__ for env in self.envelopes],
        }


def _base_basilisk_6dof_spec(case_id: str, *, target_mode: str = "nadir") -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.basilisk_6dof.v1",
        "target": {"level": "whole_spacecraft", "name": "basilisk_6dof", "mode": "nominal"},
        "simulation": {
            "duration_s": 900.0,
            "sample_s": 10.0,
            "epoch_utc": "2026-07-06T00:00:00Z",
            "solver": {"method": "basilisk", "step_s": 0.5},
        },
        "orbit_environment": {
            "central_body": "earth",
            "altitude_m": 520_000.0,
            "eccentricity": 0.001,
            "inclination_deg": 97.5,
            "raan_deg": 10.0,
            "arg_perigee_deg": 0.0,
            "true_anomaly_deg": 0.0,
            "enable_eclipse": True,
            "sun_model": "spice_or_analytic",
            "force_models": {"spherical_harmonics": {"enabled": True, "degree": 2}},
        },
        "parameters": {
            "spacecraft": {
                "mass_kg": 120.0,
                "inertia_kg_m2": [12.0, 10.0, 8.0],
                "initial_sigma_bn": [0.04, -0.02, 0.01],
                "initial_omega_bn_b_rad_s": [0.0015, -0.0008, 0.0004],
                "area_m2": 1.1,
                "drag_coefficient": 2.2,
                "srp_coefficient": 1.3,
            },
            "coupling": {
                "target_mode": target_mode,
                "target_frame": "lvlh" if target_mode in {"nadir", "hill", "velocity"} else "inertial_or_sun",
                "attitude_frame": "body",
                "inertial_frame": "J2000",
                "time_scale": "UTC",
                "effectors": ["gravity", "reaction_wheels", "external_torque"],
                "require_frame_metadata": True,
            },
            "adcs": {
                "target_mode": target_mode,
                "fsw_step_s": 1.0,
                "controller": {"control_law": "mrp_feedback", "K": 3.5, "P": 30.0, "Ki": 0.0, "max_control_torque_nm": 0.05},
                "reaction_wheels": {"geometry": "pyramid", "num_wheels": 4, "wheel_inertia_kg_m2": 0.08, "max_torque_nm": 0.05, "max_speed_rad_s": 6000.0},
                "estimator": {"type": "simple_nav"},
                "sensors": {"enabled": ["imu", "star_tracker", "css"], "star_tracker_noise_deg": 0.002, "sun_sensor_noise_deg": 0.2},
            },
        },
        "outputs": {"output_root": f"reports/basilisk_6dof_integration_benchmark/{case_id}", "include_summary": True, "include_trace": True},
        "metadata": {"case_id": case_id, "benchmark": "BSK-6DOF-2"},
    }


def basilisk_6dof_integration_cases() -> list[Basilisk6DofIntegrationCase]:
    """Return deterministic BSK-6DOF-2 native integration-gate cases."""

    nadir = _base_basilisk_6dof_spec("bsk_6dof2_nadir_lvlh_frame_gate", target_mode="nadir")

    sun = _base_basilisk_6dof_spec("bsk_6dof2_sun_pointing_eclipse_gate", target_mode="sun_pointing")
    sun["simulation"]["duration_s"] = 1200.0
    sun["orbit_environment"]["force_models"].update({"srp": True})
    sun["parameters"]["coupling"].update({"target_frame": "sun", "effectors": ["gravity", "reaction_wheels", "external_torque", "srp"]})
    sun["parameters"]["adcs"].update({"target_mode": "sun_safe", "estimator": {"type": "css_est"}, "sensors": {"enabled": ["imu", "css", "sun_sensor"], "sun_sensor_noise_deg": 0.25}})

    detumble = _base_basilisk_6dof_spec("bsk_6dof2_detumble_rw_momentum_gate", target_mode="detumble")
    detumble["parameters"]["spacecraft"].update({"initial_omega_bn_b_rad_s": [0.006, -0.003, 0.0015]})
    detumble["parameters"]["adcs"].update({
        "target_mode": "detumble",
        "controller": {"control_law": "rate_damping", "K": 2.5, "P": 20.0, "Ki": 0.0, "max_control_torque_nm": 0.06},
        "sensors": {"enabled": ["imu"], "star_tracker_noise_deg": 0.002, "sun_sensor_noise_deg": 0.2},
    })

    drag_srp = _base_basilisk_6dof_spec("bsk_6dof2_drag_srp_environment_torque_gate", target_mode="nadir")
    drag_srp["orbit_environment"].update({"altitude_m": 300_000.0})
    drag_srp["orbit_environment"]["force_models"].update({"atmosphere_model": "exponential", "drag": True, "srp": True})
    drag_srp["parameters"]["coupling"].update({"effectors": ["gravity", "reaction_wheels", "external_torque", "drag", "srp"]})

    third_body = _base_basilisk_6dof_spec("bsk_6dof2_third_body_spice_gate", target_mode="hill")
    third_body["simulation"]["duration_s"] = 1800.0
    third_body["orbit_environment"]["force_models"].update({"third_bodies": ["sun", "moon"], "spice_ephemeris": True, "spice_kernels": ["de430.bsp", "naif0012.tls", "de-403-masses.tpc", "pck00010.tpc"], "spice_data_path": "supportData/EphemerisData"})
    third_body["parameters"]["coupling"].update({"target_mode": "hill", "target_frame": "lvlh", "effectors": ["gravity", "reaction_wheels", "external_torque", "third_body", "spice"]})
    third_body["parameters"]["adcs"].update({"target_mode": "hill"})

    common_metadata = ("time_s", "epoch_utc", "time_scale", "inertial_frame", "attitude_frame", "target_frame", "dynamics_step_s", "fsw_step_s", "sample_s")
    return [
        Basilisk6DofIntegrationCase(
            case_id="bsk_6dof2_nadir_lvlh_frame_gate",
            description="Nadir/LVLH pointing integration gate linking orbit state to ADCS target-frame metadata.",
            integration_scope="orbit_state_to_nadir_guidance",
            task_spec=nadir,
            expected_graph_edges=("orbit_primary", "adcs_primary", "spacecraft.scStateOutMsg", "att_guidance.attRefOutMsg / attGuidOutMsg"),
            expected_metadata_fields=common_metadata,
            expected_modules=("spacecraft.Spacecraft", "simIncludeGravBody.gravBodyFactory", "reactionWheelStateEffector", "extForceTorque.ExtForceTorque", "fswAlgorithms.hillPoint"),
            envelopes=(
                Basilisk6DofIntegrationEnvelope("qoi.integration.frame_consistency_pass", 1.0, 1.0, "Frame metadata must remain consistent when executed"),
                Basilisk6DofIntegrationEnvelope("qoi.integration.time_alignment_max_error_s", 0.0, 1.0e-9, "Dynamics/FSW time alignment when executed"),
            ),
        ),
        Basilisk6DofIntegrationCase(
            case_id="bsk_6dof2_sun_pointing_eclipse_gate",
            description="Sun pointing / sun-safe integration gate linking sun vector, eclipse context, SRP, and FSW target selection.",
            integration_scope="sun_vector_eclipse_to_sun_guidance",
            task_spec=sun,
            expected_graph_edges=("orbit_primary", "adcs_primary", "sunVector/SRP", "att_guidance.attRefOutMsg / attGuidOutMsg"),
            expected_metadata_fields=common_metadata,
            expected_modules=("spacecraft.Spacecraft", "radiationPressure", "fswAlgorithms.sunSafePoint"),
            envelopes=(Basilisk6DofIntegrationEnvelope("qoi.adcs.final_pointing_error_deg", 0.0, 3.0, "Sun pointing sanity when executed"),),
        ),
        Basilisk6DofIntegrationCase(
            case_id="bsk_6dof2_detumble_rw_momentum_gate",
            description="Detumble and reaction-wheel momentum integration gate.",
            integration_scope="body_rate_to_rw_torque_and_momentum_trace",
            task_spec=detumble,
            expected_graph_edges=("adcs_primary", "reactionWheelStateEffector.rwSpeedOutMsg", "rwMotorTorque.motorTorqueOutMsg"),
            expected_metadata_fields=common_metadata,
            expected_modules=("reactionWheelStateEffector", "simIncludeRW.rwFactory", "fswAlgorithms.rwMotorTorque"),
            envelopes=(Basilisk6DofIntegrationEnvelope("qoi.adcs.max_abs_rate_rad_s", 0.0, 0.03, "Detumble rate envelope when executed"),),
        ),
        Basilisk6DofIntegrationCase(
            case_id="bsk_6dof2_drag_srp_environment_torque_gate",
            description="Drag/SRP environment coupling gate for low-altitude LEO configuration.",
            integration_scope="environment_force_torque_to_spacecraft_dynamics",
            task_spec=drag_srp,
            expected_graph_edges=("orbit_primary", "atmosphere", "sunVector/SRP", "extForceTorque"),
            expected_metadata_fields=common_metadata,
            expected_modules=("dragDynamicEffector", "exponentialAtmosphere", "radiationPressure", "extForceTorque.ExtForceTorque"),
            envelopes=(Basilisk6DofIntegrationEnvelope("qoi.orbit.altitude_min_m", 250_000.0, 360_000.0, "Drag/SRP LEO altitude sanity when executed"),),
        ),
        Basilisk6DofIntegrationCase(
            case_id="bsk_6dof2_third_body_spice_gate",
            description="Third-body/SPICE integration gate for orbit environment to ADCS hill-frame target contract.",
            integration_scope="spice_third_body_to_hill_frame_guidance",
            task_spec=third_body,
            expected_graph_edges=("orbit_primary", "spiceInterface", "adcs_primary", "att_guidance.attRefOutMsg / attGuidOutMsg"),
            expected_metadata_fields=common_metadata,
            expected_modules=("spiceInterface.SpiceInterface", "fswAlgorithms.hillPoint", "spacecraft.Spacecraft"),
            envelopes=(Basilisk6DofIntegrationEnvelope("qoi.integration.frame_consistency_pass", 1.0, 1.0, "SPICE/hill frame metadata sanity when executed"),),
        ),
    ]


def _flatten_blueprint_modules(blueprint: Mapping[str, Any]) -> list[str]:
    modules: list[str] = []
    processes = blueprint.get("processes") if isinstance(blueprint.get("processes"), Mapping) else {}
    for process in processes.values():
        if isinstance(process, Mapping):
            modules.extend(str(x) for x in process.get("modules", []) if str(x).strip())
    graph = blueprint.get("spacecraft_graph") if isinstance(blueprint.get("spacecraft_graph"), Mapping) else {}
    for key in ("state_effectors", "dynamic_effectors", "environment_effectors"):
        modules.extend(str(x) for x in graph.get(key, []) if str(x).strip())
    return modules


def _metadata_gate(case: Basilisk6DofIntegrationCase, blueprint: Mapping[str, Any]) -> dict[str, Any]:
    meta = blueprint.get("metadata_contract") if isinstance(blueprint.get("metadata_contract"), Mapping) else {}
    coupling = blueprint.get("coupling_contract") if isinstance(blueprint.get("coupling_contract"), Mapping) else {}
    child = blueprint.get("child_capabilities") if isinstance(blueprint.get("child_capabilities"), Mapping) else {}
    fields = set(str(x) for x in meta.get("time_fields", [])) | set(str(x) for x in meta.get("frame_fields", [])) | set(str(x) for x in meta.get("solver_fields", []))
    fields.update(k for k in ("time_scale", "inertial_frame", "attitude_frame", "target_frame") if k in coupling)
    missing = sorted(set(case.expected_metadata_fields) - fields)
    child_ok = child.get("orbit_primary") == "orbit_environment.basilisk_hf.v1" and child.get("adcs_primary") == "subsystem.adcs_basilisk_fsw.v1"
    fallback_ok = "fallback" in str(blueprint.get("fallback_policy", "")).lower() and "No implicit" in str(blueprint.get("fallback_policy", ""))
    return {
        "case_id": case.case_id,
        "status": "pass" if not missing and child_ok and fallback_ok else "fail",
        "missing_metadata_fields": missing,
        "child_capabilities_ok": child_ok,
        "fallback_policy_ok": fallback_ok,
        "target_mode": coupling.get("target_mode"),
        "target_frame": coupling.get("target_frame"),
        "time_scale": coupling.get("time_scale"),
    }


def _graph_gate(case: Basilisk6DofIntegrationCase, blueprint: Mapping[str, Any]) -> dict[str, Any]:
    blob = json.dumps(blueprint, sort_keys=True, ensure_ascii=False)
    missing_edges = sorted(edge for edge in case.expected_graph_edges if edge not in blob)
    configured_modules = _flatten_blueprint_modules(blueprint)
    missing_modules = sorted(module for module in case.expected_modules if module not in configured_modules and module not in blob)
    return {
        "case_id": case.case_id,
        "status": "pass" if not missing_edges and not missing_modules else "fail",
        "missing_graph_edges": missing_edges,
        "missing_expected_modules": missing_modules,
        "configured_modules": configured_modules,
    }


def _qoi(summary: Mapping[str, Any], name: str) -> Any:
    if name.startswith("qoi."):
        key = name[len("qoi."):]
        qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
        return qoi.get(key)
    return summary.get(name)


def _local_proxy_spec_for(case: Basilisk6DofIntegrationCase) -> dict[str, Any] | None:
    if not case.local_proxy_comparable:
        return None
    native = json.loads(json.dumps(case.task_spec))
    params = native.get("parameters", {}) if isinstance(native.get("parameters"), Mapping) else {}
    coupling = params.get("coupling", {}) if isinstance(params.get("coupling"), Mapping) else {}
    spacecraft = params.get("spacecraft", {}) if isinstance(params.get("spacecraft"), Mapping) else {}
    adcs = params.get("adcs", {}) if isinstance(params.get("adcs"), Mapping) else {}
    sim = native.get("simulation", {}) if isinstance(native.get("simulation"), Mapping) else {}
    orbit = native.get("orbit_environment", {}) if isinstance(native.get("orbit_environment"), Mapping) else {}
    force = orbit.get("force_models", {}) if isinstance(orbit.get("force_models"), Mapping) else {}
    target_mode = str(coupling.get("target_mode") or adcs.get("target_mode") or "nadir").strip().lower().replace("-", "_")
    local_mode = {"sun_pointing": "sun", "sun_safe": "sun", "hill": "nadir", "velocity": "inertial", "inertial3d": "inertial"}.get(target_mode, target_mode)
    if local_mode not in {"nadir", "sun", "detumble", "inertial"}:
        local_mode = "nadir"
    initial_rate = spacecraft.get("initial_omega_bn_b_rad_s", [0.0015, -0.0008, 0.0004])
    try:
        initial_rate_deg_s = [float(x) * 57.29577951308232 for x in initial_rate]
    except Exception:
        initial_rate_deg_s = [0.08, -0.04, 0.02]
    return {
        "schema_version": native.get("schema_version", "0.1.0"),
        "task_id": case.case_id + "_local_int1_baseline",
        "task_type": "whole_spacecraft",
        "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
        "target": {"level": "whole_spacecraft", "name": "orbit_adcs", "mode": "nominal"},
        "simulation": {
            "duration_s": sim.get("duration_s", 900.0),
            "sample_s": sim.get("sample_s", 10.0),
            "backend": "python",
            "epoch_utc": sim.get("epoch_utc", "2026-07-06T00:00:00Z"),
            "solver": {"method": "rk4", "step_s": min(float((sim.get("solver") or {}).get("step_s", 0.5)), 10.0), "include_endpoint": True},
        },
        "spacecraft": {"adcs": {"dyn_step_s": 0.2, "fsw_step_s": 0.2}},
        "orbit_environment": {
            "altitude_m": orbit.get("altitude_m", 520_000.0),
            "eccentricity": orbit.get("eccentricity", 0.001),
            "inclination_deg": orbit.get("inclination_deg", 97.5),
            "raan_deg": orbit.get("raan_deg", 10.0),
            "true_anomaly_deg": orbit.get("true_anomaly_deg", 0.0),
            "enable_eclipse": orbit.get("enable_eclipse", True),
            "sun_model": "analytic",
            "magnetic_field_model": "dipole",
            "force_models": {"j2": True, "drag": bool(force.get("drag")), "srp": bool(force.get("srp"))},
            "atmosphere": {"enabled": bool(force.get("drag")), "reference_altitude_m": 400_000.0, "reference_density_kg_m3": 4.0e-12, "scale_height_m": 60_000.0},
        },
        "parameters": {
            "spacecraft": {
                "mass_kg": spacecraft.get("mass_kg", 120.0),
                "drag_area_m2": spacecraft.get("area_m2", 1.1),
                "drag_coefficient": spacecraft.get("drag_coefficient", 2.2),
                "srp_area_m2": spacecraft.get("area_m2", 1.1),
                "reflectivity_coefficient": spacecraft.get("srp_coefficient", 1.3),
            },
            "integration": {"pointing_target": local_mode, "frame_contract": "explicit_INT1_baseline_for_BSK_6DOF2", "coupling_policy": "local_proxy_comparison_only"},
            "adcs": {
                "target_mode": local_mode,
                "initial_attitude_error_deg": 8.0 if local_mode != "detumble" else 3.0,
                "initial_rate_deg_s": initial_rate_deg_s,
                "control_kp_nm_per_rad": 0.12,
                "control_kd_nm_per_rad_s": 0.9,
                "pointing_requirement_deg": 2.0,
                "environment_torques": {"gravity_gradient": True, "magnetic": True, "aerodynamic": bool(force.get("drag")), "srp": bool(force.get("srp"))},
                "gyro_bias_deg_s": [0.005, -0.002, 0.001],
                "gyro_noise_std_deg_s": 0.001,
                "star_tracker_noise_deg": 0.002,
                "sun_sensor_noise_deg": 0.2,
            },
        },
        "outputs": {"output_root": f"reports/basilisk_6dof_integration_benchmark/{case.case_id}_local_int1_baseline", "include_summary": True, "include_trace": True},
        "metadata": {"case_id": case.case_id + "_local_int1_baseline", "benchmark": "BSK-6DOF-2-local-int1-comparison"},
    }


def _run_local_int1_comparison(case: Basilisk6DofIntegrationCase) -> dict[str, Any]:
    local_spec = _local_proxy_spec_for(case)
    if local_spec is None:
        return {"case_id": case.case_id, "status": "not_applicable", "reason": "no local INT-1 baseline equivalent"}
    adapter = OrbitAdcsFidelityAdapter()
    issues = adapter.validate(local_spec)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        return {
            "case_id": case.case_id,
            "status": "validation_error",
            "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
            "error_count": len(errors),
            "errors": [issue.to_dict() for issue in errors],
        }
    result = adapter.run(local_spec)
    qoi = result.summary.get("qoi", {}) if isinstance(result.summary.get("qoi"), Mapping) else {}
    return {
        "case_id": case.case_id,
        "status": "computed",
        "capability_id": "whole_spacecraft.orbit_adcs_fidelity.v1",
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
        return "basilisk_6dof_runtime_passed"
    if basilisk_status == "configured_but_not_executed":
        return "configured_not_executed"
    return basilisk_status


def run_basilisk_6dof_integration_benchmark(report_dir: str | Path) -> dict[str, Any]:
    """Run BSK-6DOF-2 integration-gate harness and write reports."""

    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    adapter = SpacecraftBasilisk6DofAdapter()
    case_results: list[dict[str, Any]] = []
    metadata_rows: list[dict[str, Any]] = []
    graph_rows: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    availability_seen: dict[str, Any] | None = None

    for case in basilisk_6dof_integration_cases():
        config = Basilisk6DofConfig.from_task_spec(case.task_spec)
        availability = check_basilisk_6dof_availability(config)
        availability_seen = availability.to_dict()
        blueprint = build_basilisk_6dof_blueprint(config)
        issues = adapter.validate(case.task_spec)
        errors = [issue for issue in issues if issue.severity == "error" and issue.code not in {"basilisk_unavailable"}]
        result = adapter.run(case.task_spec)
        summary = result.summary
        status = _case_status(availability_available=availability.available, errors=errors, result_summary=summary)

        graph_gate = _graph_gate(case, blueprint)
        metadata_gate = _metadata_gate(case, blueprint)
        graph_rows.append(graph_gate)
        metadata_rows.append(metadata_gate)
        if graph_gate["status"] != "pass" or metadata_gate["status"] != "pass":
            status = "integration_gate_fail"

        env_evaluations: list[dict[str, Any]] = []
        if status == "basilisk_6dof_runtime_passed":
            for env in case.envelopes:
                row = env.evaluate(_qoi(summary, env.name))
                row["case_id"] = case.case_id
                env_evaluations.append(row)
                envelope_rows.append(row)
            if env_evaluations and any(row["status"] == "fail" for row in env_evaluations):
                status = "tolerance_fail"

        local_comparison = _run_local_int1_comparison(case)
        local_rows.append(local_comparison)
        case_results.append({
            "case_id": case.case_id,
            "description": case.description,
            "integration_scope": case.integration_scope,
            "status": status,
            "basilisk_status": summary.get("basilisk_status"),
            "physical_validation_status": summary.get("physical_validation_status"),
            "can_claim_high_fidelity": False,
            "error_count": len(errors),
            "non_basilisk_errors": [issue.to_dict() for issue in errors],
            "graph_gate": graph_gate,
            "metadata_gate": metadata_gate,
            "execution_scope": blueprint.get("execution_scope"),
            "blueprint": blueprint,
            "summary_qoi": summary.get("qoi", {}),
            "envelopes": env_evaluations,
            "local_proxy_comparison": local_comparison,
        })

    runtime_pass_count = sum(1 for row in case_results if row["status"] == "basilisk_6dof_runtime_passed")
    prepared_count = sum(1 for row in case_results if row["status"] == "benchmark_config_prepared")
    configured_not_executed_count = sum(1 for row in case_results if row["status"] == "configured_not_executed")
    fail_count = sum(1 for row in case_results if row["status"] in {"validation_error", "tolerance_fail", "integration_gate_fail"})
    availability_available = bool((availability_seen or {}).get("available"))
    if availability_available and runtime_pass_count == len(case_results):
        benchmark_status = "basilisk_6dof_runtime_passed"
    elif availability_available and runtime_pass_count > 0:
        benchmark_status = "partial_basilisk_6dof_runtime_coverage"
    elif availability_available and configured_not_executed_count:
        benchmark_status = "configured_not_executed_runtime_scope_pending"
    else:
        benchmark_status = "not_run_basilisk_missing"

    report = {
        "schema_version": BSK_6DOF2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "BSK-6DOF-2",
        "status": "pass" if fail_count == 0 else "fail",
        "basilisk_6dof_benchmark_status": benchmark_status,
        "availability": availability_seen or check_basilisk_6dof_availability().to_dict(),
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "fail_count": fail_count,
        "metadata_gate_pass_count": sum(1 for row in metadata_rows if row.get("status") == "pass"),
        "graph_gate_pass_count": sum(1 for row in graph_rows if row.get("status") == "pass"),
        "local_proxy_comparison_count": sum(1 for row in local_rows if row.get("status") == "computed"),
        "case_results": case_results,
        "metadata_gates": metadata_rows,
        "graph_gates": graph_rows,
        "local_proxy_comparisons": local_rows,
        "tolerance_envelope_count": len(envelope_rows),
        "can_claim_high_fidelity": False,
        "validation_scope": "basilisk_native_6dof_integration_gate_and_configuration_blueprints; runtime pass requires installed Basilisk and is not flight validation",
        "fallback_policy": "INT-1 local orbit+ADCS comparisons are explicit baselines, never implicit fallback for whole_spacecraft.basilisk_6dof.v1",
    }

    (out / "basilisk_6dof_integration_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "description", "integration_scope", "status", "basilisk_status", "physical_validation_status", "error_count", "execution_scope"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in case_results:
            writer.writerow({key: row.get(key) for key in fieldnames})
    with (out / "metadata_gate.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "status", "missing_metadata_fields", "child_capabilities_ok", "fallback_policy_ok", "target_mode", "target_frame", "time_scale"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in metadata_rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), list) else row.get(key) for key in fieldnames})
    with (out / "graph_gate.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "status", "missing_graph_edges", "missing_expected_modules"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in graph_rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), list) else row.get(key) for key in fieldnames})
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
        "schema_version": "bsk_6dof2.basilisk_6dof_readiness_matrix.v1",
        "status": report["status"],
        "basilisk_6dof_benchmark_status": benchmark_status,
        "basilisk_6dof_runtime_passed": benchmark_status == "basilisk_6dof_runtime_passed",
        "benchmark_config_prepared": prepared_count == len(case_results) and not availability_available,
        "metadata_gate_pass_count": report["metadata_gate_pass_count"],
        "graph_gate_pass_count": report["graph_gate_pass_count"],
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "local_proxy_comparison_count": report["local_proxy_comparison_count"],
        "can_claim_high_fidelity": False,
        "claim_guardrail": "Do not mark basilisk_6dof_runtime_passed or high_fidelity_ready without actual Basilisk runtime artifacts and external/reference validation.",
    }
    (out / "readiness_matrix.json").write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


__all__ = [
    "BSK_6DOF2_BENCHMARK_SCHEMA_VERSION",
    "Basilisk6DofIntegrationEnvelope",
    "Basilisk6DofIntegrationCase",
    "basilisk_6dof_integration_cases",
    "run_basilisk_6dof_integration_benchmark",
]
