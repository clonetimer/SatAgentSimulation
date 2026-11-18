from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template


EVENT_START_S = 5.0
EVENT_END_S = 15.0


def _run_adcs_case(
    tmp_path: Path,
    run_id: str,
    *,
    event_category: str | None = None,
    event: dict[str, Any] | None = None,
):
    pytest.importorskip("Basilisk")
    from importlib.metadata import version

    assert version("bsk") == "2.11.0"
    spec = instantiate_scenario_template("adcs_unified_native", task_id=run_id)
    spec["simulation"].update(duration_s=20.0, step_s=0.2, sample_s=1.0)
    values = spec["parameters"]["values"]
    values.update(
        initial_pointing_error_deg=45.0,
        controller_p=60.0,
        simulation_seed=1701,
        gyro_base_noise_std_rad_s=1.0e-5,
    )
    spec["assurance"]["allow_proxy"] = False
    if event is not None:
        if event_category not in {"faults", "degradations", "constraints"}:
            raise ValueError("ADCS event category must be explicit")
        spec["events"] = {"faults": [], "degradations": [], "constraints": []}
        spec["events"][event_category] = [event]

    prepared = prepare_run(spec, output_root=tmp_path / "runs", run_id=run_id)
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.validation.result.value == "PASS", executed.validation.model_dump(mode="json")
    if event is not None:
        assert executed.validation.injection_evidence
        assert all(item.delivery_result.value == "PASS" for item in executed.validation.injection_evidence)
        assert all(item.effect_result.value == "PASS" for item in executed.validation.injection_evidence)
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity
    assert executed.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert executed.summary["external_or_proxy_module_count"] == 0
    if event is not None:
        assert executed.summary["runtime_event_count"] == 1
        assert executed.summary["fault_environment_episode_count"] == 1
        assert executed.summary["physical_effect_verified_count"] == 1
    return executed


def _during(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if EVENT_START_S <= float(row["time_s"]) < EVENT_END_S]


def test_native_adcs_rw_jamming_has_paired_physical_response_and_sealed_evidence(tmp_path: Path) -> None:
    nominal = _run_adcs_case(tmp_path, "adcs_rw_jamming_nominal")
    injected = _run_adcs_case(
        tmp_path,
        "adcs_rw_jamming_injected",
        event_category="faults",
        event={
            "id": "rw0_jam",
            "effect": "adcs_rw_jamming",
            "target": "adcs",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": {"wheel_index": 0, "brake_torque_nm": 0.2},
        },
    )
    base_rows = _during(nominal.trace_rows)
    fault_rows = _during(injected.trace_rows)
    assert base_rows and fault_rows
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(1.0) for row in base_rows)
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.0) for row in fault_rows)
    assert any(row["adcs.control.applied_torque_nm_0"] < -0.05 for row in fault_rows)
    assert min(row["adcs.rw.speed_rad_s_0"] for row in fault_rows) < min(
        row["adcs.rw.speed_rad_s_0"] for row in base_rows
    )
    assert any("adcs_rw_jamming" in row["event.active_effects"] for row in fault_rows)


def test_native_adcs_rw_motor_failure_removes_real_torque_authority(tmp_path: Path) -> None:
    nominal = _run_adcs_case(tmp_path, "adcs_rw_motor_failure_nominal")
    injected = _run_adcs_case(
        tmp_path,
        "adcs_rw_motor_failure_injected",
        event_category="faults",
        event={
            "id": "rw0_motor_open",
            "effect": "adcs_rw_motor_failure",
            "target": "adcs",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": {"wheel_index": 0},
        },
    )
    base_rows = _during(nominal.trace_rows)
    fault_rows = _during(injected.trace_rows)
    assert max(abs(row["adcs.rw.command_torque_nm_0"]) for row in base_rows) > 0.05
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(1.0) for row in base_rows)
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.0) for row in fault_rows)
    assert max(abs(row["adcs.rw.command_torque_nm_0"]) for row in fault_rows) > 0.05
    assert all(row["adcs.control.applied_torque_nm_0"] == pytest.approx(0.0, abs=1.0e-12) for row in fault_rows)


def test_native_adcs_torque_authority_loss_is_partial_not_binary_failure(tmp_path: Path) -> None:
    nominal = _run_adcs_case(tmp_path, "adcs_rw_torque_loss_nominal")
    injected = _run_adcs_case(
        tmp_path,
        "adcs_rw_torque_loss_injected",
        event_category="degradations",
        event={
            "id": "rw0_partial_torque",
            "effect": "adcs_rw_torque_authority_loss",
            "target": "adcs",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": {"wheel_index": 0, "torque_scale": 0.25},
        },
    )
    base_rows = _during(nominal.trace_rows)
    fault_rows = _during(injected.trace_rows)
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(1.0) for row in base_rows)
    assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.25, abs=1.0e-8) for row in fault_rows)
    assert max(abs(row["adcs.rw.command_torque_nm_0"]) for row in fault_rows) > 0.05
    assert max(abs(row["adcs.control.applied_torque_nm_0"]) for row in fault_rows) <= 0.05 + 1.0e-8
    assert any(
        abs(row["adcs.control.applied_torque_nm_0"]) < abs(row["adcs.rw.command_torque_nm_0"]) - 1.0e-5
        for row in fault_rows
    )


def test_native_adcs_gyro_bias_step_changes_measured_rate_and_feedback_input(tmp_path: Path) -> None:
    nominal = _run_adcs_case(tmp_path, "adcs_gyro_bias_nominal")
    injected = _run_adcs_case(
        tmp_path,
        "adcs_gyro_bias_injected",
        event_category="faults",
        event={
            "id": "gyro_x_bias",
            "effect": "gyro_bias_step",
            "target": "adcs",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": {"bias_step_deg_s": [0.2, 0.0, 0.0]},
        },
    )
    base_rows = [row for row in _during(nominal.trace_rows) if row["adcs.fusion.body_rate_valid"] == 1]
    fault_rows = [row for row in _during(injected.trace_rows) if row["adcs.fusion.body_rate_valid"] == 1]
    assert base_rows and fault_rows
    expected_bias = math.radians(0.2)
    assert max(row["adcs.sensor.gyro_bias_norm_rad_s"] for row in fault_rows) == pytest.approx(expected_bias, rel=1.0e-6)
    for row in fault_rows:
        measured_delta = row["adcs.sensor.gyro_measured_platform_rad_s_x"] - row["adcs.sensor.raw_gyro_rad_s_x"]
        assert measured_delta == pytest.approx(expected_bias, abs=1.0e-9)
        assert row["adcs.fusion.body_rate_valid"] == 1
    assert max(row["adcs.sensor.gyro_bias_norm_rad_s"] for row in base_rows) == pytest.approx(0.0, abs=1.0e-12)
    assert any(
        abs(left["adcs.fusion.estimated_omega_BN_B_x"] - right["adcs.fusion.estimated_omega_BN_B_x"]) > 1.0e-4
        for left, right in zip(base_rows, fault_rows)
    )


def test_native_adcs_gyro_noise_increase_changes_noise_process_without_invalidating_samples(tmp_path: Path) -> None:
    nominal = _run_adcs_case(tmp_path, "adcs_gyro_noise_nominal")
    injected = _run_adcs_case(
        tmp_path,
        "adcs_gyro_noise_injected",
        event_category="degradations",
        event={
            "id": "gyro_noise_burst",
            "effect": "gyro_noise_increase",
            "target": "adcs",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": {"noise_scale": 50.0},
        },
    )
    base_rows = [row for row in _during(nominal.trace_rows) if row["adcs.fusion.body_rate_valid"] == 1]
    fault_rows = [row for row in _during(injected.trace_rows) if row["adcs.fusion.body_rate_valid"] == 1]
    assert base_rows and fault_rows
    assert max(row["adcs.sensor.gyro_noise_norm_rad_s"] for row in base_rows) == pytest.approx(0.0, abs=1.0e-12)
    assert max(row["adcs.sensor.gyro_noise_norm_rad_s"] for row in fault_rows) > 2.0e-4
    assert all(row["adcs.sensor.imu_sample_valid"] == 1 for row in fault_rows)
    assert all(row["adcs.fusion.body_rate_valid"] == 1 for row in fault_rows)
    assert any(
        abs(left["adcs.sensor.gyro_measured_platform_rad_s_x"] - right["adcs.sensor.gyro_measured_platform_rad_s_x"]) > 1.0e-4
        for left, right in zip(base_rows, fault_rows)
    )
    assert any(
        abs(left["adcs.fusion.estimated_omega_BN_B_x"] - right["adcs.fusion.estimated_omega_BN_B_x"]) > 1.0e-4
        for left, right in zip(base_rows, fault_rows)
    )
