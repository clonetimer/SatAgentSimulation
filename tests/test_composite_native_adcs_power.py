"""P10-A1B: active composite capability uses official per-wheel RW power nodes."""
from __future__ import annotations

import pytest


def _run(initial_soc: float):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import macros
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig

    graph = build_whole_spacecraft_graph(WholeSpacecraftConfig(
        initial_soc=initial_soc,
        mission_initial_sigma_bn=(0.1, 0.0, 0.0),
        recorder_step_s=0.2,
        propulsion_enabled=False,
    ))
    graph.sim.InitializeSimulation()
    graph.sim.ConfigureStopTime(macros.sec2nano(8.0))
    graph.sim.ExecuteSimulation()
    return graph


def test_active_composite_uses_official_per_wheel_reaction_wheel_power():
    graph = _run(0.62)
    nodes = graph.component_registry["adcs_power_nodes"]
    assert nodes
    assert "adcs_power_bridge" not in graph.component_registry
    wheel_count = len(graph.adcs_graph.rw_closed_loop.rw_state_effector.rwOutMsgs)
    assert len(nodes) == wheel_count

    pdu = graph.recorders["pdu_adcs_status"]
    power_recorders = [graph.recorders[f"adcs_power_{i}"] for i in range(wheel_count)]
    rw_recorders = [graph.recorders[f"rw_state_{i}"] for i in range(wheel_count)]
    assert all(len(rec.times()) >= 3 for rec in power_recorders)
    assert all(len(rec.times()) >= 3 for rec in rw_recorders)

    runtime_dynamic_power = []
    sample_count = min(len(pdu.times()), *(len(rec.times()) for rec in power_recorders + rw_recorders))
    for sample_index in range(1, sample_count):
        enabled = bool(int(pdu.deviceStatus[sample_index]))
        observed = sum(max(0.0, -float(rec.netPower[sample_index])) for rec in power_recorders)
        expected = 0.0
        if enabled:
            expected = 10.0 + sum(
                abs(float(rw.u_current[sample_index]) * float(rw.Omega[sample_index])) / 0.75
                for rw in rw_recorders
            )
        assert observed == pytest.approx(expected, abs=1e-5)
        if enabled:
            runtime_dynamic_power.append(observed)

    assert runtime_dynamic_power
    assert max(runtime_dynamic_power) > 10.0


def test_active_composite_pdu_disable_turns_off_native_adcs_power_nodes():
    graph = _run(0.05)
    nodes = graph.component_registry["adcs_power_nodes"]
    pdu = graph.recorders["pdu_adcs_status"]
    power_recorders = [graph.recorders[f"adcs_power_{i}"] for i in range(len(nodes))]
    sample_count = min(len(pdu.times()), *(len(rec.times()) for rec in power_recorders))
    assert sample_count >= 3
    for sample_index in range(1, sample_count):
        assert int(pdu.deviceStatus[sample_index]) == 0
        observed = sum(max(0.0, -float(rec.netPower[sample_index])) for rec in power_recorders)
        assert observed == pytest.approx(0.0, abs=1e-6)
