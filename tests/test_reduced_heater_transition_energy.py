"""A reduced heater funds and deposits the same completed thermal interval."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")


def _run(scenario: str, *, active: bool, duration_s: float):
    from Basilisk.architecture import messaging, sysModel
    from Basilisk.utilities import macros
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftCouplingConfig

    cfg = WholeSpacecraftConfig(
        initial_soc=0.9, solar_power_w=0.0, thermal_step_s=10.0,
        recorder_step_s=0.2, thermal_use_network=False, propulsion_enabled=False,
        coupling=WholeSpacecraftCouplingConfig(enable_adcs_pointing_to_payload_comm_gate=False),
    )
    graph = build_whole_spacecraft_graph(cfg)
    node = graph.thermal_graph.heaters["battery"]
    node.heater_forced_state = active and scenario != "on"
    permit = None
    if scenario in {"permit_on", "permit_off"}:
        payload = messaging.DeviceStatusMsgPayload()
        payload.deviceStatus = int(scenario == "permit_off")
        permit = messaging.DeviceStatusMsg().write(payload)
        node.subscribe_enable(permit)

    class HeaterTransition(sysModel.SysModel):
        def UpdateState(self, CurrentSimNanos):
            after_transition = int(CurrentSimNanos) >= 5_000_000_000
            if scenario == "on":
                node.heater_forced_state = active and after_transition
            elif scenario == "off":
                node.heater_forced_state = active and not after_transition
            elif scenario == "scale":
                node.heater_power_scale = 0.25 if after_transition else 1.0
            elif permit is not None:
                payload = messaging.DeviceStatusMsgPayload()
                payload.deviceStatus = int(after_transition if scenario == "permit_on" else not after_transition)
                permit.write(payload, CurrentSimNanos, self.moduleID)

    transition = HeaterTransition()
    transition.ModelTag = f"reducedHeaterTransition_{scenario}"
    graph.sim.AddModelToTask("wholeSpacecraftSimTask", transition, None, 56)
    graph.sim.InitializeSimulation()
    graph.sim.ConfigureStopTime(macros.sec2nano(duration_s))
    graph.sim.ExecuteSimulation()
    return graph, node


def _source_energy(graph, *, start_s=0.0, end_s):
    recorder = graph.recorders["heater_eps_power_battery"]
    times = np.asarray(recorder.times(), dtype=float) * 1.0e-9
    return sum(
        -float(recorder.netPower[i - 1]) * max(0.0, min(end_s, times[i]) - max(start_s, times[i - 1]))
        for i in range(1, len(times))
    )


def _temperature_heater_energy(active, control):
    """Infer added heater energy from actual temperature and other heat terms."""
    assert [row.time_s for row in active.trace] == [row.time_s for row in control.trace]
    capacity = active.cfg.thermal_capacity_j_per_c
    assert capacity == control.cfg.thermal_capacity_j_per_c
    energy = 0.0
    for index in range(1, len(active.trace)):
        now_a, prev_a = active.trace[index], active.trace[index - 1]
        now_b, prev_b = control.trace[index], control.trace[index - 1]
        dt = now_a.time_s - prev_a.time_s
        energy += capacity * ((now_a.temp_c - prev_a.temp_c) - (now_b.temp_c - prev_b.temp_c))
        energy += dt * (now_a.passive_cooling_w - now_b.passive_cooling_w)
        energy += dt * (now_a.cooling_power_w - now_b.cooling_power_w)
        energy -= dt * (now_a.heat_input_w - now_b.heat_input_w)
    return energy


@pytest.mark.parametrize("scenario, completed_j, published_w", [
    ("on", 0.0, 12.0),
    ("off", 120.0, 0.0),
    ("scale", 120.0, 3.0),
    ("permit_on", 0.0, 12.0),
    ("permit_off", 120.0, 0.0),
])
def test_transition_uses_previously_held_heat_then_publishes_next_state(scenario, completed_j, published_w):
    active_graph, active = _run(scenario, active=True, duration_s=10.0)
    control_graph, control = _run(scenario, active=False, duration_s=10.0)

    # This is a physical-temperature assertion, independent of the new energy
    # counters. The first completed interval has identical baseline heat/loss.
    assert active.temp_c - control.temp_c == pytest.approx(
        completed_j / active.cfg.thermal_capacity_j_per_c, abs=1e-12,
    )
    assert _temperature_heater_energy(active, control) == pytest.approx(completed_j, abs=1e-9)
    assert _source_energy(active_graph, end_s=10.0) == pytest.approx(completed_j, abs=1e-9)
    assert _source_energy(control_graph, end_s=10.0) == 0.0
    assert active.cumulative_heater_heat_energy_j == pytest.approx(completed_j)
    assert active.cumulative_heater_electrical_energy_j == pytest.approx(completed_j)
    assert active.last_heater_deposited_power_w == pytest.approx(completed_j / 10.0)
    assert active.last_heat_power_w == active.last_electrical_power_w == pytest.approx(published_w)
    assert -float(active.electricalPowerOutMsg.read().netPower) == pytest.approx(published_w)
    assert active.trace[-1].heater_power_w == pytest.approx(published_w)
    assert active.trace[-1].heater_deposited_power_w == pytest.approx(completed_j / 10.0)
    assert active.trace[-1].heater_electrical_interval_power_w == pytest.approx(completed_j / 10.0)
    assert active.input_error_count == 0


@pytest.mark.parametrize("scenario,duration_s,completed_j,pending_j", [
    ("on", 15.0, 0.0, 60.0),
    ("on", 20.0, 120.0, 0.0),
    ("off", 15.0, 120.0, 0.0),
    ("scale", 15.0, 120.0, 15.0),
    ("scale", 20.0, 150.0, 0.0),
])
def test_native_battery_observed_window_matches_deposited_plus_pending_heat(scenario, duration_s, completed_j, pending_j):
    from whole_spacecraft.battery_evidence import check_native_battery_evidence

    active_graph, active = _run(scenario, active=True, duration_s=duration_s)
    control_graph, control = _run(scenario, active=False, duration_s=duration_s)
    evidence = [check_native_battery_evidence(
        graph.recorders["conservation_battery"],
        expected_update_times_ns=graph.task_cadence_probes["dynamics"].update_times_ns,
    ) for graph in (active_graph, control_graph)]
    assert evidence[0].start_s == evidence[1].start_s == pytest.approx(0.2)
    assert evidence[0].end_s == evidence[1].end_s == pytest.approx(duration_s)
    native_window_cost = evidence[1].storage_delta_j - evidence[0].storage_delta_j
    source_window_j = _source_energy(active_graph, start_s=evidence[0].start_s, end_s=duration_s)
    assert native_window_cost == pytest.approx(source_window_j, abs=1e-7)

    completed_until_s = float(active.last_ns) * 1.0e-9
    measured_pending_j = _source_energy(active_graph, start_s=completed_until_s, end_s=duration_s)
    assert measured_pending_j == pytest.approx(pending_j, abs=1e-9)
    assert active.cumulative_heater_heat_energy_j == pytest.approx(completed_j)
    assert active.cumulative_heater_electrical_energy_j == pytest.approx(completed_j)
    assert _temperature_heater_energy(active, control) == pytest.approx(completed_j, abs=1e-9)
    # The native battery audit begins at its first positive-capacity output;
    # remove only the measured source energy before that shared start time.
    before_window_j = _source_energy(active_graph, end_s=evidence[0].start_s)
    assert active.cumulative_heater_heat_energy_j + measured_pending_j - before_window_j == pytest.approx(native_window_cost, abs=1e-7)
    if completed_j == 0.0:
        assert active.temp_c == pytest.approx(control.temp_c, abs=1e-12)
    else:
        assert active.temp_c > control.temp_c
    for name, fields in (("spacecraft_state", ("r_BN_N", "v_BN_N")), ("storage", ("storageLevel",)), ("instrument", ("baudRate",))):
        for field in fields:
            np.testing.assert_array_equal(getattr(active_graph.recorders[name], field), getattr(control_graph.recorders[name], field))


def test_unconfigured_heater_has_no_interval_heat_or_electrical_authority():
    graph, _ = _run("on", active=True, duration_s=20.0)
    for name in ("electronics", "payload"):
        node = graph.thermal_graph.nodes[name]
        assert node.heater_hardware_present is False
        assert node.electricalPowerOutMsg is None
        assert node.last_heater_deposited_power_w == 0.0
        assert node.cumulative_heater_heat_energy_j == 0.0
        assert node.cumulative_heater_electrical_energy_j == 0.0
