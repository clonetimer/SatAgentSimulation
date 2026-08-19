"""Selective unified Basilisk assembly runner for v8.22.

v8.22 promotes the propulsion focused-to-unified feasibility runner into the
main selective unified assembly path.  The runner now colocates EPS,
Comm/Data, Thermal scheduled custom module, ADCS ST/IMU sensor-fusion RW
closed-loop, and propulsion thruster/fuelTank chain in the same Basilisk
``SimulationBaseClass``.

Truthfulness boundary:
* Uses Basilisk native modules for spacecraft, RW, sensors, FSW algorithms,
  battery/load nodes, data modules, thrusterDynamicEffector and fuelTank.
* Uses Basilisk Python SysModel modules for PDU/load shedding, thermal state,
  constant heat input, device requests and ST/IMU fusion estimator.
* Does not claim full all-subsystem Basilisk full-dynamics, native Basilisk
  thermal network, mission-grade reboost validation, or flight-validated ADCS.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .propulsion_unified_feasibility import (
    PropulsionUnifiedFeasibilityConfig,
    PropulsionUnifiedFeasibilitySummary,
    PropulsionUnifiedFeasibilityTraceRow,
    run_propulsion_unified_feasibility,
)
from subsystems.fault_base import FaultSpec
from whole_spacecraft._runtime_fault_injector import FaultInjector


# 扩展配置类，添加故障规格支持
@dataclass(frozen=True)
class SelectiveUnifiedAssemblyConfig(PropulsionUnifiedFeasibilityConfig):
    """选择性统一装配配置，继承推进统一可行性配置并添加故障注入支持。

    新增属性:
        fault_specs: 可选的故障规格元组，定义需要在装配过程中注入的故障。
                    为 None 时表示不进行故障注入。
    """
    fault_specs: Optional[tuple[FaultSpec, ...]] = None


# 扩展摘要类，添加故障注入状态字段
@dataclass(frozen=True)
class SelectiveUnifiedAssemblySummary(PropulsionUnifiedFeasibilitySummary):
    """选择性统一装配摘要，继承推进统一可行性摘要并添加故障注入状态。

    新增属性:
        fault_injection_status: 故障注入状态，None 表示未进行故障注入，
                               "scheduled" 表示已调度故障注入。
    """
    fault_injection_status: Optional[str] = None


# 保留向后兼容的别名
SelectiveUnifiedAssemblyTraceRow = PropulsionUnifiedFeasibilityTraceRow


def run_selective_unified_assembly(
    config: SelectiveUnifiedAssemblyConfig | None = None,
) -> tuple[SelectiveUnifiedAssemblySummary, tuple[SelectiveUnifiedAssemblyTraceRow, ...]]:
    """运行选择性统一装配，支持故障注入。

    该函数委托给 v8.21 推进统一装配运行器，然后将其作为 v8.22 主选择性统一装配路径暴露。
    保持一个实现可以防止可行性和推广路径之间的分歧。

    参数:
        config: 选择性统一装配配置，包含可选的故障规格。

    返回:
        tuple[SelectiveUnifiedAssemblySummary, tuple[SelectiveUnifiedAssemblyTraceRow, ...]]:
            包含装配摘要和跟踪行数据的元组。

    故障注入流程:
        1. 如果配置中的 fault_specs 不为 None，将其传递给底层装配函数
        2. 底层装配函数在装配完成后创建 FaultInjector 并调度故障
        3. 在返回的摘要中设置 fault_injection_status 字段
    """
    # 如果未提供配置，使用默认配置
    if config is None:
        config = SelectiveUnifiedAssemblyConfig()

    # 运行推进统一可行性装配，传递故障规格
    # 注意：需要传入 PropulsionUnifiedFeasibilityConfig，因此提取父类字段
    base_config = PropulsionUnifiedFeasibilityConfig(
        **{k: v for k, v in asdict(config).items() if k != 'fault_specs'}
    )
    summary, rows = run_propulsion_unified_feasibility(base_config, config.fault_specs)

    # 构建扩展的摘要对象，添加故障注入状态
    promoted = SelectiveUnifiedAssemblySummary(
        backend="selective_unified_basilisk_assembly_v8_22_propulsion_promoted",
        basilisk_simbase_used=summary.basilisk_simbase_used,
        execute_simulation_used=summary.execute_simulation_used,
        unified_simbase=summary.unified_simbase,
        included_subsystems=(
            "eps",
            "comm_data",
            "thermal_scheduled",
            "adcs_sensor_fusion_rw_closed_loop",
            "propulsion_unified_chain",
        ),
        excluded_subsystems=summary.excluded_subsystems,
        native_modules=summary.native_modules,
        scheduled_custom_modules=summary.scheduled_custom_modules,
        message_contracts=summary.message_contracts,
        duration_s=summary.duration_s,
        sample_count=summary.sample_count,
        final_soc=summary.final_soc,
        final_data_storage_bits=summary.final_data_storage_bits,
        final_attitude_error_ratio=summary.final_attitude_error_ratio,
        min_thermal_margin_c=summary.min_thermal_margin_c,
        load_shed_event_count=summary.load_shed_event_count,
        initial_fuel_mass_kg=summary.initial_fuel_mass_kg,
        final_fuel_mass_kg=summary.final_fuel_mass_kg,
        propellant_used_kg=summary.propellant_used_kg,
        final_velocity_x_m_s=summary.final_velocity_x_m_s,
        propulsion_unified=summary.propulsion_unified,
        swig_lifecycle_status=summary.swig_lifecycle_status,
        message_ownership_status=summary.message_ownership_status,
        status=summary.status,
        not_claimed=summary.not_claimed,
        # 故障注入状态：根据 fault_specs 是否存在决定
        fault_injection_status="scheduled" if config.fault_specs is not None and len(config.fault_specs) > 0 else None,
    )
    return promoted, rows


def write_selective_unified_assembly_dataset(output_dir: str | Path, config: SelectiveUnifiedAssemblyConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_selective_unified_assembly(config)
    summary_path = output_dir / "selective_unified_assembly_summary.json"
    trace_path = output_dir / "selective_unified_assembly_trace.csv"
    manifest_path = output_dir / "selective_unified_assembly_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(SelectiveUnifiedAssemblyTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "selective_unified_basilisk_assembly_v8_22_propulsion_promoted",
        "backend_truth": "single SimulationBaseClass assembly with EPS, Comm/Data, Thermal scheduled custom module, ADCS ST/IMU fusion RW closed-loop, and propulsion thruster/fuelTank chain on the same spacecraft",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="datasets/selective_unified_basilisk_assembly")
    args = parser.parse_args()
    print(json.dumps(write_selective_unified_assembly_dataset(args.output_dir), indent=2, ensure_ascii=False))
