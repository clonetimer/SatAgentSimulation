"""P10-A3: native SimplePowerSink coupling for thermal heaters."""

from __future__ import annotations

import pytest


def _run(force_heaters: bool):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.utilities import macros
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig

    cfg = WholeSpacecraftConfig(
        initial_soc=0.80,
        thermal_use_network=True,
        thermal_step_s=0.2,
        recorder_step_s=0.2,
        propulsion_enabled=False,
    )
    graph = build_whole_spacecraft_graph(cfg)
    heaters = graph.thermal_graph.network.heaters
    assert heaters
    for heater in heaters.values():
        heater.forced_state = bool(force_heaters)

    graph.sim.InitializeSimulation()
    graph.sim.ConfigureStopTime(macros.sec2nano(3.0))
    graph.sim.ExecuteSimulation()
    return graph


def test_native_heater_status_drives_official_power_nodes_and_battery() -> None:
    active = _run(True)
    idle = _run(False)

    active_nodes = active.component_registry["heater_power_nodes"]
    idle_nodes = idle.component_registry["heater_power_nodes"]
    heaters = active.thermal_graph.network.heaters
    assert set(active_nodes) == set(heaters)
    assert set(idle_nodes) == set(heaters)
    assert active.component_registry.get("heater_eps_feedback") is None

    coupling = {
        item.name: item
        for item in active.coupling_matrix
    }["thermal_heaters_to_eps_load"]
    assert coupling.coupling_status == "coupled"
    assert "SimplePowerSink.nodeStatusInMsg" in coupling.interface

    expected_total_w = 0.0
    for name, heater in heaters.items():
        node = active_nodes[name]
        assert node.__class__.__name__ == "SimplePowerSink"
        assert node.nodeStatusInMsg.isLinked()
        expected_w = abs(float(heater.params.power_w))
        expected_total_w += expected_w

        active_values = list(active.recorders[f"heater_eps_power_{name}"].netPower)
        idle_values = list(idle.recorders[f"heater_eps_power_{name}"].netPower)
        assert len(active_values) > 1 and len(idle_values) > 1
        assert all(float(value) == pytest.approx(-expected_w, abs=1e-9) for value in active_values[1:])
        assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in idle_values[1:])

    # The SimpleBattery runs before the thermal task's heater power nodes;
    # compare the next battery sample to the power-node sample that it consumes.
    active_battery = active.recorders["battery"]
    idle_battery = idle.recorders["battery"]
    assert list(active_battery.times()) == list(idle_battery.times())
    comparisons = 0
    for index in range(1, len(active_battery.currentNetPower) - 1):
        active_power = float(active_battery.currentNetPower[index + 1])
        idle_power = float(idle_battery.currentNetPower[index + 1])
        if abs(active_power - idle_power) > 1e-8:
            assert active_power == pytest.approx(idle_power - expected_total_w, abs=1e-5)
            comparisons += 1
            break
    assert comparisons == 1

    assert max(row.heater_eps_load_w for row in active.trace_rows) == pytest.approx(expected_total_w, abs=1e-6)
    assert max(row.heater_eps_load_w for row in idle.trace_rows) == pytest.approx(0.0, abs=1e-9)
