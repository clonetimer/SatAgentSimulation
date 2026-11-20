"""Native DT-1: simultaneous SPICE/Eclipse SRP and NRLMSISE-00 drag.

The BIRDS-4 reference GLB does not define force geometry. Deliberately
exaggerated fixture dimensions below are not vehicle defaults or calibration.
The comparisons observe actual same-graph Basilisk dynamics and keep their
force frames distinct: SRP is N-inertial; drag is spacecraft body B.
"""
from dataclasses import replace
import math
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
)


def _run(values, duration_s=300.):
    return UnifiedNativeRuntime(UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=duration_s, step_s=1.0, sample_s=10.0,
        adcs_only=False, values=values, events=(),
    )).run()


def _position(row):
    return np.array([row[f"orbit.position_m_{axis}"] for axis in "xyz"])


def _semimajor(row):
    mu = 3.986004418e14
    r = _position(row)
    v = np.array([row[f"orbit.velocity_m_s_{axis}"] for axis in "xyz"])
    energy = float(v @ v) / 2. - mu / float(np.linalg.norm(r))
    return -mu / (2. * energy)


def _stage_weather(tmp_path, monkeypatch):
    source = Path(__file__).parent / "fixtures" / "celestrak_space_weather_2026-03-04.csv"
    local = tmp_path / "SW-Last5Years.csv"
    shutil.copyfile(source, local)
    script = Path(__file__).parents[1] / "scripts" / "fetch_space_weather_asset.py"
    subprocess.run([
        sys.executable, str(script), "--offline",
        "--output", str(local), "--source-url",
        "test-fixture://celestrak-compatible-sample",
    ], check=True, capture_output=True, text=True)
    monkeypatch.setenv("SAT_SIM_SPACE_WEATHER_PATH", str(local))


def test_real_drag_and_srp_act_together_on_one_native_spacecraft(monkeypatch, tmp_path):
    _stage_weather(tmp_path, monkeypatch)
    base = {
        "environment_profile": "spice_wmm",
        "epoch_utc": "2026-03-04T21:48:00Z",
        "orbit_radius_m": 6_650_000.0,
        "eccentricity": 0.0,
        "inclination_deg": 0.0,
        "spacecraft_mass_kg": 100.0,
        "controller_k": 0.0,
        "controller_p": 0.0,
    }
    # Obtain the actual Sun vector from the native SPICE interface. Do not
    # fabricate or estimate a dated Sun ephemeris from nominal solar time.
    probe = _run(base, duration_s=10.)
    sun = np.array([
        probe.trace_rows[0][f"orbit.sun_position_m_{axis}"] for axis in "xyz"
    ])
    anomaly = math.atan2(float(sun[1]), float(sun[0])) % (2. * math.pi)
    common = {**base, "initial_true_anomaly_rad": anomaly}

    drag = {
        "atmosphere_model": "nrlmsise00",
        "drag_area_m2": 1.0, "drag_coefficient": 2.2,
    }
    srp = {
        "srp_model": "cannonball",
        "srp_reference_area_m2": 20.0,
        "srp_coefficient_reflection": 1.5,
    }
    baseline = _run(common)
    drag_only = _run({**common, **drag})
    srp_only = _run({**common, **srp})
    both = _run({**common, **drag, **srp})

    assert "aerodynamic_environment" not in baseline.summary
    assert "solar_radiation_pressure" not in baseline.summary
    assert "aerodynamic_environment" in both.summary
    assert "solar_radiation_pressure" in both.summary
    assert both.summary["aerodynamic_environment"]["status"] == "PASS"
    assert both.summary["solar_radiation_pressure"]["status"] == "PASS"
    assert both.summary["orbital_environment"]["drag_enabled"] is True
    assert both.summary["orbital_environment"]["space_weather"]["coverage_status"] == "PASS"
    assert both.summary["orbital_environment"]["space_weather"]["source_url"] == (
        "test-fixture://celestrak-compatible-sample"
    )
    assert both.summary["solar_radiation_pressure"]["sun_source"] == "JPL_DE430_SPICE"

    manifest = both.metadata["runtime_manifest"]
    assert manifest["process"] == "UnifiedNativeProcess"
    assert manifest["task"] == "UnifiedNativeTask"
    modules = {item["tag"] for item in manifest["instantiated_modules"]}
    assert "nrlmsise_drag_effector" in modules
    assert "spice_eclipse_cannonball_srp" in modules
    assert "space_weather_data" in modules
    assert "msis_atmosphere" in modules
    assert any("nrlmsise_drag_effector" in edge for edge in manifest["connections"])
    assert any("spice_eclipse_cannonball_srp" in edge for edge in manifest["connections"])

    rows = both.trace_rows
    assert len(rows) == 31
    density = [r["environment.atmosphere_density_kg_m3"] for r in rows]
    dforce = [r["orbit.drag_force_norm_n"] for r in rows]
    sforce = [r["orbit.srp_force_norm_n"] for r in rows]
    eclipse = [r["orbit.eclipse_factor"] for r in rows]
    power = [r["orbit.drag_force_relative_velocity_power_w"] for r in rows]
    assert min(density) > 0. and all(math.isfinite(x) for x in density)
    assert min(dforce) > 0. and all(math.isfinite(x) for x in dforce)
    assert min(sforce) > 0. and all(math.isfinite(x) for x in sforce)
    assert min(eclipse) > 0.99
    assert max(power) <= 1e-12 and min(power) < 0.
    assert all(math.isfinite(x) for x in power)
    for row in rows:
        rel_sun_n = np.array([
            row[f"orbit.sun_position_m_{axis}"] - row[f"orbit.position_m_{axis}"]
            for axis in "xyz"
        ])
        srp_force_n = np.array([row[f"orbit.srp_force_n_{axis}"] for axis in "xyz"])
        assert float(rel_sun_n @ srp_force_n) < 0.

    p_base = _position(baseline.trace_rows[-1])
    p_drag = _position(drag_only.trace_rows[-1])
    p_srp = _position(srp_only.trace_rows[-1])
    p_both = _position(both.trace_rows[-1])
    delta_drag_given_srp = float(np.linalg.norm(p_both - p_srp))
    delta_srp_given_drag = float(np.linalg.norm(p_both - p_drag))
    delta_drag_alone = float(np.linalg.norm(p_drag - p_base))
    delta_srp_alone = float(np.linalg.norm(p_srp - p_base))
    # Each force must still affect orbital motion when the other is enabled.
    # This is a nonlinear spacecraft integration, NOT an assumed linear sum.
    assert delta_drag_given_srp > 0.001
    assert delta_srp_given_drag > 0.001
    assert delta_drag_alone > 0.001
    assert delta_srp_alone > 0.001
    assert _semimajor(both.trace_rows[-1]) < _semimajor(srp_only.trace_rows[-1])
    print(
        "DT1_NATIVE_DRAG_SRP_JOINT_MEASURED "
        f"mode=sunlit sample_count={len(rows)} "
        f"density_min_kg_m3={min(density):.9e} "
        f"drag_force_max_n={max(dforce):.9e} "
        f"srp_force_max_n={max(sforce):.9e} "
        f"eclipse_min={min(eclipse):.9e} "
        f"drag_power_min_w={min(power):.9e} "
        f"delta_drag_given_srp_m={delta_drag_given_srp:.9e} "
        f"delta_srp_given_drag_m={delta_srp_given_drag:.9e} "
        f"delta_drag_alone_m={delta_drag_alone:.9e} "
        f"delta_srp_alone_m={delta_srp_alone:.9e} "
        f"semimajor_srp_only_m={_semimajor(srp_only.trace_rows[-1]):.9f} "
        f"semimajor_both_m={_semimajor(both.trace_rows[-1]):.9f} "
        "fixture_mass_kg=100 fixture_drag_area_m2=1.0 "
        "fixture_cd=2.2 fixture_srp_area_m2=20.0 fixture_cr=1.5 "
        "fixture_duration_s=300 fixture_step_s=1",
        flush=True,
    )

    # Same drag atmosphere and initial orbit, but eclipse suppresses SRP.
    dark = {**base, "initial_true_anomaly_rad": (anomaly + math.pi) % (2. * math.pi)}
    dark_drag = _run({**dark, **drag})
    dark_both = _run({**dark, **drag, **srp})
    dark_rows = dark_both.trace_rows
    assert max(r["orbit.eclipse_factor"] for r in dark_rows) < 1.e-3
    assert max(r["orbit.srp_force_norm_n"] for r in dark_rows) < 1.e-10
    assert max(r["orbit.drag_force_norm_n"] for r in dark_rows) > 0.
    dark_delta = float(np.linalg.norm(
        _position(dark_drag.trace_rows[-1]) - _position(dark_both.trace_rows[-1])
    ))
    assert dark_delta < 1.e-4
    print(
        "DT1_NATIVE_DRAG_SRP_JOINT_MEASURED "
        f"mode=umbra sample_count={len(dark_rows)} "
        f"eclipse_max={max(r['orbit.eclipse_factor'] for r in dark_rows):.9e} "
        f"srp_force_max_n={max(r['orbit.srp_force_norm_n'] for r in dark_rows):.9e} "
        f"drag_force_max_n={max(r['orbit.drag_force_norm_n'] for r in dark_rows):.9e} "
        f"drag_only_vs_joint_position_m={dark_delta:.9e}",
        flush=True,
    )
