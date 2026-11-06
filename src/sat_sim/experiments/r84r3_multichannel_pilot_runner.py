"""Execute the R84-R3A genuine Basilisk multi-channel telemetry integration pilot.

用途：执行 12 个固定 Basilisk 原生工况和 1 次同 seed 回放，核验 IMU、RW 速度、FSW 命令与姿态误差的真实消息来源。
参数：--r84r1-artifact 指向 SHA 固定的 R84-R1 Actions ZIP；--output-dir 指定证据目录。
输出：分离的多通道遥测、IMU 子集、工况清单、物理 QA、源归档及执行哈希；不训练模型。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
from typing import Any
from zipfile import ZipFile

from sat_sim.experiments.native_imu_rw_multichannel_pilot_r84r3 import (
    CAPACITY_NM,
    OBSERVATION_FIELDS,
    QA_PHYSICAL_FIELDS,
    TELEMETRY_FIELDS,
    run_native_case,
)
from scripts.audit_r83m_native_archive import audit_archive

CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "research" / "r84r3_native_multichannel_telemetry_pilot.json"
R84R1_SHA256 = "4bc1f9d0f788dce5e8d33aace1fe8860d739158793bcf6fae28352c785742a8e"
R84R0_SHA256 = "5d1439272f651519fec011d9728165f3afc46b1b3d14d305357ae271b26752af"
R83M_SHA256 = "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
MAX_PHYSICAL_DELTA = 1e-8
REPLAY_TOLERANCE = 1e-12
REFERENCES = ("zero", "pulse09")
CAPACITIES = ("healthy", "loss50", "loss75")
NOISES = (0.0, 0.0001)
CAPACITY_INDEX = {"healthy": 0, "loss50": 1, "loss75": 2}
REFERENCE_INDEX = {"zero": 0, "pulse09": 1}
NOISE_INDEX = {0.0: 0, 0.0001: 1}
TELEMETRY_KEYS = {"time_s", *TELEMETRY_FIELDS}
IMU_KEYS = {"time_s", *OBSERVATION_FIELDS}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def read_jsonl(data: bytes, label: str) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"R84R3_INVALID_JSONL:{label}")
    return rows


def specs() -> list[dict[str, Any]]:
    result = []
    for reference in REFERENCES:
        for capacity in CAPACITIES:
            for noise in NOISES:
                seed = (
                    50_000_000
                    + REFERENCE_INDEX[reference] * 100_000
                    + CAPACITY_INDEX[capacity] * 10_000
                    + NOISE_INDEX[noise] * 1_000
                    + 331
                )
                result.append({
                    "reference_mode": reference,
                    "capacity_mode": capacity,
                    "gyro_noise_std_rad_s": noise,
                    "seed": seed,
                })
    if len(result) != 12 or len({item["seed"] for item in result}) != 12:
        raise RuntimeError("R84R3_FROZEN_ARM_OR_SEED_COUNT_MISMATCH")
    return result


def max_delta(left: list[dict[str, float]], right: list[dict[str, float]], fields: tuple[str, ...]) -> dict[str, float]:
    if len(left) != len(right):
        raise RuntimeError("R84R3_PAIRED_SAMPLE_COUNT_MISMATCH")
    maxima = {field: 0.0 for field in fields}
    for a, b in zip(left, right):
        if abs(float(a["time_s"]) - float(b["time_s"])) > 1e-12:
            raise RuntimeError("R84R3_PAIRED_TIME_AXIS_MISMATCH")
        for field in fields:
            maxima[field] = max(maxima[field], abs(float(a[field]) - float(b[field])))
    return maxima


def max_value(values: dict[str, float]) -> float:
    return max(values.values(), default=0.0)


def load_sources(r84r1_artifact: Path, output_dir: Path, config: dict[str, Any]):
    raw = r84r1_artifact.read_bytes()
    if sha(raw) != R84R1_SHA256:
        raise RuntimeError("R84R3_R84R1_ARTIFACT_SHA256_MISMATCH")
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    preserved_r84r1 = source_dir / "r84r1-reference.zip"
    preserved_r84r1.write_bytes(raw)

    with ZipFile(r84r1_artifact) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("R84R3_R84R1_ARTIFACT_CRC_FAILURE")
        manifest = json.loads(archive.read("r84r1_execution_manifest.json"))
        if manifest.get("status") != "PASS" or manifest.get("unique_native_run_count") != 3159:
            raise RuntimeError("R84R3_R84R1_REFERENCE_NOT_QUALIFIED")
        for name, digest in manifest["artifacts"].items():
            if sha(archive.read(name)) != digest:
                raise RuntimeError(f"R84R3_R84R1_MEMBER_HASH_MISMATCH:{name}")
        r83m_bytes = archive.read("source/r83m-original.zip")
        r0_bytes = archive.read("source/r84-r0-reference.zip")
        r1_qa = read_jsonl(archive.read("r84r1_physics_qa.jsonl"), "r84r1_physics_qa")

    if sha(r83m_bytes) != R83M_SHA256:
        raise RuntimeError("R84R3_R83M_ORIGINAL_SHA256_MISMATCH")
    if sha(r0_bytes) != R84R0_SHA256:
        raise RuntimeError("R84R3_R84R0_REFERENCE_SHA256_MISMATCH")
    r83m_path = source_dir / "r83m-original.zip"
    r0_path = source_dir / "r84r0-reference.zip"
    r83m_path.write_bytes(r83m_bytes)
    r0_path.write_bytes(r0_bytes)
    r83m_receipt = audit_archive(r83m_path)
    with ZipFile(r83m_path) as archive:
        r83m_payload = json.loads(archive.read("r83m_genuine_native_54_original.json"))

    loss50_reference = {}
    for item in r1_qa:
        if item.get("cohort") != "loss50_noise_free_physics_reference":
            continue
        key = (int(item["wheel_index_QA_ONLY"]), int(item["initial_state_index"]), str(item["reference_mode"]))
        if "qa_rows" in item:
            loss50_reference[key] = item["qa_rows"]
    if (0, 0, "zero") not in loss50_reference or (0, 0, "pulse09") not in loss50_reference:
        raise RuntimeError("R84R3_LOSS50_NATIVE_REFERENCE_MISSING")
    return r83m_receipt, r83m_payload, loss50_reference, {
        "r83m_original_sha256": sha(r83m_bytes),
        "r84r0_reference_sha256": sha(r0_bytes),
        "r84r1_reference_sha256": sha(raw),
        "r84r1_reference_artifact_sha256": config["source_references"]["r84r1_artifact_sha256"],
    }


def run_pilot(r84r1_artifact: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_NOT_EXECUTED":
        raise RuntimeError("R84R3_PREREGISTRATION_STATUS_INVALID")
    design = config["physical_design"]
    if design.get("unique_arm_count") != 12 or design.get("total_native_executions") != 13:
        raise RuntimeError("R84R3_FROZEN_RUN_COUNT_CHANGED")
    output_dir.mkdir(parents=True, exist_ok=True)
    r83m_receipt, r83m_payload, loss50_reference, source_hashes = load_sources(r84r1_artifact, output_dir, config)

    telemetry_path = output_dir / "r84r3_multichannel_telemetry.jsonl"
    imu_path = output_dir / "r84r3_imu_sensor_observations.jsonl"
    case_path = output_dir / "r84r3_case_manifest.jsonl"
    qa_path = output_dir / "r84r3_physics_qa.jsonl"
    for path in (telemetry_path, imu_path, case_path, qa_path):
        path.unlink(missing_ok=True)

    executed: dict[tuple[str, str, float], dict[str, Any]] = {}
    physical_comparisons = []
    telemetry_records = []
    imu_records = []
    case_records = []
    qa_records = []
    for index, spec in enumerate(specs(), start=1):
        reference = spec["reference_mode"]
        capacity = spec["capacity_mode"]
        noise = spec["gyro_noise_std_rad_s"]
        seed = spec["seed"]
        result = run_native_case(
            wheel=0,
            initial=0,
            reference_mode=reference,
            capacity_mode=capacity,
            gyro_noise_std_rad_s=noise,
            seed=seed,
        )
        sensor_rows = result.pop("sensor_rows")
        telemetry_rows = result.pop("telemetry_rows")
        qa_rows = result.pop("qa_rows")
        if len(sensor_rows) != 31 or len(telemetry_rows) != 31 or len(qa_rows) != 31:
            raise RuntimeError(f"R84R3_SAMPLE_COUNT_MISMATCH:{index}")
        for row_index, (sensor, telemetry, qa) in enumerate(zip(sensor_rows, telemetry_rows, qa_rows)):
            if set(sensor) != IMU_KEYS or set(telemetry) != TELEMETRY_KEYS:
                raise RuntimeError(f"R84R3_TELEMETRY_WHITELIST_VIOLATION:{index}:{row_index}")
            if abs(sensor["time_s"] - telemetry["time_s"]) > 1e-12 or abs(telemetry["time_s"] - qa["time_s"]) > 1e-12:
                raise RuntimeError(f"R84R3_CHANNEL_TIME_MISMATCH:{index}:{row_index}")
            for field in OBSERVATION_FIELDS:
                if abs(sensor[field] - telemetry[field]) > 1e-12:
                    raise RuntimeError(f"R84R3_IMU_CHANNEL_MISMATCH:{index}:{row_index}:{field}")
            for wheel in range(3):
                speed = telemetry[f"sensor.adcs.rw.speed_rad_s_{wheel}"]
                command = telemetry[f"command.adcs.rw.motor_torque_nm_{wheel}"]
                if abs(speed - qa[f"rw.native_speed_rad_s_{wheel}"]) > 1e-12:
                    raise RuntimeError(f"R84R3_RW_SPEED_PROVENANCE_MISMATCH:{index}:{row_index}:{wheel}")
                if abs(command - qa[f"rw.native_allocator_request_nm_{wheel}"]) > 1e-12:
                    raise RuntimeError(f"R84R3_RW_COMMAND_PROVENANCE_MISMATCH:{index}:{row_index}:{wheel}")
            if abs(telemetry["estimate.adcs.pointing_error_deg"] - qa["fsw.pointing_error_deg_QA_ONLY"]) > 1e-12:
                raise RuntimeError(f"R84R3_POINTING_ERROR_PROVENANCE_MISMATCH:{index}:{row_index}")
            if any("QA_ONLY" in key or key.startswith(("hub.", "fault_", "label.")) for key in telemetry):
                raise RuntimeError(f"R84R3_TELEMETRY_TRUTH_LEAKAGE:{index}:{row_index}")

        if capacity in ("healthy", "loss75"):
            source_case_id = f"rw0_initial0_{reference}_{capacity}"
            baseline = r83m_payload["physical_source_cases"][source_case_id]["rows"]
            baseline_source = "SHA_PINNED_R83M_ORIGINAL"
        else:
            baseline = loss50_reference[(0, 0, reference)]
            source_case_id = f"rw0_initial0_{reference}_loss50_noise_free"
            baseline_source = "R84R1_NATIVE_NOISE_FREE_LOSS50_REFERENCE"
        delta = max_delta(qa_rows, baseline, QA_PHYSICAL_FIELDS)
        max_physical_delta = max_value(delta)
        if max_physical_delta > MAX_PHYSICAL_DELTA:
            raise RuntimeError(f"R84R3_PHYSICS_BASELINE_MISMATCH:{index}:{max_physical_delta}")

        capacity_receipt = next(
            (item for item in result["native_event_receipts"] if item.get("event") == "NATIVE_CPP_RW_CAPACITY_VERIFICATION"),
            None,
        )
        expected_cap = CAPACITY_NM[capacity]
        if not capacity_receipt:
            raise RuntimeError(f"R84R3_CPP_CAPACITY_RECEIPT_MISSING:{index}")
        if abs(float(capacity_receipt["effector_cpp_readback_after_nm"]) - expected_cap) > 1e-12:
            raise RuntimeError(f"R84R3_CPP_CAPACITY_READBACK_MISMATCH:{index}")
        if float(result["max_post_fault_applied_torque_abs_nm_QA_ONLY"]) > expected_cap + 1e-9:
            raise RuntimeError(f"R84R3_APPLIED_TORQUE_EXCEEDS_CAP:{index}")

        record_id = f"r84r3_{index:02d}"
        case_record = {
            "record_id": record_id,
            "case_id": result["case_id"],
            "reference_mode": reference,
            "capacity_mode_QA_ONLY": capacity,
            "gyro_noise_std_rad_s": noise,
            "rng_seed": seed,
            "target_wheel_index_QA_ONLY": 0,
            "initial_state_index": 0,
            "sample_count": 31,
            "telemetry_provenance": result["telemetry_provenance"],
            "native_event_receipts": result["native_event_receipts"],
            "native_distribution": result["distribution"],
            "controller_feedback_source": result["controller_feedback_source"],
        }
        qa_record = {
            "record_id": record_id,
            "case_id": result["case_id"],
            "reference_mode": reference,
            "capacity_mode_QA_ONLY": capacity,
            "rng_seed": seed,
            "baseline_source": baseline_source,
            "baseline_case_id": source_case_id,
            "max_abs_physics_delta_by_field": delta,
            "max_abs_physics_delta": max_physical_delta,
            "qa_rows": qa_rows,
            "native_envelopes": {
                "max_abs_allocator_request_nm": result["max_abs_allocator_request_nm"],
                "max_abs_wheel_speed_rad_s": result["max_abs_wheel_speed_rad_s"],
                "max_abs_body_rate_rad_s": result["max_abs_body_rate_rad_s"],
                "max_post_fault_applied_torque_abs_nm_QA_ONLY": result["max_post_fault_applied_torque_abs_nm_QA_ONLY"],
            },
        }
        telemetry_records.append({"record_id": record_id, "rows": telemetry_rows})
        imu_records.append({"record_id": record_id, "rows": sensor_rows})
        case_records.append(case_record)
        qa_records.append(qa_record)
        executed[(reference, capacity, noise)] = {
            "record_id": record_id,
            "telemetry_rows": telemetry_rows,
            "sensor_rows": sensor_rows,
            "qa_rows": qa_rows,
        }
        physical_comparisons.append({
            "record_id": record_id,
            "baseline_source": baseline_source,
            "baseline_case_id": source_case_id,
            "max_abs_physics_delta": max_physical_delta,
        })

    # The sensor noise is observer-only. Compare each noise-off/on pair:
    # IMU should change; native wheel-speed, command and pointing channels must not.
    noise_checks = []
    non_imu_fields = tuple(field for field in TELEMETRY_FIELDS if field not in OBSERVATION_FIELDS)
    for reference in REFERENCES:
        for capacity in CAPACITIES:
            off = executed[(reference, capacity, 0.0)]
            on = executed[(reference, capacity, 0.0001)]
            imu_delta = max_delta(off["telemetry_rows"], on["telemetry_rows"], OBSERVATION_FIELDS)
            other_delta = max_delta(off["telemetry_rows"], on["telemetry_rows"], non_imu_fields)
            if max_value(imu_delta) <= 0.0:
                raise RuntimeError(f"R84R3_IMU_NOISE_ON_OFF_NO_DIFFERENCE:{reference}:{capacity}")
            if max_value(other_delta) > 1e-12:
                raise RuntimeError(f"R84R3_IMU_NOISE_CHANGED_NON_IMU_CHANNELS:{reference}:{capacity}")
            noise_checks.append({
                "reference_mode": reference,
                "capacity_mode_QA_ONLY": capacity,
                "max_abs_imu_noise_on_off_delta": max_value(imu_delta),
                "max_abs_non_imu_delta": max_value(other_delta),
                "non_imu_channels_unchanged": True,
            })

    # Exact same-seed replay is a deterministic message/recorder control.
    replay_key = ("pulse09", "loss75", 0.0001)
    reference_run = executed[replay_key]
    replay = run_native_case(
        wheel=0, initial=0, reference_mode=replay_key[0], capacity_mode=replay_key[1],
        gyro_noise_std_rad_s=replay_key[2], seed=specs()[-1]["seed"],
    )
    replay_sensor = replay["sensor_rows"]
    replay_telemetry = replay["telemetry_rows"]
    replay_qa = replay["qa_rows"]
    sensor_delta = max_delta(reference_run["sensor_rows"], replay_sensor, OBSERVATION_FIELDS)
    telemetry_delta = max_delta(reference_run["telemetry_rows"], replay_telemetry, TELEMETRY_FIELDS)
    qa_delta = max_delta(reference_run["qa_rows"], replay_qa, QA_PHYSICAL_FIELDS)
    if max_value(sensor_delta) > REPLAY_TOLERANCE or max_value(telemetry_delta) > REPLAY_TOLERANCE or max_value(qa_delta) > REPLAY_TOLERANCE:
        raise RuntimeError("R84R3_SEEDED_REPLAY_NOT_EXACT")
    replay_id = "r84r3_seeded_replay_pulse09_loss75_noise_on"
    telemetry_records.append({"record_id": replay_id, "rows": replay_telemetry, "repeatability_control": True})
    imu_records.append({"record_id": replay_id, "rows": replay_sensor, "repeatability_control": True})
    case_records.append({
        "record_id": replay_id,
        "case_id": replay["case_id"],
        "repeatability_control": True,
        "replay_of_record_id": reference_run["record_id"],
        "rng_seed": specs()[-1]["seed"],
        "sample_count": 31,
    })
    qa_records.append({
        "record_id": replay_id,
        "repeatability_control": True,
        "replay_of_record_id": reference_run["record_id"],
        "max_abs_physics_delta": max_value(qa_delta),
        "qa_rows": replay_qa,
    })

    append_jsonl(telemetry_path, telemetry_records)
    append_jsonl(imu_path, imu_records)
    append_jsonl(case_path, case_records)
    append_jsonl(qa_path, qa_records)

    summary = {
        "schema_version": "sat-sim.r84r3.multichannel-pilot-summary.v1",
        "status": "PASS",
        "unique_arm_count": 12,
        "native_execution_count_including_replay": 13,
        "recorder_samples_per_execution": 31,
        "physical_baseline_comparisons": len(physical_comparisons),
        "max_abs_physics_delta": max(item["max_abs_physics_delta"] for item in physical_comparisons),
        "noise_on_off_checks": noise_checks,
        "seeded_replay": {
            "sensor_max_abs_delta": max_value(sensor_delta),
            "telemetry_max_abs_delta": max_value(telemetry_delta),
            "physics_qa_max_abs_delta": max_value(qa_delta),
            "passed": True,
        },
        "channel_provenance": {
            "imu": "official native sensor message with configured gyro noise",
            "rw_speed": "ideal native RW state-feedback upper bound; no encoder noise or quantization",
            "command_torque": "native FSW command; not delivered torque",
            "pointing_error": "native FSW tracking error estimate",
            "actual_torque": "QA-only native applied torque",
            "effective_max_torque": "QA-only C++ actuator truth",
            "motor_current": "not recorded; no native current sensor model",
        },
        "model_trained": False,
        "formal_training_or_test_eligible": False,
        "flight_or_hardware_safety_qualified": False,
        "stable_release_eligible": False,
    }
    for path, rows in (
        (telemetry_path, telemetry_records),
        (imu_path, imu_records),
        (case_path, case_records),
        (qa_path, qa_records),
    ):
        summary.setdefault("artifact_sha256", {})[path.name] = sha_file(path)
    summary["source_sha256"] = source_hashes
    summary["original_r83m_physics_audit"] = r83m_receipt
    summary["artifact_sha256"]["source/r83m-original.zip"] = sha_file(output_dir / "source/r83m-original.zip")
    summary["artifact_sha256"]["source/r84r1-reference.zip"] = sha_file(output_dir / "source/r84r1-reference.zip")
    summary["artifact_sha256"]["source/r84r0-reference.zip"] = sha_file(output_dir / "source/r84r0-reference.zip")
    write_json(output_dir / "r84r3_execution_manifest.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r84r1-artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_pilot(args.r84r1_artifact, args.output_dir)
    except Exception as exc:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / "r84r3_failure.json", {
            "schema_version": "sat-sim.r84r3.failure.v1",
            "status": "FAILED",
            "exception_type": type(exc).__name__,
            "reason": str(exc),
            "model_trained": False,
        })
        raise
    print(json.dumps({
        "status": result["status"],
        "unique_arm_count": result["unique_arm_count"],
        "native_execution_count_including_replay": result["native_execution_count_including_replay"],
        "max_abs_physics_delta": result["max_abs_physics_delta"],
        "seeded_replay": result["seeded_replay"],
        "model_trained": result["model_trained"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
