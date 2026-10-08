#!/usr/bin/env python3
"""Generate one paired sample per ADCS condition and quantify injection effects.

用途：逐项验证 ADCS 故障注入是否产生可观测且可量化的物理影响。
参数：通过命令行参数配置输出目录、仿真时长、步长与随机种子。
输出：生成成对样本、故障效应指标和机器可读验证报告。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from generate_whole_spacecraft_adcs_eps_dataset import (
    CAPABILITY_ID, CONDITIONS, MISSIONS, _write_csv, _write_json,
    build_task_spec, select_output_row,
)


def _max_abs_delta(nominal: Sequence[Mapping[str, Any]], fault: Sequence[Mapping[str, Any]], fields: Sequence[str], start_s: float) -> float:
    values = []
    for left, right in zip(nominal, fault, strict=False):
        if float(right["time_s"]) < start_s:
            continue
        for field in fields:
            try:
                values.append(abs(float(right[field]) - float(left[field])))
            except (KeyError, TypeError, ValueError):
                pass
    return max(values, default=0.0)


def _max_internal_delta(rows: Sequence[Mapping[str, Any]], left_fields: Sequence[str], right_fields: Sequence[str], start_s: float) -> float:
    values = []
    for row in rows:
        if float(row["time_s"]) < start_s:
            continue
        for left, right in zip(left_fields, right_fields, strict=False):
            values.append(abs(float(row[left]) - float(row[right])))
    return max(values, default=0.0)


def _pre_fault_max_delta(nominal: Sequence[Mapping[str, Any]], fault: Sequence[Mapping[str, Any]], start_s: float) -> float:
    ignored = {"sample_id", "condition_id", "event_category", "effect", "is_nominal", "pair_id"}
    values = []
    for left, right in zip(nominal, fault, strict=False):
        if float(right["time_s"]) >= start_s:
            continue
        for field in set(left) & set(right) - ignored:
            try:
                values.append(abs(float(right[field]) - float(left[field])))
            except (TypeError, ValueError):
                pass
    return max(values, default=0.0)


def effect_evidence(condition_id: str, nominal: Sequence[Mapping[str, Any]], fault: Sequence[Mapping[str, Any]], start_s: float) -> dict[str, Any]:
    wheel_speeds = [f"adcs.rw.speed_rad_s_{i}" for i in range(4)]
    applied = [f"adcs.control.applied_torque_nm_{i}" for i in range(4)]
    gyro = [f"adcs.sensor.gyro_measured_rad_s_{axis}" for axis in "xyz"]
    accel = [f"adcs.sensor.accel_measured_m_s2_{axis}" for axis in "xyz"]
    star = [f"adcs.star_tracker.q{i}" for i in range(4)]
    star_raw = [f"adcs.star_tracker.raw_q{i}" for i in range(4)]
    sun = [f"adcs.sun_sensor.direction_b_{axis}" for axis in "xyz"]
    sun_raw = [f"adcs.sun_sensor.raw_direction_b_{axis}" for axis in "xyz"]
    measured_speed = [f"adcs.rw.measured_speed_rad_s_{i}" for i in range(4)]
    metrics = {
        "pre_fault_max_numeric_delta": _pre_fault_max_delta(nominal, fault, start_s),
        "pointing_error_delta_deg": _max_abs_delta(nominal, fault, ["adcs.pointing_error_deg"], start_s),
        "wheel_speed_delta_rad_s": _max_abs_delta(nominal, fault, wheel_speeds, start_s),
        "applied_torque_delta_nm": _max_abs_delta(nominal, fault, applied, start_s),
        "gyro_delta_rad_s": _max_abs_delta(nominal, fault, gyro, start_s),
        "accel_delta_m_s2": _max_abs_delta(nominal, fault, accel, start_s),
        "star_output_delta": _max_internal_delta(fault, star, star_raw, start_s),
        "sun_output_delta": _max_internal_delta(fault, sun, sun_raw, start_s),
        "rw_speed_sensor_delta_rad_s": _max_internal_delta(fault, measured_speed, wheel_speeds, start_s),
        "star_invalid_samples": sum(int(row["adcs.star_tracker.valid"]) == 0 for row in fault if float(row["time_s"]) >= start_s),
        "sun_invalid_samples": sum(int(row["adcs.sun_sensor.valid"]) == 0 for row in fault if float(row["time_s"]) >= start_s),
        "fallback_samples": sum(int(row["adcs.fusion.source_mode"]) != 3 for row in fault if float(row["time_s"]) >= start_s),
        "event_active_samples": sum(int(row.get("event_active", 0)) for row in fault),
    }
    direct = max(
        float(metrics["applied_torque_delta_nm"]), float(metrics["gyro_delta_rad_s"]),
        float(metrics["accel_delta_m_s2"]), float(metrics["star_output_delta"]),
        float(metrics["sun_output_delta"]), float(metrics["rw_speed_sensor_delta_rad_s"]),
    )
    categorical = int(metrics["star_invalid_samples"]) + int(metrics["sun_invalid_samples"]) + int(metrics["fallback_samples"])
    passed = float(metrics["pre_fault_max_numeric_delta"]) <= 1.0e-12 and int(metrics["event_active_samples"]) > 0 and (direct > 1.0e-10 or categorical > 0 or float(metrics["wheel_speed_delta_rad_s"]) > 1.0e-8)
    return {"status": "PASS" if passed else "FAIL", "metrics": metrics}


def run(args: argparse.Namespace) -> dict[str, Any]:
    mission = next(item for item in MISSIONS if item.mission_id == args.mission)
    adapter = get_adapter_for_capability(CAPABILITY_ID); capability = get_capability(CAPABILITY_ID).data
    root = Path(args.output); root.mkdir(parents=True, exist_ok=True)
    nominal_spec, nominal_meta = build_task_spec(mission, CONDITIONS[0], sample_index=args.sample_index, base_seed=args.seed, duration_s=args.duration_s, step_s=args.step_s, sample_s=args.sample_s)
    nominal_result = adapter.run(nominal_spec, capability)
    nominal_rows = [select_output_row(row, nominal_meta) for row in nominal_result.trace_rows]
    _write_csv(root / "nominal" / "telemetry.csv", nominal_rows)
    records = []
    for index, condition in enumerate(CONDITIONS[1:], 1):
        spec, metadata = build_task_spec(mission, condition, sample_index=args.sample_index, base_seed=args.seed, duration_s=args.duration_s, step_s=args.step_s, sample_s=args.sample_s)
        result = adapter.run(spec, capability); rows = [select_output_row(row, metadata) for row in result.trace_rows]
        evidence = effect_evidence(condition.condition_id, nominal_rows, rows, float(metadata["event_start_s"]))
        condition_root = root / condition.condition_id; _write_csv(condition_root / "telemetry.csv", rows)
        record = {"condition_id": condition.condition_id, "effect": condition.effect, "event_start_s": metadata["event_start_s"], "event_parameters": metadata["event_parameters"], "simulation_status": result.summary.get("overall_status"), **evidence}
        _write_json(condition_root / "effect_evidence.json", record); records.append(record)
        print(f"[{index}/{len(CONDITIONS)-1}] {condition.condition_id}: {record['status']}", flush=True)
    report = {"mission_id": mission.mission_id, "sample_index": args.sample_index, "paired_seed": nominal_meta["pair_seed"], "condition_count": len(records), "pass_count": sum(item["status"] == "PASS" for item in records), "fail_count": sum(item["status"] != "PASS" for item in records), "records": records}
    _write_json(root / "fault_effect_report.json", report)
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/generated/adcs_single_fault_validation")
    parser.add_argument("--mission", default="attitude_acquisition", choices=[item.mission_id for item in MISSIONS])
    parser.add_argument("--sample-index", type=int, default=0); parser.add_argument("--seed", type=int, default=20260810)
    parser.add_argument("--duration-s", type=float, default=60.0); parser.add_argument("--step-s", type=float, default=0.2); parser.add_argument("--sample-s", type=float, default=1.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
