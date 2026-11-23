"""Actual native environment, orbital geometry and integrator verification."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import math

import numpy as np
import pytest

from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID
from sat_sim.interactive.continuous_initial_config import ContinuousInitialConfiguration


def config(**values):
    return UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=10., step_s=.2, sample_s=1., adcs_only=False,
        values=values, events=())


def position(row):
    return np.array([row[f'orbit.position_m_{axis}'] for axis in 'xyz'])


def test_native_raan_and_perigee_change_actual_orbit_geometry():
    baseline = UnifiedNativeRuntime(config()).run()
    changed = UnifiedNativeRuntime(config(raan_deg=90., arg_perigee_deg=45.)).run()
    a, b = position(baseline.trace_rows[0]), position(changed.trace_rows[0])
    assert abs(np.linalg.norm(a)-np.linalg.norm(b)) < 1e-6
    assert np.linalg.norm(a-b) > 5e6
    # At true anomaly zero and the default 35-degree inclination, a 45-degree
    # argument of perigee moves the initial point north of the equatorial plane.
    assert b[2] > 2e6
    selection = ContinuousInitialConfiguration.model_validate({'orbit':{'raan_deg':90.,'arg_perigee_deg':45.}})
    assert selection.native_values() == {'raan_deg':90.,'arg_perigee_deg':45.}


@pytest.mark.parametrize('values', [
    {'epoch_utc':'2026-01-01T00:00:00'},
    {'epoch_utc':'not-a-date'},
    {'environment_profile':'invented'},
    {'orbit_radius_m':6_500_000.,'eccentricity':.2},
    {'raan_deg':float('nan')},
])
def test_invalid_environment_is_rejected_before_native_execution(values):
    with pytest.raises(ValueError):
        NativeOrbitalEnvironment(values, 10.)


def test_requested_spice_never_falls_back_when_assets_are_missing(tmp_path, monkeypatch):
    from integrated.orbit_environment import assets
    monkeypatch.setattr(assets, 'DEFAULT_SPICE_DIR', tmp_path)
    for key in ('SAT_SIM_ASSET_ROOT', 'SAT_SIM_SPICE_PATH'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(FileNotFoundError, match='SPICE kernels missing'):
        NativeOrbitalEnvironment({'environment_profile':'spice_wmm'}, 10.)


def test_real_spice_and_wmm_change_with_utc_epoch():
    january = UnifiedNativeRuntime(config(environment_profile='spice_wmm', epoch_utc='2026-01-01T00:00:00Z')).run()
    july = UnifiedNativeRuntime(config(environment_profile='spice_wmm', epoch_utc='2026-07-01T00:00:00Z')).run()
    for result in (january, july):
        evidence = result.summary['orbital_environment']
        assert evidence['ephemeris'] == 'JPL_DE430_SPICE'
        assert evidence['magnetic_field'] == 'WMM2025'
        assert evidence['gravity'] == 'earth_J2_plus_sun_moon'
        assert all(row['sha256'] for row in evidence['assets']['spice'])
        binding = evidence["initialized_earth_gravity"]
        assert binding["body_mu_m3_s2"] == binding["harmonics_mu_m3_s2"] == 398600436000000.0
        assert binding["body_equatorial_radius_m"] == binding["harmonics_equatorial_radius_m"] == 6378136.6
        assert binding["harmonics_degree"] == 2
        gravity = evidence['assets']['gravity_j2']
        coefficients = Path(gravity['path']).read_bytes()
        assert gravity['name'] == 'GGM03S-J2-only.txt'
        assert gravity['size_bytes'] == len(coefficients)
        assert gravity['sha256'] == sha256(coefficients).hexdigest()
        first, last = result.trace_rows[0], result.trace_rows[-1]
        sun = np.array([first[f'orbit.sun_position_m_{axis}'] for axis in 'xyz'])
        assert 1.4e11 < np.linalg.norm(sun) < 1.6e11
        assert abs(last['orbit.sun_position_m_x']-first['orbit.sun_position_m_x']) > 1000.
        field = math.sqrt(sum(first[f'adcs.magnetometer.tesla_{axis}']**2 for axis in 'xyz'))
        assert 1e-6 < field < 1e-3
        assert result.summary['runtime_message_read_error_count'] == 0
    assert january.trace_rows[0]['orbit.sun_position_m_y'] < -1e11
    assert july.trace_rows[0]['orbit.sun_position_m_y'] > 1e11


def test_three_integrator_steps_converge_for_native_spice_orbit():
    points = []
    base = config(environment_profile='spice_wmm', controller_k=0., controller_p=0.)
    for step in (10., 5., 2.5):
        result = UnifiedNativeRuntime(replace(base, duration_s=3600., step_s=step, sample_s=20.)).run()
        points.append(position(result.trace_rows[-1]))
    coarse_delta = np.linalg.norm(points[0]-points[1])
    fine_delta = np.linalg.norm(points[1]-points[2])
    observed_order = math.log(coarse_delta/fine_delta, 2.)
    # The coupled environment is sampled on scheduler ticks; its observed
    # convergence must be measured rather than inherited from the RK4 name.
    # Require better than first order and a declared 2 m refinement budget.
    assert 1.5 < observed_order < 4.5, (coarse_delta, fine_delta, observed_order)
    assert fine_delta < 2., (coarse_delta, fine_delta)


def test_point_mass_native_integrator_retains_fourth_order_convergence():
    points = []
    base = config(controller_k=0., controller_p=0.)
    for step in (20., 10., 5.):
        result = UnifiedNativeRuntime(replace(base, duration_s=3600., step_s=step, sample_s=20.)).run()
        points.append(position(result.trace_rows[-1]))
    coarse_delta = np.linalg.norm(points[0]-points[1])
    fine_delta = np.linalg.norm(points[1]-points[2])
    assert 3.5 < math.log(coarse_delta/fine_delta, 2.) < 4.5
    assert fine_delta < 1.

def test_unified_runtime_executes_native_nrlmsise00_and_drag(monkeypatch, tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "celestrak_space_weather_2026-03-04.csv"
    local_asset = tmp_path / "SW-Last5Years.csv"
    shutil.copyfile(fixture, local_asset)
    setup_script = Path(__file__).parents[1] / "scripts" / "fetch_space_weather_asset.py"
    subprocess.run(
        [
            sys.executable, str(setup_script), "--offline",
            "--output", str(local_asset),
            "--source-url", "test-fixture://celestrak-compatible-sample",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.setenv("SAT_SIM_SPACE_WEATHER_PATH", str(local_asset))
    base = config(
        environment_profile="spice_wmm",
        epoch_utc="2026-03-04T21:48:00Z",
        orbit_radius_m=6_650_000.0,
        spacecraft_mass_kg=10.0,
        controller_k=0.0,
        controller_p=0.0,
    )
    run_config = replace(base, duration_s=600.0, step_s=1.0, sample_s=10.0)
    baseline = UnifiedNativeRuntime(run_config).run()
    result = UnifiedNativeRuntime(replace(
        run_config,
        values={
            **run_config.values,
            "atmosphere_model": "nrlmsise00",
            "drag_area_m2": 10.0,
            "drag_coefficient": 2.2,
        },
    )).run()

    environment = result.summary["orbital_environment"]
    assert environment["atmosphere_model"] == "NRLMSISE-00"
    assert environment["drag_enabled"] is True
    assert environment["space_weather"]["coverage_status"] == "PASS"
    assert environment["space_weather"]["sha256"]
    assert environment["space_weather"]["provenance_manifest_status"] == "PASS"
    assert environment["space_weather"]["source_url"] == "test-fixture://celestrak-compatible-sample"
    assert environment["space_weather"]["retrieval_time_unknown"] is True
    aero = result.summary["aerodynamic_environment"]
    assert aero["status"] == "PASS"
    assert aero["wind_model_scope"] == "earth_co_rotation_zero_thermospheric_wind"
    assert aero["configured_dynamic_effector_area_m2"] == pytest.approx(10.0)
    assert aero["configured_dynamic_effector_cd"] == pytest.approx(2.2)

    rows = list(result.trace_rows)
    assert rows
    assert all(math.isfinite(row["environment.atmosphere_density_kg_m3"]) for row in rows)
    assert all(row["environment.atmosphere_density_kg_m3"] > 0.0 for row in rows)
    assert all(math.isfinite(row["orbit.drag_force_norm_n"]) for row in rows)
    assert all(math.isfinite(row["orbit.drag_force_relative_velocity_power_w"]) for row in rows)
    assert all(row["orbit.drag_force_relative_velocity_power_w"] <= 1.0e-12 for row in rows)
    assert min(row["orbit.drag_force_relative_velocity_power_w"] for row in rows) < 0.0
    assert all(math.isfinite(row["orbit.drag_relative_speed_m_s"]) for row in rows)
    assert max(row["orbit.drag_relative_speed_m_s"] for row in rows) > 1000.0
    assert max(row["orbit.drag_force_norm_n"] for row in rows) > 0.0, (
        aero,
        max(row["environment.atmosphere_density_kg_m3"] for row in rows),
        max(row["orbit.drag_relative_speed_m_s"] for row in rows),
    )
    assert rows[-1]["environment.space_weather.ap_24_0"] == pytest.approx(6.0)
    assert rows[-1]["environment.space_weather.f107_1944_0"] == pytest.approx(138.5)

    def semi_major_axis(row):
        r = position(row)
        v = np.array([row[f"orbit.velocity_m_s_{axis}"] for axis in "xyz"])
        specific_energy = float(v @ v) / 2.0 - 3.986004418e14 / float(np.linalg.norm(r))
        return -3.986004418e14 / (2.0 * specific_energy)

    baseline_a = semi_major_axis(baseline.trace_rows[-1])
    drag_a = semi_major_axis(rows[-1])
    # Report actual CI measurements, not only a boolean assertion.
    # Keep the physical test configuration and acceptance criteria unchanged.
    print(
        "DT1_NATIVE_DRAG_AB_MEASURED "
        f"sample_count={len(rows)} "
        f"density_min_kg_m3={min(row['environment.atmosphere_density_kg_m3'] for row in rows):.9e} "
        f"density_max_kg_m3={max(row['environment.atmosphere_density_kg_m3'] for row in rows):.9e} "
        f"drag_force_max_n={max(row['orbit.drag_force_norm_n'] for row in rows):.9e} "
        f"drag_power_min_w={min(row['orbit.drag_force_relative_velocity_power_w'] for row in rows):.9e} "
        f"relative_speed_max_m_s={max(row['orbit.drag_relative_speed_m_s'] for row in rows):.9f} "
        f"semi_major_axis_no_drag_m={baseline_a:.9f} "
        f"semi_major_axis_with_drag_m={drag_a:.9f} "
        f"delta_a_m={drag_a - baseline_a:.9f} "
        "fixture_area_m2=10.0 fixture_cd=2.2 fixture_mass_kg=10.0 fixture_duration_s=600.0",
        flush=True,
    )
    assert drag_a < baseline_a, (baseline_a, drag_a)
    assert baseline.summary["orbital_environment"]["drag_enabled"] is False

def test_nrlmsise00_rejects_simplified_environment_without_spice():
    with pytest.raises(ValueError, match="NRLMSISE00_REQUIRES_SPICE_WMM_ENVIRONMENT"):
        NativeOrbitalEnvironment({"atmosphere_model": "nrlmsise00"}, 10.0)
