"""P10-A6: actual native thruster activity owns composite electrical loads."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest


def test_composite_task_parameter_reaches_actual_native_electrical_nodes() -> None:
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from sat_sim.adapters.whole_spacecraft_composite_digital_twin import WholeSpacecraftCompositeDigitalTwinAdapter

    result = WholeSpacecraftCompositeDigitalTwinAdapter().run({
        "task_id": "p10a6_composite_parameter",
        "capability_id": "whole_spacecraft.composite_digital_twin.v1",
        "task_type": "whole_spacecraft",
        "target": {"level": "whole_spacecraft", "name": "composite_digital_twin", "mode": "nominal"},
        "simulation": {"duration_s": 2.0, "sample_s": 0.2},
        "parameters": {"propulsion_power_w": 17.0, "propulsion_on_time_s": [0.8, 0.0],
                       "thermal_step_s": 0.2, "initial_soc": 0.9},
    })
    assert max(row["propulsion_power_w"] for row in result.trace_rows) == pytest.approx(17.0)
    assert result.trace_rows[-1]["propulsion_cumulative_energy_j"] == pytest.approx(13.6, abs=1e-10)


def _run(*, power_enabled: bool = True, duration_s: float = 4.0, native_input_factor: float | None = None, **overrides):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

    cfg = replace(WholeSpacecraftConfig(
        initial_soc=0.90,
        solar_power_w=0.0,
        recorder_step_s=0.2,
        thermal_step_s=0.2,
        propulsion_on_time_s=(0.8, 0.0),
    ), **overrides)
    graph = build_whole_spacecraft_graph(cfg)
    nodes = graph.component_registry.get("propulsion_power_nodes")
    assert nodes, "composite propulsion must expose official per-thruster power nodes"
    assert len(nodes) == len(graph.propulsion_graph.thruster_effector.thrusterOutMsgs)
    assert all(type(node).__name__ == "SimplePowerSink" for node in nodes)
    assert all(node.nodeStatusInMsg.isLinked() for node in nodes)

    if native_input_factor is not None:
        bad_output = messaging.THROutputMsgPayload()
        bad_output.thrustFactor = native_input_factor
        bad_msg = messaging.THROutputMsg().write(bad_output)
        graph.component_registry["propulsion_status_adapters"][0].thrusterInMsg.subscribeTo(bad_msg)
        graph.component_registry["_test_propulsion_bad_output_msg"] = bad_msg

    if not power_enabled:
        # Change only the electrical nodes. Native on-time commands, thruster
        # dynamics, tank flow, spacecraft state and thermal wiring stay real.
        disabled = messaging.DeviceStatusMsgPayload()
        disabled.deviceStatus = 0
        disabled_msg = messaging.DeviceStatusMsg().write(disabled)
        for node in nodes:
            node.nodeStatusInMsg.subscribeTo(disabled_msg)
        graph.component_registry["_test_propulsion_disabled_msg"] = disabled_msg

    summary, rows = _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(
        duration_s=duration_s,
        sample_s=cfg.recorder_step_s,
        structure=cfg,
    ))
    return graph, summary, rows


def test_composite_native_propulsion_tracks_each_thruster_independently() -> None:
    graph, summary, rows = _run()
    assert graph.coupling_matrix["propulsion_activity_to_eps_thermal"].coupling_status == "coupled"
    assert graph.component_registry.get("propulsion_power_bridge") is None
    assert len(graph.component_registry["propulsion_status_adapters"]) == 2

    for index in range(2):
        native = graph.recorders[f"propulsion_thruster_{index}"]
        status = graph.recorders[f"propulsion_active_status_{index}"]
        power = graph.recorders[f"propulsion_power_{index}"]
        assert list(native.times()) == list(status.times()) == list(power.times())
        for factor, enabled, watts in zip(native.thrustFactor, status.deviceStatus, power.netPower):
            assert bool(enabled) == (float(factor) > 0.0)
            assert float(watts) == pytest.approx(-20.0 if enabled else 0.0, abs=1e-12)

    assert max(graph.recorders["propulsion_active_status_0"].deviceStatus) == 1
    assert max(graph.recorders["propulsion_active_status_1"].deviceStatus) == 0
    assert max(row.propulsion_power_w for row in rows) == pytest.approx(20.0)
    assert max(row.propulsion_activity for row in rows) == pytest.approx(1.0)
    assert summary.propulsion_power_input_status == "PASS"
    assert summary.propulsion_power_input_error_count == 0
    assert summary.data_conservation_status == "PASS"


def test_composite_thermal_source_reports_sum_of_independent_thruster_loads() -> None:
    graph, _, _ = _run(duration_s=0.4)
    heat_bridge = graph.thermal_graph.network.power_bridges["structure"]
    assert heat_bridge.last_source_power_w["propulsion.actual_activity"] == pytest.approx(20.0)


def test_composite_native_propulsion_power_closes_eps_and_heat_without_changing_dynamics() -> None:
    active, active_summary, active_rows = _run()
    control, control_summary, control_rows = _run(power_enabled=False)

    # An accidental power-to-command gate, reordered physical model, or fuel
    # equation change breaks equality of these actual native recorder arrays.
    for recorder_name, fields in (
        ("propulsion_thruster_0", ("thrustFactor", "thrustForce", "thrustForce_B", "thrustTorquePntB_B")),
        ("propulsion_thruster_1", ("thrustFactor", "thrustForce", "thrustForce_B", "thrustTorquePntB_B")),
        ("fuel_tank", ("fuelMass", "fuelMassDot")),
        ("spacecraft_state", ("r_BN_N", "v_BN_N", "sigma_BN", "omega_BN_B")),
    ):
        left, right = active.recorders[recorder_name], control.recorders[recorder_name]
        np.testing.assert_array_equal(left.times(), right.times())
        for field in fields:
            np.testing.assert_array_equal(getattr(left, field), getattr(right, field))

    power = active.recorders["propulsion_power_0"]
    active_battery, control_battery = active.recorders["battery"], control.recorders["battery"]
    # The existing EPS task precedes native thruster activity, so the battery
    # reads the official power message from the previous dynamics tick.
    for index in range(1, len(power.times())):
        assert float(active_battery.currentNetPower[index]) - float(control_battery.currentNetPower[index]) == pytest.approx(
            float(power.netPower[index - 1]), abs=1e-8,
        )

    assert active_rows[-1].propulsion_cumulative_energy_j > 0.0
    assert active_battery.storageLevel[-1] < control_battery.storageLevel[-1]
    assert float(control_battery.storageLevel[-1]) - float(active_battery.storageLevel[-1]) == pytest.approx(
        active_rows[-1].propulsion_cumulative_energy_j, abs=1e-7,
    )
    assert control_rows[-1].propulsion_cumulative_energy_j == pytest.approx(0.0, abs=1e-12)
    assert all(row.propulsion_power_w == pytest.approx(0.0) for row in control_rows)
    assert max(row.propulsion_activity for row in control_rows) == pytest.approx(1.0)

    active_heat = active.thermal_graph.network.power_bridges["structure"]
    control_heat = control.thermal_graph.network.power_bridges["structure"]
    electrical_j = active_heat.cumulative_source_electrical_energy_j["propulsion.actual_activity"]
    heat_j = active_heat.cumulative_source_heat_energy_j["propulsion.actual_activity"]
    assert electrical_j > 0.0
    assert heat_j == pytest.approx(0.85 * electrical_j, abs=1e-10)
    assert control_heat.cumulative_source_electrical_energy_j["propulsion.actual_activity"] == pytest.approx(0.0, abs=1e-12)
    assert active_rows[-1].thermal_structure_temp_c > control_rows[-1].thermal_structure_temp_c
    # The existing t=0 battery recorder is unwritten in this official-wheel
    # setup. Preserve its established conservation verdict; the causal energy
    # difference above must still close exactly for the added propulsion load.
    assert active_summary.energy_balance_residual_j == pytest.approx(control_summary.energy_balance_residual_j, abs=1e-7)


def test_composite_propulsion_energy_uses_native_samples_when_output_is_coarse() -> None:
    fine, _, fine_rows = _run(propulsion_on_time_s=(0.8, 0.4))
    coarse, _, coarse_rows = _run(propulsion_on_time_s=(0.8, 0.4), recorder_step_s=2.0)
    for index in range(2):
        left, right = fine.recorders[f"propulsion_power_{index}"], coarse.recorders[f"propulsion_power_{index}"]
        np.testing.assert_array_equal(left.times(), right.times())
        np.testing.assert_array_equal(left.netPower, right.netPower)
        assert len(right.times()) > len(coarse_rows)

    # Each official sink output is held until its next dynamics update; no
    # interpolation across a valve transition or coarse telemetry resampling.
    expected_j = sum(
        -float(recorder.netPower[index - 1]) * float(recorder.times()[index] - recorder.times()[index - 1]) * 1e-9
        for recorder in (fine.recorders["propulsion_power_0"], fine.recorders["propulsion_power_1"])
        for index in range(1, len(recorder.times()))
    )
    assert expected_j > 0.0
    assert fine_rows[-1].propulsion_cumulative_energy_j == pytest.approx(expected_j, abs=1e-10)
    assert coarse_rows[-1].propulsion_cumulative_energy_j == pytest.approx(expected_j, abs=1e-10)
    for row in coarse_rows:
        matching = next(item for item in fine_rows if item.time_s == pytest.approx(row.time_s))
        assert row.propulsion_power_w == pytest.approx(matching.propulsion_power_w)
        assert row.propulsion_activity == pytest.approx(matching.propulsion_activity)
        assert row.propulsion_cumulative_energy_j == pytest.approx(matching.propulsion_cumulative_energy_j)


def test_composite_zero_configured_propulsion_draw_preserves_real_activity() -> None:
    graph, summary, rows = _run(propulsion_power_w=0.0)
    assert max(graph.recorders["propulsion_thruster_0"].thrustFactor) > 0.0
    assert max(row.propulsion_activity for row in rows) == pytest.approx(1.0)
    assert all(row.propulsion_power_w == pytest.approx(0.0) for row in rows)
    assert rows[-1].propulsion_cumulative_energy_j == pytest.approx(0.0)
    assert summary.propulsion_power_input_status == "PASS"
    assert summary.propulsion_power_input_error_count == 0


def test_composite_invalid_native_factor_fails_health_and_only_its_own_sink() -> None:
    graph, summary, rows = _run(native_input_factor=float("nan"), propulsion_on_time_s=(0.8, 0.8))
    assert all(float(value) == pytest.approx(0.0) for value in graph.recorders["propulsion_power_0"].netPower)
    assert min(graph.recorders["propulsion_power_1"].netPower) == pytest.approx(-20.0)
    assert any(sample.input_status == "ERROR" for sample in graph.component_registry["propulsion_status_adapters"][0].trace)
    assert max(row.propulsion_power_w for row in rows) == pytest.approx(20.0)
    assert summary.propulsion_power_input_status == "FAIL"
    assert summary.propulsion_power_input_error_count > 0
    assert summary.execution_status == "FAIL"


@pytest.mark.parametrize("watts", [-0.01, float("nan"), float("inf"), 500.01])
def test_composite_rejects_invalid_propulsion_power_before_building(watts: float) -> None:
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig

    with pytest.raises(ValueError, match="propulsion_power_w"):
        build_whole_spacecraft_graph(WholeSpacecraftConfig(propulsion_power_w=watts))


def test_composite_health_retains_early_native_input_error_after_trace_rollover() -> None:
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from Basilisk.architecture import messaging, sysModel
    from whole_spacecraft.builder import build_whole_spacecraft_graph
    from whole_spacecraft.runner import _execute_whole_spacecraft_graph
    from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

    class ActivitySource(sysModel.SysModel):
        """Publish an invalid startup sample followed by valid native messages."""

        def __init__(self):
            super().__init__()
            self.ModelTag = "propulsionDiagnosticStimulus"
            self.output = messaging.THROutputMsg()
            self.UpdateState(0)

        def UpdateState(self, current_ns):
            payload = messaging.THROutputMsgPayload()
            payload.thrustFactor = float("nan") if current_ns == 0 else 0.25
            self.output.write(payload, current_ns, self.moduleID)

    cfg = WholeSpacecraftConfig(initial_soc=0.9, recorder_step_s=2.0, thermal_step_s=1.0)
    graph = build_whole_spacecraft_graph(cfg)
    stimulus = ActivitySource()
    adapter = graph.component_registry["propulsion_status_adapters"][0]
    adapter.thrusterInMsg.subscribeTo(stimulus.output)
    graph.sim.AddModelToTask(graph.sim_task_name, stimulus, None, 1)
    summary, _ = _execute_whole_spacecraft_graph(graph, WholeSpacecraftRunConfig(
        duration_s=60.0, sample_s=2.0, structure=cfg,
    ))

    assert len(adapter.trace) == 256
    assert all(sample.input_status == "VALID" for sample in adapter.trace)
    assert adapter.input_error_count > 0
    assert summary.propulsion_power_input_error_count == adapter.input_error_count
    assert summary.propulsion_power_input_status == "FAIL"
    assert summary.execution_status == "FAIL"
