from __future__ import annotations

from sat_sim.form_schema import capability_form_schema


CAPABILITY_ID = "subsystem.adcs_unified_native.v1"


def test_unified_native_adcs_guided_event_catalog_is_exact_physical_gate_allowlist() -> None:
    schema = capability_form_schema(CAPABILITY_ID)
    faults = {item["effect"]: item for item in schema["event_catalog"]["faults"]}
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}

    assert set(faults) == {"adcs_rw_jamming", "adcs_rw_motor_failure", "gyro_bias_step"}
    assert set(degradations) == {"adcs_rw_torque_authority_loss", "gyro_noise_increase"}
    assert schema["event_catalog"]["constraints"] == []

    jamming = faults["adcs_rw_jamming"]
    assert jamming["magnitude_applicable"] is False
    assert {field["name"] for field in jamming["parameter_fields"]} == {"wheel_index", "brake_torque_nm"}
    assert jamming["default_event"]["parameters"]["brake_torque_nm"] == 0.2

    motor_failure = faults["adcs_rw_motor_failure"]
    assert {field["name"] for field in motor_failure["parameter_fields"]} == {"wheel_index"}
    assert "torque_scale" not in motor_failure["default_event"]["parameters"]

    gyro_bias = faults["gyro_bias_step"]
    assert {field["name"] for field in gyro_bias["parameter_fields"]} == {"bias_step_deg_s"}
    assert gyro_bias["default_event"]["parameters"]["bias_step_deg_s"] == [0.2, 0.0, 0.0]

    torque_loss = degradations["adcs_rw_torque_authority_loss"]
    assert {field["name"] for field in torque_loss["parameter_fields"]} == {"wheel_index", "torque_scale"}
    assert torque_loss["default_event"]["parameters"]["torque_scale"] == 0.5

    gyro_noise = degradations["gyro_noise_increase"]
    assert {field["name"] for field in gyro_noise["parameter_fields"]} == {"noise_scale"}
    assert gyro_noise["default_event"]["parameters"]["noise_scale"] == 10.0


def test_adcs_event_allowlist_does_not_hide_reference_model_catalog() -> None:
    reference = capability_form_schema("subsystem.adcs_fidelity.v1")
    reference_degradations = {item["effect"] for item in reference["event_catalog"]["degradations"]}
    assert "adcs_rw_friction_increase" in reference_degradations
