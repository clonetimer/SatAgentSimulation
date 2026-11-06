"""Contract tests for the retrospective R84-R2-E0 raw-IMU feature screen."""
from __future__ import annotations

import pytest

from scripts.audit_r84r2_raw_imu_feature_screen import (
    ARTIFACT_SHA256,
    FEATURES,
    R83M_SHA256,
    R0_SHA256,
    ScreenError,
    extract_features,
    nearest_rank,
    screen_artifact,
)


def synthetic_sensor_rows():
    rows=[]
    for i in range(31):
        t=i*0.2
        active=1.0 if 2.6-1e-12 <= t <= 3.6+1e-12 else 0.0
        rows.append({
            "time_s":t,
            "imu.raw_gyro_rad_s_x":active,
            "imu.raw_gyro_rad_s_y":0.0,
            "imu.raw_gyro_rad_s_z":0.0,
        })
    return rows


def test_fixed_feature_family_uses_only_three_official_raw_gyro_axes():
    features=extract_features(synthetic_sensor_rows())
    assert tuple(features)==FEATURES
    assert features["delta_mean_x_rad_s"]==1.0
    assert features["delta_std_x_rad_s"]==0.0
    assert features["delta_rms_3d_rad_s"]==1.0
    assert all(value==0.0 for name,value in features.items() if name!="delta_mean_x_rad_s" and name!="delta_rms_3d_rad_s")


def test_sensor_feature_extractor_rejects_truth_or_qa_leakage():
    rows=synthetic_sensor_rows()
    rows[0]["hub.omega_bn_b_rad_s_0"]=0.0
    with pytest.raises(ScreenError,match="SENSOR_WHITELIST"):
        extract_features(rows)


def test_sensor_feature_extractor_rejects_wrong_time_grid():
    rows=synthetic_sensor_rows()
    rows[8]["time_s"]=1.7
    with pytest.raises(ScreenError,match="SENSOR_TIME_GRID"):
        extract_features(rows)


def test_nearest_rank_is_frozen_and_deterministic():
    assert nearest_rank(list(range(1,21)),0.90)==18
    assert nearest_rank([4.0,1.0,3.0,2.0],0.50)==2.0


def test_artifact_provenance_is_fixed_and_not_a_new_confirmation():
    assert len(ARTIFACT_SHA256)==64
    assert len(R83M_SHA256)==64
    assert len(R0_SHA256)==64
    assert "EXPLORATORY_RETROSPECTIVE_ONLY" in open(
        __import__("scripts.audit_r84r2_raw_imu_feature_screen",fromlist=["SCHEMA"]).__file__,
        encoding="utf-8"
    ).read()


def test_non_original_artifact_is_rejected_before_analysis(tmp_path):
    path=tmp_path/"not-the-original.zip"
    path.write_bytes(b"synthetic data is not the R84-R1 source artifact")
    with pytest.raises(ScreenError,match="R84R1_ARTIFACT_SHA256_MISMATCH"):
        screen_artifact(path)
