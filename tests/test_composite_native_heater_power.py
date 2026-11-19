"""P10-A3: direct native thermal-heater electrical power coupling."""

from __future__ import annotations

import pytest


def _run(force_heaters: bool):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

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
        heater.power_scale = 0.5

    run_cfg = WholeSpacecraftRunConfig(duration_s=3.0, sample_s=0.2, structure=cfg)
    summary, rows = _execute_whole_spacecraft_graph(graph, run_cfg)
    return graph, summary, rows


def test_native_heater_status_drives_official_power_nodes_and_battery() -> None:
    active, active_summary, active_rows = _run(True)
    idle, idle_summary, idle_rows = _run(False)

    active_sources = active.component_registry["heater_power_sources"]
    idle_sources = idle.component_registry["heater_power_sources"]
    heaters = active.thermal_graph.network.heaters
    assert set(active_sources) == set(heaters)
    assert set(idle_sources) == set(heaters)
    assert active.component_registry.get("heater_eps_feedback") is None

    coupling = active.coupling_matrix["thermal_heaters_to_eps_load"]
    assert coupling.coupling_status == "coupled"
    assert "ThermalHeater.electricalPowerOutMsg" in coupling.interface

    expected_total_w = 0.0
    for name, heater in heaters.items():
        source = active_sources[name]
        assert source is heater
        expected_w = abs(float(heater.params.power_w)) * 0.5
        expected_total_w += expected_w

        active_values = list(active.recorders[f"heater_eps_power_{name}"].netPower)
        idle_values = list(idle.recorders[f"heater_eps_power_{name}"].netPower)
        assert len(active_values) > 1 and len(idle_values) > 1
        assert all(float(value) == pytest.approx(-expected_w, abs=1e-9) for value in active_values[1:])
        assert all(float(value) == pytest.approx(0.0, abs=1e-9) for value in idle_values[1:])

    # The SimpleBattery may sample the thermal task output one task tick later;
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

    assert max(row.heater_eps_load_w for row in active_rows) == pytest.approx(expected_total_w, abs=1e-6)
    assert max(row.heater_eps_load_w for row in idle_rows) == pytest.approx(0.0, abs=1e-9)
    assert active_summary.heater_electrical_energy_j > 0.0
    assert idle_summary.heater_electrical_energy_j == pytest.approx(0.0, abs=1e-9)
