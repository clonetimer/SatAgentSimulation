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
