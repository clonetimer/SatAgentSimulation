"""Execute R84-R2 four-pulse multi-feature native Basilisk IMU observability.

用途：运行 2,862 个唯一 18 秒原生工况和 1 次同 seed 回放；执行健康误报控制、loss50/loss75 与固定 16 特征统计。
参数：--r83m-archive、--r84r1-artifact、--output-dir。
输出：原始 IMU JSONL、条件清单、完整物理 QA、特征、分析与 SHA-256 收据；不训练模型。
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

from sat_sim.experiments.native_imu_rw_observability_r84r2 import (
    CAPACITY_NM,
    INITIAL_STATES,
    NOISE_SIGMAS,
    OBSERVATION_FIELDS,
    QA_PHYSICAL_FIELDS,
    REFERENCE_MODES,
    WHEELS,
    run_native_case,
)
from scripts.audit_r83m_native_archive import audit_archive

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "research" / "r84r2_multipulse_multifeature_observability_prereg.json"
PRIMARY_SIGMA = 0.0001
SENSITIVITY_SIGMAS = (0.00005, 0.0002)
PRIMARY_REPETITIONS = 30
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
    "noise_free_physics_reference": None,
}
COHORT_CODES = {
    "healthy_calibration": 1,
    "healthy_false_alarm_control": 2,
    "loss50_primary": 3,
    "loss75_primary": 4,
    "healthy_sensitivity": 5,
    "loss50_sensitivity": 6,
    "loss75_sensitivity": 7,
    "noise_free_physics_reference": 8,
}
CAPACITY_INDEX = {"healthy": 0, "loss50": 1, "loss75": 2}
NOISE_INDEX = {0.0: 0, 0.00005: 1, 0.0001: 2, 0.0002: 3}
REFERENCE_INDEX = {"zero": 0, "pulse09x4": 1}
WINDOWS = (
    {"pulse": 1, "polarity": 1.0, "pre": (0.4, 1.6), "post": (2.6, 3.6)},
    {"pulse": 2, "polarity": -1.0, "pre": (4.8, 6.0), "post": (7.0, 8.0)},
    {"pulse": 3, "polarity": 1.0, "pre": (9.2, 10.4), "post": (11.4, 12.4)},
    {"pulse": 4, "polarity": -1.0, "pre": (13.6, 14.8), "post": (15.8, 16.8)},
)
FEATURE_FIELDS = tuple(
    [f"aligned_mean_response_{axis}" for axis in "xyz"]
    + [f"mean_abs_pulse_mean_delta_{axis}" for axis in "xyz"]
    + [f"mean_rms_delta_{axis}" for axis in "xyz"]
    + [f"mean_std_delta_{axis}" for axis in "xyz"]
    + [f"mean_diff_rms_delta_{axis}" for axis in "xyz"]
    + ["mean_vector_rms_delta"]
)
PRIMARY_COHORTS = (
    "healthy_calibration",
    "healthy_false_alarm_control",
    "loss50_primary",
    "loss75_primary",
)
SENSITIVITY_COHORTS = (
    "healthy_sensitivity",
    "loss50_sensitivity",
    "loss75_sensitivity",
)
PHYSICAL_CAPACITY_COHORTS = ("healthy", "loss50", "loss75")
PHYSICAL_MATCH_TOLERANCE = 1e-8
REPLAY_TOLERANCE = 1e-12
R83M_PREFIX = "rw{wheel}_initial{initial}_{reference}_{capacity}"


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
        raise RuntimeError("R84R2_PHYSICS_BASELINE_SAMPLE_COUNT_MISMATCH")
    maxima = {field: 0.0 for field in fields}
    for a, b in zip(left, right):
        if abs(float(a["time_s"]) - float(b["time_s"])) > 1e-12:
            raise RuntimeError("R84R2_PHYSICS_BASELINE_TIME_AXIS_MISMATCH")
        for field in fields:
            maxima[field] = max(maxima[field], abs(float(a[field]) - float(b[field])))
    return maxima


def _max_delta(values: dict[str, float]) -> float:
    return max(values.values(), default=0.0)


def _load_sources(r83m_archive: Path, r84r1_artifact: Path, config: dict[str, Any]):
    r0 = config["references"]
    if _sha256(r84r1_artifact) != r0["r84r1_artifact_sha256"]:
        raise RuntimeError("R84R2_R84R1_REFERENCE_ARTIFACT_SHA256_MISMATCH")
    receipt = audit_archive(r83m_archive)
    if receipt.get("physics_archive_verified") is not True:
        raise RuntimeError("R84R2_R83M_ORIGINAL_ARCHIVE_NOT_VERIFIED")
    if receipt.get("archive_sha256") != r0["original_r83m_archive_sha256"]:
        raise RuntimeError("R84R2_R83M_ARCHIVE_DOES_NOT_MATCH_PREREGISTRATION")
    with ZipFile(r83m_archive, "r") as archive:
        payload = json.loads(archive.read("r83m_genuine_native_54_original.json"))
    with ZipFile(r84r1_artifact, "r") as archive:
        nested_r0 = archive.read("source/r84-r0-reference.zip")
    nested_r0_sha = hashlib.sha256(nested_r0).hexdigest()
    if nested_r0_sha != r0["r84r0_artifact_sha256"]:
        raise RuntimeError("R84R2_NESTED_R84R0_REFERENCE_SHA256_MISMATCH")
    return receipt, payload, nested_r0


def seed_for(
    cohort: str,
    wheel: int,
    initial: int,
    reference_mode: str,
    capacity_mode: str,
    noise_sigma: float,
    repetition: int,
) -> int:
    if cohort not in COHORT_CODES:
        raise ValueError("R84R2_UNKNOWN_SEED_COHORT")
    if capacity_mode not in CAPACITY_INDEX:
        raise ValueError("R84R2_UNKNOWN_SEED_CAPACITY")
    if noise_sigma not in NOISE_INDEX:
        raise ValueError("R84R2_UNKNOWN_SEED_NOISE_LEVEL")
    if reference_mode not in REFERENCE_INDEX:
        raise ValueError("R84R2_UNKNOWN_SEED_REFERENCE_MODE")
    return (
        20_000_000
        + COHORT_CODES[cohort] * 10_000_000
        + wheel * 1_000_000
        + initial * 100_000
        + REFERENCE_INDEX[reference_mode] * 10_000
        + CAPACITY_INDEX[capacity_mode] * 1_000
        + NOISE_INDEX[noise_sigma] * 100
        + repetition
    )


def iter_unique_run_specs() -> Iterable[dict[str, Any]]:
    """Yield exactly the preregistered 2,862 unique physical executions."""
    for wheel in WHEELS:
        for initial in range(len(INITIAL_STATES)):
            for reference_mode in REFERENCE_MODES:
                for capacity_mode in PHYSICAL_CAPACITY_COHORTS:
                    yield {
                        "cohort": "noise_free_physics_reference",
                        "wheel": wheel,
                        "initial": initial,
                        "reference_mode": reference_mode,
                        "capacity_mode": capacity_mode,
                        "gyro_noise_std_rad_s": 0.0,
                        "repetition": 0,
                    }
                for cohort in PRIMARY_COHORTS:
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
                    for cohort in SENSITIVITY_COHORTS:
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
    design = config["frozen_replication_design"]
    if design.get("unique_native_runs") != 2862 or design.get("total_native_executions") != 2863:
        raise RuntimeError("R84R2_FROZEN_EXECUTION_COUNTS_CHANGED")
    specs = list(iter_unique_run_specs())
    if len(specs) != 2862:
        raise RuntimeError("R84R2_PREFLIGHT_UNIQUE_RUN_COUNT_MISMATCH")
    seeds = [
        seed_for(
            x["cohort"], x["wheel"], x["initial"], x["reference_mode"],
            x["capacity_mode"], x["gyro_noise_std_rad_s"], x["repetition"]
        )
        for x in specs
    ]
    if len(set(seeds)) != len(seeds) or min(seeds) <= 0 or max(seeds) >= 2**31:
        raise RuntimeError("R84R2_PREFLIGHT_SEED_COLLISION_OR_RANGE")
    from collections import Counter
    counts = Counter(x["cohort"] for x in specs)
    expected = {
        "noise_free_physics_reference": 54,
        "healthy_calibration": 540,
        "healthy_false_alarm_control": 540,
        "loss50_primary": 540,
        "loss75_primary": 540,
        "healthy_sensitivity": 216,
        "loss50_sensitivity": 216,
        "loss75_sensitivity": 216,
    }
    if dict(counts) != expected:
        raise RuntimeError("R84R2_PREFLIGHT_COHORT_COUNTS_MISMATCH")
    healthy = {
        seed for x, seed in zip(specs, seeds)
        if x["cohort"] in ("healthy_calibration", "healthy_false_alarm_control", "healthy_sensitivity")
    }
    fault = {
        seed for x, seed in zip(specs, seeds)
        if x["capacity_mode"] in ("loss50", "loss75")
        and x["cohort"] != "noise_free_physics_reference"
    }
    if healthy.intersection(fault):
        raise RuntimeError("R84R2_PREFLIGHT_HEALTHY_FAULT_SEED_OVERLAP")
    cal = {
        seed for x, seed in zip(specs, seeds) if x["cohort"] == HEALTHY_CALIBRATION
    }
    control = {
        seed for x, seed in zip(specs, seeds) if x["cohort"] == HEALTHY_CONTROL
    }
    if cal.intersection(control):
        raise RuntimeError("R84R2_PREFLIGHT_CALIBRATION_CONTROL_SEED_OVERLAP")
    return specs


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _rms(values: list[float]) -> float:
    return math.sqrt(_mean([value * value for value in values]))


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        raise ValueError("R84R2_STD_REQUIRES_TWO_SAMPLES")
    avg = _mean(values)
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def extract_multifeatures(sensor_rows: list[dict[str, float]]) -> dict[str, float]:
    """Compute the fixed 16-feature vector from raw gyro only."""
    expected_keys = {"time_s", *OBSERVATION_FIELDS}
    if len(sensor_rows) != 91 or any(set(row) != expected_keys for row in sensor_rows):
        raise RuntimeError("R84R2_SENSOR_FEATURE_INPUT_SCHEMA_MISMATCH")
    time_values = [float(row["time_s"]) for row in sensor_rows]
    for index, time_s in enumerate(time_values):
        if abs(time_s - 0.2 * index) > 1e-10:
            raise RuntimeError("R84R2_SENSOR_FEATURE_TIME_GRID_MISMATCH")

    per_pulse: list[dict[str, Any]] = []
    for window in WINDOWS:
        pre = [
            row for row in sensor_rows
            if window["pre"][0] - 1e-10 <= row["time_s"] <= window["pre"][1] + 1e-10
        ]
        post = [
            row for row in sensor_rows
            if window["post"][0] - 1e-10 <= row["time_s"] <= window["post"][1] + 1e-10
        ]
        if len(pre) != 7 or len(post) != 6:
            raise RuntimeError("R84R2_PREDECLARED_PULSE_WINDOW_COUNT_MISMATCH")
        axis_stats = {}
        for axis in "xyz":
            key = f"imu.raw_gyro_rad_s_{axis}"
            a = [float(row[key]) for row in pre]
            b = [float(row[key]) for row in post]
            delta_mean = _mean(b) - _mean(a)
            delta_rms = _rms(b) - _rms(a)
            delta_std = _sample_std(b) - _sample_std(a)
            delta_diff_rms = (
                _rms([(b[i + 1] - b[i]) / 0.2 for i in range(len(b) - 1)])
                - _rms([(a[i + 1] - a[i]) / 0.2 for i in range(len(a) - 1)])
            )
            axis_stats[axis] = {
                "mean_delta": delta_mean,
                "aligned_mean_delta": float(window["polarity"]) * delta_mean,
                "abs_mean_delta": abs(delta_mean),
                "rms_delta": delta_rms,
                "std_delta": delta_std,
                "diff_rms_delta": delta_diff_rms,
            }
        pre_vector = [
            math.sqrt(sum(float(row[f"imu.raw_gyro_rad_s_{axis}"]) ** 2 for axis in "xyz"))
            for row in pre
        ]
        post_vector = [
            math.sqrt(sum(float(row[f"imu.raw_gyro_rad_s_{axis}"]) ** 2 for axis in "xyz"))
            for row in post
        ]
        per_pulse.append({
            "pulse": window["pulse"],
            "polarity": window["polarity"],
            "axis": axis_stats,
            "vector_rms_delta": _rms(post_vector) - _rms(pre_vector),
        })

    result: dict[str, float] = {}
    for axis in "xyz":
        result[f"aligned_mean_response_{axis}"] = _mean(
            [pulse["axis"][axis]["aligned_mean_delta"] for pulse in per_pulse]
        )
        result[f"mean_abs_pulse_mean_delta_{axis}"] = _mean(
            [pulse["axis"][axis]["abs_mean_delta"] for pulse in per_pulse]
        )
        result[f"mean_rms_delta_{axis}"] = _mean(
            [pulse["axis"][axis]["rms_delta"] for pulse in per_pulse]
        )
        result[f"mean_std_delta_{axis}"] = _mean(
            [pulse["axis"][axis]["std_delta"] for pulse in per_pulse]
        )
        result[f"mean_diff_rms_delta_{axis}"] = _mean(
            [pulse["axis"][axis]["diff_rms_delta"] for pulse in per_pulse]
        )
    result["mean_vector_rms_delta"] = _mean(
        [pulse["vector_rms_delta"] for pulse in per_pulse]
    )
    if set(result) != set(FEATURE_FIELDS) or not all(math.isfinite(v) for v in result.values()):
        raise RuntimeError("R84R2_FEATURE_VECTOR_INVALID")
    return result


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("R84R2_EMPTY_STATISTIC")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _nearest_rank(values: list[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("R84R2_EMPTY_THRESHOLD_CALIBRATION")
    return ordered[max(1, math.ceil(probability * len(ordered))) - 1]


def _distribution(values: list[float]) -> dict[str, Any]:
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
    if not left or not right:
        raise ValueError("R84R2_EMPTY_CLIFFS_DELTA_INPUT")
    greater = less = 0
    for a in left:
        for b in right:
            if a > b:
                greater += 1
            elif a < b:
                less += 1
    return (greater - less) / (len(left) * len(right))


def wasserstein_1_equal_n(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("R84R2_WASSERSTEIN_REQUIRES_EQUAL_NONEMPTY_SAMPLES")
    a, b = sorted(left), sorted(right)
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _robust_parameters(calibration: list[dict[str, float]]) -> tuple[dict[str, float], dict[str, float]]:
    centers: dict[str, float] = {}
    scales: dict[str, float] = {}
    for field in FEATURE_FIELDS:
        values = [float(item[field]) for item in calibration]
        center = median(values)
        deviations = [abs(value - center) for value in values]
        mad = median(deviations)
        q75 = _quantile(values, 0.75)
        q25 = _quantile(values, 0.25)
        scale = 1.4826 * mad
        if scale <= 1e-12:
            scale = (q75 - q25) / 1.349
        if scale <= 1e-12:
            scale = 1e-12
        centers[field] = center
        scales[field] = scale
    return centers, scales


def _composite_score(feature: dict[str, float], centers: dict[str, float], scales: dict[str, float]) -> float:
    return max(abs(float(feature[field]) - centers[field]) / scales[field] for field in FEATURE_FIELDS)


def _analysis(case_records: list[dict[str, Any]], feature_by_id: dict[str, dict[str, float]]) -> dict[str, Any]:
    groups: dict[tuple[int, int, str, float], dict[str, list[dict[str, float]]]] = {}
    for case in case_records:
        if case["cohort"] == "noise_free_physics_reference" or case.get("repeatability_control"):
            continue
        key = (
            int(case["wheel_index_QA_ONLY"]),
            int(case["initial_state_index"]),
            str(case["reference_mode"]),
            float(case["gyro_white_noise_std_rad_s"]),
        )
        groups.setdefault(key, {}).setdefault(str(case["cohort"]), []).append(
            feature_by_id[case["record_id"]]
        )

    primary_results: list[dict[str, Any]] = []
    sensitivity_results: list[dict[str, Any]] = []
    feature_effects: dict[str, dict[str, list[float]]] = {
        field: {"loss50": [], "loss75": []} for field in FEATURE_FIELDS
    }
    composite_effects = {"loss50": [], "loss75": []}
    pooled_counts = {"healthy_false_alarms": 0, "healthy_controls": 0, "loss50_exceedances": 0, "loss75_exceedances": 0, "fault_runs_per_severity": 0}

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
            controls = group[HEALTHY_CONTROL]
            loss50 = group["loss50_primary"]
            loss75 = group["loss75_primary"]
            if any(len(items) != 30 for items in (calibration, controls, loss50, loss75)):
                raise RuntimeError("R84R2_PRIMARY_COHORT_COUNT_MISMATCH")
            centers, scales = _robust_parameters(calibration)
            cal_scores = [_composite_score(item, centers, scales) for item in calibration]
            control_scores = [_composite_score(item, centers, scales) for item in controls]
            loss50_scores = [_composite_score(item, centers, scales) for item in loss50]
            loss75_scores = [_composite_score(item, centers, scales) for item in loss75]
            threshold = _nearest_rank(cal_scores, 0.90)
            false_count = sum(value > threshold for value in control_scores)
            loss50_count = sum(value > threshold for value in loss50_scores)
            loss75_count = sum(value > threshold for value in loss75_scores)
            pooled_counts["healthy_false_alarms"] += false_count
            pooled_counts["healthy_controls"] += len(controls)
            pooled_counts["loss50_exceedances"] += loss50_count
            pooled_counts["loss75_exceedances"] += loss75_count
            pooled_counts["fault_runs_per_severity"] += len(loss50)

            feature_summary = {}
            for field in FEATURE_FIELDS:
                cal_values = [item[field] for item in calibration]
                control_values = [item[field] for item in controls]
                loss50_values = [item[field] for item in loss50]
                loss75_values = [item[field] for item in loss75]
                d50 = cliffs_delta(loss50_values, control_values)
                d75 = cliffs_delta(loss75_values, control_values)
                w50 = wasserstein_1_equal_n(loss50_values, control_values)
                w75 = wasserstein_1_equal_n(loss75_values, control_values)
                feature_effects[field]["loss50"].append(d50)
                feature_effects[field]["loss75"].append(d75)
                feature_summary[field] = {
                    "calibration": _distribution(cal_values),
                    "healthy_control": _distribution(control_values),
                    "loss50": _distribution(loss50_values),
                    "loss75": _distribution(loss75_values),
                    "cliffs_delta_loss50_vs_healthy_control": d50,
                    "cliffs_delta_loss75_vs_healthy_control": d75,
                    "wasserstein_1_loss50_vs_healthy_control_rad_s": w50,
                    "wasserstein_1_loss75_vs_healthy_control_rad_s": w75,
                }
            composite_effects["loss50"].append(cliffs_delta(loss50_scores, control_scores))
            composite_effects["loss75"].append(cliffs_delta(loss75_scores, control_scores))
            primary_results.append({
                **context,
                "healthy_calibration_count": len(calibration),
                "healthy_false_alarm_control_count": len(controls),
                "healthy_only_composite_threshold_90th_nearest_rank": threshold,
                "healthy_false_alarm_count": false_count,
                "healthy_false_alarm_rate": false_count / len(controls),
                "loss50_composite_score_distribution": _distribution(loss50_scores),
                "loss50_threshold_exceedance_count": loss50_count,
                "loss50_threshold_exceedance_rate": loss50_count / len(loss50),
                "loss75_composite_score_distribution": _distribution(loss75_scores),
                "loss75_threshold_exceedance_count": loss75_count,
                "loss75_threshold_exceedance_rate": loss75_count / len(loss75),
                "composite_cliffs_delta_loss50_vs_healthy_control": cliffs_delta(loss50_scores, control_scores),
                "composite_cliffs_delta_loss75_vs_healthy_control": cliffs_delta(loss75_scores, control_scores),
                "composite_wasserstein_1_loss50_vs_healthy_control": wasserstein_1_equal_n(loss50_scores, control_scores),
                "composite_wasserstein_1_loss75_vs_healthy_control": wasserstein_1_equal_n(loss75_scores, control_scores),
                "feature_results": feature_summary,
            })
        else:
            for cohort in SENSITIVITY_COHORTS:
                if len(group.get(cohort, [])) != SENSITIVITY_REPETITIONS:
                    raise RuntimeError("R84R2_SENSITIVITY_COHORT_COUNT_MISMATCH")
            healthy = group["healthy_sensitivity"]
            loss50 = group["loss50_sensitivity"]
            loss75 = group["loss75_sensitivity"]
            centers, scales = _robust_parameters(healthy)
            hscore = [_composite_score(item, centers, scales) for item in healthy]
            s50 = [_composite_score(item, centers, scales) for item in loss50]
            s75 = [_composite_score(item, centers, scales) for item in loss75]
            sensitivity_results.append({
                **context,
                "healthy_composite_score_distribution": _distribution(hscore),
                "loss50_composite_score_distribution": _distribution(s50),
                "loss75_composite_score_distribution": _distribution(s75),
                "composite_cliffs_delta_loss50_vs_healthy": cliffs_delta(s50, hscore),
                "composite_cliffs_delta_loss75_vs_healthy": cliffs_delta(s75, hscore),
                "composite_wasserstein_1_loss50_vs_healthy": wasserstein_1_equal_n(s50, hscore),
                "composite_wasserstein_1_loss75_vs_healthy": wasserstein_1_equal_n(s75, hscore),
                "feature_results": {
                    field: {
                        "healthy": _distribution([item[field] for item in healthy]),
                        "loss50": _distribution([item[field] for item in loss50]),
                        "loss75": _distribution([item[field] for item in loss75]),
                        "cliffs_delta_loss50_vs_healthy": cliffs_delta(
                            [item[field] for item in loss50], [item[field] for item in healthy]
                        ),
                        "cliffs_delta_loss75_vs_healthy": cliffs_delta(
                            [item[field] for item in loss75], [item[field] for item in healthy]
                        ),
                    }
                    for field in FEATURE_FIELDS
                },
                "primary_threshold_transferred": False,
            })

    if len(primary_results) != 18 or len(sensitivity_results) != 36:
        raise RuntimeError("R84R2_ANALYSIS_CONTEXT_COUNT_MISMATCH")
    return {
        "schema_version": "sat-sim.r84r2.multipulse-multifeature-analysis.v1",
        "statistical_unit": "one independent complete 18-second native Basilisk execution",
        "feature_source": "official raw IMU gyro only",
        "feature_fields": list(FEATURE_FIELDS),
        "pulse_windows": [
            {"pulse": w["pulse"], "polarity": w["polarity"], "pre": list(w["pre"]), "post": list(w["post"])}
            for w in WINDOWS
        ],
        "composite_score": "max absolute robust-z across 16 frozen features; robust center/scale from 30 healthy calibration runs",
        "threshold": "nearest-rank 90th percentile of 30 healthy calibration composite scores",
        "primary_context_count": len(primary_results),
        "primary_pooled_counts": pooled_counts,
        "primary_pooled_rates": {
            "healthy_false_alarm_rate": pooled_counts["healthy_false_alarms"] / pooled_counts["healthy_controls"],
            "loss50_threshold_exceedance_rate": pooled_counts["loss50_exceedances"] / pooled_counts["fault_runs_per_severity"],
            "loss75_threshold_exceedance_rate": pooled_counts["loss75_exceedances"] / pooled_counts["fault_runs_per_severity"],
        },
        "primary_composite_effects": {
            severity: {
                "mean_cliffs_delta_across_contexts": sum(values) / len(values),
                "median_cliffs_delta_across_contexts": median(values),
                "min_cliffs_delta_across_contexts": min(values),
                "max_cliffs_delta_across_contexts": max(values),
            }
            for severity, values in composite_effects.items()
        },
        "primary_feature_effects": {
            field: {
                severity: {
                    "mean_cliffs_delta_across_contexts": sum(values) / len(values),
                    "median_cliffs_delta_across_contexts": median(values),
                    "min_cliffs_delta_across_contexts": min(values),
                    "max_cliffs_delta_across_contexts": max(values),
                }
                for severity, values in by_severity.items()
            }
            for field, by_severity in feature_effects.items()
        },
        "primary_results": primary_results,
        "sensitivity_context_count": len(sensitivity_results),
        "sensitivity_results": sensitivity_results,
        "no_classifier_training": True,
        "no_formal_false_alarm_qualification": True,
        "no_formal_training_or_test_eligibility": True,
    }


def run_pilot(r83m_archive: Path, r84r1_artifact: Path, output_dir: Path) -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("status") != "PREREGISTERED_NOT_EXECUTED":
        raise RuntimeError("R84R2_PREREGISTRATION_STATUS_INVALID")
    run_plan = validate_preregistered_run_plan(config)
    original_receipt, original_payload, nested_r0 = _load_sources(r83m_archive, r84r1_artifact, config)

    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = output_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    preserved_r83m = source_dir / "r83m-original.zip"
    shutil.copy2(r83m_archive, preserved_r83m)
    (source_dir / "r83m-original.sha256").write_text(
        f"{_sha256(preserved_r83m)}  r83m-original.zip\n", encoding="utf-8"
    )
    preserved_r84r1 = source_dir / "r84r1-reference.zip"
    shutil.copy2(r84r1_artifact, preserved_r84r1)
    (source_dir / "r84r1-reference.sha256").write_text(
        f"{_sha256(preserved_r84r1)}  r84r1-reference.zip\n", encoding="utf-8"
    )
    preserved_r84r0 = source_dir / "r84-r0-reference.zip"
    preserved_r84r0.write_bytes(nested_r0)
    (source_dir / "r84-r0-reference.sha256").write_text(
        f"{_sha256(preserved_r84r0)}  r84-r0-reference.zip\n", encoding="utf-8"
    )

    sensor_path = output_dir / "r84r2_sensor_observations.jsonl"
    case_path = output_dir / "r84r2_case_manifest.jsonl"
    qa_path = output_dir / "r84r2_physics_qa.jsonl"
    feature_path = output_dir / "r84r2_features.jsonl"
    progress_path = output_dir / "r84r2_progress.json"
    sensor_stream = sensor_path.open("w", encoding="utf-8")
    case_stream = case_path.open("w", encoding="utf-8")
    qa_stream = qa_path.open("w", encoding="utf-8")
    feature_stream = feature_path.open("w", encoding="utf-8")

    seed_set: set[int] = set()
    case_records: list[dict[str, Any]] = []
    feature_by_id: dict[str, dict[str, float]] = {}
    physical_baselines: dict[tuple[int, int, str, str], list[dict[str, float]]] = {}
    selected_replay_reference: dict[str, Any] | None = None
    selected_replay_spec: dict[str, Any] | None = None
    physical_comparison_count = 0
    r83m_comparison_count = 0
    max_abs_physics_delta = 0.0
    run_index = 0

    def execute_one(spec: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
        nonlocal run_index, physical_comparison_count, r83m_comparison_count
        nonlocal max_abs_physics_delta, selected_replay_reference, selected_replay_spec
        cohort = str(spec["cohort"])
        wheel = int(spec["wheel"])
        initial = int(spec["initial"])
        reference_mode = str(spec["reference_mode"])
        capacity_mode = str(spec["capacity_mode"])
        noise_sigma = float(spec["gyro_noise_std_rad_s"])
        repetition = int(spec["repetition"])
        seed = seed_for(
            cohort, wheel, initial, reference_mode, capacity_mode, noise_sigma, repetition
        )
        if not replay:
            if seed in seed_set:
                raise RuntimeError(f"R84R2_DUPLICATED_INDEPENDENT_SEED:{seed}")
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
        if len(sensor_rows) != 91 or any(set(row) != expected_sensor_keys for row in sensor_rows):
            raise RuntimeError("R84R2_SENSOR_FEATURE_WHITELIST_VIOLATION")
        if any(
            any("QA_ONLY" in key or key.startswith(("hub.", "fault_", "source_fault_")) for key in row)
            for row in sensor_rows
        ):
            raise RuntimeError("R84R2_SENSOR_FEATURE_TRUTH_LEAKAGE")

        baseline_key = (wheel, initial, reference_mode, capacity_mode)
        if cohort == "noise_free_physics_reference":
            if baseline_key in physical_baselines:
                raise RuntimeError("R84R2_DUPLICATED_NOISE_FREE_PHYSICS_REFERENCE")
            physical_baselines[baseline_key] = qa_rows
            baseline_rows = qa_rows
            baseline_source = "SELF_NOISE_FREE_NATIVE_REFERENCE"
        else:
            if baseline_key not in physical_baselines:
                raise RuntimeError("R84R2_NOISE_FREE_PHYSICS_REFERENCE_MISSING")
            baseline_rows = physical_baselines[baseline_key]
            baseline_source = "R84R2_NOISE_FREE_NATIVE_REFERENCE"

        delta_by_field = _max_abs_delta(qa_rows, baseline_rows, QA_PHYSICAL_FIELDS)
        max_delta = _max_delta(delta_by_field)
        if max_delta > PHYSICAL_MATCH_TOLERANCE:
            raise RuntimeError(
                f"R84R2_OBSERVER_OR_PHYSICS_BASELINE_MISMATCH:{cohort}:{wheel}:{initial}:{reference_mode}:{capacity_mode}:{max_delta}"
            )
        max_abs_physics_delta = max(max_abs_physics_delta, max_delta)
        physical_comparison_count += 1

        # The first 6 s are required to match the immutable original R83m
        # reference for healthy/loss75. The second pulse begins only at 6.2 s.
        if cohort == "noise_free_physics_reference" and capacity_mode in ("healthy", "loss75"):
            r83m_mode = "pulse09" if reference_mode == "pulse09x4" else "zero"
            source_case_id = R83M_PREFIX.format(
                wheel=wheel, initial=initial, reference=r83m_mode, capacity=capacity_mode
            )
            r83m_rows = original_payload["physical_source_cases"][source_case_id]["rows"]
            first_six = [row for row in qa_rows if row["time_s"] <= 6.0 + 1e-12]
            if len(first_six) != 31 or len(r83m_rows) != 31:
                raise RuntimeError("R84R2_R83M_FIRST_SIX_SECOND_SAMPLE_COUNT_MISMATCH")
            r83m_delta = _max_abs_delta(first_six, r83m_rows, QA_PHYSICAL_FIELDS)
            if _max_delta(r83m_delta) > PHYSICAL_MATCH_TOLERANCE:
                raise RuntimeError(
                    f"R84R2_FIRST_SIX_SECONDS_DO_NOT_MATCH_R83M:{source_case_id}:{_max_delta(r83m_delta)}"
                )
            r83m_comparison_count += 1

        record_id = f"r84r2_{run_index + 1:05d}" if not replay else "r84r2_seeded_replay_control"
        feature = extract_multifeatures(sensor_rows)
        if not replay:
            run_index += 1
            feature_by_id[record_id] = feature

        case_record = {
            "record_id": record_id,
            "case_id": result["case_id"],
            "cohort": cohort,
            "wheel_index_QA_ONLY": wheel,
            "initial_state_index": initial,
            "initial_attitude_mrp": result["initial_attitude_mrp"],
            "reference_mode": reference_mode,
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
            "max_abs_physics_delta_by_field": delta_by_field,
            "max_abs_physics_delta": max_delta,
            "r83m_first_six_seconds_compared": cohort == "noise_free_physics_reference" and capacity_mode in ("healthy", "loss75"),
            "native_event_receipts": result["native_event_receipts"],
            "native_envelopes": {
                "max_abs_allocator_request_nm": result["max_abs_allocator_request_nm"],
                "max_abs_wheel_speed_rad_s": result["max_abs_wheel_speed_rad_s"],
                "max_abs_body_rate_rad_s": result["max_abs_body_rate_rad_s"],
                "max_post_fault_applied_torque_abs_nm_QA_ONLY": result["max_post_fault_applied_torque_abs_nm_QA_ONLY"],
            },
            "qa_rows": qa_rows,
        }

        _append_jsonl(sensor_stream, {"record_id": record_id, "rows": sensor_rows})
        _append_jsonl(case_stream, case_record)
        _append_jsonl(qa_stream, qa_record)
        if not replay:
            _append_jsonl(feature_stream, {"record_id": record_id, **feature})
            case_records.append(case_record)

        if (
            not replay
            and cohort == "loss75_primary"
            and wheel == 1
            and initial == 1
            and reference_mode == "pulse09x4"
            and repetition == 0
        ):
            selected_replay_reference = {"sensor_rows": sensor_rows, "qa_rows": qa_rows}
            selected_replay_spec = spec.copy()

        if run_index and run_index % 50 == 0:
            for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
                stream.flush()
            _write_json(progress_path, {
                "schema_version": "sat-sim.r84r2.progress.v1",
                "status": "RUNNING",
                "unique_runs_completed": run_index,
                "expected_unique_runs": 2862,
                "unique_seed_count": len(seed_set),
                "physical_comparison_count": physical_comparison_count,
                "r83m_first_six_second_comparison_count": r83m_comparison_count,
                "max_abs_physics_delta": max_abs_physics_delta,
                "last_record_id": record_id,
            })
            gc.collect()
        return {"record_id": record_id, "sensor_rows": sensor_rows, "qa_rows": qa_rows, "seed": seed}

    try:
        for spec in run_plan:
            execute_one(spec)
        if run_index != 2862 or len(seed_set) != 2862:
            raise RuntimeError("R84R2_UNIQUE_RUN_OR_SEED_COUNT_MISMATCH")
        if len(physical_baselines) != 54:
            raise RuntimeError("R84R2_PHYSICAL_BASELINE_COUNT_MISMATCH")
        if r83m_comparison_count != 36:
            raise RuntimeError("R84R2_R83M_FIRST_SIX_SECOND_COMPARISON_COUNT_MISMATCH")
        if selected_replay_reference is None or selected_replay_spec is None:
            raise RuntimeError("R84R2_REPLAY_REFERENCE_CASE_NOT_FOUND")

        replay = execute_one(selected_replay_spec, replay=True)
        replay_sensor_delta = _max_abs_delta(
            replay["sensor_rows"], selected_replay_reference["sensor_rows"], OBSERVATION_FIELDS
        )
        replay_qa_delta = _max_abs_delta(
            replay["qa_rows"], selected_replay_reference["qa_rows"], QA_PHYSICAL_FIELDS
        )
        if _max_delta(replay_sensor_delta) > REPLAY_TOLERANCE or _max_delta(replay_qa_delta) > REPLAY_TOLERANCE:
            raise RuntimeError("R84R2_SAME_SEED_REPLAY_NOT_EXACT")

        analysis = _analysis(case_records, feature_by_id)
        _write_json(output_dir / "r84r2_multifeature_observability_analysis.json", analysis)
        for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
            stream.flush()
            stream.close()

        if len(feature_by_id) != 2862:
            raise RuntimeError("R84R2_FEATURE_RECORD_COUNT_MISMATCH")
        if physical_comparison_count != 2863:
            raise RuntimeError("R84R2_PHYSICS_COMPARISON_COUNT_MISMATCH")

        artifact_files = (
            "r84r2_sensor_observations.jsonl",
            "r84r2_case_manifest.jsonl",
            "r84r2_physics_qa.jsonl",
            "r84r2_features.jsonl",
            "r84r2_multifeature_observability_analysis.json",
            "source/r83m-original.zip",
            "source/r83m-original.sha256",
            "source/r84r1-reference.zip",
            "source/r84r1-reference.sha256",
            "source/r84-r0-reference.zip",
            "source/r84-r0-reference.sha256",
        )
        manifest = {
            "schema_version": "sat-sim.r84r2.native-imu-execution-manifest.v1",
            "status": "PASS",
            "research_only": True,
            "unique_native_run_count": 2862,
            "unique_rng_seed_count": len(seed_set),
            "native_execution_count_including_replay": 2863,
            "sample_count_per_execution": 91,
            "physical_context_count": 18,
            "physical_reference_count": len(physical_baselines),
            "physical_comparison_count_including_replay": physical_comparison_count,
            "r83m_first_six_second_comparison_count": r83m_comparison_count,
            "max_abs_physics_delta_to_noise_free_baselines": max_abs_physics_delta,
            "native_distribution": "bsk==2.11.0+satfix2",
            "original_upstream_wheel_sha256": config["fixed_native_engine"]["original_upstream_wheel_sha256"],
            "r83m_original_archive_sha256": original_receipt["archive_sha256"],
            "r84r1_artifact_sha256": config["references"]["r84r1_artifact_sha256"],
            "r84r0_artifact_sha256": config["references"]["r84r0_artifact_sha256"],
            "all_reference_archives_preserved": True,
            "sensor_feature_allowlist": list(OBSERVATION_FIELDS),
            "sensor_file_contains_only_record_id_time_and_approved_raw_gyro": True,
            "healthy_calibration_and_control_seeds_disjoint": True,
            "healthy_and_fault_seeds_disjoint": True,
            "checks": {
                "r83m_original_archive_sha_and_physics_audit": True,
                "r84r1_reference_artifact_sha256_verified": True,
                "r84r0_nested_reference_artifact_sha256_verified": True,
                "all_2862_unique_runs_have_unique_rng_seeds": len(seed_set) == 2862,
                "all_native_recorders_have_91_aligned_samples": True,
                "all_native_cpp_capacity_readbacks_match_predeclared_modes": True,
                "all_native_applied_torques_respect_cpp_capacity": True,
                "all_54_physical_noise_free_references_created": len(physical_baselines) == 54,
                "all_noise_runs_match_same_context_noise_free_native_physics": max_abs_physics_delta <= PHYSICAL_MATCH_TOLERANCE,
                "healthy_and_loss75_first_six_seconds_match_original_r83m": r83m_comparison_count == 36,
                "observer_noise_does_not_change_native_physics": max_abs_physics_delta <= PHYSICAL_MATCH_TOLERANCE,
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
        _write_json(output_dir / "r84r2_execution_manifest.json", manifest)
        _write_json(progress_path, {
            "schema_version": "sat-sim.r84r2.progress.v1",
            "status": "PASS",
            "unique_runs_completed": 2862,
            "native_execution_count_including_replay": 2863,
            "unique_seed_count": len(seed_set),
            "physical_comparison_count": physical_comparison_count,
            "r83m_first_six_second_comparison_count": r83m_comparison_count,
            "max_abs_physics_delta": max_abs_physics_delta,
        })
        return manifest
    except Exception as exc:
        for stream in (sensor_stream, case_stream, qa_stream, feature_stream):
            try:
                stream.flush()
                stream.close()
            except Exception:
                pass
        _write_json(output_dir / "r84r2_failure.json", {
            "schema_version": "sat-sim.r84r2.native-imu-failure.v1",
            "status": "FAILED",
            "exception_type": type(exc).__name__,
            "reason": str(exc),
            "unique_runs_completed": run_index,
            "unique_seed_count": len(seed_set),
            "physical_comparison_count": physical_comparison_count,
            "r83m_first_six_second_comparison_count": r83m_comparison_count,
            "max_abs_physics_delta": max_abs_physics_delta,
            "diagnostic_model_trained": False,
        })
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--r83m-archive", type=Path, required=True)
    parser.add_argument("--r84r1-artifact", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = run_pilot(args.r83m_archive, args.r84r1_artifact, args.output_dir)
    print(json.dumps({
        "status": result["status"],
        "unique_native_run_count": result["unique_native_run_count"],
        "native_execution_count_including_replay": result["native_execution_count_including_replay"],
        "physical_comparison_count_including_replay": result["physical_comparison_count_including_replay"],
        "r83m_first_six_second_comparison_count": result["r83m_first_six_second_comparison_count"],
        "max_abs_physics_delta_to_noise_free_baselines": result["max_abs_physics_delta_to_noise_free_baselines"],
        "checks": result["checks"],
        "artifacts": result["artifacts"],
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
