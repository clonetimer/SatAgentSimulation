"""Actual native environment, orbital geometry and integrator verification."""
from dataclasses import replace
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
