"""Native thermal-heater electrical output contract.

The integrated whole-spacecraft runtime must use each ThermalHeater's
actual electricalPowerOutMsg for EPS accounting. This focused test verifies
that message's electrical-vs-deposited-heat semantics and PDU fail-closed gate.
"""

from __future__ import annotations

import pytest


def _run(*, pdu_enabled: bool, power_scale: float):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from subsystems.thermal.network_config import HeaterParams, ThermalNodeParams
    from subsystems.thermal.thermal_network import ThermalHeater, ThermalNode

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("NativeThermalHeaterPowerProcess")
    task = "NativeThermalHeaterPowerTask"
    process.addTask(sim.CreateNewTask(task, macros.sec2nano(0.2)))

    node = ThermalNode(
        "battery",
        ThermalNodeParams(
            mass_kg=1.0,
            specific_heat_j_per_kg_k=900.0,
            surface_area_m2=0.1,
            emissivity=0.8,
            absorptivity=0.3,
            initial_temp_k=270.0,
            min_temp_k=220.0,
            max_temp_k=360.0,
        ),
    )
    heater = ThermalHeater(
        "battery",
        HeaterParams(power_w=12.0, setpoint_k=286.0, hysteresis_k=5.0, efficiency=0.95),
    )
    heater.set_node(node)
    heater.power_scale = power_scale
    status = messaging.DeviceStatusMsgPayload()
    status.deviceStatus = int(pdu_enabled)
    status_msg = messaging.DeviceStatusMsg().write(status)
    heater.subscribe_enable(status_msg)

    sim.AddModelToTask(task, node, None, 20)
    sim.AddModelToTask(task, heater, None, 10)
    electrical = heater.electricalPowerOutMsg.recorder(macros.sec2nano(0.2))
    deposited = heater.powerOutMsg.recorder(macros.sec2nano(0.2))
    sim.AddModelToTask(task, electrical, None, 1)
    sim.AddModelToTask(task, deposited, None, 1)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.6))
    sim.ExecuteSimulation()
    return heater, electrical, deposited


def test_native_thermal_heater_publishes_electrical_consumption_and_deposited_heat_separately() -> None:
    heater, electrical, deposited = _run(pdu_enabled=True, power_scale=0.5)

    electrical_w = [max(0.0, -float(value)) for value in electrical.netPower]
    deposited_w = [max(0.0, -float(value)) for value in deposited.netPower]
    assert electrical_w
    assert any(value == pytest.approx(6.0, abs=1e-9) for value in electrical_w)
    assert any(value == pytest.approx(5.7, abs=1e-9) for value in deposited_w)
    for electrical_value, heat_value in zip(electrical_w, deposited_w):
        assert heat_value == pytest.approx(electrical_value * 0.95, abs=1e-9)
    assert heater.last_electrical_power_w == pytest.approx(6.0, abs=1e-9)


def test_native_thermal_heater_fails_closed_when_eps_pdu_disables_heater() -> None:
    _, electrical, deposited = _run(pdu_enabled=False, power_scale=1.0)
    assert electrical.netPower
    assert deposited.netPower
    assert all(float(value) == pytest.approx(0.0, abs=1e-12) for value in electrical.netPower)
    assert all(float(value) == pytest.approx(0.0, abs=1e-12) for value in deposited.netPower)
