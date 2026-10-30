"""Actual native EPS/thermal accounting, not a calibrated hardware claim."""
import pytest
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID


def run(**values):
    cfg = UnifiedRuntimeConfig(capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=10., step_s=.2, sample_s=1., adcs_only=False,
        values={"environment_profile":"spice_wmm", "solar_efficiency":0.,
                "bus_power_w":0., "adcs_power_w":0., "payload_power_w":0.,
                "downlink_power_w":0., "heater_power_w":0.,
                "controller_k":0., "controller_p":0.,
                "battery_heater_setpoint_k":310., **values}, events=())
    return UnifiedNativeRuntime(cfg).run()


def test_thermostat_debits_full_electrical_power_and_deposits_efficient_heat():
    on, off = run(battery_heater_power_w=12.), run(battery_heater_power_w=0.)
    for active, inactive in zip(on.trace_rows[2:], off.trace_rows[2:]):
        assert active["eps.loads.battery_thermostat_power_w"] == pytest.approx(12.)
        assert inactive["eps.loads.battery_thermostat_power_w"] == 0.
        assert active["eps.net_power_w"] - inactive["eps.net_power_w"] == pytest.approx(-12.)
        assert active["thermal.battery_heat_input_w"] - inactive["thermal.battery_heat_input_w"] == pytest.approx(11.4)
    a, b = on.trace_rows, off.trace_rows
    consumption_delta = ((b[-1]["eps.battery_energy_wh"]-b[0]["eps.battery_energy_wh"])
                       -(a[-1]["eps.battery_energy_wh"]-a[0]["eps.battery_energy_wh"])) * 3600.
    assert consumption_delta == pytest.approx(12.*10., abs=1e-6)


def test_housekeeping_heat_is_funded_by_actual_bus_and_payload_has_no_constant_heat():
    on, off = run(bus_power_w=18., battery_heater_power_w=0.), run(battery_heater_power_w=0.)
    for active, inactive in zip(on.trace_rows[2:], off.trace_rows[2:]):
        for node, watts in (("battery",2.),("electronics",8.),("adcs",3.),("solar_panel",5.)):
            assert active[f"thermal.{node}_heat_input_w"] - inactive[f"thermal.{node}_heat_input_w"] == pytest.approx(watts)
        assert inactive["thermal.payload_heat_input_w"] == pytest.approx(.95*inactive["eps.loads.payload_power_w"])


def test_heater_fault_scale_reports_actual_heat_and_electrical_consumption():
    from subsystems.thermal.thermal_network import ThermalHeater
    from subsystems.thermal.network_config import build_nominal_thermal_network_config
    heater = ThermalHeater("battery", build_nominal_thermal_network_config().heaters["battery"])
    heater.forced_state = True
    heater.power_scale = .25
    heater.UpdateState(0)
    assert heater.last_electrical_power_w == pytest.approx(3.)
    assert heater.last_heat_power_w == pytest.approx(2.85)
    assert heater.electricalPowerOutMsg.read().netPower == pytest.approx(-3.)
    assert heater.powerOutMsg.read().netPower == pytest.approx(-2.85)
