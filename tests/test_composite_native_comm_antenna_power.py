"""P10-A2: native SimpleAntenna -> gated AntennaPower/base nodes -> EPS/thermal."""

from __future__ import annotations

import pytest


def _run_composite(
    *,
    initial_soc: float = 0.90,
    duration_s: float = 40.0,
    solar_power_w: float = 95.0,
    disable_tx_power: bool = False,
    antenna_off: bool = False,
):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import (
        _apply_run_config_to_graph,
        _execute_whole_spacecraft_graph,
    )
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

    structure = WholeSpacecraftConfig(
        initial_soc=initial_soc,
        solar_power_w=solar_power_w,
        mission_initial_sigma_bn=(0.0, 0.0, 0.0),
        recorder_step_s=0.2,
        propulsion_enabled=False,
    )
    run_cfg = WholeSpacecraftRunConfig(
        duration_s=duration_s,
        sample_s=0.2,
        structure=structure,
    )
    graph = build_whole_spacecraft_graph(structure)
    _apply_run_config_to_graph(graph, run_cfg)

    if antenna_off:
        # Exercise a real no-TX state: turn off the physical spacecraft antenna
        # before execution so SimpleAntenna -> LinkBudget -> DownlinkHandling
        # naturally produces no RF attempt while the Comm PDU remains enabled.
        off_payload = messaging.AntennaStateMsgPayload()
        off_payload.antennaState = 0
        off_message = messaging.AntennaStateMsg().write(off_payload)
        rf_graph = graph.component_registry["native_rf_access_graph"]
        rf_graph.spacecraft_antenna.antennaSetStateInMsg.subscribeTo(off_message)
        graph.component_registry["_test_antenna_off_state_msg"] = off_message

    if disable_tx_power:
        comm_power_nodes = graph.component_registry["comm_power_nodes"]
        assert comm_power_nodes
        disabled_payload = messaging.DeviceStatusMsgPayload()
        disabled_payload.deviceStatus = 0
        disabled_message = messaging.DeviceStatusMsg().write(disabled_payload)
        disabled_antenna_payload = messaging.AntennaLogMsgPayload()
        disabled_antenna_payload.antennaState = 0
        disabled_antenna_payload.P_Tx = 0.0
        disabled_antenna_payload.P_Rx = 0.0
        disabled_antenna_message = messaging.AntennaLogMsg().write(disabled_antenna_payload)
        # Disable only the power-side antenna log and its non-RF base sink.
        # The physical SimpleAntenna, LinkBudget, DownlinkHandling, storage,
        # PDU and mission-gate paths remain unchanged.
        comm_power_nodes[0].antennaSetStateInMsg.subscribeTo(disabled_antenna_message)
        comm_power_nodes[1].nodeStatusInMsg.subscribeTo(disabled_message)
        graph.component_registry["_test_comm_tx_power_disabled_status_msg"] = disabled_message
        graph.component_registry["_test_comm_tx_power_disabled_antenna_msg"] = disabled_antenna_message

    summary, rows = _execute_whole_spacecraft_graph(graph, run_cfg)
    return graph, summary, rows


def _assert_same_recorder_fields(left, right, recorder_name: str, fields: tuple[str, ...]) -> None:
    left_recorder = left.recorders[recorder_name]
    right_recorder = right.recorders[recorder_name]
    assert list(left_recorder.times()) == list(right_recorder.times())
    for field in fields:
        left_values = list(getattr(left_recorder, field))
        right_values = list(getattr(right_recorder, field))
        assert left_values == pytest.approx(right_values, abs=1e-12), (recorder_name, field)


def test_composite_native_antenna_power_closes_eps_thermal_and_preserves_rf_physics() -> None:
    active_graph, active_summary, _ = _run_composite()
    power_off_graph, power_off_summary, power_off_rows = _run_composite(disable_tx_power=True)

    nodes = active_graph.component_registry["comm_power_nodes"]
    assert len(nodes) == 3
    assert nodes[0].ModelTag == "commAntennaPower"
    assert nodes[1].ModelTag == "commBasePower"
    assert nodes[2].ModelTag == "commIdlePower"
    assert nodes[0].basePowerNeed == pytest.approx(0.0)
    assert nodes[1].nodePowerOut == pytest.approx(-25.0)
    assert active_graph.component_registry["comm_antenna_tx_power_w"] == pytest.approx(5.0)
    assert active_summary.max_comm_control_power_w == pytest.approx(30.0, abs=1e-6)
    # The control run retains the historical 2 W PDU-enabled idle draw, but
    # the TX power node is independently held off.
    assert power_off_summary.max_comm_control_power_w == pytest.approx(2.0, abs=1e-6)

    tx_status = active_graph.recorders["comm_tx_status"].deviceStatus
    idle_status = active_graph.recorders["comm_idle_status"].deviceStatus
    tx_power = active_graph.recorders["comm_power_tx"].netPower
    base_power = active_graph.recorders["comm_power_base"].netPower
    idle_power = active_graph.recorders["comm_power_idle"].netPower
    active_battery = active_graph.recorders["battery"]
    control_battery = power_off_graph.recorders["battery"]
    assert list(active_battery.times()) == list(control_battery.times())

    tx_indices = [
        index for index, enabled in enumerate(tx_status)
        if index > 0 and int(enabled) == 1
    ]
    assert tx_indices, "the native DownlinkHandling path must exercise an actual RF transmission"
    assert all(row.comm_control_power_w == pytest.approx(0.0, abs=1e-6) for row in power_off_rows if row.time_s > 0.0)

    for index in tx_indices:
        assert -float(tx_power[index]) == pytest.approx(5.0, abs=1e-6)
        assert -float(base_power[index]) == pytest.approx(25.0, abs=1e-6)
        assert -float(idle_power[index]) == pytest.approx(0.0, abs=1e-6)
        # SimpleBattery executes before PDU and the downstream Comm power
        # nodes. Its recorded bus balance at i+1 therefore consumes the
        # PowerNodeUsageMsg published at i (one explicit feedback tick).
        battery_index = index + 1
        assert battery_index < len(active_battery.currentNetPower)
        assert float(active_battery.currentNetPower[battery_index]) == pytest.approx(
            float(control_battery.currentNetPower[battery_index]) - 30.0,
            abs=1e-5,
        )
        assert -float(power_off_graph.recorders["comm_power_tx"].netPower[index]) == pytest.approx(0.0, abs=1e-6)
        assert -float(power_off_graph.recorders["comm_power_base"].netPower[index]) == pytest.approx(0.0, abs=1e-6)

    active_thermal_bridge = active_graph.thermal_graph.network.power_bridges["electronics"]
    control_thermal_bridge = power_off_graph.thermal_graph.network.power_bridges["electronics"]
    active_tx_electrical_j = active_thermal_bridge.cumulative_source_electrical_energy_j.get("comm.antenna_tx", 0.0)
    active_tx_heat_j = active_thermal_bridge.cumulative_source_heat_energy_j.get("comm.antenna_tx", 0.0)
    control_tx_electrical_j = control_thermal_bridge.cumulative_source_electrical_energy_j.get("comm.antenna_tx", 0.0)
    active_base_electrical_j = active_thermal_bridge.cumulative_source_electrical_energy_j.get("comm.base_tx", 0.0)
    active_base_heat_j = active_thermal_bridge.cumulative_source_heat_energy_j.get("comm.base_tx", 0.0)
    control_base_electrical_j = control_thermal_bridge.cumulative_source_electrical_energy_j.get("comm.base_tx", 0.0)
    assert active_tx_electrical_j > 0.0
    assert active_tx_heat_j == pytest.approx(0.90 * active_tx_electrical_j, rel=1e-8)
    assert control_tx_electrical_j == pytest.approx(0.0, abs=1e-9)
    assert active_base_electrical_j > 0.0
    assert active_base_heat_j == pytest.approx(0.90 * active_base_electrical_j, rel=1e-8)
    assert control_base_electrical_j == pytest.approx(0.0, abs=1e-9)

    # Power accounting is downstream of the RF chain: enabling/disabling the
    # AntennaPower load must not alter access, link budget, BER/PER or delivery.
    _assert_same_recorder_fields(
        active_graph,
        power_off_graph,
        "rf_link_budget",
        ("CNR1", "CNR2", "distance", "frequency", "bandwidth", "antennaState1", "antennaState2"),
    )
    _assert_same_recorder_fields(
        active_graph,
        power_off_graph,
        "downlink",
        ("linkActive", "attemptedDataRate", "deliveredDataRate", "droppedDataRate", "ber", "per"),
    )


def test_composite_native_comm_power_is_zero_when_pdu_disables_comm() -> None:
    graph, summary, _ = _run_composite(
        initial_soc=0.05,
        duration_s=5.0,
        solar_power_w=0.0,
    )
    adapter = graph.component_registry["comm_status_adapter"]
    runtime_samples = adapter.trace[1:]
    assert runtime_samples
    assert all(not sample.pdu_enabled for sample in runtime_samples)
    assert all(not sample.tx_enabled and not sample.idle_enabled for sample in runtime_samples)

    for recorder_name in ("comm_power_tx", "comm_power_base", "comm_power_idle"):
        values = list(getattr(graph.recorders[recorder_name], "netPower"))[1:]
        assert values
        assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in values)

    assert summary.max_comm_control_power_w == pytest.approx(0.0, abs=1e-9)



def test_composite_native_comm_idle_power_is_two_w_when_physical_antenna_is_off():
    graph, summary, rows = _run_composite(antenna_off=True, duration_s=8.0)
    adapter = graph.component_registry["comm_status_adapter"]
    runtime_samples = adapter.trace[1:]
    assert runtime_samples
    assert all(sample.pdu_enabled for sample in runtime_samples)
    assert all(not sample.tx_enabled and sample.idle_enabled for sample in runtime_samples)

    downlink = graph.recorders["downlink"]
    assert all(int(value) == 0 for value in list(downlink.linkActive)[1:])
    assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in list(downlink.attemptedDataRate)[1:])

    for recorder_name, expected in (
        ("comm_power_tx", 0.0),
        ("comm_power_base", 0.0),
        ("comm_power_idle", -2.0),
    ):
        values = list(getattr(graph.recorders[recorder_name], "netPower"))[1:]
        assert values
        assert all(float(value) == pytest.approx(expected, abs=1e-9) for value in values)

    runtime_power = [row.comm_control_power_w for row in rows if row.time_s > 0.0]
    assert runtime_power
    assert max(runtime_power) == pytest.approx(2.0, abs=1e-6)
    assert summary.max_comm_control_power_w >= 2.0
