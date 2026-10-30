"""Thermal geometry verification and actual native-runtime coupling checks.

These verify the declared uniform-Earth engineering model, not flight truth.
"""
from dataclasses import replace
import math

import numpy as np
import pytest
from Basilisk.architecture import messaging
from Basilisk.simulation import albedo
from Basilisk.utilities import SimulationBaseClass, macros

from sat_sim.thermal_environment import surface_irradiance, ASTRONOMICAL_UNIT_M
from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
)


def _official_albedo(position, sun_position, normal, order=160):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess('albedoVerificationProcess')
    process.addTask(sim.CreateNewTask('albedoVerificationTask', macros.sec2nano(1.)))
    state = messaging.SCStatesMsgPayload()
    state.r_BN_N = position
    state_msg = messaging.SCStatesMsg().write(state)
    sun = messaging.SpicePlanetStateMsgPayload()
    sun.PositionVector = sun_position
    sun_msg = messaging.SpicePlanetStateMsg().write(sun)
    earth = messaging.SpicePlanetStateMsgPayload()
    earth.PlanetName = 'earth'
    earth.J20002Pfix = np.eye(3).tolist()
    earth_msg = messaging.SpicePlanetStateMsg().write(earth)
    model = albedo.Albedo()
    model.ModelTag = 'officialAlbedoVerification'
    model.spacecraftStateInMsg.subscribeTo(state_msg)
    model.sunPositionInMsg.subscribeTo(sun_msg)
    model.addPlanetandAlbedoAverageModel(earth_msg, .30, order, order*2)
    model.addInstrumentConfig(math.pi/2., normal, [0., 0., 0.])
    sim.AddModelToTask('albedoVerificationTask', model)
    recorder = model.albOutMsgs[0].recorder()
    sim.AddModelToTask('albedoVerificationTask', recorder)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(0)
    sim.ExecuteSimulation()
    return float(recorder.AfluxAtInstrument[-1])


@pytest.mark.parametrize('phase', [0., math.pi/3., math.pi/2.])
def test_uniform_lambertian_flux_agrees_with_independent_official_basilisk(phase):
    position = np.array([7_000_000., 0., 0.])
    sun = ASTRONOMICAL_UNIT_M*np.array([math.cos(phase), math.sin(phase), 0.])
    normal = [-1., 0., 0.]
    ours = float(surface_irradiance(position, sun, [normal], radial_order=64,
                                   azimuth_order=256).albedo_w_m2[0])
    official = _official_albedo(position.tolist(), sun.tolist(), normal)
    # Independent latitude/longitude vs sky-disk quadrature and Earth constants.
    # 1% is an implementation agreement threshold, not a physical error bound.
    assert ours == pytest.approx(official, rel=.01, abs=.02), (ours, official)


def _config(**values):
    return UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=30., step_s=.2, sample_s=1., adcs_only=False,
        values={'environment_profile':'spice_wmm', **values}, events=())


def test_actual_native_thermal_boundary_is_recorded_and_has_no_double_solar_source():
    result = UnifiedNativeRuntime(_config()).run()
    boundary = result.summary['thermal_environment']
    assert boundary['model'] == 'finite_spherical_earth_lambertian_v1'
    assert result.summary['runtime_message_read_error_count'] == 0
    for name in ('battery', 'payload', 'comm', 'solar_panel'):
        rows = result.trace_rows
        assert all(row[f'thermal.{name}_earth_ir_heat_w'] > 0 for row in rows)
        assert all(row[f'thermal.{name}_albedo_heat_w'] >= 0 for row in rows)
        assert all(row[f'thermal.{name}_direct_solar_heat_w'] >= -1e-6 for row in rows)
    # The exact same upstream panel message must fund electrical and thermal ledgers.
    assert any(row['thermal.solar_panel_electrical_extracted_w'] > 0 for row in rows)
    assert all(math.isfinite(row['thermal.payload_temp_k']) for row in rows)


def test_native_thermal_boundary_segmented_execution_matches_offline():
    cfg = _config()
    offline = UnifiedNativeRuntime(cfg).run()
    segmented = UnifiedNativeRuntime(replace(cfg, execution_stops_s=(5., 10., 20., 30.))).run()
    for name in ('battery', 'payload', 'comm', 'solar_panel'):
        assert [r[f'thermal.{name}_temp_k'] for r in offline.trace_rows] == pytest.approx(
            [r[f'thermal.{name}_temp_k'] for r in segmented.trace_rows], abs=1e-10)


@pytest.mark.parametrize('values', [
    {'thermal_earth_ir_w_m2': float('nan')}, {'thermal_earth_ir_w_m2': -1.},
    {'thermal_earth_albedo': 1.01}, {'thermal_panel_front_absorptivity': .1},
    {'thermal_panel_back_absorptivity': -1.},
])
def test_invalid_environment_or_impossible_optical_efficiency_is_rejected(values):
    with pytest.raises(ValueError, match='NATIVE_THERMAL_ENV_'):
        UnifiedNativeRuntime(_config(**values)).run()


def test_native_earth_flux_inputs_change_real_heat_and_temperature():
    baseline = UnifiedNativeRuntime(_config()).run()
    zero_earth = UnifiedNativeRuntime(_config(thermal_earth_ir_w_m2=0., thermal_earth_albedo=0.)).run()
    assert zero_earth.summary['thermal_environment']['earth_ir_exitance_w_m2'] == 0.
    assert all(row['thermal.payload_earth_ir_heat_w'] == 0. for row in zero_earth.trace_rows)
    assert all(row['thermal.payload_albedo_heat_w'] == 0. for row in zero_earth.trace_rows)
    assert baseline.trace_rows[-1]['thermal.payload_temp_k'] > zero_earth.trace_rows[-1]['thermal.payload_temp_k']


@pytest.mark.parametrize('values', [
    {'thermal_earth_ir_w_m2': float('nan')}, {'thermal_earth_ir_w_m2': -1.},
    {'thermal_earth_albedo': 1.01}, {'thermal_panel_front_absorptivity': .1},
    {'thermal_panel_back_absorptivity': -1.},
])
def test_invalid_environment_or_impossible_optical_efficiency_is_rejected(values):
    with pytest.raises(ValueError, match='NATIVE_THERMAL_ENV_'):
        UnifiedNativeRuntime(_config(**values)).run()


def test_native_earth_flux_inputs_change_real_heat_and_temperature():
    baseline = UnifiedNativeRuntime(_config()).run()
    zero_earth = UnifiedNativeRuntime(_config(thermal_earth_ir_w_m2=0., thermal_earth_albedo=0.)).run()
    assert zero_earth.summary['thermal_environment']['earth_ir_exitance_w_m2'] == 0.
    assert all(row['thermal.payload_earth_ir_heat_w'] == 0. for row in zero_earth.trace_rows)
    assert all(row['thermal.payload_albedo_heat_w'] == 0. for row in zero_earth.trace_rows)
    assert baseline.trace_rows[-1]['thermal.payload_temp_k'] > zero_earth.trace_rows[-1]['thermal.payload_temp_k']
