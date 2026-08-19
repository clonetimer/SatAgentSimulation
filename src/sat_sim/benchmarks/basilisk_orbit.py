"""BSK-ORB-2 Basilisk-native orbit benchmark harness.

The harness deliberately separates three outcomes:

* ``basilisk_benchmark_passed``: Basilisk was installed and a runtime case
  executed inside the Basilisk-native adapter.
* ``benchmark_config_prepared``: the repository contains auditable benchmark
  cases and configuration blueprints, but Basilisk is not installed in the
  current environment.
* ``configured_not_executed``: Basilisk is available but the selected extended
  force-model path is still outside the adapter's current executable scope.
* ``runtime_unavailable``: Basilisk is available but external runtime data such as
  SPICE kernels is missing, so no pass is claimed.

That separation prevents the project from treating local proxy propagation or
configuration-blueprint generation as a real Basilisk validation result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import csv
import json
from pathlib import Path

from sat_sim.adapters.orbit_environment_basilisk_hf import OrbitEnvironmentBasiliskHfAdapter
from sat_sim.adapters.orbit_environment_orbit_fidelity import OrbitEnvironmentOrbitFidelityAdapter
from sat_sim.orbit.basilisk_hf import (
    BASILISK_ORBIT_HF_SCHEMA_VERSION,
    BasiliskOrbitHfConfig,
    build_basilisk_orbit_blueprint,
    check_basilisk_availability,
)

BSK_ORB2_BENCHMARK_SCHEMA_VERSION = "bsk_orb2.basilisk_orbit_validation_benchmark.v1"
BSK_RUN3_SPICE_THIRD_BODY_SCHEMA_VERSION = "bsk_run3.spice_third_body_runtime.v1"


@dataclass(frozen=True)
class BasiliskOrbitBenchmarkEnvelope:
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
class BasiliskOrbitBenchmarkCase:
    """A BSK-ORB-2 benchmark case and its optional local proxy comparator."""

    case_id: str
    description: str
    force_model_scope: str
    task_spec: dict[str, Any]
    expected_basilisk_modules: tuple[str, ...]
    local_proxy_comparable: bool
    envelopes: tuple[BasiliskOrbitBenchmarkEnvelope, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "description": self.description,
            "force_model_scope": self.force_model_scope,
            "task_spec": self.task_spec,
            "expected_basilisk_modules": list(self.expected_basilisk_modules),
            "local_proxy_comparable": self.local_proxy_comparable,
            "envelopes": [env.__dict__ for env in self.envelopes],
        }


def _base_basilisk_spec(case_id: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "task_id": case_id,
        "task_type": "orbit_environment",
        "capability_id": "orbit_environment.basilisk_hf.v1",
        "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
        "simulation": {"duration_s": 900.0, "sample_s": 60.0, "epoch_utc": "2026-01-01T00:00:00Z"},
        "orbit_environment": {
            "central_body": "earth",
            "altitude_m": 520_000.0,
            "eccentricity": 0.001,
            "inclination_deg": 97.5,
            "raan_deg": 0.0,
            "arg_perigee_deg": 0.0,
            "true_anomaly_deg": 0.0,
            "force_models": {"spherical_harmonics": {"enabled": False, "degree": 2}},
        },
        "parameters": {
            "spacecraft": {
                "mass_kg": 120.0,
                "area_m2": 1.0,
                "drag_area_m2": 1.2,
                "drag_coefficient": 2.2,
                "srp_area_m2": 1.0,
                "srp_coefficient": 1.3,
            }
        },
        "outputs": {"output_root": f"reports/basilisk_orbit_benchmark/{case_id}"},
        "metadata": {"case_id": case_id, "benchmark": "BSK-ORB-2"},
    }


def basilisk_orbit_benchmark_cases() -> list[BasiliskOrbitBenchmarkCase]:
    """Return deterministic BSK-ORB-2 benchmark cases."""

    gravity = _base_basilisk_spec("bsk_orb2_circular_leo_gravity_only")

    sph = _base_basilisk_spec("bsk_orb2_spherical_harmonics_j2")
    sph["orbit_environment"]["force_models"] = {"spherical_harmonics": {"enabled": True, "degree": 2}}

    third_body = _base_basilisk_spec("bsk_run3_third_body_spice_runtime")
    third_body["simulation"].update({"duration_s": 1800.0, "epoch_utc": "2026-01-01T00:00:00Z"})
    third_body["orbit_environment"]["force_models"] = {
        "spherical_harmonics": {"enabled": True, "degree": 2},
        "third_bodies": ["sun", "moon"],
        "spice_ephemeris": True,
        "spice_kernels": ["de430.bsp", "naif0012.tls", "de-403-masses.tpc", "pck00010.tpc"],
        "spice_data_path": "supportData/EphemerisData",
        "spice_zero_base": "Earth",
    }
    third_body["metadata"].update({"benchmark": "BSK-RUN-3", "runtime_scope": "spice_third_body"})

    drag = _base_basilisk_spec("bsk_run4_drag_atmosphere_runtime")
    drag["orbit_environment"]["altitude_m"] = 300_000.0
    drag["orbit_environment"]["force_models"] = {
        "spherical_harmonics": {"enabled": True, "degree": 2},
        "atmosphere_model": "exponential",
        "drag": True,
    }
    drag["orbit_environment"]["atmosphere"] = {
        "reference_altitude_m": 300_000.0,
        "reference_density_kg_m3": 2.0e-10,
        "scale_height_m": 50_000.0,
    }
    drag["metadata"].update({"benchmark": "BSK-RUN-4", "runtime_scope": "drag_atmosphere"})

    srp = _base_basilisk_spec("bsk_run4_srp_runtime")
    srp["orbit_environment"]["force_models"] = {
        "spherical_harmonics": {"enabled": True, "degree": 2},
        "srp": True,
    }
    srp["parameters"]["spacecraft"]["srp_area_m2"] = 3.0
    srp["metadata"].update({"benchmark": "BSK-RUN-4", "runtime_scope": "cannonball_srp"})

    mars = _base_basilisk_spec("bsk_orb2_mars_spherical_harmonics_config")
    mars["orbit_environment"].update({"central_body": "mars", "altitude_m": 400_000.0, "inclination_deg": 45.0})
    mars["orbit_environment"]["force_models"] = {"spherical_harmonics": {"enabled": True, "degree": 2}}

    return [
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_orb2_circular_leo_gravity_only",
            description="Basilisk-native gravity-only circular LEO runtime benchmark candidate.",
            force_model_scope="central_gravity",
            task_spec=gravity,
            expected_basilisk_modules=("spacecraft.Spacecraft", "simIncludeGravBody.gravBodyFactory"),
            local_proxy_comparable=True,
            envelopes=(
                BasiliskOrbitBenchmarkEnvelope("qoi.orbit.altitude_min_m", 450_000.0, 590_000.0, "LEO altitude sanity when executed"),
                BasiliskOrbitBenchmarkEnvelope("qoi.orbit.altitude_delta_m", -120_000.0, 120_000.0, "Short-run altitude drift sanity when executed"),
            ),
        ),
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_orb2_spherical_harmonics_j2",
            description="Basilisk spherical-harmonics/J2 configuration and optional runtime benchmark.",
            force_model_scope="spherical_harmonics_degree_2",
            task_spec=sph,
            expected_basilisk_modules=("GravBodyData.useSphericalHarmonicsGravityModel",),
            local_proxy_comparable=True,
        ),
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_run3_third_body_spice_runtime",
            description="BSK-RUN-3 third-body Sun/Moon plus SPICE ephemeris runtime smoke when kernels are available.",
            force_model_scope="third_body_spice_runtime",
            task_spec=third_body,
            expected_basilisk_modules=("gravBodyFactory.createBodies", "gravBodyFactory.createSpiceInterface", "spiceInterface.SpiceInterface", "pyswice.furnsh_c"),
            local_proxy_comparable=False,
        ),
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_run4_drag_atmosphere_runtime",
            description="BSK-RUN-4 exponential atmosphere plus drag dynamic effector runtime smoke.",
            force_model_scope="atmosphere_drag_runtime",
            task_spec=drag,
            expected_basilisk_modules=("exponentialAtmosphere.ExponentialAtmosphere", "dragDynamicEffector.DragDynamicEffector"),
            local_proxy_comparable=True,
            envelopes=(
                BasiliskOrbitBenchmarkEnvelope("qoi.environment.atmosphere_density_max_kg_m3", 1.0e-14, 1.0e-7, "Atmosphere density should be non-zero for the configured LEO smoke case"),
                BasiliskOrbitBenchmarkEnvelope("qoi.environment.drag_force_b_norm_max_n", 1.0e-8, 1.0e-1, "Drag effector body-frame force should be non-zero but bounded for the smoke case"),
            ),
        ),
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_run4_srp_runtime",
            description="BSK-RUN-4 cannonball solar-radiation-pressure runtime smoke with constant Sun message.",
            force_model_scope="srp_runtime",
            task_spec=srp,
            expected_basilisk_modules=("radiationPressure.RadiationPressure", "messaging.SpicePlanetStateMsg", "messaging.EclipseMsg"),
            local_proxy_comparable=True,
            envelopes=(
                BasiliskOrbitBenchmarkEnvelope("qoi.environment.srp_force_n_norm_max_n", 1.0e-8, 1.0e-2, "SRP cannonball force should be non-zero but bounded for the smoke case"),
            ),
        ),
        BasiliskOrbitBenchmarkCase(
            case_id="bsk_orb2_mars_spherical_harmonics_config",
            description="Mars central-body spherical-harmonics configuration coverage.",
            force_model_scope="mars_spherical_harmonics_configuration",
            task_spec=mars,
            expected_basilisk_modules=("gravFactory.createMars", "GravBodyData.useSphericalHarmonicsGravityModel"),
            local_proxy_comparable=False,
        ),
    ]


def _nested_qoi(summary: Mapping[str, Any], qoi_name: str) -> Any:
    if qoi_name.startswith("qoi."):
        key = qoi_name[len("qoi."):]
        qoi = summary.get("qoi") if isinstance(summary.get("qoi"), Mapping) else {}
        return qoi.get(key)
    return summary.get(qoi_name)


def _local_proxy_spec_for(case: BasiliskOrbitBenchmarkCase) -> dict[str, Any] | None:
    """Convert a comparable Basilisk case into an explicit local-proxy baseline."""

    if not case.local_proxy_comparable:
        return None
    spec = json.loads(json.dumps(case.task_spec))
    orbit = spec["orbit_environment"]
    force = orbit.get("force_models", {})
    if force.get("third_bodies") or force.get("spice_ephemeris"):
        return None
    local_force = {
        "j2": bool((force.get("spherical_harmonics") or {}).get("enabled", False)),
        "drag": bool(force.get("drag") or force.get("atmosphere_model")),
        "srp": bool(force.get("srp") or force.get("solar_radiation_pressure")),
    }
    orbit["force_models"] = local_force
    if local_force["drag"]:
        orbit["atmosphere"] = {
            "enabled": True,
            "reference_altitude_m": float(orbit.get("altitude_m", 300_000.0)),
            "reference_density_kg_m3": 2.0e-10,
            "scale_height_m": 50_000.0,
        }
    spec["capability_id"] = "orbit_environment.orbit_fidelity.v1"
    spec["task_id"] = case.case_id + "_local_proxy_baseline"
    spec["metadata"]["case_id"] = case.case_id + "_local_proxy_baseline"
    spec["simulation"]["solver"] = {"method": "rk4", "step_s": spec["simulation"]["sample_s"], "include_endpoint": True}
    spec["outputs"]["output_root"] = f"reports/basilisk_orbit_benchmark/{case.case_id}_local_proxy_baseline"
    return spec


def _run_local_proxy_comparison(case: BasiliskOrbitBenchmarkCase) -> dict[str, Any]:
    local_spec = _local_proxy_spec_for(case)
    if local_spec is None:
        return {"case_id": case.case_id, "status": "not_applicable", "reason": "no local proxy equivalent for requested Basilisk force-model surface"}
    adapter = OrbitEnvironmentOrbitFidelityAdapter()
    issues = adapter.validate(local_spec)
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        return {
            "case_id": case.case_id,
            "status": "validation_error",
            "capability_id": "orbit_environment.orbit_fidelity.v1",
            "error_count": len(errors),
            "errors": [issue.to_dict() for issue in errors],
        }
    result = adapter.run(local_spec)
    qoi = result.summary.get("qoi", {}) if isinstance(result.summary.get("qoi"), Mapping) else {}
    return {
        "case_id": case.case_id,
        "status": "computed",
        "capability_id": "orbit_environment.orbit_fidelity.v1",
        "fallback_policy": "explicit_comparison_only_not_implicit_fallback",
        "qoi": qoi,
        "trace_rows": result.summary.get("trace_rows"),
        "physical_validation_status": result.summary.get("physical_validation_status"),
    }


def _case_status(*, availability_available: bool, errors: list[Any], result_summary: Mapping[str, Any]) -> str:
    if errors:
        return "validation_error"
    basilisk_status = str(result_summary.get("basilisk_status") or "unknown")
    if not availability_available and basilisk_status == "unavailable":
        return "benchmark_config_prepared"
    if basilisk_status == "executed":
        return "basilisk_benchmark_passed"
    if basilisk_status == "unavailable":
        return "benchmark_config_prepared" if not availability_available else "runtime_unavailable"
    if basilisk_status == "configured_but_not_executed":
        return "configured_not_executed"
    return basilisk_status


def run_basilisk_orbit_benchmark(report_dir: str | Path) -> dict[str, Any]:
    """Run BSK-ORB-2 benchmark harness and write JSON/CSV artifacts."""

    out = Path(report_dir)
    out.mkdir(parents=True, exist_ok=True)
    adapter = OrbitEnvironmentBasiliskHfAdapter()
    case_results: list[dict[str, Any]] = []
    envelope_rows: list[dict[str, Any]] = []
    local_rows: list[dict[str, Any]] = []
    availability_seen: dict[str, Any] | None = None

    for case in basilisk_orbit_benchmark_cases():
        config = BasiliskOrbitHfConfig.from_task_spec(case.task_spec)
        availability = check_basilisk_availability(config)
        availability_seen = availability.to_dict()
        blueprint = build_basilisk_orbit_blueprint(config)
        issues = adapter.validate(case.task_spec)
        errors = [issue for issue in issues if issue.severity == "error" and issue.code != "basilisk_unavailable"]
        result = adapter.run(case.task_spec)
        summary = result.summary
        status = _case_status(availability_available=availability.available, errors=errors, result_summary=summary)

        env_evaluations = []
        if status == "basilisk_benchmark_passed":
            for env in case.envelopes:
                row = env.evaluate(_nested_qoi(summary, env.name))
                row["case_id"] = case.case_id
                env_evaluations.append(row)
                envelope_rows.append(row)
            if env_evaluations and any(row["status"] == "fail" for row in env_evaluations):
                status = "tolerance_fail"

        missing_expected_modules = sorted(set(case.expected_basilisk_modules) - set(blueprint.get("configured_modules", [])))
        local_comparison = _run_local_proxy_comparison(case)
        local_rows.append(local_comparison)
        case_results.append({
            "case_id": case.case_id,
            "description": case.description,
            "force_model_scope": case.force_model_scope,
            "status": status,
            "basilisk_status": summary.get("basilisk_status"),
            "physical_validation_status": summary.get("physical_validation_status"),
            "can_claim_high_fidelity": False,
            "error_count": len(errors),
            "non_basilisk_errors": [issue.to_dict() for issue in errors],
            "expected_basilisk_modules": list(case.expected_basilisk_modules),
            "missing_expected_modules": missing_expected_modules,
            "configured_modules": blueprint.get("configured_modules", []),
            "execution_scope": blueprint.get("execution_scope"),
            "blueprint": blueprint,
            "summary_qoi": summary.get("qoi", {}),
            "envelopes": env_evaluations,
            "local_proxy_comparison": local_comparison,
        })

    runtime_pass_count = sum(1 for row in case_results if row["status"] == "basilisk_benchmark_passed")
    prepared_count = sum(1 for row in case_results if row["status"] == "benchmark_config_prepared")
    configured_not_executed_count = sum(1 for row in case_results if row["status"] == "configured_not_executed")
    runtime_unavailable_count = sum(1 for row in case_results if row["status"] == "runtime_unavailable")
    fail_count = sum(1 for row in case_results if row["status"] in {"validation_error", "tolerance_fail"})
    availability_available = bool((availability_seen or {}).get("available"))
    if availability_available and runtime_pass_count == len(case_results):
        basilisk_benchmark_status = "basilisk_runtime_passed"
    elif availability_available and runtime_pass_count > 0:
        basilisk_benchmark_status = "partial_basilisk_runtime_coverage"
    else:
        basilisk_benchmark_status = "not_run_basilisk_missing"
    report = {
        "schema_version": BSK_ORB2_BENCHMARK_SCHEMA_VERSION,
        "route_version": "BSK-ORB-2 + BSK-RUN-3 + BSK-RUN-4",
        "status": "pass" if fail_count == 0 and all(not row["missing_expected_modules"] for row in case_results) else "fail",
        "basilisk_benchmark_status": basilisk_benchmark_status,
        "availability": availability_seen or check_basilisk_availability().to_dict(),
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "configured_not_executed_count": configured_not_executed_count,
        "runtime_unavailable_count": runtime_unavailable_count,
        "fail_count": fail_count,
        "local_proxy_comparison_count": sum(1 for row in local_rows if row.get("status") == "computed"),
        "case_results": case_results,
        "local_proxy_comparisons": local_rows,
        "tolerance_envelope_count": len(envelope_rows),
        "can_claim_high_fidelity": False,
        "validation_scope": "basilisk_native_benchmark_harness, central/J2 runtime, BSK-RUN-3 SPICE/third-body runtime when kernels are available, and BSK-RUN-4 drag/SRP runtime smoke; not flight validation",
        "fallback_policy": "local proxy comparisons are explicit baselines, never implicit fallback for orbit_environment.basilisk_hf.v1",
    }

    (out / "basilisk_orbit_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out / "case_results.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["case_id", "description", "force_model_scope", "status", "basilisk_status", "physical_validation_status", "error_count", "execution_scope"]
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
        "schema_version": "bsk_run4.basilisk_orbit_readiness_matrix.v1",
        "status": report["status"],
        "basilisk_benchmark_status": basilisk_benchmark_status,
        "basilisk_runtime_passed": basilisk_benchmark_status == "basilisk_runtime_passed",
        "benchmark_config_prepared": prepared_count == len(case_results) and not availability_available,
        "can_claim_high_fidelity": False,
        "claim_guardrail": "Do not mark basilisk_benchmark_passed or high_fidelity_ready without actual Basilisk runtime artifacts and external/reference validation.",
        "case_count": len(case_results),
        "runtime_pass_count": runtime_pass_count,
        "prepared_count": prepared_count,
        "local_proxy_comparison_count": report["local_proxy_comparison_count"],
        "runtime_unavailable_count": runtime_unavailable_count,
        "spice_third_body_runtime_passed": any(row["case_id"] == "bsk_run3_third_body_spice_runtime" and row["status"] == "basilisk_benchmark_passed" for row in case_results),
        "drag_atmosphere_runtime_passed": any(row["case_id"] == "bsk_run4_drag_atmosphere_runtime" and row["status"] == "basilisk_benchmark_passed" for row in case_results),
        "srp_runtime_passed": any(row["case_id"] == "bsk_run4_srp_runtime" and row["status"] == "basilisk_benchmark_passed" for row in case_results),
    }
    report["readiness"] = readiness
    (out / "readiness_matrix.json").write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "basilisk_orbit_benchmark_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


__all__ = [
    "BSK_ORB2_BENCHMARK_SCHEMA_VERSION",
    "BasiliskOrbitBenchmarkEnvelope",
    "BasiliskOrbitBenchmarkCase",
    "BSK_RUN3_SPICE_THIRD_BODY_SCHEMA_VERSION",
    "basilisk_orbit_benchmark_cases",
    "run_basilisk_orbit_benchmark",
]
