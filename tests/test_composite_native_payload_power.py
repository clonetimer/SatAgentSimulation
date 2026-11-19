"""P10-A4: native SimplePowerSink payload electrical load coupling."""

from __future__ import annotations

import pytest


def _run(*, power_enabled: bool, initial_soc: float = 0.90, duration_s: float = 8.0):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

    cfg = WholeSpacecraftConfig(
        initial_soc=initial_soc,
        solar_power_w=95.0 if initial_soc > 0.5 else 0.0,
        recorder_step_s=0.2,
        propulsion_enabled=False,
    )
    graph = build_whole_spacecraft_graph(cfg)
    power_node = graph.component_registry["payload_power_node"]
    assert power_node.__class__.__name__ == "SimplePowerSink"
    assert power_node.nodePowerOut == pytest.approx(-abs(cfg.payload_power_w))
    assert power_node.nodeStatusInMsg.isLinked()

    if not power_enabled:
        # Disable only the power-side node. The instrument, PDU, mission gate,
        # generated data and storage links remain untouched for causal A/B.
        disabled = messaging.DeviceStatusMsgPayload()
        disabled.deviceStatus = 0
        disabled_msg = messaging.DeviceStatusMsg().write(disabled)
        power_node.nodeStatusInMsg.subscribeTo(disabled_msg)
        graph.component_registry["_test_payload_power_disabled_msg"] = disabled_msg

    run_cfg = WholeSpacecraftRunConfig(
        duration_s=duration_s,
        sample_s=0.2,
        structure=cfg,
    )
    summary, rows = _execute_whole_spacecraft_graph(graph, run_cfg)
    return graph, summary, rows


def test_payload_native_power_node_closes_battery_and_thermal_without_changing_data_path() -> None:
    active, active_summary, active_rows = _run(power_enabled=True)
    control, control_summary, control_rows = _run(power_enabled=False)

    assert active.coupling_matrix["payload_activity_to_eps_thermal"].coupling_status == "coupled"
    assert "official SimplePowerSink" in active.coupling_matrix["payload_activity_to_eps_thermal"].interface
    assert active.component_registry.get("payload_power_bridge") is None

    active_power = list(active.recorders["payload_power"].netPower)
    control_power = list(control.recorders["payload_power"].netPower)
    active_status = list(active.recorders["payload_active_status"].deviceStatus)
    assert len(active_power) > 2 and len(control_power) == len(active_power)
    assert any(int(value) == 1 for value in active_status[1:])
    assert all(float(value) == pytest.approx(-38.0, abs=1e-9) for value, status in zip(active_power[1:], active_status[1:]) if int(status) == 1)
    assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in control_power[1:])

    # Payload generation and storage are upstream/downstream of the power-only
    # A/B switch and must remain bit-for-bit equivalent.
    for recorder_name, fields in (
        ("instrument", ("baudRate", "dataName")),
        ("storage", ("currentStorage",)),
    ):
        left = active.recorders[recorder_name]
        right = control.recorders[recorder_name]
        assert list(left.times()) == list(right.times())
        for field in fields:
            if hasattr(left, field) and hasattr(right, field):
                assert list(getattr(left, field)) == pytest.approx(list(getattr(right, field)), abs=1e-12)

    active_battery = active.recorders["battery"]
    control_battery = control.recorders["battery"]
    assert list(active_battery.times()) == list(control_battery.times())
    battery_comparisons = 0
    for index in range(1, len(active_power) - 1):
        if -float(active_power[index]) > 0.0:
            assert float(active_battery.currentNetPower[index + 1]) == pytest.approx(
                float(control_battery.currentNetPower[index + 1]) - 38.0,
                abs=1e-5,
            )
            battery_comparisons += 1
            break
    assert battery_comparisons == 1

    active_thermal = active.thermal_graph.network.power_bridges["payload"]
    control_thermal = control.thermal_graph.network.power_bridges["payload"]
    electrical_j = active_thermal.cumulative_source_electrical_energy_j.get("payload.instrument_active", 0.0)
    heat_j = active_thermal.cumulative_source_heat_energy_j.get("payload.instrument_active", 0.0)
    control_electrical_j = control_thermal.cumulative_source_electrical_energy_j.get("payload.instrument_active", 0.0)
    assert electrical_j > 0.0
    assert heat_j == pytest.approx(0.95 * electrical_j, rel=1e-8)
    assert control_electrical_j == pytest.approx(0.0, abs=1e-9)
    assert max(row.payload_generated_bps for row in active_rows) == pytest.approx(
        max(row.payload_generated_bps for row in control_rows), abs=1e-9
    )


def test_payload_native_power_is_zero_when_pdu_sheds_payload() -> None:
    graph, summary, rows = _run(power_enabled=True, initial_soc=0.05, duration_s=5.0)
    samples = graph.component_registry["payload_status_adapter"].trace[1:]
    assert samples
    assert all(not sample.pdu_enabled for sample in samples)
    assert all(not sample.payload_active for sample in samples)
    values = list(graph.recorders["payload_power"].netPower)[1:]
    assert values
    assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in values)
    assert max((row.payload_generated_bps for row in rows), default=0.0) == pytest.approx(0.0, abs=1e-9)
