"""Execute and audit the preregistered R84-R0 native Basilisk IMU pilot.

The original R83m artifact is a mandatory SHA-pinned physical baseline. The
script emits sensor observations separately from condition labels and private
physics QA. It never creates a classifier dataset or trains a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any
from zipfile import ZipFile

from sat_sim.experiments.native_imu_rw_observability_r84 import (
    CAPACITY_MODES,
    FIRST_POST_FAULT_SAMPLE_S,
    NOISE_SEED,
    NOISE_SIGMAS,
    OBSERVATION_FIELDS,
    QA_PHYSICAL_FIELDS,
    REFERENCE_MODES,
    run_native_case,
)
from scripts.audit_r83m_native_archive import audit_archive

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "research" / "r84_native_imu_observability_pilot.json"
R83M_CASE_PREFIX = "rw0_initial0_"
PHYSICAL_MATCH_TOLERANCE = 1e-8
PAIR_PREFault_TOLERANCE = 1e-12
REPLAY_TOLERANCE = 1e-12


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _max_abs_delta(left: list[dict[str, float]], right: list[dict[str, float]], fields: tuple[str, ...]) -> dict[str, float]:
    if len(left) != len(right):
        raise RuntimeError("R84_PAIR_SAMPLE_COUNT_MISMATCH")
    maxima = {field: 0.0 for field in fields}
    for a, b in zip(left, right):
        if abs(float(a["time_s"]) - float(b["time_s"])) > 1e-12:
            raise RuntimeError("R84_PAIR_TIME_AXIS_MISMATCH")
        for field in fields:
            maxima[field] = max(maxima[field], abs(float(a[field]) - float(b[field])))
    return maxima


def _max_delta_value(values: dict[str, float]) -> float:
    return max(values.values(), default=0.0)


def _load_original_r83m(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = audit_archive(path)
    with ZipFile(path, "r") as archive:
        payload = json.loads(archive.read("r83m_genuine_native_54_original.json"))
    if receipt.get("physics_archive_verified") is not True:
        raise RuntimeError("R84_R83M_ORIGINAL_PHYSICS_ARCHIVE_NOT_VERIFIED")
    return receipt, payload


def _write_partial(output_dir: Path, sensor_records: list[dict[str, Any]], case_manifest: list[dict[str, Any]], qa_records: list[dict[str, Any]]) -> None:
    _write_json(output_dir / "r84_partial_sensor_observations.json", {
        "schema_version": "sat-sim.r84.native-imu-sensor-observations.partial.v1",
        "approved_fields": list(OBSERVATION_FIELDS),
        "records": sensor_records,
    })
    _write_json(output_dir / "r84_partial_case_manifest.json", {
        "schema_version": "sat-sim.r84.native-imu-case-manifest.partial.v1",
        "records": case_manifest,
    })
    _write_json(output_dir / "r84_partial_physics_qa.json", {
        "schema_version": "sat-sim.r84.native-imu-physics-qa.partial.v1",
        "records": qa_records,
    })


def run_pilot(original_archive: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    design = config["frozen_pilot_design"]
    if config.get("status") != "PREREGISTERED_NOT_EXECUTED":
        raise RuntimeError("R84_PREREGISTRATION_STATUS_INVALID")
    if design.get("case_count") != 8 or design.get("unique_arm_count") != 8:
        raise RuntimeError("R84_FROZEN_UNIQUE_ARM_COUNT_CHANGED")
    if design.get("total_native_executions") != 9:
        raise RuntimeError("R84_FROZEN_EXECUTION_COUNT_CHANGED")
    if config["fixed_native_engine"]["distribution"] != "bsk==2.11.0+satfix1":
        raise RuntimeError("R84_FROZEN_BASILISK_DISTRIBUTION_CHANGED")

    original_receipt, original_payload = _load_original_r83m(original_archive)
    if original_receipt["archive_sha256"] != config["r83m_reference"]["original_archive_sha256"]:
        raise RuntimeError("R84_R83M_ARCHIVE_DOES_NOT_MATCH_PREREGISTRATION")

    output_dir.mkdir(parents=True, exist_ok=True)
    sensor_records: list[dict[str, Any]] = []
    case_manifest: list[dict[str, Any]] = []
    qa_records: list[dict[str, Any]] = []
    executed: dict[tuple[str, str, float], dict[str, Any]] = {}
    source_comparisons: list[dict[str, Any]] = []

    record_number = 0
    for reference_mode in REFERENCE_MODES:
        for capacity_mode in CAPACITY_MODES:
            for noise_sigma in NOISE_SIGMAS:
                record_number += 1
                record_id = f"r84_obs_{record_number:02d}"
                result = run_native_case(
                    reference_mode=reference_mode,
                    capacity_mode=capacity_mode,
                    gyro_noise_std_rad_s=noise_sigma,
                    seed=NOISE_SEED,
                )
                sensor_rows = result.pop("sensor_rows")
                qa_rows = result.pop("qa_rows")

                # The observation artifact contains ONLY time and official
                # native gyro measurements. Condition labels and QA are in
                # separate files and are never projected into model features.
                expected_keys = {"time_s", *OBSERVATION_FIELDS}
                if any(set(row) != expected_keys for row in sensor_rows):
                    raise RuntimeError("R84_SENSOR_FEATURE_WHITELIST_VIOLATION")
                if any(any("QA_ONLY" in key or key.startswith(("hub.", "fault_", "source_fault_")) for key in row) for row in sensor_rows):
                    raise RuntimeError("R84_SENSOR_FEATURE_TRUTH_LEAKAGE")

                source_case_id = R83M_CASE_PREFIX + reference_mode + "_" + capacity_mode
                source_case = original_payload["physical_source_cases"][source_case_id]
                source_rows = source_case["rows"]
                if len(source_rows) != len(qa_rows):
                    raise RuntimeError("R84_R83M_BASELINE_SAMPLE_COUNT_MISMATCH")
                physical_deltas = _max_abs_delta(qa_rows, source_rows, QA_PHYSICAL_FIELDS)
                max_physical_delta = _max_delta_value(physical_deltas)
                baseline_pass = max_physical_delta <= PHYSICAL_MATCH_TOLERANCE
                source_comparisons.append({
                    "record_id": record_id,
                    "r83m_source_case_id": source_case_id,
                    "max_abs_delta_by_physical_field": physical_deltas,
                    "max_abs_delta": max_physical_delta,
                    "tolerance": PHYSICAL_MATCH_TOLERANCE,
                    "observer_did_not_change_plant_or_fsw": baseline_pass,
                })
                if not baseline_pass:
                    raise RuntimeError(f"R84_OBSERVER_CHANGED_R83M_PHYSICS:{record_id}:{max_physical_delta}")

                sensor_records.append({"record_id": record_id, "rows": sensor_rows})
                case_manifest.append({
                    "record_id": record_id,
                    "case_id": result["case_id"],
                    "reference_mode": reference_mode,
                    "reference_angle_deg": result["reference_angle_deg"],
                    "capacity_mode_QA_ONLY": capacity_mode,
                    "gyro_white_noise_std_rad_s": noise_sigma,
                    "rng_seed": NOISE_SEED,
                    "native_distribution": result["distribution"],
                    "native_module_chain": result["native_module_chain"],
                    "controller_feedback_source": result["controller_feedback_source"],
                    "event_receipts": result["native_event_receipts"],
                    "sample_count": len(sensor_rows),
                })
                qa_records.append({
                    "record_id": record_id,
                    "case_id": result["case_id"],
                    "reference_mode": reference_mode,
                    "capacity_mode_QA_ONLY": capacity_mode,
                    "gyro_white_noise_std_rad_s": noise_sigma,
                    "rng_seed": NOISE_SEED,
                    "qa_rows": qa_rows,
                    "native_receipts": result["native_event_receipts"],
                    "native_envelopes": {
                        "max_abs_allocator_request_nm": result["max_abs_allocator_request_nm"],
                        "max_abs_wheel_speed_rad_s": result["max_abs_wheel_speed_rad_s"],
                        "max_abs_body_rate_rad_s": result["max_abs_body_rate_rad_s"],
                        "max_post_fault_applied_torque_abs_nm_QA_ONLY": result["max_post_fault_applied_torque_abs_nm_QA_ONLY"],
                    },
                })
                executed[(reference_mode, capacity_mode, noise_sigma)] = {
                    "record_id": record_id,
                    "sensor_rows": sensor_rows,
                    "qa_rows": qa_rows,
                    "result": result,
                }
                _write_partial(output_dir, sensor_records, case_manifest, qa_records)

    # One exact repeat of the preregistered noisy pulse09/faulted arm is a
    # deterministic replay QA control, not a ninth unique experimental arm.
    replay_key = ("pulse09", "loss75", 0.0001)
    replay_reference = executed[replay_key]
    replay_result = run_native_case(
        reference_mode=replay_key[0],
        capacity_mode=replay_key[1],
        gyro_noise_std_rad_s=replay_key[2],
        seed=NOISE_SEED,
    )
    replay_sensor = replay_result.pop("sensor_rows")
    replay_qa = replay_result.pop("qa_rows")
    replay_sensor_delta = _max_abs_delta(replay_reference["sensor_rows"], replay_sensor, OBSERVATION_FIELDS)
    replay_qa_delta = _max_abs_delta(replay_reference["qa_rows"], replay_qa, QA_PHYSICAL_FIELDS)
    replay_max_sensor_delta = _max_delta_value(replay_sensor_delta)
    replay_max_qa_delta = _max_delta_value(replay_qa_delta)
    replay_pass = replay_max_sensor_delta <= REPLAY_TOLERANCE and replay_max_qa_delta <= REPLAY_TOLERANCE
    if not replay_pass:
        raise RuntimeError("R84_NATIVE_IMU_SEEDED_REPLAY_NOT_DETERMINISTIC")
    replay_id = "r84_replay_seeded_pulse09_loss75_noise_on"
    sensor_records.append({"record_id": replay_id, "rows": replay_sensor})
    case_manifest.append({
        "record_id": replay_id,
        "case_id": replay_result["case_id"],
        "repeatability_control": True,
        "replay_of_record_id": replay_reference["record_id"],
        "reference_mode": replay_key[0],
        "capacity_mode_QA_ONLY": replay_key[1],
        "gyro_white_noise_std_rad_s": replay_key[2],
        "rng_seed": NOISE_SEED,
        "sample_count": len(replay_sensor),
    })
    qa_records.append({
        "record_id": replay_id,
        "repeatability_control": True,
        "replay_of_record_id": replay_reference["record_id"],
        "qa_rows": replay_qa,
    })

    # Paired physical fault effects are evaluated only in QA. The deployment
    # feature file remains a single-sensor stream and never contains a healthy
    # twin, fault label, fault timestamp, hub truth or applied motor torque.
    healthy_fault_pairs: list[dict[str, Any]] = []
    for reference_mode in REFERENCE_MODES:
        for noise_sigma in NOISE_SIGMAS:
            healthy = executed[(reference_mode, "healthy", noise_sigma)]
            faulted = executed[(reference_mode, "loss75", noise_sigma)]
            pre_indices = [i for i, row in enumerate(healthy["sensor_rows"])
                           if row["time_s"] <= 2.4 + 1e-12]
            post_indices = [i for i, row in enumerate(healthy["sensor_rows"])
                            if row["time_s"] >= FIRST_POST_FAULT_SAMPLE_S - 1e-12]
            pre_delta = {field: max(
                abs(healthy["sensor_rows"][i][field] - faulted["sensor_rows"][i][field])
                for i in pre_indices
            ) for field in OBSERVATION_FIELDS}
            post_delta = {field: max(
                abs(healthy["sensor_rows"][i][field] - faulted["sensor_rows"][i][field])
                for i in post_indices
            ) for field in OBSERVATION_FIELDS}
            pre_max = _max_delta_value(pre_delta)
            healthy_fault_pairs.append({
                "reference_mode": reference_mode,
                "gyro_white_noise_std_rad_s": noise_sigma,
                "healthy_record_id": healthy["record_id"],
                "fault_record_id": faulted["record_id"],
                "prefault_max_abs_sensor_delta_by_axis": pre_delta,
                "prefault_max_abs_sensor_delta": pre_max,
                "prefault_identical_under_same_seed": pre_max <= PAIR_PREFault_TOLERANCE,
                "postfault_max_abs_sensor_delta_by_axis": post_delta,
                "postfault_max_abs_sensor_delta": _max_delta_value(post_delta),
                "interpretation": "offline matched-physics observability comparison only; not a deployable detector score",
            })
            if pre_max > PAIR_PREFault_TOLERANCE:
                raise RuntimeError("R84_HEALTHY_FAULT_PREFault_SENSOR_STREAMS_DIVERGED")

    noise_ab: list[dict[str, Any]] = []
    for reference_mode in REFERENCE_MODES:
        for capacity_mode in CAPACITY_MODES:
            noise_off = executed[(reference_mode, capacity_mode, 0.0)]
            noise_on = executed[(reference_mode, capacity_mode, 0.0001)]
            deltas = _max_abs_delta(noise_off["sensor_rows"], noise_on["sensor_rows"], OBSERVATION_FIELDS)
            maximum = _max_delta_value(deltas)
            noise_ab.append({
                "reference_mode": reference_mode,
                "capacity_mode_QA_ONLY": capacity_mode,
                "noise_off_record_id": noise_off["record_id"],
                "noise_on_record_id": noise_on["record_id"],
                "max_abs_noise_on_off_delta_by_axis": deltas,
                "max_abs_noise_on_off_delta": maximum,
                "official_native_noise_changes_raw_measurement": maximum > 0.0,
            })
            if maximum <= 0.0:
                raise RuntimeError("R84_OFFICIAL_IMU_NOISE_ON_OFF_NO_DIFFERENCE")

    # Sensor configuration must not alter any native physical QA channels.
    observer_noise_ab: list[dict[str, Any]] = []
    for reference_mode in REFERENCE_MODES:
        for capacity_mode in CAPACITY_MODES:
            noise_off = executed[(reference_mode, capacity_mode, 0.0)]
            noise_on = executed[(reference_mode, capacity_mode, 0.0001)]
            deltas = _max_abs_delta(noise_off["qa_rows"], noise_on["qa_rows"], QA_PHYSICAL_FIELDS)
            maximum = _max_delta_value(deltas)
            observer_noise_ab.append({
                "reference_mode": reference_mode,
                "capacity_mode_QA_ONLY": capacity_mode,
                "max_abs_delta_by_physical_field": deltas,
                "max_abs_delta": maximum,
                "sensor_noise_did_not_change_physics": maximum <= PHYSICAL_MATCH_TOLERANCE,
            })
            if maximum > PHYSICAL_MATCH_TOLERANCE:
                raise RuntimeError("R84_IMU_NOISE_CHANGED_NATIVE_PHYSICS")

    # All eight unique arms plus the one repeat control are retained, including
    # zero-excitation negative/unobservable cases.
    if len(executed) != 8 or len(sensor_records) != 9 or len(qa_records) != 9:
        raise RuntimeError("R84_EXECUTION_OR_RECORD_COUNT_MISMATCH")

    sensor_payload = {
        "schema_version": "sat-sim.r84.native-imu-observations.v1",
        "feature_fields": ["time_s", *OBSERVATION_FIELDS],
        "records": sensor_records,
        "feature_projection_only": True,
        "no_fault_or_qa_metadata_in_sensor_rows": True,
        "not_a_training_dataset": True,
    }
    case_payload = {
        "schema_version": "sat-sim.r84.native-imu-case-manifest.v1",
        "unique_arm_count": 8,
        "native_execution_count_including_one_replay": 9,
        "records": case_manifest,
    }
    qa_payload = {
        "schema_version": "sat-sim.r84.native-imu-physics-qa.v1",
        "original_r83m_archive_receipt": original_receipt,
        "r83m_observer_ab_comparisons": source_comparisons,
        "healthy_fault_sensor_observability_pairs": healthy_fault_pairs,
        "official_imu_noise_on_off": noise_ab,
        "observer_noise_physics_invariance": observer_noise_ab,
        "seeded_replay": {
            "replay_record_id": replay_id,
            "reference_record_id": replay_reference["record_id"],
            "seed": NOISE_SEED,
            "max_abs_sensor_delta": replay_max_sensor_delta,
            "max_abs_qa_delta": replay_max_qa_delta,
            "passed": replay_pass,
        },
        "negative_unobservable_cases_retained": True,
        "diagnostic_model_trained": False,
        "classifier_metrics_generated": False,
        "formal_training_or_test_eligible": False,
    }

    _write_json(output_dir / "r84_sensor_observations.json", sensor_payload)
    _write_json(output_dir / "r84_case_manifest.json", case_payload)
    _write_json(output_dir / "r84_physics_qa.json", qa_payload)

    source_file = Path(run_native_case.__code__.co_filename).resolve()
    config_sha = _sha256(CONFIG_PATH)
    manifest = {
        "schema_version": "sat-sim.r84.native-imu-execution-manifest.v1",
        "status": "PASS",
        "research_only": True,
        "unique_arm_count": 8,
        "native_execution_count_including_one_replay": 9,
        "sample_count_per_unique_arm": 31,
        "sample_interval_s": 0.2,
        "duration_s": 6.0,
        "native_distribution": "bsk==2.11.0+satfix1",
        "original_upstream_wheel_sha256": config["fixed_native_engine"]["original_wheel_sha256"],
        "r83m_original_archive_sha256": original_receipt["archive_sha256"],
        "r83m_original_physics_baseline_verified": True,
        "sensor_feature_allowlist": list(OBSERVATION_FIELDS),
        "sensor_feature_rows_contain_truth_or_qa": False,
        "native_imu_observer_only": True,
        "simpleNav_fsw_feedback_unchanged": True,
        "checks": {
            "original_r83m_archive_sha_and_physics_audit": True,
            "native_imu_recorder_time_axis_matches_all_physics_recorders": True,
            "native_imu_noise_on_off_changes_raw_measurement": all(x["official_native_noise_changes_raw_measurement"] for x in noise_ab),
            "native_imu_seeded_replay_exact": replay_pass,
            "healthy_fault_prefault_sensor_streams_identical": all(x["prefault_identical_under_same_seed"] for x in healthy_fault_pairs),
            "imu_noise_does_not_change_native_plant_or_fsw": all(x["sensor_noise_did_not_change_physics"] for x in observer_noise_ab),
            "imu_observer_does_not_change_original_r83m_native_physics": all(x["observer_did_not_change_plant_or_fsw"] for x in source_comparisons),
            "sensor_feature_allowlist_and_no_truth_leakage": True,
            "zero_excitation_negative_cases_retained": True,
        },
        "artifacts": {
            name: _sha256(output_dir / name)
            for name in ("r84_sensor_observations.json", "r84_case_manifest.json", "r84_physics_qa.json")
        },
        "source_module_sha256": _sha256(source_file),
        "preregistration_sha256": config_sha,
        "diagnostic_model_trained": False,
        "formal_training_or_test_eligible": False,
        "flight_or_hardware_safety_qualified": False,
        "stable_release_eligible": False,
    }
    _write_json(output_dir / "r84_execution_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r83m-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = run_pilot(args.r83m_archive, args.output_dir)
    except Exception as exc:
        _write_json(args.output_dir / "r84_failure.json", {
            "schema_version": "sat-sim.r84.native-imu-failure.v1",
            "status": "FAILED",
            "exception_type": type(exc).__name__,
            "reason": str(exc),
            "diagnostic_model_trained": False,
        })
        raise
    print(json.dumps({
        "status": result["status"],
        "unique_arm_count": result["unique_arm_count"],
        "native_execution_count_including_one_replay": result["native_execution_count_including_one_replay"],
        "checks": result["checks"],
        "artifacts": result["artifacts"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
