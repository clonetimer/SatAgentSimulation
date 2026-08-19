from __future__ import annotations

from pathlib import Path

from sat_sim.capability_registry import get_adapter_for_capability, get_capability

CAPABILITY_ID = "whole_spacecraft.unified_native.v1"


def _spec(task_id: str, *, duration_s: float = 3.0, values: dict | None = None) -> dict:
    return {
        "task_id": task_id,
        "simulation": {
            "level": "whole_spacecraft",
            "duration_s": duration_s,
            "step_s": 0.2,
            "sample_s": 0.5,
            "backend": "basilisk",
        },
        "model": {"capability_id": CAPABILITY_ID},
        "assurance": {"allow_proxy": False},
        "parameters": {"values": dict(values or {})},
        "outputs": {"plots": []},
    }


def _run(spec: dict):
    return get_adapter_for_capability(CAPABILITY_ID).run(spec, get_capability(CAPABILITY_ID).data)


def test_native_odh_is_the_only_production_downlink_and_nominal_statuses_pass() -> None:
    result = _run(_spec("v0565_native_odh_nominal", values={"payload_max_pointing_error_deg": 180.0}))
    tags = {item["tag"] for item in result.metadata["runtime_manifest"]["instantiated_modules"]}
    assert "native_downlink_handling" in tags
    assert "transmitter_compat_disabled" not in tags
    assert result.summary["execution_status"] == "PASS"
    assert result.summary["mission_status"] == "PASS"
    assert result.summary["overall_status"] == "PASS"
    assert result.summary["runtime_message_read_error_count"] == 0
    assert max(float(row["comm.native_cnr"]) for row in result.trace_rows) > 0.0
    assert max(float(row["comm.downlink_bps"]) for row in result.trace_rows) > 0.0


def test_thermal_reset_is_valid_and_overtemp_gates_payload_and_comm() -> None:
    nominal = _run(_spec("v0565_thermal_reset", values={"payload_max_pointing_error_deg": 180.0}))
    assert nominal.trace_rows[0]["thermal.safe"] == 1
    assert nominal.summary["thermal_bounds_pass"] is True
    assert max(float(row["thermal.payload_heat_input_w"]) for row in nominal.trace_rows) > 0.0
    assert max(float(row["thermal.comm_heat_input_w"]) for row in nominal.trace_rows) > 0.0

    hot = _run(_spec(
        "v0565_thermal_gate",
        values={
            "initial_soc": 0.8,
            "initial_payload_temp_k": 339.0,
            "payload_power_w": 10_000.0,
            "payload_max_pointing_error_deg": 180.0,
        },
    ))
    assert hot.summary["execution_status"] == "PASS"
    assert hot.summary["mission_status"] == "FAIL"
    assert hot.summary["overall_status"] == "FAIL"
    assert hot.summary["thermal_bounds_pass"] is False
    unsafe = [row for row in hot.trace_rows if int(row["thermal.safe"]) == 0]
    assert unsafe
    # One discrete control step is permitted; all later sampled states must be gated.
    assert all(int(row["payload.active"]) == 0 for row in unsafe[1:])
    assert all(int(row["comm.active"]) == 0 for row in unsafe[1:])


def test_pdu_feedback_requires_an_observed_transition() -> None:
    nominal = _run(_spec("v0565_no_pdu_transition", values={"payload_max_pointing_error_deg": 180.0}))
    assert nominal.summary["pdu_payload_feedback_observed"] is False
    assert nominal.summary["pdu_comm_feedback_observed"] is False
    assert nominal.summary["heater_pdu_power_feedback_observed"] is False
    assert nominal.summary["payload_dynamic_power_observed"] is True
    assert nominal.summary["comm_dynamic_power_observed"] is True

    drained = _run(_spec(
        "v0565_pdu_transition",
        duration_s=15.0,
        values={
            "initial_soc": 0.60,
            "battery_capacity_wh": 1.0,
            "solar_panel_area_m2": 0.001,
            "bus_power_w": 50.0,
            "payload_max_pointing_error_deg": 180.0,
        },
    ))
    assert drained.summary["pdu_payload_feedback_observed"] is True
    assert drained.summary["pdu_comm_feedback_observed"] is True
    assert drained.summary["heater_pdu_power_feedback_observed"] is True
    assert any(int(row["eps.pdu.payload_enabled"]) == 0 for row in drained.trace_rows)
    assert any(int(row["eps.pdu.heater_enabled"]) == 0 for row in drained.trace_rows)


def test_unlinked_resource_message_is_runtime_error_not_zero_activity() -> None:
    from Basilisk.utilities import SimulationBaseClass, macros
    from integration.resource_feedback import DataActivityPowerBridge, InputReadStatus

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("v0565ReadFailureProcess")
    task = "v0565ReadFailureTask"
    process.addTask(sim.CreateNewTask(task, macros.sec2nano(0.2)))
    bridge = DataActivityPowerBridge(
        "v0565UnlinkedDataBridge",
        nominal_rate_bps=1.0,
        active_power_w=10.0,
    )
    sim.AddModelToTask(task, bridge)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(0.4))
    sim.ExecuteSimulation()
    assert bridge.trace
    assert any(sample.input_status == InputReadStatus.ERROR for sample in bridge.trace)
    assert any("not linked" in str(sample.error) for sample in bridge.trace if sample.error)


def test_run_bundle_records_execution_validation_and_overall_status(tmp_path: Path) -> None:
    from sat_sim.run_bundle import execute_prepared_run, prepare_run

    from sat_sim.scenario_templates import instantiate_scenario_template

    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0565_bundle_hot")
    spec["simulation"].update({"duration_s": 3.0, "step_s": 0.2, "sample_s": 0.5})
    spec.setdefault("parameters", {}).setdefault("values", {}).update({
        "initial_soc": 0.8,
        "initial_payload_temp_k": 339.0,
        "payload_power_w": 10_000.0,
        "payload_max_pointing_error_deg": 180.0,
    })
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    record = result.run_record
    assert record.status.value == "SUCCEEDED"
    assert record.execution_status.value == "SUCCEEDED"
    assert record.validation_status.value == "FAIL"
    assert record.overall_status.value == "FAIL"
