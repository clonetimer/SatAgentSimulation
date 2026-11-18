from __future__ import annotations

import pytest

from sat_sim.capability_registry import clear_capability_cache, get_adapter_for_capability, get_capability
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates

ADCS_ID = "subsystem.adcs_unified_native.v1"
WHOLE_ID = "whole_spacecraft.unified_native.v1"


def _short_spec(capability_id: str, *, values: dict | None = None, duration_s: float = 20.0) -> dict:
    return {
        "task": {"id": f"test_{capability_id}", "name": "unified native test"},
        "model": {"capability_id": capability_id},
        "simulation": {
            "level": "subsystem" if capability_id.startswith("subsystem.") else "whole_spacecraft",
            "subsystem": "adcs" if capability_id.startswith("subsystem.") else None,
            "duration_s": duration_s,
            "step_s": 0.2,
            "sample_s": 2.0,
            "backend": "basilisk",
        },
        "assurance": {"allow_proxy": False},
        "parameters": {"values": dict(values or {})},
        "outputs": {"plots": []},
    }


def test_new_capabilities_are_registered_and_templates_normalize() -> None:
    clear_capability_cache()
    adcs = get_capability(ADCS_ID)
    whole = get_capability(WHOLE_ID)
    assert adcs.data["implementation"]["backend_type"] == "basilisk_native_mixed"
    assert whole.data["implementation"]["backend_type"] == "basilisk_native_mixed"
    assert adcs.data["implementation"]["requires_allow_proxy"] is False
    assert whole.data["implementation"]["requires_allow_proxy"] is False
    assert instantiate_scenario_template("adcs_unified_native")["model"]["capability_id"] == ADCS_ID
    assert instantiate_scenario_template("whole_spacecraft_unified_native")["model"]["capability_id"] == WHOLE_ID
    assert list_scenario_templates()["count"] >= 47


def test_adcs_unified_runtime_is_closed_loop_and_recorder_grounded() -> None:
    adapter = get_adapter_for_capability(ADCS_ID)
    result = adapter.run(_short_spec(ADCS_ID))
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert result.summary["official_module_count"] >= 11
    assert result.summary["project_native_module_count"] >= 4
    assert result.summary["external_or_proxy_module_count"] == 0
    assert result.summary["adcs_closed_loop_improved"] is True
    assert result.trace_rows[-1]["adcs.pointing_error_deg"] < result.trace_rows[0]["adcs.pointing_error_deg"]
    assert any(abs(row["adcs.magnetometer.tesla_z"]) > 0.0 for row in result.trace_rows)
    assert result.summary["adcs_fusion"]["magnetometer_used_sample_count"] > 0
    assert any(row["adcs.fusion.magnetometer_used"] == 1 for row in result.trace_rows)
    assert all("adcs.fusion.estimated_mrp_BN_x" in row for row in result.trace_rows)
    assert all("adcs.fusion.estimated_omega_BN_B_x" in row for row in result.trace_rows)
    boundary = result.metadata["model_source_boundary"]
    assert "star_tracker" in boundary["basilisk_official_native"]
    assert "imu" in boundary["basilisk_official_native"]
    assert "magnetometer" in boundary["basilisk_official_native"]
    assert "project_adcs_sensor_fusion" in boundary["basilisk_project_native"]
    assert "project_imu_fault_injector" in boundary["basilisk_project_native"]
    assert "project_rw_command_fault_manager" in boundary["basilisk_project_native"]
    assert boundary["external_or_proxy"] == []


def test_adcs_fusion_transforms_non_identity_sensor_mounts_into_body_frame() -> None:
    adapter = get_adapter_for_capability(ADCS_ID)
    result = adapter.run(
        _short_spec(
            ADCS_ID,
            values={
                "initial_pointing_error_deg": 12.0,
                "star_tracker_mount_yaw_deg": 13.0,
                "star_tracker_mount_pitch_deg": -7.0,
                "star_tracker_mount_roll_deg": 5.0,
                "imu_mount_yaw_deg": -11.0,
                "imu_mount_pitch_deg": 6.0,
                "imu_mount_roll_deg": 9.0,
                "magnetometer_mount_yaw_deg": 8.0,
                "magnetometer_mount_pitch_deg": -4.0,
                "magnetometer_mount_roll_deg": 12.0,
                "star_tracker_noise_std_arcsec": 0.0,
                "gyro_base_noise_std_rad_s": 0.0,
            },
            duration_s=20.0,
        )
    )
    assert result.summary["adcs_closed_loop_improved"] is True
    assert result.summary["adcs_fusion"]["magnetometer_used_sample_count"] > 0
    assert result.summary["adcs_fusion"]["attitude_frame"] == "body_B_relative_to_inertial_N"
    assert result.summary["adcs_fusion"]["body_rate_frame"] == "body_B"

    first = result.trace_rows[0]
    # Reset writes the first truth/attitude sample before the scheduled IMU
    # model's first UpdateState. Attitude is available at Reset, while the
    # first measured gyro sample becomes valid on the next dynamics tick.
    assert first["adcs.fusion.magnetometer_used"] == 1
    measured_rate = result.trace_rows[1]
    for axis in "xyz":
        assert first[f"adcs.fusion.estimated_mrp_BN_{axis}"] == pytest.approx(
            first[f"attitude.truth.mrp_BN_{axis}"],
            abs=2.0e-4,
        )
        assert measured_rate[f"adcs.fusion.estimated_omega_BN_B_{axis}"] == pytest.approx(
            measured_rate[f"attitude.truth.omega_BN_B_{axis}"],
            abs=2.0e-6,
        )
    assert result.trace_rows[-1]["adcs.pointing_error_deg"] < first["adcs.pointing_error_deg"]


def test_whole_spacecraft_unified_runtime_has_message_coupling_and_bounds() -> None:
    adapter = get_adapter_for_capability(WHOLE_ID)
    result = adapter.run(_short_spec(WHOLE_ID))
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    # Three static official power sinks were intentionally replaced by
    # message-driven project-native activity bridges in v0.5.6.3.
    assert result.summary["official_module_count"] >= 18
    assert result.summary["project_native_module_count"] >= 16
    assert result.summary["engineering_proxy_module_count"] == 4
    assert result.summary["external_or_proxy_module_count"] == 4
    assert result.summary["connected_message_count"] >= 36
    assert result.summary["energy_bounds_pass"] is True
    assert result.summary["data_bounds_pass"] is True
    assert result.summary["coupling_integrity_pass"] is True
    final = result.trace_rows[-1]
    assert 0.0 <= final["eps.battery_soc"] <= 1.0
    assert 0.0 <= final["data.storage_bits"] <= final["data.storage_capacity_bits"]
    assert final["thermal.payload_temp_k"] > 0.0
    manifest = result.metadata["runtime_manifest"]
    assert manifest["excluded_runtime_categories"] == ["external_solver", "curve_only_proxy"]
    assert "battery.batPowerOutMsg->project_power_data_mode_gate" in manifest["connections"]
    assert "project_power_data_mode_gate.payloadCmdOutMsg->payload_instrument" in manifest["connections"]
    assert "payload_instrument.nodeDataOutMsg->storage" in manifest["connections"]
    project_modules = set(result.metadata["model_source_boundary"]["basilisk_project_native"])
    assert {
        "project_eps_pdu",
        "project_power_data_mode_gate",
        "project_native_rf_downlink_gate",
        "thermal_network",
        "project_whole_parameter_event_controller",
    }.issubset(project_modules)
    assert result.summary["resource_feedback_closure_status"] == "PASS"
    assert result.summary["heater_pdu_power_feedback_observed"] is False
    assert set(result.metadata["model_source_boundary"]["external_or_proxy"]) == {
        "project_adcs_control_power_bridge",
        "project_payload_activity_power_bridge",
        "project_comm_activity_power_bridge",
        "project_heater_status_power_bridge",
    }


def test_low_soc_gate_changes_power_data_and_thermal_runtime_inputs() -> None:
    adapter = get_adapter_for_capability(WHOLE_ID)
    result = adapter.run(
        _short_spec(
            WHOLE_ID,
            values={"initial_soc": 0.05, "min_operational_soc": 0.20, "payload_max_pointing_error_deg": 180.0},
            duration_s=10.0,
        )
    )
    # Ignore the initialization sample; all runtime samples remain below the threshold.
    active_rows = result.trace_rows[1:]
    assert active_rows
    assert all(row["payload.active"] == 0 for row in active_rows)
    assert all(row["comm.active"] == 0 for row in active_rows)
    assert all(row["payload.generated_bps"] == pytest.approx(0.0) for row in active_rows)
    assert all(row["comm.downlink_bps"] == pytest.approx(0.0) for row in active_rows)
    assert result.trace_rows[-1]["data.storage_bits"] == pytest.approx(0.0)


def test_backend_contract_rejects_python_for_unified_native_runtime() -> None:
    adapter = get_adapter_for_capability(WHOLE_ID)
    spec = _short_spec(WHOLE_ID)
    spec["simulation"]["backend"] = "python"
    issues = adapter.validate(spec)
    assert any(issue.code == "backend" and issue.severity == "error" for issue in issues)
