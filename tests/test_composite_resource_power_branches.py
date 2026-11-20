"""P10-A7: native electrical authority across composite public branches."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")


def _build(*, native_downlink=True, network=True, initial_soc=0.9, constant_solar=False, **overrides):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftCouplingConfig

    coupling = WholeSpacecraftCouplingConfig(
        enable_comm_native_odh_downlink=native_downlink,
        enable_adcs_pointing_to_payload_comm_gate=False,
        enable_orbit_sun_attitude_eclipse_to_eps_solar_power=not constant_solar,
    )
    cfg = replace(WholeSpacecraftConfig(
        initial_soc=initial_soc,
        solar_power_w=0.0,
        thermal_step_s=0.2,
        recorder_step_s=0.2,
        thermal_use_network=network,
        propulsion_enabled=False,
        coupling=coupling,
    ), **overrides)
    return build_whole_spacecraft_graph(cfg), cfg


def _execute(graph, duration_s=1.0):
    from Basilisk.utilities import macros

    graph.sim.InitializeSimulation()
    graph.sim.ConfigureStopTime(macros.sec2nano(duration_s))
    graph.sim.ExecuteSimulation()


def _comm_power(graph):
    nodes = graph.component_registry.get("comm_power_nodes", ())
    if nodes:
        return sum(-float(node.nodePowerOutMsg.read().netPower) for node in nodes)
    return -float(graph.component_registry["comm_power_bridge"].powerOutMsg.read().netPower)


@pytest.mark.parametrize("native_downlink,network", [(True, True), (False, True), (True, False), (False, False)])
def test_public_composite_branches_have_actual_power_owners(native_downlink, network):
    graph, _ = _build(native_downlink=native_downlink, network=network)
    scheduled = {type(model).__name__ for task in graph.sim.TaskList for model in task.TaskModels}
    assert "DataActivityPowerBridge" not in scheduled
    assert "HeaterPowerFeedback" not in scheduled
    assert graph.component_registry["comm_power_nodes"]
    assert set(graph.component_registry["heater_power_sources"]) == ({"battery", "electronics"} if network else {"battery"})
    assert graph.component_registry.get("thermal_power_bridges"), "both thermal branches expose routed heat authorities"


@pytest.mark.parametrize("rate,initial_soc,expected", [(-1.5e6, 0.9, 32.0), (-0.375e6, 0.9, 32.0), (0.0, 0.9, 2.0), (-1.5e6, 0.05, 0.0)])
def test_fallback_comm_uses_fixed_active_idle_and_off_loads(rate, initial_soc, expected):
    graph, _ = _build(native_downlink=False, initial_soc=initial_soc)
    graph.transmitter.nodeBaudRate = rate
    _execute(graph)
    assert float(graph.transmitter.nodeDataOutMsg.read().baudRate) == pytest.approx(rate)
    assert _comm_power(graph) == pytest.approx(expected)


@pytest.mark.parametrize("initial_soc,expected", [(0.9, 3.0), (0.05, 0.0)])
def test_reduced_heater_electrical_power_matches_actual_scaled_pdu_gated_heat(initial_soc, expected):
    graph, _ = _build(network=False, initial_soc=initial_soc)
    heater = graph.thermal_graph.nodes["battery"]
    heater.heater_forced_state = True
    heater.heater_power_scale = 0.25
    _execute(graph)
    assert heater.trace[-1].heater_power_w == pytest.approx(expected)
    source = graph.component_registry.get("heater_power_sources", {}).get("battery")
    assert source is heater, "the node producing heater heat must own the electrical message"
    assert -float(source.electricalPowerOutMsg.read().netPower) == pytest.approx(expected)
    assert source.last_electrical_power_w == pytest.approx(source.last_heat_power_w)


def test_reduced_nodes_without_configured_heaters_have_no_ghost_heaters_or_fault_targets():
    graph, _ = _build(network=False)
    for name in ("electronics", "payload"):
        node = graph.thermal_graph.nodes[name]
        assert node.cfg.heater_power_w == 0.0
        assert node.electricalPowerOutMsg is None
        assert node.heater_hardware_present is False
        assert f"heater.{name}" not in graph.component_registry
        assert f"thermal_heater_{name}" not in graph.component_registry
        node.heater_forced_state = True
    _execute(graph)
    assert all(graph.thermal_graph.nodes[name].trace[-1].heater_power_w == 0.0 for name in ("electronics", "payload"))
    assert all(not graph.thermal_graph.nodes[name].trace[-1].heater_on for name in ("electronics", "payload"))


def test_reduced_configured_zero_power_heater_remains_present():
    from subsystems.thermal.builder import build_nominal_thermal_config, build_thermal_basilisk_assembly_graph

    cfg = build_nominal_thermal_config()
    cfg = replace(cfg, heaters={"battery": replace(cfg.heaters["battery"], max_power_w=0.0)})
    graph = build_thermal_basilisk_assembly_graph(thermal_cfg=cfg, power_inputs=[])
    heater = graph.heaters["battery"]
    assert heater.heater_hardware_present is True
    assert heater.electricalPowerOutMsg is not None


@pytest.mark.parametrize("shadow_factor", [0.0, 1.0])
def test_reduced_electrical_heat_is_routed_once_and_independent_of_eclipse(shadow_factor):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from Basilisk.simulation import simplePowerSink
    from Basilisk.utilities import SimulationBaseClass, macros
    from subsystems.thermal.builder import (
        attach_thermal_basilisk_graph_to_task,
        build_nominal_thermal_config,
        build_thermal_basilisk_assembly_graph,
    )

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("thermalBranchTest")
    process.addTask(sim.CreateNewTask("thermalBranchTask", macros.sec2nano(0.2)))
    sink = simplePowerSink.SimplePowerSink()
    sink.nodePowerOut = -10.0
    sim.AddModelToTask("thermalBranchTask", sink, None, 2)
    shadow_payload = messaging.PowerNodeUsageMsgPayload()
    shadow_payload.netPower = shadow_factor
    shadow_msg = messaging.PowerNodeUsageMsg().write(shadow_payload)
    graph = build_thermal_basilisk_assembly_graph(
        thermal_cfg=build_nominal_thermal_config(),
        power_inputs=[(sink.nodePowerOutMsg, 0.9, "payload", "test.payload")],
        shadow_factor_msg=shadow_msg,
    )
    attach_thermal_basilisk_graph_to_task(sim, "thermalBranchTask", graph)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.4))
    sim.ExecuteSimulation()
    observed = {name: node.trace[-1].heat_input_w for name, node in graph.nodes.items()}
    assert observed == pytest.approx({"battery": 0.0, "electronics": 0.0, "payload": 9.0})
    assert graph.power_bridges["payload"].last_source_power_w["test.payload"] == pytest.approx(10.0)
    assert graph.nodes["payload"].cumulative_external_heat_energy_j == pytest.approx(3.6)


def test_reduced_heater_native_ab_closes_battery_and_deposited_heat():
    active, _ = _build(network=False)
    control, _ = _build(network=False)
    for graph, state in ((active, True), (control, False)):
        graph.thermal_graph.heaters["battery"].heater_forced_state = state
        graph.thermal_graph.heaters["battery"].heater_power_scale = 0.25
        _execute(graph)
    for name, fields in (
        ("instrument", ("baudRate",)),
        ("storage", ("storageLevel",)),
        ("spacecraft_state", ("r_BN_N", "v_BN_N", "sigma_BN", "omega_BN_B")),
    ):
        for field in fields:
            np.testing.assert_array_equal(getattr(active.recorders[name], field), getattr(control.recorders[name], field))
    difference = np.asarray(active.recorders["battery"].currentNetPower) - np.asarray(control.recorders["battery"].currentNetPower)
    assert difference[-1] == pytest.approx(-3.0)
    stored_difference = float(control.recorders["battery"].storageLevel[-1]) - float(active.recorders["battery"].storageLevel[-1])
    rows = active.thermal_graph.heaters["battery"].trace
    deposited_j = sum(right.heater_power_w * (right.time_s - left.time_s) for left, right in zip(rows, rows[1:]))
    assert deposited_j == pytest.approx(3.0)
    assert stored_difference == pytest.approx(deposited_j, abs=1e-8)


def test_reduced_heater_missing_pdu_telemetry_fails_closed_with_persistent_evidence():
    from Basilisk.architecture import messaging

    graph, _ = _build(network=False)
    heater = graph.thermal_graph.heaters["battery"]
    heater.heater_forced_state = True
    unwritten = messaging.DeviceStatusMsg()
    heater.subscribe_enable(unwritten)
    _execute(graph)
    assert all(row.heater_power_w == 0.0 for row in heater.trace)
    assert float(heater.electricalPowerOutMsg.read().netPower) == 0.0
    assert heater.enable_read_status == "ERROR"
    assert heater.input_error_count > 0
    assert 0 < len(heater.input_errors) <= 10


def test_whole_coupled_network_removes_standalone_placeholder_heat_only():
    from subsystems.thermal.network_config import build_nominal_thermal_network_config

    standalone = build_nominal_thermal_network_config()
    assert sum(node.internal_heat_w for node in standalone.nodes.values()) == pytest.approx(43.0)
    graph, _ = _build(payload_power_w=0.0)
    assert all(node.internal_heat_w == 0.0 for node in graph.thermal_graph.config.nodes.values())
    _execute(graph)
    assert graph.thermal_graph.power_bridges["payload"].last_heat_output_w == 0.0


@pytest.mark.parametrize("initial_soc,enabled", [(0.9, 1), (0.05, 0)])
def test_constant_solar_public_branch_keeps_the_shared_pdu_contract(initial_soc, enabled):
    graph, _ = _build(native_downlink=False, network=False, constant_solar=True, initial_soc=initial_soc)
    graph.transmitter.nodeBaudRate = -1.5e6
    graph.thermal_graph.nodes["battery"].heater_forced_state = True
    _execute(graph)
    assert graph.eps_graph.pdu is not None
    assert set(graph.eps_graph.status_messages) == {"payload", "adcs", "comm", "heater"}
    assert int(graph.eps_graph.status_messages["comm"].read().deviceStatus) == enabled
    assert _comm_power(graph) == pytest.approx(32.0 if enabled else 0.0)
    assert -float(graph.thermal_graph.nodes["battery"].electricalPowerOutMsg.read().netPower) == pytest.approx(12.0 if enabled else 0.0)


def test_fallback_power_side_ab_preserves_real_transmitter_storage_and_spacecraft():
    from Basilisk.architecture import messaging

    active, _ = _build(native_downlink=False)
    control, _ = _build(native_downlink=False)
    active.transmitter.nodeBaudRate = control.transmitter.nodeBaudRate = -0.375e6
    nodes = control.component_registry["comm_power_nodes"]
    assert nodes, "native power nodes are required for an independent power-side control"
    disabled = messaging.DeviceStatusMsgPayload()
    disabled.deviceStatus = 0
    disabled_msg = messaging.DeviceStatusMsg().write(disabled)
    for node in nodes:
        node.nodeStatusInMsg.subscribeTo(disabled_msg)
    _execute(active)
    _execute(control)
    for name, fields in (
        ("transmitter", ("baudRate",)),
        ("storage", ("storageLevel",)),
        ("spacecraft_state", ("r_BN_N", "v_BN_N", "sigma_BN", "omega_BN_B")),
    ):
        for field in fields:
            np.testing.assert_array_equal(getattr(active.recorders[name], field), getattr(control.recorders[name], field))
    difference = np.asarray(active.recorders["battery"].currentNetPower) - np.asarray(control.recorders["battery"].currentNetPower)
    assert difference[-1] == pytest.approx(-32.0)
    assert active.thermal_graph.power_bridges["electronics"].last_source_power_w["comm.fallback_activity"] == pytest.approx(32.0)
    assert control.thermal_graph.power_bridges["electronics"].last_source_power_w["comm.fallback_activity"] == 0.0


@pytest.mark.parametrize("bad_rate", [float("nan"), float("inf"), float("-inf"), 1.0])
def test_fallback_status_adapter_rejects_bad_native_input_and_retains_bounded_error_evidence(bad_rate):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from integration import resource_feedback

    adapter_type = getattr(resource_feedback, "TransmitterActivityStatusAdapter", None)
    assert adapter_type is not None, "fallback Comm needs a status-only adapter"
    data = messaging.DataNodeUsageMsgPayload()
    data.baudRate = bad_rate
    data_msg = messaging.DataNodeUsageMsg().write(data)
    permit = messaging.DeviceStatusMsgPayload()
    permit.deviceStatus = 1
    permit_msg = messaging.DeviceStatusMsg().write(permit)
    adapter = adapter_type("testTransmitterStatus", data_msg=data_msg, enable_status_msg=permit_msg)
    for tick in range(300):
        adapter.UpdateState(tick + 1)
    assert int(adapter.activeStatusOutMsg.read().deviceStatus) == 0
    assert int(adapter.idleStatusOutMsg.read().deviceStatus) == 0
    assert adapter.input_error_count == 300
    assert len(adapter.trace) <= 256
    assert 0 < len(adapter.input_errors) <= 10
    data.baudRate = -1.0
    data_msg.write(data)
    adapter.UpdateState(301)
    assert int(adapter.activeStatusOutMsg.read().deviceStatus) == 1
    assert adapter.input_error_count == 300


def test_fallback_status_adapter_startup_and_invalid_pdu_are_not_idle_power():
    from Basilisk.architecture import messaging
    from integration.resource_feedback import TransmitterActivityStatusAdapter

    data_msg = messaging.DataNodeUsageMsg()
    permit_msg = messaging.DeviceStatusMsg()
    adapter = TransmitterActivityStatusAdapter("testStartupStatus", data_msg=data_msg, enable_status_msg=permit_msg)
    adapter.UpdateState(0)
    assert str(adapter.trace[-1].input_status) == "NOT_READY"
    assert adapter.input_error_count == 0
    assert adapter.activeStatusOutMsg.read().deviceStatus == adapter.idleStatusOutMsg.read().deviceStatus == 0
    adapter.UpdateState(1)
    assert str(adapter.trace[-1].input_status) == "ERROR"
    assert adapter.input_error_count == 1
    data = messaging.DataNodeUsageMsgPayload()
    data.baudRate = 0.0
    data_msg.write(data)
    permit = messaging.DeviceStatusMsgPayload()
    permit.deviceStatus = 2
    permit_msg.write(permit)
    adapter.UpdateState(2)
    assert adapter.activeStatusOutMsg.read().deviceStatus == adapter.idleStatusOutMsg.read().deviceStatus == 0
    assert adapter.input_error_count == 2
