"""R84-R2 frozen design, multi-pulse features and native evidence contracts."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.run_r84r2_multipulse_multifeature_observability import (
    FEATURE_FIELDS,
    _nearest_rank,
    _robust_parameters,
    _composite_score,
    cliffs_delta,
    extract_multifeatures,
    iter_unique_run_specs,
    seed_for,
    validate_preregistered_run_plan,
    wasserstein_1_equal_n,
)
from sat_sim.experiments.native_imu_rw_observability_r84r2 import (
    DURATION_S,
    PULSE_SCHEDULE,
    STEP_S,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "research" / "r84r2_multipulse_multifeature_observability_prereg.json"
SOURCE = ROOT / "src" / "sat_sim" / "experiments" / "native_imu_rw_observability_r84r2.py"
RUNNER = ROOT / "scripts" / "run_r84r2_multipulse_multifeature_observability.py"


def preregistration() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_r84r2_protocol_is_frozen_and_research_only():
    config = preregistration()
    assert config["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert config["research_only"] is True
    assert config["frozen_physics_design"]["duration_s"] == 18.0
    assert config["frozen_physics_design"]["sampling_interval_s"] == 0.2
    assert config["frozen_physics_design"]["expected_samples_per_execution"] == 91
    assert config["frozen_physics_design"]["physical_context_count"] == 18
    assert config["frozen_replication_design"]["unique_native_runs"] == 2862
    assert config["frozen_replication_design"]["total_native_executions"] == 2863
    assert len(config["frozen_multifeature_analysis"]["feature_family"]) == 6
    assert "no_training" in config["prohibitions"]
    assert "no_stable_merge" in config["prohibitions"]


def test_r84r1_r84r0_and_original_r83m_sources_are_sha_pinned():
    refs = preregistration()["references"]
    assert refs["r84r1_artifact_id"] == 13498019393
    assert refs["r84r1_artifact_sha256"] == (
        "4bc1f9d0f788dce5e8d33aace1fe8860d739158793bcf6fae28352c785742a8e"
    )
    assert refs["r84r0_artifact_id"] == 13497015497
    assert refs["r84r0_artifact_sha256"] == (
        "5d1439272f651519fec011d9728165f3afc46b1b3d14d305357ae271b26752af"
    )
    assert refs["original_r83m_archive_sha256"] == (
        "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
    )
    engine = preregistration()["fixed_native_engine"]
    assert engine["distribution"] == "bsk==2.11.0+satfix2"
    assert engine["proxy_or_python_dynamics_allowed"] is False
    assert engine["posthoc_noise_allowed"] is False


def test_four_pulse_schedule_preserves_original_first_six_seconds():
    assert DURATION_S == 18.0
    assert STEP_S == 0.2
    assert PULSE_SCHEDULE == (
        (1.8, 0.9, 1),
        (3.8, 0.0, 1),
        (6.2, -0.9, 2),
        (8.2, 0.0, 2),
        (10.6, 0.9, 3),
        (12.6, 0.0, 3),
        (15.0, -0.9, 4),
        (17.0, 0.0, 4),
    )
    assert all(t > 6.0 for t, _, _ in PULSE_SCHEDULE[2:])


def test_all_2862_run_specs_have_collision_free_seeds_and_exact_cohort_counts():
    config = preregistration()
    specs = validate_preregistered_run_plan(config)
    assert len(specs) == 2862
    seeds = [
        seed_for(
            x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
            x["capacity_mode"], x["gyro_noise_std_rad_s"], x["repetition"]
        )
        for x in specs
    ]
    assert len(set(seeds)) == 2862
    assert min(seeds) > 0 and max(seeds) < 2**31
    from collections import Counter
    counts = Counter(x["cohort"] for x in specs)
    assert counts == {
        "noise_free_physics_reference": 54,
        "healthy_calibration": 540,
        "healthy_false_alarm_control": 540,
        "loss50_primary": 540,
        "loss75_primary": 540,
        "healthy_sensitivity": 216,
        "loss50_sensitivity": 216,
        "loss75_sensitivity": 216,
    }


def test_capacity_is_part_of_seed_and_prevents_physical_reference_collisions():
    args = ("noise_free_physics_reference", 1, 2, "pulse09x4")
    seeds = {
        seed_for(*args, capacity, 0.0, 0)
        for capacity in ("healthy", "loss50", "loss75")
    }
    assert len(seeds) == 3


def test_healthy_calibration_control_and_fault_seed_sets_are_disjoint():
    specs = list(iter_unique_run_specs())
    def seeds_for(predicate):
        return {
            seed_for(
                x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
                x["capacity_mode"], x["gyro_noise_std_rad_s"], x["repetition"]
            )
            for x in specs if predicate(x)
        }
    calibration = seeds_for(lambda x: x["cohort"] == "healthy_calibration")
    control = seeds_for(lambda x: x["cohort"] == "healthy_false_alarm_control")
    fault = seeds_for(lambda x: x["capacity_mode"] in ("loss50", "loss75")
                      and x["cohort"] != "noise_free_physics_reference")
    assert calibration.isdisjoint(control)
    assert calibration.isdisjoint(fault)
    assert control.isdisjoint(fault)


def test_multifeature_extractor_uses_four_polarity_aligned_raw_gyro_windows():
    rows = []
    pulse_windows = (
        (1, 1.0, (0.4, 1.6), (2.6, 3.6)),
        (2, -1.0, (4.8, 6.0), (7.0, 8.0)),
        (3, 1.0, (9.2, 10.4), (11.4, 12.4)),
        (4, -1.0, (13.6, 14.8), (15.8, 16.8)),
    )
    for index in range(91):
        t = index * 0.2
        x = 0.0
        for _, polarity, _, post in pulse_windows:
            if post[0] - 1e-10 <= t <= post[1] + 1e-10:
                x = polarity
        rows.append({
            "time_s": t,
            "imu.raw_gyro_rad_s_x": x,
            "imu.raw_gyro_rad_s_y": 0.0,
            "imu.raw_gyro_rad_s_z": 0.0,
        })
    features = extract_multifeatures(rows)
    assert set(features) == set(FEATURE_FIELDS)
    assert len(features) == 16
    assert features["aligned_mean_response_x"] == 1.0
    assert features["mean_abs_pulse_mean_delta_x"] == 1.0
    assert features["mean_rms_delta_x"] == 1.0
    assert features["mean_vector_rms_delta"] == 1.0
    assert all(features[f"aligned_mean_response_{axis}"] == 0.0 for axis in "yz")


def test_robust_multifeature_score_and_empirical_effect_statistics_are_deterministic():
    calibration = [
        {field: float(i) * (j + 1) for j, field in enumerate(FEATURE_FIELDS)}
        for i in range(30)
    ]
    centers, scales = _robust_parameters(calibration)
    assert set(centers) == set(scales) == set(FEATURE_FIELDS)
    assert all(value > 0 for value in scales.values())
    score = _composite_score(calibration[10], centers, scales)
    assert score >= 0.0
    assert _nearest_rank(list(range(1, 31)), 0.90) == 27
    assert cliffs_delta([3.0, 4.0], [1.0, 2.0]) == 1.0
    assert wasserstein_1_equal_n([0.0, 2.0], [1.0, 3.0]) == 1.0


def test_native_source_uses_official_imu_and_real_cpp_rw_capacity_without_feedback_rewire():
    source = SOURCE.read_text(encoding="utf-8")
    assert "from Basilisk.simulation import" in source
    assert "imuSensor" in source
    assert "imu.sensorOutMsg.recorder(sample)" in source
    assert "imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)" in source
    assert "imu.PMatrixGyro" in source
    assert "imu.setAMatrixGyro(zero_matrix)" in source
    assert "imu.RNGSeed = seed" in source
    assert "rw.ReactionWheelData[wheel].u_max" in source
    assert "rw.rwOutMsgs[wheel].recorder(sample)" in source
    assert "simpleNav.SimpleNav()" in source
    assert "controller.attNavInMsg" not in source
    assert "np.random" not in source
    assert "numpy.random" not in source


def test_runner_preserves_raw_sensor_qa_and_source_archive_separation():
    source = RUNNER.read_text(encoding="utf-8")
    assert "r84r2_sensor_observations.jsonl" in source
    assert "r84r2_case_manifest.jsonl" in source
    assert "r84r2_physics_qa.jsonl" in source
    assert "r84r2_features.jsonl" in source
    assert "r84r2_multifeature_observability_analysis.json" in source
    assert "audit_archive(r83m_archive)" in source
    assert "R84R2_R84R1_REFERENCE_ARTIFACT_SHA256_MISMATCH" in source
    assert "r83m_first_six_second_comparison_count" in source
    assert "formal_training_or_test_eligible" in source
    assert "diagnostic_model_trained" in source
