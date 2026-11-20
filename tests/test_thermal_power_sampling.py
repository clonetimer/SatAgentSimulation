"""Native electrical pulses must reach the receiving thermal state at any cadence."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("Basilisk")
from Basilisk.architecture import messaging, sysModel
from Basilisk.simulation import simplePowerSink
from Basilisk.utilities import SimulationBaseClass, macros

from subsystems.thermal.network_config import HeaterParams, ThermalNetworkConfig, ThermalNodeParams
from subsystems.thermal.thermal_network import ThermalNetworkSysModel, ThermalPowerBridge


class PulseStatus(sysModel.SysModel):
    def __init__(self, start_s, end_s):
        super().__init__()
        self.ModelTag = "pulseStatus"
        self.start_ns, self.end_ns = macros.sec2nano(start_s), macros.sec2nano(end_s)
        self.statusOutMsg = messaging.DeviceStatusMsg()

    def Reset(self, now):
        self.UpdateState(now)

    def UpdateState(self, now):
        payload = messaging.DeviceStatusMsgPayload()
        payload.deviceStatus = int(self.start_ns <= now < self.end_ns)
        self.statusOutMsg.write(payload, now, self.moduleID)


def run_pulse(thermal_step, *, duration=20.0, start=1.0, end=1.8, disconnected_receiver=False, duplicate_destination=False):
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("pulseProcess")
    process.addTask(sim.CreateNewTask("dynamics", macros.sec2nano(0.2)), 40)
    process.addTask(sim.CreateNewTask("thermal", macros.sec2nano(thermal_step)), 30)
    status = PulseStatus(start, end)
    sink = simplePowerSink.SimplePowerSink()
    sink.ModelTag = "actualPulsePower"
    sink.nodePowerOut = -20.0
    sink.nodeStatusInMsg.subscribeTo(status.statusOutMsg)
    sim.AddModelToTask("dynamics", status, None, 5)
    sim.AddModelToTask("dynamics", sink)
    config = ThermalNetworkConfig(
        nodes={"receiver": ThermalNodeParams(mass_kg=1.0, specific_heat_j_per_kg_k=100.0,
            surface_area_m2=0.0, emissivity=0.0, initial_temp_k=290.0)},
        duration_s=duration, step_s=thermal_step,
    )
    inputs = {"receiver": [(sink.nodePowerOutMsg, 0.85, "propulsion.actual_activity")]}
    if duplicate_destination:
        config.nodes["duplicate"] = config.nodes["receiver"]
        inputs["duplicate"] = [(sink.nodePowerOutMsg, 0.85, "same.actual.electrical.source")]
    network = ThermalNetworkSysModel(config, external_power_inputs_by_node=inputs)
    network.build(sim, "thermal")
    bridge = network.power_bridges["receiver"]
    bridge.enable_dynamics_sampling(sim, "dynamics", input_count=1)
    if duplicate_destination:
        network.power_bridges["duplicate"].enable_dynamics_sampling(sim, "dynamics", input_count=1)
    if disconnected_receiver:
        unrelated = messaging.PowerNodeUsageMsg().write(messaging.PowerNodeUsageMsgPayload())
        network.nodes["receiver"].heatInMsg.subscribeTo(unrelated)
    sim.AddModelToTask("thermal", network)
    recorder = sink.nodePowerOutMsg.recorder()
    sim.AddModelToTask("dynamics", recorder, None, -10)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()
    return network, bridge, recorder


@pytest.mark.parametrize("thermal_step", [0.2, 0.7, 10.0])
@pytest.mark.parametrize("start,end", [(1.0, 1.8), (9.6, 10.4)])
def test_short_native_pulse_deposits_same_heat_at_fine_coarse_and_nonharmonic_ticks(thermal_step, start, end):
    network, bridge, recorder = run_pulse(thermal_step, start=start, end=end)
    electrical_j = sum(abs(float(recorder.netPower[i - 1])) *
        float(recorder.times()[i] - recorder.times()[i - 1]) * macros.NANO2SEC
        for i in range(1, len(recorder.times())))
    assert electrical_j == pytest.approx(16.0, abs=1e-10)
    assert bridge.cumulative_electrical_energy_j == pytest.approx(electrical_j, abs=1e-10)
    assert bridge.cumulative_heat_energy_j == pytest.approx(13.6, abs=1e-10)
    node = network.nodes["receiver"]
    assert (node.temp_k - 290.0) * 100.0 == pytest.approx(13.6, abs=1e-8)
    assert node.cumulative_external_heat_energy_j == pytest.approx(13.6, abs=1e-10)
    assert bridge.pending_heat_energy_j == pytest.approx(0.0, abs=1e-10)
    assert bridge.input_error_count == 0


def test_partial_thermal_window_reports_pending_energy_without_fabricating_deposition():
    network, bridge, _ = run_pulse(10.0, duration=15.0, start=11.0, end=11.8)
    assert network.nodes["receiver"].temp_k == pytest.approx(290.0)
    assert bridge.cumulative_heat_energy_j == pytest.approx(0.0)
    assert bridge.pending_heat_energy_j == pytest.approx(13.6)
    assert bridge.pending_source_electrical_energy_j["propulsion.actual_activity"] == pytest.approx(16.0)
    assert bridge.pending_source_heat_energy_j["propulsion.actual_activity"] == pytest.approx(13.6)
    from integration.thermal_resource_evidence import build_thermal_resource_evidence
    evidence = build_thermal_resource_evidence(network)
    assert evidence["status"] == "PASS"
    assert evidence["nodes"][0]["received_heat_energy_j"] == pytest.approx(0.0)
    assert evidence["pending_heat_energy_j"] == pytest.approx(13.6)


def test_measured_closure_rejects_a_real_disconnected_receiving_node():
    from integration.thermal_resource_evidence import build_thermal_resource_evidence
    network, _, _ = run_pulse(10.0, disconnected_receiver=True)
    evidence = build_thermal_resource_evidence(network)
    assert evidence["status"] == "FAIL"
    assert evidence["nodes"][0]["transfer_residual_j"] == pytest.approx(13.6)
    assert any("receiving-node" in error for error in evidence["nodes"][0]["errors"])


def test_global_source_budget_rejects_the_same_native_power_deposited_twice():
    from integration.thermal_resource_evidence import build_thermal_resource_evidence
    network, _, _ = run_pulse(10.0, duplicate_destination=True)
    assert all((node.temp_k - 290.0) * 100.0 == pytest.approx(13.6, abs=1e-8) for node in network.nodes.values())
    evidence = build_thermal_resource_evidence(network)
    assert evidence["status"] == "FAIL"
    assert evidence["routing_errors"]


def test_sampler_latches_transient_invalid_native_power_before_later_recovery():
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("invalidPowerProcess")
    process.addTask(sim.CreateNewTask("fast", macros.sec2nano(0.2)), 40)
    process.addTask(sim.CreateNewTask("slow", macros.sec2nano(2.0)), 30)

    class Source(sysModel.SysModel):
        def __init__(self):
            super().__init__()
            self.powerOutMsg = messaging.PowerNodeUsageMsg()

        def UpdateState(self, now):
            payload = messaging.PowerNodeUsageMsgPayload()
            payload.netPower = math.nan if now == macros.sec2nano(0.4) else -10.0
            self.powerOutMsg.write(payload, now, self.moduleID)

    source = Source()
    sim.AddModelToTask("fast", source)
    bridge = ThermalPowerBridge("receiver")
    bridge.add_power_input(source.powerOutMsg, 1.0, source_label="invalid.source")
    bridge.enable_dynamics_sampling(sim, "fast")
    sim.AddModelToTask("slow", bridge)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(4.0))
    sim.ExecuteSimulation()
    assert bridge.input_error_count > 0
    assert bridge.input_read_status == "ERROR"
    assert any("finite" in error for error in bridge.read_errors)
    assert math.isfinite(bridge.cumulative_heat_energy_j)
    assert len(bridge.read_errors) <= 10


def test_repeated_source_labels_sum_both_physical_sources():
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("repeatedLabelProcess")
    process.addTask(sim.CreateNewTask("fast", macros.sec2nano(0.2)), 40)
    process.addTask(sim.CreateNewTask("slow", macros.sec2nano(2.0)), 30)
    bridge = ThermalPowerBridge("receiver")
    sources = []
    for watts in (7.0, 13.0):
        source = simplePowerSink.SimplePowerSink()
        source.nodePowerOut = -watts
        sim.AddModelToTask("fast", source)
        bridge.add_power_input(source.nodePowerOutMsg, 0.85, source_label="propulsion.actual_activity")
        sources.append(source)
    bridge.enable_dynamics_sampling(sim, "fast")
    sim.AddModelToTask("slow", bridge)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(4.0))
    sim.ExecuteSimulation()
    assert bridge.last_source_power_w["propulsion.actual_activity"] == pytest.approx(20.0)
    assert bridge.cumulative_source_electrical_energy_j["propulsion.actual_activity"] == pytest.approx(80.0)
    assert bridge.cumulative_source_heat_energy_j["propulsion.actual_activity"] == pytest.approx(68.0)


@pytest.mark.parametrize("duration,expected_electrical_j", [(10.0, 0.0), (20.0, 120.0)])
def test_network_heater_transition_retains_completed_interval_electrical_and_deposited_energy(duration, expected_electrical_j):
    from Basilisk.simulation import simpleBattery
    from integration.thermal_resource_evidence import build_thermal_resource_evidence
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("heaterProcess")
    process.addTask(sim.CreateNewTask("dynamics", macros.sec2nano(0.2)), 40)
    process.addTask(sim.CreateNewTask("thermal", macros.sec2nano(10.0)), 30)
    network = ThermalNetworkSysModel(ThermalNetworkConfig(
        nodes={"receiver": ThermalNodeParams(specific_heat_j_per_kg_k=100.0, surface_area_m2=0.0, emissivity=0.0)},
        heaters={"receiver": HeaterParams(power_w=12.0, efficiency=0.95)},
        duration_s=duration, step_s=10.0,
    ))
    network.build(sim, "thermal")
    heater = network.heaters["receiver"]
    heater.forced_state = False
    network.power_bridges["receiver"].enable_dynamics_sampling(sim, "dynamics", input_count=0)
    battery = simpleBattery.SimpleBattery()
    battery.storageCapacity, battery.storedCharge_Init = 1000.0, 500.0
    battery.addPowerNodeToModel(heater.electricalPowerOutMsg)
    sim.AddModelToTask("dynamics", battery)

    class TurnOn(sysModel.SysModel):
        def UpdateState(self, now):
            if now >= macros.sec2nano(5.0):
                heater.forced_state = True

    change = TurnOn()
    sim.AddModelToTask("dynamics", change)
    sim.AddModelToTask("thermal", network)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()
    assert 500.0 - float(battery.batPowerOutMsg.read().storageLevel) == pytest.approx(expected_electrical_j, abs=1e-8)
    assert (network.nodes["receiver"].temp_k - 290.0) * 100.0 == pytest.approx(expected_electrical_j * 0.95, abs=1e-8)
    assert heater.cumulative_heater_electrical_energy_j == pytest.approx(expected_electrical_j)
    evidence = build_thermal_resource_evidence(network)
    assert evidence["status"] == "PASS"
    assert evidence["heaters"][0]["deposited_heat_energy_j"] == pytest.approx(expected_electrical_j * 0.95)


def test_network_heater_bad_pdu_then_recovery_retains_input_failure():
    from subsystems.thermal.thermal_network import ThermalHeater
    permit = messaging.DeviceStatusMsg()
    heater = ThermalHeater("receiver", HeaterParams())
    heater.forced_state = True
    heater.subscribe_enable(permit)
    for index, value in enumerate((1, 2, 1)):
        payload = messaging.DeviceStatusMsgPayload()
        payload.deviceStatus = value
        permit.write(payload, macros.sec2nano(float(index)))
        heater.UpdateState(macros.sec2nano(float(index)))
        if value == 2:
            assert heater.last_electrical_power_w == 0.0
    assert heater.enable_read_status == "VALID"
    assert heater.input_error_count == 1
    assert heater.input_errors
