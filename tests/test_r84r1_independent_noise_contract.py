"""R84-R1 preregistration, independent-seed and raw-IMU analysis contracts."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.run_r84r1_independent_noise_observability import (
    cliffs_delta,
    extract_sensor_feature,
    iter_unique_run_specs,
    seed_for,
    validate_preregistered_run_plan,
    wasserstein_1_equal_n,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "research" / "r84r1_independent_noise_observability_prereg.json"
SOURCE = ROOT / "src" / "sat_sim" / "experiments" / "native_imu_rw_observability_r84r1.py"
RUNNER = ROOT / "scripts" / "run_r84r1_independent_noise_observability.py"


def preregistration() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_r84r1_protocol_is_frozen_and_research_only():
    config = preregistration()
    assert config["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert config["research_only"] is True
    assert config["frozen_physics_design"]["physical_context_count"] == 27
    assert config["frozen_physics_design"]["reference_modes_deg"] == {
        "zero": 0.0, "pulse05": 0.5, "pulse09": 0.9
    }
    assert config["frozen_physics_design"]["actuator_capacity_nm"] == {
        "healthy": 0.02, "loss50": 0.01, "loss75": 0.005
    }
    assert config["frozen_physics_design"]["noise_sigma_rad_s"] == {
        "low": 0.00005, "primary": 0.0001, "high": 0.0002
    }
    assert config["frozen_replication_design"]["unique_native_runs"] == 3159
    assert config["frozen_replication_design"]["total_native_executions"] == 3160
    assert "no_training" in config["prohibitions"]
    assert "no_stable_merge" in config["prohibitions"]


def test_original_r83m_and_r84r0_source_provenance_are_sha_pinned():
    config = preregistration()
    assert config["r0_reference"]["workflow_artifact_id"] == 13497015497
    assert config["r0_reference"]["workflow_artifact_sha256"] == (
        "5d1439272f651519fec011d9728165f3afc46b1b3d14d305357ae271b26752af"
    )
    assert config["r0_reference"]["original_r83m_archive_sha256"] == (
        "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
    )
    engine = config["fixed_native_engine"]
    assert engine["distribution"] == "bsk==2.11.0+satfix2"
    assert engine["proxy_fallback_allowed"] is False
    assert engine["posthoc_noise_allowed"] is False
    assert engine["dynamics_substitution_allowed"] is False


def test_all_unique_run_specs_have_disjoint_independent_seeds_and_frozen_counts():
    config = preregistration()
    specs = validate_preregistered_run_plan(config)
    assert len(specs) == 3159
    seeds = [
        seed_for(
            item["cohort"], item["wheel"], item["initial"],
            item["reference_mode"], item["gyro_noise_std_rad_s"], item["repetition"]
        )
        for item in specs
    ]
    assert len(set(seeds)) == 3159
    assert min(seeds) > 0 and max(seeds) < 2**31
    assert sum(item["cohort"] == "healthy_calibration" for item in specs) == 540
    assert sum(item["cohort"] == "healthy_false_alarm_control" for item in specs) == 540
    assert sum(item["cohort"] == "loss50_primary" for item in specs) == 540
    assert sum(item["cohort"] == "loss75_primary" for item in specs) == 540
    assert sum(item["cohort"] == "loss50_noise_free_physics_reference" for item in specs) == 27


def test_healthy_calibration_controls_and_fault_cohorts_use_disjoint_seeds():
    specs = list(iter_unique_run_specs())
    healthy_calibration = {
        seed_for(x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
                 x["gyro_noise_std_rad_s"], x["repetition"])
        for x in specs if x["cohort"] == "healthy_calibration"
    }
    healthy_control = {
        seed_for(x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
                 x["gyro_noise_std_rad_s"], x["repetition"])
        for x in specs if x["cohort"] == "healthy_false_alarm_control"
    }
    fault = {
        seed_for(x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
                 x["gyro_noise_std_rad_s"], x["repetition"])
        for x in specs if x["capacity_mode"] in ("loss50", "loss75")
    }
    assert healthy_calibration.isdisjoint(healthy_control)
    assert healthy_calibration.isdisjoint(fault)
    assert healthy_control.isdisjoint(fault)


def test_feature_extractor_uses_only_fixed_raw_gyro_windows():
    rows = []
    for index in range(31):
        t = index * 0.2
        value = 1.0 if 2.6 - 1e-12 <= t <= 3.6 + 1e-12 else 0.0
        rows.append({
            "time_s": t,
            "imu.raw_gyro_rad_s_x": value,
            "imu.raw_gyro_rad_s_y": 0.0,
            "imu.raw_gyro_rad_s_z": 0.0,
        })
    result = extract_sensor_feature(rows)
    assert result["pre_window_rms_3d_rad_s"] == 0.0
    assert result["post_window_rms_3d_rad_s"] == 1.0
    assert result["delta_rms_3d_rad_s"] == 1.0
    assert set(result) == {
        "pre_window_rms_3d_rad_s",
        "post_window_rms_3d_rad_s",
        "delta_rms_3d_rad_s",
    }


def test_distribution_statistics_are_deterministic_and_signed():
    assert cliffs_delta([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert cliffs_delta([1.0, 2.0], [3.0, 4.0]) == -1.0
    assert cliffs_delta([1.0, 2.0], [1.0, 2.0]) == 0.0
    assert wasserstein_1_equal_n([0.0, 2.0], [1.0, 3.0]) == 1.0


def test_native_source_uses_official_imu_cpp_rw_and_observer_only_fsw():
    source = SOURCE.read_text(encoding="utf-8")
    assert "from Basilisk.simulation import" in source
    assert "imuSensor" in source
    assert "imu.sensorOutMsg.recorder(sample)" in source
    assert "imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)" in source
    assert "imu.PMatrixGyro = gyro_matrix" in source
    assert "imu.setAMatrixGyro(zero_matrix)" in source
    assert "imu.RNGSeed = seed" in source
    assert "rw.ReactionWheelData[wheel].u_max" in source
    assert "rw.rwOutMsgs[wheel].recorder(sample)" in source
    assert "simpleNav.SimpleNav()" in source
    assert "controller.attNavInMsg" not in source
    assert "numpy.random" not in source
    assert "np.random" not in source


def test_runner_keeps_sensor_observations_separate_from_qa_and_training():
    source = RUNNER.read_text(encoding="utf-8")
    assert "r84r1_sensor_observations.jsonl" in source
    assert "r84r1_case_manifest.jsonl" in source
    assert "r84r1_physics_qa.jsonl" in source
    assert "r84r1_features.jsonl" in source
    assert "r84r1_observability_analysis.json" in source
    assert "audit_archive(path)" in source
    assert "diagnostic_model_trained" in source
    assert "formal_training_or_test_eligible" in source
