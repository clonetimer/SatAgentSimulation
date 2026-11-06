"""R84 pilot is a frozen research protocol, not a claim of simulation execution."""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "configs" / "research" / "r84_native_imu_observability_pilot.json"


def protocol() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def test_pilot_is_preregistered_and_makes_no_execution_claim():
    data = protocol()
    assert data["schema_version"] == "sat-sim.r84.native-imu-observability-prereg.v1"
    assert data["status"] == "PREREGISTERED_NOT_EXECUTED"
    assert data["research_only"] is True
    assert "no_training" in data["prohibitions"]
    assert "no_stable_merge" in data["prohibitions"]


def test_pilot_has_exactly_eight_fixed_physical_sensor_arms():
    design = protocol()["frozen_pilot_design"]
    arms = list(product(design["reference_modes"], design["actuator_caps_nm"], design["gyro_white_noise_std_rad_s"]))
    assert len(arms) == design["case_count"] == 8
    assert set(design["reference_modes"].values()) == {0.0, 0.9}
    assert design["actuator_caps_nm"] == {"healthy": 0.02, "loss75": 0.005}
    assert design["sampling_s"] == 0.2 and design["duration_s"] == 6.0
    assert design["expected_samples_per_case"] == 31
    assert design["unique_arm_count"] == 8
    assert design["total_native_executions"] == 9
    assert design["repeatability_control"]["repetitions"] == 1
    assert design["repeatability_control"]["seed"] == design["noise_seed"]
    assert design["fault_mutation_paused_scheduler_s"] == 2.4
    assert design["first_post_fault_native_sample_s"] == 2.6


def test_native_sensor_must_be_official_and_observer_only():
    engine = protocol()["fixed_native_engine"]
    assert engine["distribution"] == "bsk==2.11.0+satfix1"
    assert engine["original_wheel_sha256"] == "d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a"
    assert engine["compatibility_metadata_patch"].startswith("satfix1;")
    assert "imuSensor.ImuSensor" in engine["required_modules"]
    assert engine["sensor_source"] == "imuSensor.sensorOutMsg.AngVelPlatform"
    assert engine["proxy_fallback_allowed"] is False
    assert engine["kinematic_or_posthoc_imu_substitution_allowed"] is False
    assert "observer-only" in engine["controller_feedback_source"]


def test_pre_registered_sensor_noise_seed_is_fixed():
    d = protocol()["frozen_pilot_design"]
    assert d["gyro_white_noise_std_rad_s"] == [0.0, 0.0001]
    assert d["noise_seed"] == 331
    assert d["imu_sensor_position_body_m"] == [0.0, 0.0, 0.0]
    assert d["imu_platform_dcm_euler321_rad"] == [0.0, 0.0, 0.0]


def test_no_truth_or_qa_leak_into_approved_observation_fields():
    d = protocol()
    fields = d["approved_observation_fields"]
    assert fields == [f"imu.raw_gyro_rad_s_{axis}" for axis in "xyz"]
    assert not any(any(f.startswith(prefix) for prefix in d["forbidden_inference_feature_prefixes"]) for f in fields)
    assert all("QA_ONLY" not in f for f in fields)
    assert "no_feature_exports_contain_truth_or_QA_ONLY" in d["success_checks"]


def test_r83m_reference_is_bound_to_original_archive_hash():
    ref = protocol()["r83m_reference"]
    assert ref["original_artifact_id"] == 12586915899
    assert ref["original_archive_sha256"] == "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
