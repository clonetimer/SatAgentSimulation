"""Static contracts for the R84 native observer-only implementation."""
from __future__ import annotations

from pathlib import Path

from sat_sim.experiments import native_imu_rw_observability_r84 as r84

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_r84_native_imu_observability.py"


def test_native_imu_is_official_and_observer_only():
    source = Path(r84.__file__).read_text(encoding="utf-8")
    assert "from Basilisk.simulation import" in source
    assert "imuSensor" in source
    assert "imu.sensorOutMsg.recorder(sample)" in source
    assert "imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)" in source
    assert "simpleNav.SimpleNav()" in source
    assert "controller.attNavInMsg" not in source
    assert "imu.angVel" not in source
    assert "np.random" not in source
    assert "numpy.random" not in source


def test_native_imu_uses_official_white_noise_and_seeded_replay():
    source = Path(r84.__file__).read_text(encoding="utf-8")
    assert "imu.PMatrixGyro = gyro_matrix" in source
    assert "imu.setAMatrixGyro(zero_matrix)" in source
    assert "imu.RNGSeed = seed" in source
    assert r84.NOISE_SEED == 331
    assert r84.NOISE_SIGMAS == (0.0, 0.0001)


def test_fault_is_a_real_cpp_capacity_change_at_paused_scheduler_boundary():
    source = Path(r84.__file__).read_text(encoding="utf-8")
    assert "target.u_max = requested_cap" in source
    assert "rw.ReactionWheelData[WHEEL].u_max" in source
    assert "rw.rwOutMsgs[WHEEL].recorder(sample)" in source
    assert r84.FAULT_PAUSE_S == 2.4
    assert r84.FIRST_POST_FAULT_SAMPLE_S == 2.6
    assert r84.NOMINAL_CAP_NM == 0.02
    assert r84.FAULT_CAP_NM == 0.005


def test_raw_sensor_observation_schema_has_no_simulator_truth_or_qa_fields():
    assert r84.OBSERVATION_FIELDS == (
        "imu.raw_gyro_rad_s_x",
        "imu.raw_gyro_rad_s_y",
        "imu.raw_gyro_rad_s_z",
    )
    assert all("QA_ONLY" not in key and not key.startswith("hub.") for key in r84.OBSERVATION_FIELDS)
    assert all("fault" not in key for key in r84.OBSERVATION_FIELDS)


def test_r83m_physics_fields_are_separated_into_qa_sidecar():
    assert "rw.native_target_applied_torque_nm_QA_ONLY" in r84.QA_PHYSICAL_FIELDS
    assert "hub.omega_bn_b_rad_s_0" in r84.QA_PHYSICAL_FIELDS
    assert not set(r84.QA_PHYSICAL_FIELDS).intersection(r84.OBSERVATION_FIELDS)


def test_runner_requires_original_r83m_archive_and_separates_artifacts():
    source = RUNNER.read_text(encoding="utf-8")
    assert "--r83m-archive" in source
    assert "audit_archive(original_archive)" in source
    assert "r84_sensor_observations.json" in source
    assert "r84_case_manifest.json" in source
    assert "r84_physics_qa.json" in source
    assert "formal_training_or_test_eligible" in source
    assert "diagnostic_model_trained" in source
