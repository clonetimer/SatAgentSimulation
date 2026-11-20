#!/usr/bin/env python3
"""用途：测量现有原生整星模型的统计基线，不读取或重建外部参考 CSV。

参数：--output-dir 指定输出目录；--recompute-existing 仅核验已有本模型遥测并重算统计。
输出：baseline_statistics.json、baseline_task_spec.json、model_telemetry.csv.gz。
范围：仅 EPS、热稳态和姿态的可比性检查；不改参数默认值或验收阈值。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
THERMAL_NODES = ("battery", "electronics", "payload", "adcs", "structure", "solar_panel", "comm", "propulsion")
POWER_FIELDS = ("eps.net_power_w", "eps.solar_array_power_w", "eps.battery_energy_j", "eps.battery_capacity_j", "eps.battery_soc")
BATTERY_FIELDS = tuple(field for field in POWER_FIELDS if field != "eps.solar_array_power_w")
RATE_FIELDS = tuple(f"attitude.truth.omega_BN_B_{axis}" for axis in "xyz")
MRP_FIELDS = tuple(f"attitude.truth.mrp_BN_{axis}" for axis in "xyz")
THERMAL_FIELDS = tuple(f"thermal.{node}_temp_c" for node in THERMAL_NODES)
SOURCES = (
    "src/sat_sim/bsk_engine/unified_native.py",
    "src/sat_sim/bsk_engine/project_native_modules.py",
    "src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml",
    "src/subsystems/thermal/network_config.py",
    "src/subsystems/thermal/thermal_network.py",
    "src/integration/resource_feedback.py",
    "scripts/run_external_reference_alignment.py",
    "configs/reference_alignment/external_reference_statistics.json",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def stats(times: np.ndarray, values: np.ndarray) -> dict:
    if len(times) != len(values) or len(values) < 2 or not np.isfinite(values).all():
        raise ValueError("统计输入须有相同长度、至少两个有限样本")
    centered_t = times - times.mean()
    slope = float(np.dot(centered_t, values - values.mean()) / np.dot(centered_t, centered_t))
    return {
        "sample_count": len(values), "start_s": float(times[0]), "end_s": float(times[-1]),
        "mean": float(values.mean()), "median": float(np.median(values)),
        "std_population": float(values.std(ddof=0)), "rms": float(np.sqrt(np.mean(values ** 2))),
        "minimum": float(values.min()), "maximum": float(values.max()),
        "max_absolute": float(np.abs(values).max()),
        "p01_p05_p95_p99": np.quantile(values, [.01, .05, .95, .99]).tolist(),
        "linear_slope_per_hour": slope * 3600.0,
    }


def battery_sample_mask(times: np.ndarray, energy: np.ndarray, capacity: np.ndarray, power: np.ndarray) -> np.ndarray:
    """仅排除首时刻未就绪消息，包括 Unified 的 1e-12 J 展示下限。"""
    arrays = (times, energy, capacity, power)
    if any(array.shape != times.shape or not np.isfinite(array).all() for array in arrays):
        raise ValueError("电池统计输入形状不一致或含非有限值")
    startup = ((times == 0) & (energy == 0) & (power == 0)
               & ((capacity == 0) | (capacity == 1e-12)))
    valid = ~startup
    if np.any(capacity[valid] <= 1e-12):
        raise ValueError("存在非启动占位的无效电池容量，不能静默排除")
    return valid


def summarize_fields(times: np.ndarray, series: dict[str, np.ndarray]) -> tuple[dict, np.ndarray]:
    valid = battery_sample_mask(times, series["eps.battery_energy_j"],
                                series["eps.battery_capacity_j"], series["eps.net_power_w"])
    fields = {}
    for field, values in series.items():
        mask = valid if field in BATTERY_FIELDS else np.ones(len(times), dtype=bool)
        fields[field] = {"full_window": stats(times[mask], values[mask])}
        tail = mask & (times >= 2940.0)
        fields[field]["last_600_s_diagnostic_only"] = stats(times[tail], values[tail])
    return fields, valid


def recompute_existing(output: Path) -> int:
    """从已有本模型 CSV 重算；不再次运行物理模型，不触及外部参考时序。"""
    from sat_sim.task_compiler import compile_task_spec
    from sat_sim.unified_execution import build_execution_request

    original_path = output / "initial_statistics_pre_correction.json"
    original = json.loads(original_path.read_text(encoding="utf-8"))
    trace_path = output / original["model_telemetry"]["file"]
    if hashlib.sha256(trace_path.read_bytes()).hexdigest() != original["model_telemetry"]["sha256"]:
        raise ValueError("已有本模型遥测与原始记录的哈希不一致")
    input_path = output / "baseline_task_spec.json"
    if hashlib.sha256(input_path.read_bytes()).hexdigest() != original["task_spec_sha256"]:
        raise ValueError("原始请求配置被修改")
    for path, digest in original["implementation_sha256"].items():
        if path.startswith("src/") and hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"物理模型来源已经变化：{path}")
    snapshot_path = output / "initial_measurement_source.txt"
    if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != original["implementation_sha256"]["scripts/run_external_reference_alignment.py"]:
        raise ValueError("原始执行脚本快照不一致")
    with gzip.open(trace_path, "rt", encoding="utf-8", newline="") as stream:
        table = list(csv.DictReader(stream))
    times = np.asarray([float(row["time_s"]) for row in table])
    series = {field: np.asarray([float(row[field]) for row in table]) for field in table[0] if field != "time_s"}
    if len(times) != original["observed_sample_count"] or not np.allclose(np.diff(times), .3, rtol=0, atol=1e-9):
        raise ValueError("已有遥测的数量或网格不符")
    fields, valid = summarize_fields(times, series)
    report = original.copy()
    report["fields"] = fields
    report["model_execution_sources_sha256"] = report.pop("implementation_sha256")
    report["analysis_sources_sha256"] = {
        "scripts/run_external_reference_alignment.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    report["execution_script_snapshot"] = snapshot_path.name
    report["statistics_correction"] = {
        "original_report": original_path.name,
        "original_report_sha256": hashlib.sha256(original_path.read_bytes()).hexdigest(),
        "reason": "原统计未识别 Unified 首帧容量 1e-12 J 展示占位；原始遥测保持不变",
        "physics_rerun": False, "external_reference_timeseries_used": False,
        "solar_first_sample_retained": True,
    }
    spec = json.loads(input_path.read_text(encoding="utf-8"))
    request = build_execution_request(compile_task_spec(spec), spec)
    resolved = request.metadata.get("resolved_runtime_task_spec", spec)
    resolved_path = output / "baseline_runtime_spec.json"
    write_json(resolved_path, resolved)
    report["runtime_spec_provenance"] = {
        "file": resolved_path.name, "sha256": hashlib.sha256(resolved_path.read_bytes()).hexdigest(),
        "capture": "recreated_after_run_from_saved_request_not_execution_time_snapshot",
        "limitation": "首轮只保存了模板请求；此文件事后经同工作树编译重建，不冒充当时直接保存的执行输入",
        "compiler_source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            "src/sat_sim/task_compiler.py", "src/sat_sim/unified_execution.py", "src/sat_sim/capability_registry.py")},
    }
    report["eps_scope"] = {
        **original["eps_scope"],
        "invalid_zero_startup_rows_excluded_from_power_statistics": int((~valid).sum()),
        "startup_exclusion_scope": "仅电池相关字段；原生全零或Unified展示floor，限t=0且E=P=0；太阳阵首帧单独保留",
        "positive_net_power_sample_fraction": float(np.mean(series["eps.net_power_w"][valid] > 0)),
        "negative_net_power_sample_fraction": float(np.mean(series["eps.net_power_w"][valid] < 0)),
        "zero_net_power_sample_fraction": float(np.mean(series["eps.net_power_w"][valid] == 0)),
    }
    write_json(output / "baseline_statistics.json", report)
    print(json.dumps({"仅重算已有自有遥测": True, "保留原始报告": original_path.name,
                      "排除未就绪首帧数": int((~valid).sum()), "参考序列已处理": False}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/external_reference_alignment")
    parser.add_argument("--recompute-existing", action="store_true", help="核验并重算首次已保存的自有遥测，不重跑仿真")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.recompute_existing:
        return recompute_existing(output)
    if (output / "baseline_statistics.json").exists():
        parser.error("输出目录已有统计结果；使用新目录，保留先前运行证据")

    import Basilisk
    from Basilisk.utilities import RigidBodyKinematics
    from sat_sim.capability_registry import get_adapter_for_capability
    from sat_sim.scenario_templates import instantiate_scenario_template
    from sat_sim.task_compiler import compile_task_spec
    from sat_sim.unified_execution import build_execution_request

    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="external_reference_model_baseline")
    # An independent model observation window, never a reconstruction of reference timing.
    spec["simulation"].update(duration_s=3540.0, step_s=0.1, sample_s=0.3)
    write_json(output / "baseline_task_spec.json", spec)
    compiled = compile_task_spec(spec)
    request = build_execution_request(compiled, spec)
    runtime_spec = request.metadata.get("resolved_runtime_task_spec", spec)
    runtime_path = output / "baseline_runtime_spec.json"
    write_json(runtime_path, runtime_spec)
    sources_at_start = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in SOURCES}
    adapter = get_adapter_for_capability(request.capability_id)
    errors = [str(issue) for issue in adapter.validate(runtime_spec) if str(issue.severity).lower() == "error"]
    if errors:
        raise ValueError(errors)
    print("开始现有模型基线：3540 仿真秒，0.1 秒步长，0.3 秒记录间隔。", flush=True)
    start = time.monotonic()
    result = adapter.run(runtime_spec)
    elapsed = time.monotonic() - start
    if any(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest for path, digest in sources_at_start.items()):
        raise ValueError("运行期间源码或参考摘要改变，不能绑定新来源哈希")
    rows = result.trace_rows
    times = np.asarray([row["time_s"] for row in rows], dtype=float)
    if len(rows) != 11801 or not np.allclose(np.diff(times), .3, rtol=0, atol=1e-9):
        raise ValueError("模型采样网格不符合声明，禁止用不匹配网格报告统计")
    if times[0] != 0.0 or abs(times[-1] - 3540.0) > 1e-9:
        raise ValueError("模型观察窗不完整")

    series = {field: np.asarray([row[field] for row in rows], dtype=float)
              for field in POWER_FIELDS + RATE_FIELDS + MRP_FIELDS + THERMAL_FIELDS}
    if not all(row.get("attitude.truth.valid") == 1 for row in rows):
        raise ValueError("存在无效姿态真值")
    euler321 = np.asarray([RigidBodyKinematics.MRP2Euler321([row[key] for key in MRP_FIELDS]) for row in rows])
    if np.any(np.abs(euler321[:, 1]) >= math.radians(89.9)):
        raise ValueError("本模型欧拉角接近奇异点，不能输出普通展开角统计")
    for index, axis in enumerate(("yaw", "pitch", "roll")):
        series[f"model.euler321_{axis}_wrapped_deg"] = np.rad2deg(euler321[:, index])
        series[f"model.euler321_{axis}_unwrapped_deg"] = np.rad2deg(np.unwrap(euler321[:, index]))
    for axis, field in zip("xyz", RATE_FIELDS):
        series[f"model.true_body_rate_{axis}_deg_s"] = np.rad2deg(series[field])

    fields, power_mask = summarize_fields(times, series)

    trace_path = output / "model_telemetry.csv.gz"
    with gzip.open(trace_path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", *series])
        for index, time_s in enumerate(times):
            writer.writerow([float(time_s), *(float(values[index]) for values in series.values())])
    net_power = series["eps.net_power_w"][power_mask]
    report = {
        "schema_version": "external-reference-model-baseline.v1",
        "purpose": "现有模型自有运行的统计基线，不是参考 CSV 重建或参数标定",
        "reference_raw_data_accessed": False, "reference_filter_applied": False,
        "parameter_alignment_applied": False, "production_defaults_changed": False,
        "comparison_status": "UNALIGNED_MISSING_MAPPING_DEFINITIONS_AND_TOLERANCES",
        "tolerance_assessment": "NOT_ASSESSED_NO_USER_TOLERANCE",
        "reference_numeric_tolerances": None,
        "basilisk_version": Basilisk.__version__, "python_version": platform.python_version(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "implementation_sha256": sources_at_start,
        "task_spec_sha256": hashlib.sha256((output / "baseline_task_spec.json").read_bytes()).hexdigest(),
        "runtime_spec_provenance": {"file": runtime_path.name, "sha256": hashlib.sha256(runtime_path.read_bytes()).hexdigest(), "capture": "before_execution"},
        "wall_elapsed_s": elapsed, "simulation": spec["simulation"], "observed_sample_count": len(rows),
        "parameter_overrides": "仅观察窗和数值步长／记录间隔；无物理参数调整",
        "environment_scope": "现有模板 simplified 环境；未获参考工况定义，不声明同工况",
        "model_runtime_summary": result.summary,
        "model_telemetry": {"file": trace_path.name, "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(), "size_bytes": trace_path.stat().st_size},
        "statistics_definition": "等间隔采样算术均值、总体标准差(ddof=0)、线性分位数、最小二乘线性趋势；末600秒仅诊断，不替代全窗",
        "eps_scope": {
            "current_channels_available": False, "voltage_available": False,
            "power_is_not_current": True, "charge_current_limiter_available": False,
            "invalid_zero_startup_rows_excluded_from_power_statistics": int((~power_mask).sum()),
            "startup_exclusion_scope": "仅电池相关字段；兼容Unified展示floor；太阳阵首帧单独保留",
            "positive_net_power_sample_fraction": float(np.mean(net_power > 0)),
            "negative_net_power_sample_fraction": float(np.mean(net_power < 0)),
            "zero_net_power_sample_fraction": float(np.mean(net_power == 0)),
            "fraction_scope": "功率符号采样比例，不是参考调节器充放电占空比；正净输入不保证满电池仍吸收能量",
        },
        "attitude_scope": "本模型B相对N的真实角速度在B系表达；欧拉角由官方MRP2Euler321生成、先弧度展开再转度；参考坐标及旋转顺序未知",
        "fields": fields,
    }
    write_json(output / "baseline_statistics.json", report)
    print(json.dumps({"模型基线已记录": True, "样本数": len(rows), "仿真末时刻_s": float(times[-1]),
                      "电池温度均值_C": fields["thermal.battery_temp_c"]["full_window"]["mean"],
                      "净功率均值_W": fields["eps.net_power_w"]["full_window"]["mean"],
                      "容差结论": report["tolerance_assessment"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
