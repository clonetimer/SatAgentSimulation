#!/usr/bin/env python3
"""用途：仅在五测点映射确认后对照 #202 电池温度参考仿真原始时序。

参数：--reference-csv、--mapping-json、--model-csv、--model-statistics、--output。
输出：同窗共同有效样本的逐时加权统计与冻结容差对照 JSON。
不做硬件标定，不改变物理模型、原始数据或容差；缺少映射时失败关闭。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean, pstdev

SAMPLES = 11801
STEP_S = 0.3
END_S = 3540.0
MEAN_TOL_C = 0.5
STD_TOL_C = 0.15
MODEL_FIELD = "thermal.battery_temp_c"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not headers:
            raise ValueError("CSV_HEADERS_MISSING_OR_DUPLICATED")
        rows = list(reader)
    if len(rows) != SAMPLES or any(None in row for row in rows):
        raise ValueError("CSV_ROW_COUNT_OR_WIDTH_MISMATCH")
    return headers, rows


def _number(raw: object, *, field: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"NONNUMERIC_{field}") from exc
    if not math.isfinite(value):
        raise ValueError(f"NONFINITE_{field}")
    return value


def _times(rows: list[dict[str, str]], column: str) -> list[float]:
    times = [_number(row[column], field="TIME") for row in rows]
    if any(abs(t - i * STEP_S) > 1e-6 for i, t in enumerate(times)) or abs(times[-1] - END_S) > 1e-6:
        raise ValueError("TIME_GRID_MISMATCH")
    return times


def _stats(times: list[float], values: list[float]) -> dict[str, float | int]:
    mean_t, mean_v = fmean(times), fmean(values)
    denom = sum((t - mean_t) ** 2 for t in times)
    if len(values) < 2 or denom <= 0:
        raise ValueError("INSUFFICIENT_COMMON_SAMPLES")
    slope = sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values)) / denom
    return {"sample_count": len(values), "mean_c": mean_v,
            "std_population_c": pstdev(values), "linear_slope_c_per_hour": slope * 3600.0}


def compare(reference_csv: Path, mapping_json: Path, model_csv: Path, model_statistics: Path) -> dict:
    mapping = json.loads(mapping_json.read_text(encoding="utf-8"))
    if mapping.get("schema_version") != "battery-five-point-map.v1":
        raise ValueError("MAPPING_SCHEMA_INVALID")
    if mapping.get("source_kind") != "simulation_output_not_hardware_or_flight":
        raise ValueError("SOURCE_KIND_INVALID")
    if mapping.get("comparison_target") != MODEL_FIELD:
        raise ValueError("COMPARISON_TARGET_INVALID")
    if mapping.get("reference_csv_sha256") != digest(reference_csv):
        raise ValueError("REFERENCE_SHA256_MISMATCH")
    if mapping.get("status") != "CONFIRMED_BY_REFERENCE_OWNER" or not mapping.get("mapping_evidence_uri"):
        raise ValueError("MAPPING_NOT_CONFIRMED")
    channels = mapping.get("channels")
    if not isinstance(channels, list) or len(channels) != 5:
        raise ValueError("FIVE_CHANNELS_REQUIRED")
    required = ("column", "package_id", "measurement_location", "temperature_definition", "weight_basis")
    if any(not all(isinstance(c.get(k), str) and c[k].strip() for k in required) for c in channels):
        raise ValueError("CHANNEL_IDENTITY_INCOMPLETE")
    if len({c["column"] for c in channels}) != 5 or len({c["package_id"] for c in channels}) != 1:
        raise ValueError("CHANNEL_DUPLICATE_OR_MIXED_PACKAGE")
    weights = [_number(c.get("weight"), field="WEIGHT") for c in channels]
    if any(w < 0 for w in weights) or abs(sum(weights) - 1.0) > 1e-12:
        raise ValueError("WEIGHTS_INVALID")
    if any(c.get("unit") != "degC" for c in channels):
        raise ValueError("UNIT_MUST_BE_DEGC")
    mode = mapping.get("quality_policy")
    if mode not in ("all_rows_reviewed_valid", "per_channel_valid_flags"):
        raise ValueError("QUALITY_POLICY_UNCONFIRMED")
    if mode == "per_channel_valid_flags" and any(not isinstance(c.get("valid_column"), str) or not c["valid_column"] for c in channels):
        raise ValueError("VALIDITY_COLUMNS_REQUIRED")

    ref_headers, ref_rows = _table(reference_csv)
    model_headers, model_rows = _table(model_csv)
    time_column = mapping.get("time_column")
    needed = {time_column, *(c["column"] for c in channels)}
    if mode == "per_channel_valid_flags":
        needed.update(c["valid_column"] for c in channels)
    if not needed.issubset(ref_headers) or not {"time_s", MODEL_FIELD}.issubset(model_headers):
        raise ValueError("DECLARED_COLUMN_MISSING")
    times = _times(ref_rows, time_column)
    model_times = _times(model_rows, "time_s")
    if any(abs(a - b) > 1e-6 for a, b in zip(times, model_times)):
        raise ValueError("MODEL_REFERENCE_TIME_MISMATCH")
    model_report = json.loads(model_statistics.read_text(encoding="utf-8"))
    if model_report.get("model_telemetry", {}).get("sha256") != digest(model_csv):
        raise ValueError("MODEL_TELEMETRY_SHA256_MISMATCH")

    valid_times, reference_values, model_values = [], [], []
    invalid_count = 0
    for t, ref, model in zip(times, ref_rows, model_rows):
        if mode == "per_channel_valid_flags":
            flags = [ref[c["valid_column"]] for c in channels]
            if any(flag not in ("0", "1") for flag in flags):
                raise ValueError("VALID_FLAG_MUST_BE_ZERO_OR_ONE")
            if "0" in flags:
                invalid_count += 1
                continue
        vals = [_number(ref[c["column"]], field="REFERENCE_TEMPERATURE") for c in channels]
        reference_values.append(sum(w * v for w, v in zip(weights, vals)))
        model_values.append(_number(model[MODEL_FIELD], field="MODEL_TEMPERATURE"))
        valid_times.append(t)
    ref_stats = _stats(valid_times, reference_values)
    model_stats = _stats(valid_times, model_values)
    mean_gap = model_stats["mean_c"] - ref_stats["mean_c"]
    std_gap = model_stats["std_population_c"] - ref_stats["std_population_c"]
    within = abs(mean_gap) <= MEAN_TOL_C and abs(std_gap) <= STD_TOL_C
    return {
        "schema_version": "battery-five-point-comparison.v1",
        "reference_kind": mapping["source_kind"],
        "qualification": "REFERENCE_MAPPING_ASSERTED_NOT_INDEPENDENTLY_AUTHENTICATED",
        "status": "INCONCLUSIVE_INVALID_SAMPLES" if invalid_count else ("WITHIN_FROZEN_TOLERANCE" if within else "OUTSIDE_FROZEN_TOLERANCE"),
        "common_valid_count": len(valid_times), "invalid_or_excluded_count": invalid_count,
        "reference_sha256": digest(reference_csv), "mapping_sha256": digest(mapping_json),
        "model_telemetry_sha256": digest(model_csv), "model_statistics_sha256": digest(model_statistics),
        "aggregation": "weighted_at_each_common_valid_time_before_statistics",
        "package_id": channels[0]["package_id"],
        "reference": ref_stats, "model": model_stats,
        "difference_model_minus_reference": {"mean_c": mean_gap, "std_population_c": std_gap},
        "frozen_tolerance": {"mean_abs_c": MEAN_TOL_C, "std_population_abs_c": STD_TOL_C},
        "steady_state_diagnostic_only": {"reference_slope_c_per_hour": ref_stats["linear_slope_c_per_hour"],
                                          "model_slope_c_per_hour": model_stats["linear_slope_c_per_hour"]},
    }



def write_result_once(output: Path, result: dict, inputs: tuple[Path, ...]) -> None:
    """Create one immutable result file without risking original evidence."""
    resolved_output = output.resolve()
    if any(resolved_output == source.resolve() for source in inputs):
        raise ValueError("OUTPUT_OVERLAPS_INPUT_EVIDENCE")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("reference_csv", "mapping_json", "model_csv", "model_statistics", "output"):
        p.add_argument("--" + key.replace("_", "-"), type=Path, required=True)
    args = p.parse_args()
    result = compare(args.reference_csv, args.mapping_json, args.model_csv, args.model_statistics)
    write_result_once(args.output, result, (args.reference_csv, args.mapping_json,
                                            args.model_csv, args.model_statistics))
    print(json.dumps({"status": result["status"], "common_valid_count": result["common_valid_count"]}))
    return 0 if result["status"] == "WITHIN_FROZEN_TOLERANCE" else 1


if __name__ == "__main__":
    raise SystemExit(main())

