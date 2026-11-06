"""Execute R84-R1 independent-noise native Basilisk IMU observability.

用途：执行 3,159 个唯一原生工况和 1 次固定 seed 回放；统计独立健康控制与两种 RW 力矩权限损失下的 IMU 分布。
参数：--r83m-archive 指向 SHA 固定的原始 54 案例 ZIP；--output-dir 指定证据目录。
输出：原始 IMU JSONL、案例清单、独立物理 QA、单变量统计、执行哈希及失败收据；不训练模型。
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import shutil
from statistics import median
from typing import Any, Iterable
from zipfile import ZipFile

from sat_sim.experiments.native_imu_rw_observability_r84r1 import (
    CAPACITY_MODES,
    FIRST_POST_FAULT_SAMPLE_S,
    NOISE_SIGMAS,
    OBSERVATION_FIELDS,
    QA_PHYSICAL_FIELDS,
    REFERENCE_MODES,
    WHEELS,
    INITIAL_STATES,
    run_native_case,
)
from scripts.audit_r83m_native_archive import audit_archive

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "research" / "r84r1_independent_noise_observability_prereg.json"
R83M_CASE_PREFIX = "rw{wheel}_initial{initial}_{reference}_{capacity}"
PHYSICAL_MATCH_TOLERANCE = 1e-8
REPLAY_TOLERANCE = 1e-12
PRIMARY_SIGMA = 0.0001
SENSITIVITY_SIGMAS = (0.00005, 0.0002)
PRIMARY_REPETITIONS = 20
SENSITIVITY_REPETITIONS = 6
HEALTHY_CALIBRATION = "healthy_calibration"
HEALTHY_CONTROL = "healthy_false_alarm_control"
COHORT_CAPACITY = {
    "healthy_calibration": "healthy",
    "healthy_false_alarm_control": "healthy",
    "loss50_primary": "loss50",
    "loss75_primary": "loss75",
    "healthy_sensitivity": "healthy",
    "loss50_sensitivity": "loss50",
    "loss75_sensitivity": "loss75",
    "loss50_noise_free_physics_reference": "loss50",
}
COHORT_CODES = {
    "healthy_calibration": 1,
    "healthy_false_alarm_control": 2,
    "loss50_primary": 3,
    "loss75_primary": 4,
    "healthy_sensitivity": 5,
    "loss50_sensitivity": 6,
    "loss75_sensitivity": 7,
    "loss50_noise_free_physics_reference": 8,
}
NOISE_INDEX = {0.0: 0, 0.00005: 1, 0.0001: 2, 0.0002: 3}
REFERENCE_INDEX = {"zero": 0, "pulse05": 1, "pulse09": 2}
WINDOW_PRE_S = (0.4, 1.6)
WINDOW_POST_S = (2.6, 3.6)
FEATURE_FIELDS = (
    "pre_window_rms_3d_rad_s",
    "post_window_rms_3d_rad_s",
    "delta_rms_3d_rad_s",
)


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


def _append_jsonl(stream, payload: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def _max_abs_delta(
    left: list[dict[str, float]],
    right: list[dict[str, float]],
    fields: tuple[str, ...],
) -> dict[str, float]:
    if len(left) != len(right):
        raise RuntimeError("R84R1_PHYSICS_BASELINE_SAMPLE_COUNT_MISMATCH")
    maxima = {field: 0.0 for field in fields}
    for a, b in zip(left, right):
        if abs(float(a["time_s"]) - float(b["time_s"])) > 1e-12:
            raise RuntimeError("R84R1_PHYSICS_BASELINE_TIME_AXIS_MISMATCH")
        for field in fields:
            maxima[field] = max(maxima[field], abs(float(a[field]) - float(b[field])))
    return maxima


def _max_delta(values: dict[str, float]) -> float:
    return max(values.values(), default=0.0)


def _load_original_r83m(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = audit_archive(path)
    with ZipFile(path, "r") as archive:
        payload = json.loads(archive.read("r83m_genuine_native_54_original.json"))
    if receipt.get("physics_archive_verified") is not True:
        raise RuntimeError("R84R1_R83M_ORIGINAL_ARCHIVE_NOT_VERIFIED")
    return receipt, payload


def seed_for(
    cohort: str,
    wheel: int,
    initial: int,
    reference_mode: str,
    noise_sigma: float,
    repetition: int,
) -> int:
    if cohort not in COHORT_CODES:
        raise ValueError("R84R1_UNKNOWN_SEED_COHORT")
    if noise_sigma not in NOISE_INDEX:
        raise ValueError("R84R1_UNKNOWN_SEED_NOISE_LEVEL")
    if reference_mode not in REFERENCE_INDEX:
        raise ValueError("R84R1_UNKNOWN_SEED_REFERENCE_MODE")
    return (
        10_000_000
        + COHORT_CODES[cohort] * 1_000_000
        + wheel * 100_000
        + initial * 10_000
        + REFERENCE_INDEX[reference_mode] * 1_000
        + NOISE_INDEX[noise_sigma] * 100
        + repetition
    )


def iter_unique_run_specs() -> Iterable[dict[str, Any]]:
    """Yield the exact frozen 3,159 unique runs in deterministic order."""
    for wheel in WHEELS:
        for initial in range(len(INITIAL_STATES)):
            for reference_mode in REFERENCE_MODES:
                # One true Basilisk noise-free reference for the new 50% fault
                # severity, for which the R83m archive has no original arm.
                yield {
                    "cohort": "loss50_noise_free_physics_reference",
                    "wheel": wheel,
                    "initial": initial,
                    "reference_mode": reference_mode,
                    "capacity_mode": "loss50",
                    "gyro_noise_std_rad_s": 0.0,
                    "repetition": 0,
                }
                for cohort in (
                    "healthy_calibration",
                    "healthy_false_alarm_control",
                    "loss50_primary",
                    "loss75_primary",
                ):
                    for repetition in range(PRIMARY_REPETITIONS):
                        yield {
                            "cohort": cohort,
                            "wheel": wheel,
                            "initial": initial,
                            "reference_mode": reference_mode,
                            "capacity_mode": COHORT_CAPACITY[cohort],
                            "gyro_noise_std_rad_s": PRIMARY_SIGMA,
                            "repetition": repetition,
                        }
                for noise_sigma in SENSITIVITY_SIGMAS:
                    for cohort in (
                        "healthy_sensitivity",
                        "loss50_sensitivity",
                        "loss75_sensitivity",
                    ):
                        for repetition in range(SENSITIVITY_REPETITIONS):
                            yield {
                                "cohort": cohort,
                                "wheel": wheel,
                                "initial": initial,
                                "reference_mode": reference_mode,
                                "capacity_mode": COHORT_CAPACITY[cohort],
                                "gyro_noise_std_rad_s": noise_sigma,
                                "repetition": repetition,
                            }


def validate_preregistered_run_plan(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Preflight all conditions and unique seeds before starting any physics."""
    design = config["frozen_replication_design"]
    physics = config["frozen_physics_design"]
    if design.get("unique_native_runs") != 3159 or design.get("total_native_executions") != 3160:
        raise RuntimeError("R84R1_FROZEN_EXECUTION_COUNTS_CHANGED")
    if physics.get("physical_context_count") != 27:
        raise RuntimeError("R84R1_FROZEN_PHYSICAL_CONTEXT_COUNT_CHANGED")
    if physics.get("noise_sigma_rad_s") != {
        "low": 0.00005, "primary": 0.0001, "high": 0.0002
    }:
        raise RuntimeError("R84R1_FROZEN_NOISE_LEVELS_CHANGED")
    specs = list(iter_unique_run_specs())
    if len(specs) != 3159:
        raise RuntimeError("R84R1_PREFLIGHT_UNIQUE_RUN_COUNT_MISMATCH")
    seeds = [
        seed_for(
            spec["cohort"], spec["wheel"], spec["initial"],
            spec["reference_mode"], spec["gyro_noise_std_rad_s"], spec["repetition"]
        )
        for spec in specs
    ]
    if len(set(seeds)) != len(seeds):
        raise RuntimeError("R84R1_PREFLIGHT_SEED_COLLISION")
    from collections import Counter
    counts = Counter(spec["cohort"] for spec in specs)
    expected = {
        "loss50_noise_free_physics_reference": 27,
        "healthy_calibration": 540,
        "healthy_false_alarm_control": 540,
        "loss50_primary": 540,
        "loss75_primary": 540,
        "healthy_sensitivity": 162,
        "loss50_sensitivity": 162,
        "loss75_sensitivity": 162,
    }
    if dict(counts) != expected:
        raise RuntimeError("R84R1_PREFLIGHT_COHORT_COUNTS_MISMATCH")
    healthy_seeds = {
        seed for spec, seed in zip(specs, seeds)
        if spec["cohort"] in (
            "healthy_calibration", "healthy_false_alarm_control", "healthy_sensitivity"
        )
    }
    fault_seeds = {
        seed for spec, seed in zip(specs, seeds)
        if spec["cohort"] in (
            "loss50_primary", "loss75_primary", "loss50_sensitivity", "loss75_sensitivity"
        )
    }
    if healthy_seeds.intersection(fault_seeds):
        raise RuntimeError("R84R1_PREFLIGHT_HEALTHY_FAULT_SEED_OVERLAP")
    calibration = {
        seed for spec, seed in zip(specs, seeds) if spec["cohort"] == "healthy_calibration"
    }
    false_alarm = {
        seed for spec, seed in zip(specs, seeds) if spec["cohort"] == "healthy_false_alarm_control"
    }
    if calibration.intersection(false_alarm):
        raise RuntimeError("R84R1_PREFLIGHT_CALIBRATION_CONTROL_SEED_OVERLAP")
    return specs


def extract_sensor_feature(sensor_rows: list[dict[str, float]]) -> dict[str, float]:
    """Fixed event-aligned summary from official raw gyro channels only."""
    expected_keys = {"time_s", *OBSERVATION_FIELDS}
    if len(sensor_rows) != 31 or any(set(row) != expected_keys for row in sensor_rows):
        raise RuntimeError("R84R1_SENSOR_FEATURE_INPUT_SCHEMA_MISMATCH")
    pre = [
        row for row in sensor_rows
        if WINDOW_PRE_S[0] - 1e-12 <= row["time_s"] <= WINDOW_PRE_S[1] + 1e-12
    ]
    post = [
        row for row in sensor_rows
        if WINDOW_POST_S[0] - 1e-12 <= row["time_s"] <= WINDOW_POST_S[1] + 1e-12
    ]
    if len(pre) != 7 or len(post) != 6:
        raise RuntimeError("R84R1_PREDECLARED_FEATURE_WINDOW_SAMPLE_COUNT_MISMATCH")

    def rms(rows: list[dict[str, float]]) -> float:
        squared_norms = [
            sum(float(row[field]) ** 2 for field in OBSERVATION_FIELDS)
            for row in rows
        ]
        return math.sqrt(sum(squared_norms) / len(squared_norms))

    pre_rms = rms(pre)
    post_rms = rms(post)
    return {
        "pre_window_rms_3d_rad_s": pre_rms,
        "post_window_rms_3d_rad_s": post_rms,
        "delta_rms_3d_rad_s": post_rms - pre_rms,
    }


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("R84R1_EMPTY_STATISTIC")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _nearest_rank(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("R84R1_EMPTY_THRESHOLD_CALIBRATION")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _distribution_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": _quantile(ordered, 0.10),
        "median": median(ordered),
        "p90": _quantile(ordered, 0.90),
        "max": ordered[-1],
        "iqr": _quantile(ordered, 0.75) - _quantile(ordered, 0.25),
    }


def cliffs_delta(left: list[float], right: list[float]) -> float:
    """Probability(left > right) - Probability(left < right), with ties zero."""
    if not left or not right:
        raise ValueError("R84R1_EMPTY_CLIFFS_DELTA_INPUT")
    greater = less = 0
    for a in left:
        for b in right:
            if a > b:
                greater += 1
            elif a < b:
                less += 1
    return (greater - less) / (len(left) * len(right))


def wasserstein_1_equal_n(left: list[float], right: list[float]) -> float:
    """Exact empirical 1-D W1 for equal-size samples."""
    if not left or len(left) != len(right):
        raise ValueError("R84R1_WASSERSTEIN_REQUIRES_EQUAL_NONEMPTY_SAMPLES")
    a = sorted(float(value) for value in left)
    b = sorted(float(value) for value in right)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _analysis(case_records: list[dict[str, Any]], feature_by_id: dict[str, dict[str, float]]) -> dict[str, Any]:
    groups: dict[tuple[int, int, str, float], dict[str, list[float]]] = {}
    for case in case_records:
        if case.get("repeatability_control") or case["cohort"] == "loss50_noise_free_physics_reference":
            continue
        key = (
            int(case["wheel_index_QA_ONLY"]),
            int(case["initial_state_index"]),
            str(case["reference_mode"]),
            float(case["gyro_white_noise_std_rad_s"]),
        )
        group = groups.setdefault(key, {})
        cohort = str(case["cohort"])
        group.setdefault(cohort, []).append(
            float(feature_by_id[case["record_id"]]["delta_rms_3d_rad_s"])
        )

    primary: list[dict[str, Any]] = []
    sensitivity: list[dict[str, Any]] = []
    for key in sorted(groups):
        wheel, initial, reference_mode, noise_sigma = key
        group = groups[key]
        context = {
            "wheel_index_QA_ONLY": wheel,
            "initial_state_index": initial,
            "reference_mode": reference_mode,
            "gyro_white_noise_std_rad_s": noise_sigma,
        }
        if math.isclose(noise_sigma, PRIMARY_SIGMA, rel_tol=0.0, abs_tol=1e-15):
            calibration = group[HEALTHY_CALIBRATION]
            healthy_control = group[HEALTHY_CONTROL]
            calibration_center = median(calibration)
            calibration_scores = [abs(value - calibration_center) for value in calibration]
            threshold = _nearest_rank(calibration_scores, 0.90)
            control_scores = [abs(value - calibration_center) for value in healthy_control]
            false_alarm_count = sum(score > threshold for score in control_scores)
            severity_results = {}
            for cohort, severity in (("loss50_primary", "loss50"), ("loss75_primary", "loss75")):
                fault_values = group[cohort]
                fault_scores = [abs(value - calibration_center) for value in fault_values]
                exceedances = sum(score > threshold for score in fault_scores)
                severity_results[severity] = {
                    "fault_feature_distribution": _distribution_summary(fault_values),
                    "fault_anomaly_score_distribution": _distribution_summary(fault_scores),
                    "threshold_exceedance_count": exceedances,
                    "threshold_exceedance_rate": exceedances / len(fault_scores),
                    "cliffs_delta_raw_feature_vs_healthy_control": cliffs_delta(fault_values, healthy_control),
                    "wasserstein_1_raw_feature_vs_healthy_control_rad_s": wasserstein_1_equal_n(
                        fault_values, healthy_control
                    ),
                    "interpretation": "independent-seed distribution comparison; not trained detector accuracy",
                }
            primary.append({
                **context,
                "healthy_calibration_count": len(calibration),
                "healthy_false_alarm_control_count": len(healthy_control),
                "healthy_calibration_feature_distribution": _distribution_summary(calibration),
                "healthy_control_feature_distribution": _distribution_summary(healthy_control),
                "healthy_only_calibration_center_rad_s": calibration_center,
                "healthy_only_anomaly_threshold_90th_nearest_rank": threshold,
                "healthy_false_alarm_count": false_alarm_count,
                "healthy_false_alarm_rate": false_alarm_count / len(healthy_control),
                "severity_results": severity_results,
            })
        else:
            for cohort, severity in (
                ("healthy_sensitivity", "healthy"),
                ("loss50_sensitivity", "loss50"),
                ("loss75_sensitivity", "loss75"),
            ):
                if cohort not in group or len(group[cohort]) != SENSITIVITY_REPETITIONS:
                    raise RuntimeError("R84R1_SENSITIVITY_COHORT_COUNT_MISMATCH")
            healthy = group["healthy_sensitivity"]
            severity_results = {}
            for cohort, severity in (
                ("loss50_sensitivity", "loss50"),
                ("loss75_sensitivity", "loss75"),
            ):
                fault = group[cohort]
                severity_results[severity] = {
                    "fault_feature_distribution": _distribution_summary(fault),
                    "cliffs_delta_raw_feature_vs_healthy": cliffs_delta(fault, healthy),
                    "wasserstein_1_raw_feature_vs_healthy_rad_s": wasserstein_1_equal_n(fault, healthy),
                }
            sensitivity.append({
                **context,
                "healthy_sensitivity_distribution": _distribution_summary(healthy),
                "severity_results": severity_results,
                "threshold_transferred_from_primary_noise": False,
            })

    if len(primary) != 27 or len(sensitivity) != 54:
        raise RuntimeError("R84R1_ANALYSIS_CONTEXT_COUNT_MISMATCH")
    return {
        "schema_version": "sat-sim.r84r1.independent-noise-observability-analysis.v1",
        "statistical_unit": "one independent complete native Basilisk execution",
        "feature_source": "official raw IMU gyro only",
        "feature_fields": list(FEATURE_FIELDS),
        "pre_window_s": list(WINDOW_PRE_S),
        "post_window_s": list(WINDOW_POST_S),
        "primary_noise_sigma_rad_s": PRIMARY_SIGMA,
        "primary_threshold_rule": "nearest-rank 90th percentile of absolute deviations in 20 independent healthy calibration runs",
        "primary_context_count": len(primary),
        "primary_results": primary,
        "sensitivity_context_count": len(sensitivity),
        "sensitivity_results": sensitivity,
        "threshold_is_not_a_trained_model": True,
        "not_a_formal_false_alarm_qualification": True,
        "no_classifier_metrics_or_training": True,
    }


def run_pilot(r83m_archive: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_NOT_EXECUTED":
        raise RuntimeError("R84R1_PREREGISTRATION_STATUS_INVALID")
    run_plan = validate_preregistered_run_plan(config)

    original_receipt, original_payload = _load_original_r83m(r83m_archive)
    if original_receipt["archive_sha256"] != config["r0_reference"]["original_r83m_archive_sha256"]:
        raise RuntimeError("R84R1_R83M_ARCHIVE_DOES_NOT_MATCH_FROZEN_R0_SOURCE")

    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    preserved_archive = source_dir / "r83m-original.zip"
    shutil.copy2(r83m_archive, preserved_archive)
    (source_dir / "r83m-original.sha256").write_text(
        f"{_sha256(preserved_archive)}  r83m-original.zip\n", encoding="utf-8"
    )

    sensor_path = output_dir / "r84r1_sensor_observations.jsonl"
    case_path = output_dir / "r84r1_case_manifest.jsonl"
    qa_path = output_dir / "r84r1_physics_qa.jsonl"
    feature_path = output_dir / "r84r1_features.jsonl"
    progress_path = output_dir / "r84r1_progress.json"

    sensor_stream = sensor_path.open("w", encoding="utf-8")
    case_stream = case_path.open("w", encoding="utf-8")
    qa_stream = qa_path.open("w", encoding="utf-8")
    feature_stream = feature_path.open("w", encoding="utf-8")

    seed_set: set[int] = set()
    case_records: list[dict[str, Any]] = []
    feature_by_id: dict[str, dict[str, float]] = {}
    loss50_baselines: dict[tuple[int, int, str], list[dict[str, float]]] = {}
    selected_replay_reference: dict[str, Any] | None = None
    selected_replay_case: dict[str, Any] | None = None
    physical_max_delta = 0.0
    physical_comparison_count = 0
    run_index = 0

    def execute_one(spec: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
        nonlocal run_index, physical_max_delta, physical_comparison_count, selected_replay_reference, selected_replay_case
        cohort = spec["cohort"]
        wheel = int(spec["wheel"])
        initial = int(spec["initial"])
        reference_mode = str(spec["reference_mode"])
        capacity_mode = str(spec["capacity_mode"])
        noise_sigma = float(spec["gyro_noise_std_rad_s"])
        repetition = int(spec["repetition"])
        seed = seed_for(cohort, wheel, initial, reference_mode, noise_sigma, repetition)

        if not replay:
            if seed in seed_set:
                raise RuntimeError(f"R84R1_DUPLICATED_INDEPENDENT_SEED:{seed}")
            seed_set.add(seed)

        result = run_native_case(
            wheel=wheel,
            initial=initial,
            reference_mode=reference_mode,
            capacity_mode=capacity_mode,
            gyro_noise_std_rad_s=noise_sigma,
            seed=seed,
        )
        sensor_rows = result.pop("sensor_rows")
        qa_rows = result.pop("qa_rows")
        expected_sensor_keys = {"time_s", *OBSERVATION_FIELDS}
        if len(sensor_rows) != 31 or any(set(row) != expected_sensor_keys for row in sensor_rows):
            raise RuntimeError("R84R1_SENSOR_FEATURE_WHITELIST_VIOLATION")
        if any(
            any("QA_ONLY" in key or key.startswith(("hub.", "fault_", "source_fault_")) for key in row)
            for row in sensor_rows
        ):
            raise RuntimeError("R84R1_SENSOR_FEATURE_TRUTH_LEAKAGE")

        context_key = (wheel, initial, reference_mode)
        if capacity_mode == "healthy":
            baseline_case_id = R83M_CASE_PREFIX.format(
                wheel=wheel, initial=initial, reference=reference_mode, capacity="healthy"
            )
            baseline_rows = original_payload["physical_source_cases"][baseline_case_id]["rows"]
            baseline_source = "SHA_PINNED_R83M_ORIGINAL"
        elif capacity_mode == "loss75":
            baseline_case_id = R83M_CASE_PREFIX.format(
                wheel=wheel, initial=initial, reference=reference_mode, capacity="loss75"
            )
            baseline_rows = original_payload["physical_source_cases"][baseline_case_id]["rows"]
            baseline_source = "SHA_PINNED_R83M_ORIGINAL"
        else:
            if cohort == "loss50_noise_free_physics_reference":
                baseline_rows = qa_rows
                loss50_baselines[context_key] = qa_rows
                baseline_source = "SELF_NOISE_FREE_NATIVE_REFERENCE"
            else:
                if context_key not in loss50_baselines:
                    raise RuntimeError("R84R1_LOSS50_NOISE_FREE_REFERENCE_MISSING")
                baseline_rows = loss50_baselines[context_key]
                baseline_source = "R84R1_NOISE_FREE_NATIVE_REFERENCE"
            baseline_case_id = f"rw{wheel}_initial{initial}_{reference_mode}_loss50_noise_free"
        delta_by_field = _max_abs_delta(qa_rows, baseline_rows, QA_PHYSICAL_FIELDS)
        max_delta = _max_delta(delta_by_field)
        if max_delta > PHYSICAL_MATCH_TOLERANCE:
            raise RuntimeError(
                f"R84R1_OBSERVER_OR_SOURCE_PHYSICS_MISMATCH:{cohort}:{wheel}:{initial}:{reference_mode}:{max_delta}"
            )
        physical_max_delta = max(physical_max_delta, max_delta)
        physical_comparison_count += 1

        record_id = f"r84r1_{run_index + 1:05d}" if not replay else "r84r1_seeded_replay_control"
        feature = extract_sensor_feature(sensor_rows)
        if not replay:
            run_index += 1
            feature_by_id[record_id] = feature

        case_record = {
            "record_id": record_id,
            "case_id": result["case_id"],
            "cohort": cohort,
            "wheel_index_QA_ONLY": wheel,
            "initial_state_index": initial,
            "reference_mode": reference_mode,
            "reference_angle_deg": result["reference_angle_deg"],
            "capacity_mode_QA_ONLY": capacity_mode,
            "requested_native_capacity_nm_QA_ONLY": result["requested_native_capacity_nm_QA_ONLY"],
            "gyro_white_noise_std_rad_s": noise_sigma,
            "rng_seed": seed,
            "repetition_index": repetition,
            "native_distribution": result["distribution"],
            "native_module_chain": result["native_module_chain"],
            "controller_feedback_source": result["controller_feedback_source"],
            "native_event_receipts": result["native_event_receipts"],
            "sample_count": len(sensor_rows),
            "repeatability_control": replay,
        }
        qa_record = {
            "record_id": record_id,
            "case_id": result["case_id"],
            "cohort": cohort,
            "wheel_index_QA_ONLY": wheel,
            "initial_state_index": initial,
            "reference_mode": reference_mode,
            "capacity_mode_QA_ONLY": capacity_mode,
            "gyro_white_noise_std_rad_s": noise_sigma,
            "rng_seed": seed,
            "baseline_source": baseline_source,
            "baseline_case_id": baseline_case_id,
            "max_abs_physics_delta_by_field": delta_by_field,
            "max_abs_physics_delta": max_delta,
            "native_event_receipts": result["native_event_receipts"],
            "native_envelopes": {
                "max_abs_allocator_request_nm": result["max_abs_allocator_request_nm"],
                "max_abs_wheel_speed_rad_s": result["max_abs_wheel_speed_rad_s"],
                "max_abs_body_rate_rad_s": result["max_abs_body_rate_rad_s"],
                "max_post_fault_applied_torque_abs_nm_QA_ONLY": result["max_post_fault_applied_torque_abs_nm_QA_ONLY"],
            },
            "full_qa_rows_retained": cohort == "loss50_noise_free_physics_reference",
        }
        if cohort == "loss50_noise_free_physics_reference":
            qa_record["qa_rows"] = qa_rows

        _append_jsonl(sensor_stream, {"record_id": record_id, "rows": sensor_rows})
        _append_jsonl(case_stream, case_record)
        _append_jsonl(qa_stream, qa_record)
        if not replay:
            _append_jsonl(feature_stream, {"record_id": record_id, **feature})
            case_records.append(case_record)

        if not replay and cohort == "loss50_primary" and wheel == 1 and initial == 1 and reference_mode == "pulse05" and repetition == 0:
            selected_replay_reference = {"sensor_rows": sensor_rows, "qa_rows": qa_rows}
            selected_replay_case = spec.copy()

        if run_index and run_index % 100 == 0:
            for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
                stream.flush()
            _write_json(progress_path, {
                "schema_version": "sat-sim.r84r1.progress.v1",
                "status": "RUNNING",
                "unique_runs_completed": run_index,
                "expected_unique_runs": 3159,
                "unique_seed_count": len(seed_set),
                "physical_comparison_count": physical_comparison_count,
                "physical_max_abs_delta": physical_max_delta,
                "last_record_id": record_id,
            })
            gc.collect()
        return {
            "record_id": record_id,
            "sensor_rows": sensor_rows,
            "qa_rows": qa_rows,
            "feature": feature,
            "seed": seed,
        }

    try:
        for spec in run_plan:
            execute_one(spec)
        if run_index != 3159 or len(seed_set) != 3159:
            raise RuntimeError("R84R1_UNIQUE_RUN_OR_SEED_COUNT_MISMATCH")
        if len(loss50_baselines) != 27:
            raise RuntimeError("R84R1_LOSS50_PHYSICS_REFERENCE_COUNT_MISMATCH")
        if selected_replay_reference is None or selected_replay_case is None:
            raise RuntimeError("R84R1_REPLAY_REFERENCE_CASE_NOT_FOUND")

        replay = execute_one(selected_replay_case, replay=True)
        replay_sensor_delta = _max_abs_delta(
            replay["sensor_rows"],
            selected_replay_reference["sensor_rows"],
            OBSERVATION_FIELDS,
        )
        replay_qa_delta = _max_abs_delta(
            replay["qa_rows"],
            selected_replay_reference["qa_rows"],
            QA_PHYSICAL_FIELDS,
        )
        if _max_delta(replay_sensor_delta) > REPLAY_TOLERANCE or _max_delta(replay_qa_delta) > REPLAY_TOLERANCE:
            raise RuntimeError("R84R1_SAME_SEED_REPLAY_NOT_EXACT")

        analysis = _analysis(case_records, feature_by_id)
        _write_json(output_dir / "r84r1_observability_analysis.json", analysis)
        for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
            stream.flush()
            stream.close()

        expected_feature_count = 3159
        if len(feature_by_id) != expected_feature_count:
            raise RuntimeError("R84R1_FEATURE_RECORD_COUNT_MISMATCH")
        if physical_comparison_count != 3159 + 1:
            # Includes the one exact seeded replay, which is independently
            # checked against the same native physical reference.
            raise RuntimeError("R84R1_PHYSICS_COMPARISON_COUNT_MISMATCH")

        artifact_files = (
            "r84r1_sensor_observations.jsonl",
            "r84r1_case_manifest.jsonl",
            "r84r1_physics_qa.jsonl",
            "r84r1_features.jsonl",
            "r84r1_observability_analysis.json",
            "source/r83m-original.zip",
            "source/r83m-original.sha256",
        )
        manifest = {
            "schema_version": "sat-sim.r84r1.native-imu-execution-manifest.v1",
            "status": "PASS",
            "research_only": True,
            "unique_native_run_count": 3159,
            "unique_rng_seed_count": len(seed_set),
            "native_execution_count_including_replay": 3160,
            "sample_count_per_execution": 31,
            "physical_context_count": 27,
            "physical_comparison_count_including_replay": physical_comparison_count,
            "max_abs_physics_delta_to_frozen_baselines": physical_max_delta,
            "native_distribution": "bsk==2.11.0+satfix2",
            "original_upstream_wheel_sha256": config["fixed_native_engine"]["original_upstream_wheel_sha256"],
            "r83m_original_archive_sha256": original_receipt["archive_sha256"],
            "r83m_original_archive_preserved": True,
            "sensor_feature_allowlist": list(OBSERVATION_FIELDS),
            "sensor_file_contains_only_record_id_time_and_approved_raw_gyro": True,
            "healthy_calibration_and_control_seeds_disjoint": True,
            "healthy_and_fault_seeds_disjoint": True,
            "checks": {
                "r83m_original_archive_sha_and_physics_audit": True,
                "all_3159_unique_runs_have_unique_rng_seeds": len(seed_set) == 3159,
                "all_native_recorders_have_31_aligned_samples": True,
                "all_native_cpp_capacity_readbacks_match_predeclared_modes": True,
                "all_native_applied_torques_respect_cpp_capacity": True,
                "healthy_and_loss75_physics_match_original_r83m": True,
                "loss50_physics_matches_noise_free_native_reference": True,
                "observer_noise_does_not_change_native_physics": physical_max_delta <= PHYSICAL_MATCH_TOLERANCE,
                "raw_sensor_file_has_no_truth_or_condition_labels": True,
                "healthy_calibration_and_false_alarm_seeds_are_disjoint": True,
                "healthy_and_fault_seed_sets_are_disjoint": True,
                "same_seed_replay_is_exact": True,
                "all_negative_and_unobservable_conditions_retained": True,
            },
            "artifacts": {name: _sha256(output_dir / name) for name in artifact_files},
            "source_module_sha256": _sha256(Path(run_native_case.__code__.co_filename).resolve()),
            "runner_sha256": _sha256(Path(__file__).resolve()),
            "preregistration_sha256": _sha256(CONFIG_PATH),
            "diagnostic_model_trained": False,
            "classifier_metrics_generated": False,
            "formal_training_or_test_eligible": False,
            "formal_false_alarm_qualification": False,
            "flight_or_hardware_safety_qualified": False,
            "stable_release_eligible": False,
        }
        _write_json(output_dir / "r84r1_execution_manifest.json", manifest)
        _write_json(progress_path, {
            "schema_version": "sat-sim.r84r1.progress.v1",
            "status": "PASS",
            "unique_runs_completed": 3159,
            "native_execution_count_including_replay": 3160,
            "unique_seed_count": 3159,
            "physical_comparison_count": physical_comparison_count,
            "physical_max_abs_delta": physical_max_delta,
        })
        return manifest
    except Exception as exc:
        for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
            try:
                stream.flush()
                stream.close()
            except Exception:
                pass
        _write_json(output_dir / "r84r1_failure.json", {
            "schema_version": "sat-sim.r84r1.native-imu-failure.v1",
            "status": "FAILED",
            "exception_type": type(exc).__name__,
            "reason": str(exc),
            "unique_runs_completed": run_index,
            "unique_seed_count": len(seed_set),
            "physical_comparison_count": physical_comparison_count,
            "physical_max_abs_delta": physical_max_delta,
            "diagnostic_model_trained": False,
        })
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r83m-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = run_pilot(args.r83m_archive, args.output_dir)
    print(json.dumps({
        "status": result["status"],
        "unique_native_run_count": result["unique_native_run_count"],
        "native_execution_count_including_replay": result["native_execution_count_including_replay"],
        "physical_comparison_count_including_replay": result["physical_comparison_count_including_replay"],
        "checks": result["checks"],
        "artifacts": result["artifacts"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
