"""R84-R3A multi-channel native telemetry pilot contracts."""
from __future__ import annotations

import json
from pathlib import Path

from sat_sim.experiments.native_imu_rw_multichannel_pilot_r84r3 import (
    OBSERVATION_FIELDS,
    QA_PHYSICAL_FIELDS,
    TELEMETRY_FIELDS,
)
from sat_sim.experiments.r84r3_multichannel_pilot_runner import (
    CONFIG_PATH,
    TELEMETRY_KEYS,
    max_delta,
    specs,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/sat_sim/experiments/native_imu_rw_multichannel_pilot_r84r3.py"
RUNNER = ROOT / "src/sat_sim/experiments/r84r3_multichannel_pilot_runner.py"


def test_preregistered_pilot_has_twelve_unique_arms_and_one_replay():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert config["physical_design"]["unique_arm_count"] == 12
    assert config["physical_design"]["total_native_executions"] == 13
    planned = specs()
    assert len(planned) == 12
    assert len({item["seed"] for item in planned}) == 12
    assert all(0 < item["seed"] < 2**31 for item in planned)


def test_telemetry_allowlist_contains_only_official_imu_rw_speed_commands_and_pointing():
    assert len(OBSERVATION_FIELDS) == 3
    assert len(TELEMETRY_FIELDS) == 10
    assert TELEMETRY_KEYS == {"time_s", *TELEMETRY_FIELDS}
    assert all(f"sensor.adcs.rw.speed_rad_s_{i}" in TELEMETRY_FIELDS for i in range(3))
    assert all(f"command.adcs.rw.motor_torque_nm_{i}" in TELEMETRY_FIELDS for i in range(3))
    assert "estimate.adcs.pointing_error_deg" in TELEMETRY_FIELDS
    assert not any("QA_ONLY" in key or key.startswith("hub.") for key in TELEMETRY_FIELDS)
    assert not any("motor_current" in key or "actual_torque" in key or "effective_max_torque" in key for key in TELEMETRY_FIELDS)


def test_actual_torque_and_cpp_capacity_are_separate_physics_qa_only():
    assert "rw.native_target_applied_torque_nm_QA_ONLY" in QA_PHYSICAL_FIELDS
    assert "hub.omega_bn_b_rad_s_0" in QA_PHYSICAL_FIELDS
    assert not set(QA_PHYSICAL_FIELDS).intersection(TELEMETRY_FIELDS)


def test_native_source_records_official_imu_rw_speed_fsw_command_and_attitude_error():
    source = SOURCE.read_text(encoding="utf-8")
    assert "imu.sensorOutMsg.recorder(sample)" in source
    assert "rw.rwSpeedOutMsg.recorder(sample)" in source
    assert "motor_rec = motor.rwMotorTorqueOutMsg.recorder(sample)" in source
    assert "tracking_rec = tracking_error.attGuidOutMsg.recorder(sample)" in source
    assert "tracking_rec.sigma_BR[index]" in source
    assert "imu.PMatrixGyro = gyro_matrix" in source
    assert "imu.setAMatrixGyro(zero_matrix)" in source
    assert "imu.RNGSeed = seed" in source
    assert "np.random" not in source and "numpy.random" not in source


def test_runner_cross_checks_all_model_input_channels_against_native_physics_qa():
    source = RUNNER.read_text(encoding="utf-8")
    assert "R84R3_RW_SPEED_PROVENANCE_MISMATCH" in source
    assert "R84R3_RW_COMMAND_PROVENANCE_MISMATCH" in source
    assert "R84R3_POINTING_ERROR_PROVENANCE_MISMATCH" in source
    assert "R84R3_CPP_CAPACITY_READBACK_MISMATCH" in source
    assert "R84R3_SEEDED_REPLAY_NOT_EXACT" in source
    assert "R84R3_IMU_NOISE_CHANGED_NON_IMU_CHANNELS" in source


def test_noise_a_b_only_changes_imu_and_replay_delta_helper_is_deterministic():
    left=[{"time_s":0.0,"a":1.0,"b":2.0},{"time_s":0.2,"a":2.0,"b":3.0}]
    right=[{"time_s":0.0,"a":1.0,"b":2.0},{"time_s":0.2,"a":2.0,"b":3.0}]
    assert max_delta(left,right,("a","b")) == {"a":0.0,"b":0.0}
