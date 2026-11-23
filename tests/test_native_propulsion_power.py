"""P10-A6: actual thruster activity controls official auxiliary electrical loads."""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

pytest.importorskip("Basilisk")

from Basilisk.architecture import messaging
from Basilisk.simulation import simplePowerSink
from Basilisk.utilities import SimulationBaseClass, macros

from integration import resource_feedback
from sat_sim.capability_registry import get_adapter_for_capability

WHOLE = "whole_spacecraft.unified_native.v1"


def _component(factor: float | None):
    adapter_class = getattr(resource_feedback, "ThrusterActivityStatusAdapter", None)
    assert adapter_class is not None, "native thruster status adapter is missing"
    source = messaging.THROutputMsg()
    if factor is not None:
        payload = messaging.THROutputMsgPayload()
        payload.thrustFactor = factor
        source.write(payload)
    adapter = adapter_class("propulsionStatus", thruster_output_msg=source)
    sink = simplePowerSink.SimplePowerSink()
    sink.ModelTag = "propulsionPower"
    sink.nodePowerOut = -20.0
    sink.nodeStatusInMsg.subscribeTo(adapter.activeStatusOutMsg)
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("powerProcess")
    process.addTask(sim.CreateNewTask("powerTask", macros.sec2nano(0.1)))
    sim.AddModelToTask("powerTask", adapter, None, 2)
    sim.AddModelToTask("powerTask", sink, None, 1)
    recorder = sink.nodePowerOutMsg.recorder()
    sim.AddModelToTask("powerTask", recorder, None, 0)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.3))
    sim.ExecuteSimulation()
    return adapter, recorder


@pytest.mark.parametrize("factor,expected_w", [(0.0, 0.0), (0.25, 20.0), (1.0, 20.0)])
def test_native_sink_draws_fixed_auxiliary_load_only_for_actual_activity(factor, expected_w):
    adapter, recorder = _component(factor)
    assert len(recorder.times()) >= 3
    assert list(recorder.netPower) == pytest.approx([-expected_w] * len(recorder.times()))
    assert adapter.trace[-1].input_status is resource_feedback.InputReadStatus.VALID
    assert adapter.trace[-1].thruster_active is (factor > 0.0)
    assert not hasattr(adapter, "powerOutMsg")


@pytest.mark.parametrize("factor", [math.nan, math.inf, -math.inf, -0.1, 1.1, None])
def test_invalid_thruster_activity_fails_closed_and_reports_error(factor):
    adapter, recorder = _component(factor)
    assert list(recorder.netPower) == pytest.approx([0.0] * len(recorder.times()))
    assert adapter.trace[-1].input_status is resource_feedback.InputReadStatus.ERROR
    assert adapter.trace[-1].error
    if factor is None:
        assert adapter.trace[0].input_status is resource_feedback.InputReadStatus.NOT_READY


def test_thruster_status_adapter_requires_a_real_input():
    adapter_class = getattr(resource_feedback, "ThrusterActivityStatusAdapter", None)
    assert adapter_class is not None, "native thruster status adapter is missing"
    with pytest.raises(ValueError, match="THRUSTER_ACTIVITY_INPUT_REQUIRED"):
        adapter_class("missing", thruster_output_msg=None)


def _whole_spec(**values):
    return {
        "task": {"id": "p10a6_propulsion", "name": "native propulsion power"},
        "model": {"capability_id": WHOLE},
        "simulation": {"level": "whole_spacecraft", "backend": "basilisk",
                       "duration_s": 12.0, "step_s": 0.1, "sample_s": 0.1},
        "parameters": {"values": {
            "propulsion_enabled": True, "propulsion_burn_start_s": 0.2,
            "propulsion_burn_on_time_s": 8.0, "propulsion_power_w": 20.0,
            "initial_soc": 0.9, "propulsion_min_burn_soc": 0.3,
            **values,
        }},
        "assurance": {"allow_proxy": False}, "outputs": {"plots": []},
    }


def test_ongoing_burn_keeps_electrical_load_after_start_permission_drops():
    result = get_adapter_for_capability(WHOLE).run(_whole_spec(
        initial_soc=0.60, battery_capacity_wh=1.0, bus_power_w=50.0,
        solar_panel_area_m2=0.001, propulsion_min_burn_soc=0.55,
    ))
    ongoing_without_start_permission = [
        row for row in result.trace_rows
        if row["propulsion.burn_permitted"] == 0
        and row["propulsion.total_thrust_n"] > 0.0
    ]
    assert ongoing_without_start_permission, "fixture must lose permission during a real burn"
    assert all(row["propulsion.electrical_power_w"] == pytest.approx(40.0)
               for row in ongoing_without_start_permission)
    assert result.summary["resource_feedback_bridge_health"]["propulsion"]["status"] == "PASS"


def test_native_propulsion_power_closes_eps_and_heat_without_changing_dynamics():
    adapter = get_adapter_for_capability(WHOLE)
    on = adapter.run(_whole_spec())
    initial = on.trace_rows[0]
    assert initial["time_s"] == 0
    assert initial["eps.battery_capacity_j"] == pytest.approx(160 * 3600)
    assert initial["eps.battery_soc"] == pytest.approx(0.9)
    assert abs(initial["eps.net_power_w"]) > 0
    off_spec = _whole_spec(propulsion_power_w=0.0)
    off_spec["mission"] = {"required_couplings": ["propulsion_activity_to_eps_thermal"]}
    off = adapter.run(off_spec)
    health = on.summary["resource_feedback_bridge_health"]["propulsion"]
    assert health["source"] == "Basilisk.simulation.simplePowerSink.SimplePowerSink"
    assert health["status"] == "PASS"
    assert off.summary["resource_feedback_bridge_health"]["propulsion"]["status"] == "PASS"
    assert off.summary["causal_requirement_evidence_status"] == "INCONCLUSIVE"
    assert on.summary["engineering_proxy_module_count"] == 0
    assert on.summary["propulsion_power_model"]["permission_semantics"] == "burn_start_only_not_power_supply"
    assert len(on.trace_rows) == len(off.trace_rows)
    assert on.summary["propellant_used_kg"] == pytest.approx(off.summary["propellant_used_kg"], abs=1e-12)
    assert on.summary["final_orbit_radius_m"] == pytest.approx(off.summary["final_orbit_radius_m"], abs=1e-6)
    for left, right in zip(on.trace_rows, off.trace_rows):
        assert left["time_s"] == right["time_s"]
        assert left["propulsion.total_thrust_n"] == pytest.approx(right["propulsion.total_thrust_n"], abs=1e-12)
        assert left["propulsion.fuel_mass_kg"] == pytest.approx(right["propulsion.fuel_mass_kg"], abs=1e-12)
        assert right["propulsion.electrical_power_w"] == pytest.approx(0.0, abs=1e-9)
        assert left["propulsion.electrical_power_w"] == pytest.approx(
            left["propulsion.electrical_power_w_0"] + left["propulsion.electrical_power_w_1"], abs=1e-9,
        )
    # Compare the steady burn so the existing battery feedback tick cannot be
    # confused with a different load or a shifted transition sample.
    index = next(i for i, row in enumerate(on.trace_rows) if row["time_s"] >= 2.0)
    assert on.trace_rows[index]["propulsion.electrical_power_w"] == pytest.approx(40.0)
    assert on.trace_rows[index]["eps.net_power_w"] == pytest.approx(
        off.trace_rows[index]["eps.net_power_w"] - 40.0, abs=1e-5,
    )
    assert max(row["thermal.propulsion_temp_k"] for row in on.trace_rows) > max(
        row["thermal.propulsion_temp_k"] for row in off.trace_rows
    )


def test_short_burn_power_evidence_is_independent_of_export_sampling():
    adapter = get_adapter_for_capability(WHOLE)
    spec = _whole_spec(propulsion_burn_start_s=0.5, propulsion_burn_on_time_s=0.8)
    spec["mission"] = {"required_couplings": ["propulsion_activity_to_eps_thermal"]}
    fine = adapter.run(spec)
    spec["simulation"]["sample_s"] = 3.0
    coarse = adapter.run(spec)
    assert max(row["propulsion.electrical_power_w"] for row in coarse.trace_rows) == 0.0
    assert coarse.summary["propellant_used_kg"] == pytest.approx(fine.summary["propellant_used_kg"], abs=1e-12)
    for result in (fine, coarse):
        health = result.summary["resource_feedback_bridge_health"]["propulsion"]
        assert health["power_max_w"] == pytest.approx(40.0)
        assert health["positive_power_sample_count"] > 0
        assert result.summary["causal_requirement_evidence_status"] == "SINGLE_RUN_OBSERVED"
    assert coarse.summary["resource_feedback_bridge_health"]["propulsion"]["sample_count"] == (
        fine.summary["resource_feedback_bridge_health"]["propulsion"]["sample_count"]
    )


def test_controlled_native_segments_preserve_earlier_burn_evidence():
    from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime, _config

    class Advances:
        def __init__(self):
            self.stops = iter((*[index * 0.2 for index in range(1, 61)], None))

        def runtime_prepared(self):
            pass

        def wait_for_advance(self):
            return next(self.stops)

        def take_commands(self):
            return ()

        def runtime_advanced(self, stop_time_s, rows):
            pass

    spec = _whole_spec(propulsion_burn_start_s=0.5, propulsion_burn_on_time_s=0.8)
    spec["simulation"]["sample_s"] = 3.0
    spec["mission"] = {"required_couplings": ["propulsion_activity_to_eps_thermal"]}
    config = replace(_config(spec, WHOLE, False), execution_controller=Advances(), interactive_unbounded=True)
    result = UnifiedNativeRuntime(config).run()
    assert all(row["time_s"] >= 9.0 for row in result.trace_rows)
    health = result.summary["resource_feedback_bridge_health"]["propulsion"]
    assert health["power_max_w"] == pytest.approx(40.0)
    assert health["positive_power_sample_count"] > 0
    assert health["sample_count"] == 121
    assert result.summary["causal_requirement_evidence_status"] == "SINGLE_RUN_OBSERVED"


def test_low_soc_no_start_has_no_thruster_electrical_load():
    result = get_adapter_for_capability(WHOLE).run(_whole_spec(initial_soc=0.1, propulsion_min_burn_soc=0.95))
    assert result.summary["propellant_used_kg"] == 0.0
    assert all(row["propulsion.electrical_power_w"] == 0.0 for row in result.trace_rows)
    assert result.summary["resource_feedback_bridge_health"]["propulsion"]["status"] == "PASS"


def test_thruster_diagnostics_retain_early_errors_after_trace_rollover_and_reset():
    source = messaging.THROutputMsg()
    adapter = resource_feedback.ThrusterActivityStatusAdapter(
        "boundedPropulsionDiagnostics", thruster_output_msg=source,
    )
    adapter.Reset(0)
    assert adapter.activeStatusOutMsg.read().deviceStatus == 0

    payload = messaging.THROutputMsgPayload()
    payload.thrustFactor = math.nan
    source.write(payload, 1)
    adapter.UpdateState(1)
    payload.thrustFactor = 0.25
    source.write(payload, 2)
    for current_ns in range(2, 302):
        adapter.UpdateState(current_ns)

    assert len(adapter.trace) == 256
    assert all(sample.input_status is resource_feedback.InputReadStatus.VALID for sample in adapter.trace)
    assert adapter.input_error_count == 1
    assert adapter.sample_count == 302
    assert adapter.not_ready_sample_count == 1
    assert len(adapter.input_errors) == 1
    assert adapter.input_errors[0]
    assert adapter.activeStatusOutMsg.read().deviceStatus == 1

    adapter.Reset(302)
    assert adapter.input_error_count == 0
    assert adapter.input_errors == []
    assert adapter.not_ready_sample_count == 0
    assert adapter.sample_count == 1
    assert len(adapter.trace) == 1
    assert adapter.trace[0].time_ns == 302
    assert adapter.activeStatusOutMsg.read().deviceStatus == 1


def test_thruster_diagnostic_details_are_bounded_without_losing_error_count():
    payload = messaging.THROutputMsgPayload()
    payload.thrustFactor = -0.1
    source = messaging.THROutputMsg().write(payload)
    adapter = resource_feedback.ThrusterActivityStatusAdapter(
        "boundedPropulsionErrors", thruster_output_msg=source,
    )
    adapter.Reset(0)
    for current_ns in range(1, 301):
        adapter.UpdateState(current_ns)

    assert adapter.input_error_count == 301
    assert adapter.sample_count == 301
    assert adapter.not_ready_sample_count == 0
    assert len(adapter.input_errors) == 10
    assert all(adapter.input_errors)
    assert len(adapter.trace) == 256
    assert adapter.activeStatusOutMsg.read().deviceStatus == 0
