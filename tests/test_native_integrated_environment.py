"""Exact-head DT-1 four-way environmental integration in one Basilisk task.

Same run: NRLMSISE-00 drag, native cannonball SRP, full GGM03S degree 4,
and the IAGA IGRF14 coefficient model via the official Basilisk MAG engine.

The values below are *synthetic test inputs*; no as-built or flight calibration.
Separate existing tests independently verify source integrity and dynamics.
"""
from __future__ import annotations

from pathlib import Path
import math
import os
import shutil
import subprocess
import sys

import numpy as np

from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
)


def run_whole(values, *, duration=120.):
    return UnifiedNativeRuntime(UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=duration, step_s=1., sample_s=10.,
        adcs_only=False, events=(), values=values,
    )).run()


def vector(row, prefix):
    return np.array([row[f"{prefix}_{axis}"] for axis in "xyz"], dtype=float)


def configure_weather(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures" / "celestrak_space_weather_2026-03-04.csv"
    destination = tmp_path / "SW-Last5Years.csv"
    shutil.copyfile(source, destination)
    subprocess.run([
        sys.executable,
        str(Path(__file__).parents[1] / "scripts" / "fetch_space_weather_asset.py"),
        "--offline",
        "--output", str(destination),
        "--source-url", "test-fixture://celestrak-compatible-sample",
    ], check=True, capture_output=True, text=True)
    monkeypatch.setenv("SAT_SIM_SPACE_WEATHER_PATH", str(destination))


def test_actual_four_effects_coexist_with_independent_ablation(tmp_path, monkeypatch):
    configure_weather(tmp_path, monkeypatch)
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-03-04T21:48:00Z",
        "orbit_radius_m": 6_650_000.,
        "eccentricity": 0.0,
        "inclination_deg": 0.,
        "spacecraft_mass_kg": 100.,
        "controller_k": 0.,
        "controller_p": 0.,
    }
    # The initial illumination is derived from the native DE430 SPICE Sun
    # output, not a fabricated ephemeris time series.
    probe = run_whole(base, duration=10.)
    sun = vector(probe.trace_rows[0], "orbit.sun_position_m")
    sun_anomaly = math.atan2(float(sun[1]), float(sun[0])) % (2*math.pi)
    base["initial_true_anomaly_rad"] = sun_anomaly
    drag = {"atmosphere_model":"nrlmsise00",
            "drag_area_m2":1., "drag_coefficient":2.2}
    srp = {"srp_model":"cannonball",
           "srp_reference_area_m2":20., "srp_coefficient_reflection":1.5}
    high = {"earth_gravity_model":"ggm03s_degree4",
            "magnetic_field_model":"igrf14"}
    all_on = run_whole({**base,**high,**drag,**srp})
    no_drag = run_whole({**base,**high,**srp})
    no_srp = run_whole({**base,**high,**drag})
    wmm = run_whole({**base,**high,**drag,**srp,
                     "magnetic_field_model":"wmm2025"})
    j2 = run_whole({**base,**high,**drag,**srp,
                    "earth_gravity_model":"legacy_j2_only"})
    evidence = all_on.summary["orbital_environment"]
    assert evidence["drag_enabled"] is True
    assert evidence["space_weather"]["coverage_status"] == "PASS"
    assert evidence["space_weather"]["source_url"] == "test-fixture://celestrak-compatible-sample"
    assert evidence["gravity_degree"] == 4
    assert evidence["gravity_model"] == "ggm03s_degree4"
    assert evidence["gravity_source"]["source_git_blob_sha1"] == "6022af24f8aaeba461eb6a3c22330265578a8710"
    assert evidence["magnetic_field_model_id"] == "IGRF14_2025_2030"
    assert evidence["magnetic_source_is_WMM2025"] is False
    assert evidence["assets"]["igrf14"]["source_git_blob_sha1"] == "13e7122692044f292afcdf9cac03c017837fa064"
    assert all_on.summary["solar_radiation_pressure"]["status"] == "PASS"
    assert all_on.summary["aerodynamic_environment"]["status"] == "PASS"
    assert all_on.summary["gravity_harmonics"]["degree_and_order"] == 4
    assert all_on.summary["runtime_message_read_error_count"] == 0
    manifest = all_on.metadata["runtime_manifest"]
    assert manifest["process"] == "UnifiedNativeProcess"
    assert manifest["task"] == "UnifiedNativeTask"
    tags = {m["tag"] for m in manifest["instantiated_modules"]}
    assert {"msis_atmosphere","nrlmsise_drag_effector",
            "spice_eclipse_cannonball_srp","spice_ephemeris",
            "magnetic_field","magnetometer"}.issubset(tags)
    rows = all_on.trace_rows
    assert len(rows) == 13
    samples = rows[1:]
    assert len(samples) == 12
    density = [r["environment.atmosphere_density_kg_m3"] for r in samples]
    drag_force = [r["orbit.drag_force_norm_n"] for r in samples]
    srp_force = [r["orbit.srp_force_norm_n"] for r in samples]
    eclipse = [r["orbit.eclipse_factor"] for r in samples]
    power = [r["orbit.drag_force_relative_velocity_power_w"] for r in samples]
    assert min(density) > 0.
    assert min(drag_force) > 0.
    assert min(srp_force) > 0.
    assert min(eclipse) > 0.99
    assert max(power) <= 1e-12 and min(power) < 0.
    assert all(math.isfinite(x) for x in density+drag_force+srp_force+eclipse+power)
    for r in samples:
        a = np.array([r[f"orbit.earth_fixed_dcm_{i}{j}"]
                      for i in range(3) for j in range(3)]).reshape(3,3)
        b = np.array([r[f"environment.igrf14.earth_j20002pfix_{i}{j}"]
                      for i in range(3) for j in range(3)]).reshape(3,3)
        assert np.linalg.norm(a-b) < 1e-10
        assert r["orbit.earth_orientation_computed"] == 1
        assert r["environment.igrf14.earth_orientation_computed"] == 1
        rel_sun = vector(r,"orbit.sun_position_m")-vector(r,"orbit.position_m")
        srp_n = vector(r,"orbit.srp_force_n")
        assert float(rel_sun @ srp_n) < 0.
    finish = lambda output: vector(output.trace_rows[-1],"orbit.position_m")
    delta_drag = float(np.linalg.norm(finish(all_on)-finish(no_drag)))
    delta_srp = float(np.linalg.norm(finish(all_on)-finish(no_srp)))
    delta_gravity = float(np.linalg.norm(finish(all_on)-finish(j2)))
    magnetometer_delta = max(
        float(np.linalg.norm(vector(a,"adcs.magnetometer.tesla") -
                             vector(b,"adcs.magnetometer.tesla")))
        for a,b in zip(all_on.trace_rows[1:],wmm.trace_rows[1:]))
    assert delta_drag > 0.001
    assert delta_srp > 0.001
    assert delta_gravity > 0.001
    assert magnetometer_delta > 1e-10
    print(
        "DT1_FOUR_EFFECTS_NATIVE_MEASURED "
        f"samples={len(samples)} density_min_kg_m3={min(density):.9e} "
        f"drag_force_max_n={max(drag_force):.9e} "
        f"srp_force_max_n={max(srp_force):.9e} "
        f"drag_power_min_w={min(power):.9e} "
        f"eclipse_min={min(eclipse):.9e} "
        f"delta_drag_m={delta_drag:.9e} "
        f"delta_srp_m={delta_srp:.9e} "
        f"delta_gravity_legacy_j2_m={delta_gravity:.9e} "
        f"max_magnetometer_igrf_vs_wmm_t={magnetometer_delta:.9e} "
        "fixture_mass_kg=100 fixture_drag_area_m2=1 fixture_cd=2.2 "
        "fixture_srp_area_m2=20 fixture_cr=1.5 "
        "fixture_duration_s=120 fixture_step_s=1",
        flush=True
    )


def test_all_four_effects_one_real_orbit_crosses_eclipse_with_drag_still_active(tmp_path, monkeypatch):
    """Run a complete Earth orbit with all four true native models enabled.

    The earlier short 120 s A/B tests prove each effect changes the model.
    This covers a *full illuminated -> penumbra -> umbra -> illuminated*
    environmental cycle, plus a same-initial-state SRP-off control, without
    pretending the default 3600 s continuous test uses this configuration.
    """
    configure_weather(tmp_path, monkeypatch)
    semimajor_m = 6_650_000.
    # At this test orbit, an actual Keplerian period is about 5396.89 s.
    # 5400 s includes a complete orbital revolution, not just an arc.
    period_s = 2. * math.pi * math.sqrt(semimajor_m**3 / 3.986004415e14)
    duration_s = 5400.
    assert duration_s >= period_s
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-03-04T21:48:00Z",
        "orbit_radius_m": semimajor_m,
        "eccentricity": 0.0,
        "inclination_deg": 0.,
        "spacecraft_mass_kg": 100.,
        "controller_k": 0.,
        "controller_p": 0.,
    }
    # Use the actual time-stamped native DE430 Sun vector at the initial
    # physical epoch. The orbital phase is not inferred from wall clock.
    probe = run_whole(base, duration=10.)
    sun = vector(probe.trace_rows[0], "orbit.sun_position_m")
    base["initial_true_anomaly_rad"] = math.atan2(float(sun[1]), float(sun[0])) % (2.*math.pi)
    fixture = {
        **base,
        "earth_gravity_model": "ggm03s_degree4",
        "magnetic_field_model": "igrf14",
        "atmosphere_model": "nrlmsise00",
        "drag_area_m2": 1.,
        "drag_coefficient": 2.2,
        "srp_model": "cannonball",
        "srp_reference_area_m2": 20.,
        "srp_coefficient_reflection": 1.5,
    }

    def orbit_sim(values):
        return UnifiedNativeRuntime(UnifiedRuntimeConfig(
            capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            duration_s=duration_s,
            step_s=5.,
            sample_s=60.,
            adcs_only=False,
            events=(),
            values=values,
        )).run()

    full = orbit_sim(fixture)
    without_srp = orbit_sim({**fixture, "srp_model":"disabled"})
    assert full.summary["orbital_environment"]["drag_enabled"] is True
    assert full.summary["orbital_environment"]["gravity_degree"] == 4
    assert full.summary["orbital_environment"]["magnetic_field_model_id"] == "IGRF14_2025_2030"
    assert full.summary["orbital_environment"]["space_weather"]["coverage_status"] == "PASS"
    assert full.summary["solar_radiation_pressure"]["status"] == "PASS"
    assert full.summary["aerodynamic_environment"]["status"] == "PASS"
    assert full.summary["runtime_message_read_error_count"] == 0
    rows = full.trace_rows
    assert len(rows) == 91
    assert abs(rows[-1]["time_s"]-duration_s) < 1e-6
    samples = rows[1:]
    illuminated = [r for r in samples if r["orbit.eclipse_factor"] >= 0.999]
    umbra = [r for r in samples if r["orbit.eclipse_factor"] <= 0.001]
    assert len(illuminated) >= 30
    assert len(umbra) >= 10
    assert len(illuminated) + len(umbra) <= len(samples)
    for r in samples:
        factor = float(r["orbit.eclipse_factor"])
        density = float(r["environment.atmosphere_density_kg_m3"])
        drag = float(r["orbit.drag_force_norm_n"])
        drag_work = float(r["orbit.drag_force_relative_velocity_power_w"])
        solar = float(r["orbit.srp_force_norm_n"])
        assert 0. <= factor <= 1.
        assert all(math.isfinite(v) for v in (density,drag,drag_work,solar))
        assert density > 0. and drag > 0. and drag_work < 0. and solar >= 0.
        assert np.isfinite(vector(r, "adcs.magnetometer.reference_n_tesla")).all()
        dcm = np.array([r[f"orbit.earth_fixed_dcm_{i}{j}"]
                        for i in range(3) for j in range(3)]).reshape(3,3)
        igrf_dcm = np.array([r[f"environment.igrf14.earth_j20002pfix_{i}{j}"]
                             for i in range(3) for j in range(3)]).reshape(3,3)
        assert np.linalg.norm(dcm - igrf_dcm) < 1e-10
    for r in umbra:
        assert r["orbit.srp_force_norm_n"] < 1e-10
        assert r["orbit.drag_force_norm_n"] > 0.
    for r in illuminated:
        assert r["orbit.srp_force_norm_n"] > 0.
        assert float(
            vector(r,"orbit.srp_force_n") @
            (vector(r,"orbit.sun_position_m") - vector(r,"orbit.position_m"))
        ) < 0.
    assert np.linalg.norm(
        np.array([samples[0][f"orbit.earth_fixed_dcm_{i}{j}"] for i in range(3) for j in range(3)]) -
        np.array([samples[-1][f"orbit.earth_fixed_dcm_{i}{j}"] for i in range(3) for j in range(3)])
    ) > 0.1
    delta_srp_m = float(np.linalg.norm(
        vector(full.trace_rows[-1],"orbit.position_m") -
        vector(without_srp.trace_rows[-1],"orbit.position_m")
    ))
    assert delta_srp_m > 0.001
    forces = [float(r["orbit.drag_force_norm_n"]) for r in samples]
    srp_forces = [float(r["orbit.srp_force_norm_n"]) for r in samples]
    print(
        "DT1_FOUR_EFFECTS_FULL_ORBIT_MEASURED "
        f"duration_s={duration_s:.0f} period_s={period_s:.6f} "
        f"step_s=5 output_s=60 samples={len(samples)} "
        f"sunlit_samples={len(illuminated)} umbra_samples={len(umbra)} "
        f"drag_min_n={min(forces):.9e} drag_max_n={max(forces):.9e} "
        f"srp_max_n={max(srp_forces):.9e} "
        f"srp_umbra_max_n={max(r['orbit.srp_force_norm_n'] for r in umbra):.9e} "
        f"srp_on_vs_off_final_position_m={delta_srp_m:.9e} "
        "full_ggm03s_degree=4 magnetic_model=IGRF14_2025_2030 "
        "space_weather_source=test-fixture://celestrak-compatible-sample",
        flush=True,
    )


def test_four_effects_full_orbit_timestep_refinement(tmp_path, monkeypatch):
    """Predeclared h/h2/h4 refinement with the four effects in one native graph.

    This is an internal numerical check under synthetic engineering inputs.
    It does not use an independent trajectory reference or task error budget.
    """
    configure_weather(tmp_path, monkeypatch)
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-03-04T21:48:00Z",
        "orbit_radius_m": 6_650_000.,
        "eccentricity": 0.,
        "inclination_deg": 0.,
        "spacecraft_mass_kg": 100.,
        "controller_k": 0.,
        "controller_p": 0.,
    }
    probe = run_whole(base, duration=10.)
    sun = vector(probe.trace_rows[0], "orbit.sun_position_m")
    base["initial_true_anomaly_rad"] = math.atan2(float(sun[1]), float(sun[0])) % (2. * math.pi)
    values = {
        **base,
        "earth_gravity_model": "ggm03s_degree4",
        "magnetic_field_model": "igrf14",
        "atmosphere_model": "nrlmsise00",
        "drag_area_m2": 1.,
        "drag_coefficient": 2.2,
        "srp_model": "cannonball",
        "srp_reference_area_m2": 20.,
        "srp_coefficient_reflection": 1.5,
    }
    runs = []
    for step_s in (10., 5., 2.5):
        runs.append(UnifiedNativeRuntime(UnifiedRuntimeConfig(
            capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            duration_s=5400., step_s=step_s, sample_s=60.,
            adcs_only=False, events=(), values=values,
        )).run())
    assert all(len(run.trace_rows) == 91 for run in runs)
    assert all(run.summary["orbital_environment"]["gravity_degree"] == 4 for run in runs)
    assert all(run.summary["aerodynamic_environment"]["status"] == "PASS" for run in runs)
    assert all(run.summary["solar_radiation_pressure"]["status"] == "PASS" for run in runs)
    assert all(run.summary["orbital_environment"]["magnetic_field_model_id"] == "IGRF14_2025_2030" for run in runs)
    assert all(run.summary["orbital_environment"]["space_weather"]["coverage_status"] == "PASS" for run in runs)
    for run in runs:
        assert np.allclose([row["time_s"] for row in run.trace_rows],
                           range(0, 5401, 60), rtol=0., atol=1e-6)
        assert any(row["orbit.eclipse_factor"] < .001 for row in run.trace_rows[1:])
        assert any(row["orbit.eclipse_factor"] > .999 for row in run.trace_rows[1:])
        assert all(row["orbit.drag_force_norm_n"] > 0. for row in run.trace_rows[1:])
    positions = [vector(run.trace_rows[-1], "orbit.position_m") for run in runs]
    velocities = [vector(run.trace_rows[-1], "orbit.velocity_m_s") for run in runs]
    coarse_m = float(np.linalg.norm(positions[0] - positions[1]))
    fine_m = float(np.linalg.norm(positions[1] - positions[2]))
    coarse_m_s = float(np.linalg.norm(velocities[0] - velocities[1]))
    fine_m_s = float(np.linalg.norm(velocities[1] - velocities[2]))
    order = math.log(coarse_m / fine_m, 2.)
    radial = positions[2] / np.linalg.norm(positions[2])
    normal = np.cross(positions[2], velocities[2])
    normal /= np.linalg.norm(normal)
    transverse = np.cross(normal, radial)
    fine_delta = positions[1] - positions[2]
    fine_rtn_m = [float(fine_delta @ axis) for axis in (radial, transverse, normal)]
    max_trace_m = max(float(np.linalg.norm(
        vector(a, "orbit.position_m") - vector(b, "orbit.position_m")))
        for a, b in zip(runs[1].trace_rows, runs[2].trace_rows))
    print(
        "DT3_FOUR_EFFECTS_FULL_ORBIT_REFINEMENT "
        f"duration_s=5400 sample_s=60 steps_s=10,5,2.5 "
        f"coarse_position_delta_m={coarse_m:.9e} "
        f"fine_position_delta_m={fine_m:.9e} "
        f"observed_position_order={order:.8f} "
        f"coarse_velocity_delta_m_s={coarse_m_s:.9e} "
        f"fine_velocity_delta_m_s={fine_m_s:.9e} "
        f"fine_rtn_m={','.join(f'{value:.9e}' for value in fine_rtn_m)} "
        f"max_fine_trace_delta_m={max_trace_m:.9e} "
        "reference=internal_refinement_not_independent_truth",
        flush=True,
    )
    # Reuse the pre-existing coupled-SPICE numerical gate, not a new
    # after-the-fact tolerance selected from this run's measured values.
    assert 1.5 < order < 4.5, (coarse_m, fine_m, order)
    assert fine_m < 2., (coarse_m, fine_m)
    assert coarse_m_s > fine_m_s > 0., (coarse_m_s, fine_m_s)

def test_four_effects_pass_canonical_taskspec_exported_script_and_sealed_run_bundle(tmp_path, monkeypatch):
    """Product entry regression: opt-in physics cannot vanish after TaskSpec export.

    The LLM itself is deliberately not under test. A deterministic, validated
    product TaskSpec is exported to runnable Python and executed; the same
    inputs are separately committed as a real sealed Run Bundle. No dynamics
    fixture/acceptance threshold is modified for this product-path check.
    """
    import json
    from importlib.metadata import version

    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
    from sat_sim.scenario_templates import instantiate_scenario_template
    from sat_sim.script_exporter import export_runner_script

    assert version("bsk") == "2.11.0"
    configure_weather(tmp_path, monkeypatch)
    epoch = "2026-03-04T21:48:00Z"
    orbit_radius_m = 6_650_000.0
    # Source the daylight phase from real DE430 Sun, never a fabricated
    # ephemeris. This is a test-only fixture, not an approved orbit reference.
    probe = run_whole({
        "environment_profile": "spice_wmm",
        "epoch_utc": epoch,
        "orbit_radius_m": orbit_radius_m,
        "eccentricity": 0.0,
        "inclination_deg": 0.0,
    }, duration=10.)
    sun = vector(probe.trace_rows[0], "orbit.sun_position_m")
    anomaly = math.atan2(float(sun[1]), float(sun[0])) % (2.0 * math.pi)

    task = instantiate_scenario_template(
        "whole_spacecraft_unified_native", task_id="dt1_product_entry_four_effects")
    task["simulation"].update(duration_s=30.0, step_s=1.0, sample_s=10.0)
    task["assurance"]["allow_proxy"] = False
    selected = task["parameters"]["values"]
    selected.update({
        "environment_profile": "spice_wmm",
        "epoch_utc": epoch,
        "orbit_radius_m": orbit_radius_m,
        "eccentricity": 0.0,
        "inclination_deg": 0.0,
        "initial_true_anomaly_rad": anomaly,
        "spacecraft_mass_kg": 100.0,
        "controller_k": 0.0,
        "controller_p": 0.0,
        "earth_gravity_model": "ggm03s_degree4",
        "magnetic_field_model": "igrf14",
        "atmosphere_model": "nrlmsise00",
        "drag_area_m2": 1.0,
        "drag_coefficient": 2.2,
        "srp_model": "cannonball",
        "srp_reference_area_m2": 20.0,
        "srp_coefficient_reflection": 1.5,
    })
    spec_file = tmp_path / "product_taskspec.json"
    spec_file.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    exported = export_runner_script(
        spec_file, tmp_path / "four_effects_runner.py",
        kind="capability-python", output_root=tmp_path / "script_outputs")
    code = exported.output_path.read_text(encoding="utf-8")
    compile(code, str(exported.output_path), "exec")
    for item in ("ggm03s_degree4", "igrf14", "nrlmsise00", "cannonball"):
        assert item in code, f"exported script lost opt-in model: {item}"

    # Run the *exported script*, rather than merely compiling its syntax.
    child_env = dict(os.environ)
    source_root = Path(__file__).parents[1]
    child_env["PYTHONPATH"] = os.pathsep.join((str(source_root / "src"),
                                              str(source_root),
                                              child_env.get("PYTHONPATH", "")))
    script_run = subprocess.run(
        [sys.executable, str(exported.output_path)],
        cwd=source_root, env=child_env,
        text=True, capture_output=True, timeout=180, check=False)
    # The production runner exits 1 for either failed planning or a failed
    # validation outcome. Capture the *actual* reason, rather than printing
    # only the trailing bytes of a long Run Summary and guessing the root.
    marker = '{\n  "ok":'
    start = script_run.stdout.rfind(marker)
    assert start >= 0, (
        f"exported runner produced no final JSON; exit={script_run.returncode} "
        f"stdout head={script_run.stdout[:1500]} "
        f"stdout tail={script_run.stdout[-1500:]} "
        f"stderr={script_run.stderr[-1000:]}")
    script_result = json.loads(script_run.stdout[start:])
    if script_run.returncode != 0 or not script_result.get("ok", False):
        outcome = script_result.get("validation_outcome") or {}
        physical = outcome.get("physical_validation") or {}
        nonpass = [
            {"check_id": row.get("check_id"), "result": row.get("result"),
             "reason_code": row.get("reason_code")}
            for row in outcome.get("checks", [])
            if row.get("result") not in ("PASS",)
        ]
        raise AssertionError(
            "DT1_GENERATED_SCRIPT_PRODUCT_GATE_FAILED "
            f"exit={script_run.returncode} result={outcome.get('result')} "
            f"reason={outcome.get('reason_code')} "
            f"nonpass={nonpass[:12]} "
            f"physical_issues={(physical.get('issues') or [])[:8]} "
            f"physical_fail_count={physical.get('fail_count')} "
            f"physical_results={physical.get('check_results')} "
            f"plan_status={script_result.get('planning', {}).get('validation')} "
            f"summary_status={script_result.get('summary', {}).get('overall_status')}")
    assert script_result["capability_id"] == "whole_spacecraft.unified_native.v1"
    assert script_result["validation_outcome"]["result"] == "PASS"

    # Independently execute the same TaskSpec through the product Run Bundle.
    prepared = prepare_run(
        task, output_root=tmp_path / "bundles", run_id="dt1_product_four_effects")
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1, hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True
    bundle_spec = json.loads(
        (Path(prepared.bundle_root) / "input" / "task_spec.json").read_text(encoding="utf-8"))
    assert all(bundle_spec["parameters"]["values"][key] == selected[key] for key in (
        "earth_gravity_model", "magnetic_field_model", "atmosphere_model",
        "drag_area_m2", "drag_coefficient", "srp_model",
        "srp_reference_area_m2", "srp_coefficient_reflection"))

    for output in (script_result["summary"], executed.summary):
        env = output["orbital_environment"]
        assert env["gravity_degree"] == 4
        assert env["magnetic_field_model_id"] == "IGRF14_2025_2030"
        assert env["drag_enabled"] is True
        assert env["space_weather"]["source_url"] == "test-fixture://celestrak-compatible-sample"
        assert output["aerodynamic_environment"]["status"] == "PASS"
        assert output["aerodynamic_environment"]["drag_force_max_n"] > 0.0
        assert output["solar_radiation_pressure"]["status"] == "PASS"
        assert output["gravity_harmonics"]["degree_and_order"] == 4
        assert output["runtime_message_read_error_count"] == 0
    assert max(row["orbit.drag_force_norm_n"] for row in executed.trace_rows[1:]) > 0.0
    assert max(row["orbit.srp_force_norm_n"] for row in executed.trace_rows[1:]) > 0.0

    # Same submitted physics inputs through both official product routes.
    script_drag_n = script_result["summary"]["aerodynamic_environment"]["drag_force_max_n"]
    bundle_drag_n = executed.summary["aerodynamic_environment"]["drag_force_max_n"]
    assert math.isclose(script_drag_n, bundle_drag_n, rel_tol=1e-6, abs_tol=1e-12)
    print(
        "DT1_PRODUCT_SCRIPT_BUNDLE_MEASURED "
        f"script_drag_max_n={script_drag_n:.9e} "
        f"bundle_drag_max_n={bundle_drag_n:.9e} "
        f"bundle_rows={len(executed.trace_rows)} "
        f"bundle_plan_sha256={prepared.execution_plan_sha256} "
        "script_executed=true bundle_sealed=true "
        "fixture_source=test-fixture://celestrak-compatible-sample",
        flush=True,
    )


def test_dt12_four_native_environment_effects_and_rw_deadband_in_same_run(tmp_path, monkeypatch):
    """A/B on the SAME five-effect whole-spacecraft graph, not separate PR tests.

    Controlled project RW deadband is a test-only electrical-driver assumption;
    all four external physics models are genuinely enabled in both Basilisk runs.
    No flight, calibrated motor electronics, or as-built CAD claims.
    """
    configure_weather(tmp_path, monkeypatch)
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-03-04T21:48:00Z",
        "orbit_radius_m": 6_650_000.,
        "eccentricity": 0.,
        "inclination_deg": 0.,
        "spacecraft_mass_kg": 100.,
        "initial_pointing_error_deg": 35.,
    }
    # Genuine DE430 first-tick Sun direction fixes the physical orbit phase;
    # it is not guessed or borrowed from the separate f=0 GMAT case.
    probe = run_whole(base, duration=10.)
    sun = vector(probe.trace_rows[0], "orbit.sun_position_m")
    base["initial_true_anomaly_rad"] = math.atan2(
        float(sun[1]), float(sun[0])) % (2. * math.pi)
    four = {
        **base,
        "atmosphere_model": "nrlmsise00",
        "drag_area_m2": 1.,
        "drag_coefficient": 2.2,
        "srp_model": "cannonball",
        "srp_reference_area_m2": 20.,
        "srp_coefficient_reflection": 1.5,
        "earth_gravity_model": "ggm03s_degree4",
        "magnetic_field_model": "igrf14",
    }
    # Only one scalar physical input changes between these two independent
    # real Basilisk runs; physics, initial state, seed, grid and assets do not.
    nominal = run_whole({**four, "rw_command_deadband_nm": 0.}, duration=60.)
    with_deadband = run_whole({**four, "rw_command_deadband_nm": .2}, duration=60.)
    times = [row["time_s"] for row in nominal.trace_rows]
    assert times == [row["time_s"] for row in with_deadband.trace_rows]
    # Preserve the existing native time-grid representation contract: a
    # double-precision 60 s sample may be 60.00000000000001. No change to
    # integration step, sampling grid, physics or the A/B acceptance criteria.
    assert len(times) == 7 and abs(times[0]) < 1e-9 and abs(times[-1] - 60.) < 1e-6

    for name, result in (("nominal", nominal), ("deadband", with_deadband)):
        environment = result.summary["orbital_environment"]
        assert environment["drag_enabled"] is True
        assert environment["space_weather"]["coverage_status"] == "PASS"
        assert environment["gravity_model"] == "ggm03s_degree4"
        assert environment["gravity_degree"] == 4
        assert environment["magnetic_field_model_id"] == "IGRF14_2025_2030"
        assert result.summary["aerodynamic_environment"]["status"] == "PASS"
        assert result.summary["solar_radiation_pressure"]["status"] == "PASS"
        assert result.summary["runtime_message_read_error_count"] == 0
        modules = {m["tag"] for m in result.metadata["runtime_manifest"]["instantiated_modules"]}
        assert {"reaction_wheels", "project_rw_command_fault_manager",
                "msis_atmosphere", "nrlmsise_drag_effector",
                "spice_eclipse_cannonball_srp", "magnetic_field",
                "magnetometer"}.issubset(modules)
        for row in result.trace_rows[1:]:
            density = float(row["environment.atmosphere_density_kg_m3"])
            drag = float(row["orbit.drag_force_norm_n"])
            solar = float(row["orbit.srp_force_norm_n"])
            work = float(row["orbit.drag_force_relative_velocity_power_w"])
            field = float(np.linalg.norm(vector(row, "adcs.magnetometer.tesla")))
            assert all(math.isfinite(x) for x in (density, drag, solar, work, field))
            assert density > 0. and drag > 0. and work < 0.
            assert solar > 0. and field > 1e-8
        assert max(abs(float(row["adcs.rw.command_torque_nm_0"]))
                   for row in result.trace_rows[1:]) > 1e-5, name

    nominal_torque = max(abs(float(row["adcs.control.applied_torque_nm_0"]))
                         for row in nominal.trace_rows[1:])
    blocked_torque = max(abs(float(row["adcs.control.applied_torque_nm_0"]))
                         for row in with_deadband.trace_rows[1:])
    speed_delta = max(abs(float(a["adcs.rw.speed_rad_s_0"]) -
                          float(b["adcs.rw.speed_rad_s_0"]))
                      for a, b in zip(nominal.trace_rows[1:],
                                      with_deadband.trace_rows[1:]))
    assert nominal_torque > 1e-5
    assert blocked_torque < 1e-12
    assert math.isfinite(speed_delta) and speed_delta > 1e-5
    print(
        "DT12_FIVE_EFFECTS_SAME_RUN_AB_MEASURED "
        f"nominal_applied_rw0_torque_max_nm={nominal_torque:.9e} "
        f"deadband_applied_rw0_torque_max_nm={blocked_torque:.9e} "
        f"rw0_speed_delta_max_rad_s={speed_delta:.9e} "
        f"duration_s={times[-1]:.0f} step_s=1 sample_s=10 "
        "environment=nrlmsise00+spice_eclipse_srp+ggm03s_degree4+igrf14 "
        "rw_deadband_off_nm=0 rw_deadband_on_nm=0.2 "
        "weather_source=test-fixture://celestrak-compatible-sample",
        flush=True,
    )
