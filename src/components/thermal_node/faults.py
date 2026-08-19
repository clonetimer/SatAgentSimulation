from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
热节点故障类型模块

定义热节点(ThermalNode)部件可能发生的故障类型。

文献支撑:
- 天拓五号卫星推进系统在轨故障诊断与定位方法[J]. 国防科技大学学报, 2024, 46(5): 141-149.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- 基于MBSE的卫星总体设计与FMEA方法融合及应用研究[R]. 2025.
"""

from enum import Enum


class ThermalNodeFaultType(Enum):
    """
    热节点故障类型枚举

    热节点故障主要包括温度失控、热阻变化和热传导路径故障等模式。

    枚举值:
        TemperatureRunaway: 温度失控
            - 热节点温度不受控上升或下降
            - magnitude 表示温度偏离设定值的程度（K）

        ThermalResistanceIncrease: 热阻增加
            - 热传导路径热阻增大
            - magnitude 表示热阻增加倍数（1.0为正常）

        HeatPathBlocked: 热路径阻塞
            - 热传导路径被阻塞
            - magnitude 表示阻塞程度（0-1）

        TemperatureSensorFailure: 温度传感器失效
            - 温度传感器读数错误或失效
            - magnitude 表示传感器失效数量比例（0-1）

        HeatInputExcess: 热量输入过量
            - 外部热量输入超过预期
            - magnitude 表示热量超量比例（0-1）

        HeatInputLoss: 热量输入丢失
            - 外部热量输入中断
            - magnitude 表示热量损失比例（0-1）

        InsulationDegradation: 绝热层退化
            - 绝热材料性能下降
            - magnitude 表示绝热性能下降比例（0-1）

        ThermalCouplingFailure: 热耦合失效
            - 与相邻热节点的热耦合失效
            - magnitude 表示失效耦合数量比例（0-1）

        PhaseChangeFailure: 相变材料失效
            - 相变蓄热材料失效
            - magnitude 表示相变能力下降比例（0-1）
    """
    TemperatureRunaway = "temperature_runaway"
    ThermalResistanceIncrease = "thermal_resistance_increase"
    HeatPathBlocked = "heat_path_blocked"
    TemperatureSensorFailure = "temperature_sensor_failure"
    HeatInputExcess = "heat_input_excess"
    HeatInputLoss = "heat_input_loss"
    InsulationDegradation = "insulation_degradation"
    ThermalCouplingFailure = "thermal_coupling_failure"
    PhaseChangeFailure = "phase_change_failure"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class ThermalNodeL2FaultType(str, Enum):
    THERMAL_RUNAWAY = 'thermal_runaway'
    SENSOR_BIAS = 'sensor_bias'
    BLOCKED_HEAT_PATH = 'blocked_heat_path'


def _effects_for(kind: ThermalNodeL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ThermalNodeL2FaultType.THERMAL_RUNAWAY: (
            ParameterEffect('heat_input_w', 'add', 35.0, 'fault heat input drives node temperature upward'),
            ParameterEffect('temperature_k', 'add', 15.0, 'fault heat input drives node temperature upward'),
        ),
        ThermalNodeL2FaultType.SENSOR_BIAS: (
            ParameterEffect('sensor_bias_k', 'add', 12.0, 'thermal sensor fault biases measured temperature'),
        ),
        ThermalNodeL2FaultType.BLOCKED_HEAT_PATH: (
            ParameterEffect('thermal_resistance_k_w', 'multiply', 2.5, 'blocked conduction path increases thermal resistance'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class ThermalNodeFault(TimeWindowFault):
    """Base L2-light fault for thermal_node."""
    fault_type_enum: ThermalNodeL2FaultType = ThermalNodeL2FaultType.THERMAL_RUNAWAY
    start_s: float = 120.0
    end_s: float = 300.0
    severity: float = 1.0
    target_id: str = ""

    def __post_init__(self) -> None:
        self.name = type(self).__name__
        self.fault_type = self.fault_type_enum.value
        self.effects = _effects_for(self.fault_type_enum)
        self.physical_mechanism = PHYSICAL_MECHANISMS[self.fault_type_enum]


PHYSICAL_MECHANISMS = {
    ThermalNodeL2FaultType.THERMAL_RUNAWAY: 'fault heat input drives node temperature upward',
    ThermalNodeL2FaultType.SENSOR_BIAS: 'thermal sensor fault biases measured temperature',
    ThermalNodeL2FaultType.BLOCKED_HEAT_PATH: 'blocked conduction path increases thermal resistance',
}


@dataclass
class ThermalNodeThermalRunawayFault(ThermalNodeFault):
    """fault heat input drives node temperature upward."""
    fault_type_enum: ThermalNodeL2FaultType = ThermalNodeL2FaultType.THERMAL_RUNAWAY
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class ThermalNodeSensorBiasFault(ThermalNodeFault):
    """thermal sensor fault biases measured temperature."""
    fault_type_enum: ThermalNodeL2FaultType = ThermalNodeL2FaultType.SENSOR_BIAS
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class ThermalNodeBlockedHeatPathFault(ThermalNodeFault):
    """blocked conduction path increases thermal resistance."""
    fault_type_enum: ThermalNodeL2FaultType = ThermalNodeL2FaultType.BLOCKED_HEAT_PATH
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[ThermalNodeFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        ThermalNodeThermalRunawayFault(),
        ThermalNodeSensorBiasFault(),
    ]

__all__ = [
    'ThermalNodeFaultType', 'ThermalNodeFault', "default_faults",
    'ThermalNodeThermalRunawayFault',
    'ThermalNodeSensorBiasFault',
    'ThermalNodeBlockedHeatPathFault',
]

# --- Local config-level fault application support migrated from root legacy module ---
from dataclasses import replace
from dataclasses import fields as _fault_dc_fields, is_dataclass as _fault_is_dataclass

def _fault_name(spec: FaultSpec) -> str:
    return getattr(spec.fault_type, "name", str(spec.fault_type))

def _fault_value(spec: FaultSpec) -> str:
    return getattr(spec.fault_type, "value", str(spec.fault_type))

def _mag(spec: FaultSpec, default: float = 1.0) -> float:
    try:
        return max(0.0, float(spec.magnitude))
    except Exception:
        return default

def _safe_replace_fault(config, **updates):
    if not updates:
        return config
    if _fault_is_dataclass(config):
        allowed = {f.name for f in _fault_dc_fields(config)}
        filtered = {key: value for key, value in updates.items() if key in allowed}
        if filtered:
            return replace(config, **filtered)
    return config

def _loss_scalar(value, fraction: float):
    return max(0.0, float(value) * (1.0 - max(0.0, float(fraction))))

def _loss_like(value, fraction: float):
    if isinstance(value, tuple):
        return tuple(_loss_scalar(v, fraction) for v in value)
    if isinstance(value, list):
        return [_loss_scalar(v, fraction) for v in value]
    return _loss_scalar(value, fraction)

def _zero_like(value):
    if isinstance(value, tuple):
        return tuple(0.0 for _ in value)
    if isinstance(value, list):
        return [0.0 for _ in value]
    return 0.0

def _increase_scalar(value, fraction: float):
    return max(0.0, float(value) * (1.0 + max(0.0, float(fraction))))

def _increase_like(value, fraction: float):
    if isinstance(value, tuple):
        return tuple(_increase_scalar(v, fraction) for v in value)
    if isinstance(value, list):
        return [_increase_scalar(v, fraction) for v in value]
    return _increase_scalar(value, fraction)

def apply_thermal_node_faults(tn_config, fault_specs: list[FaultSpec]) -> object:
    cfg = tn_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"TemperatureRunaway", "HeatInputExcess", "Overheating"}:
            if hasattr(cfg, "heat_gain_k_per_w"):
                updates["heat_gain_k_per_w"] = _increase_scalar(cfg.heat_gain_k_per_w, mag)
            if hasattr(cfg, "max_temp_k"):
                updates["max_temp_k"] = _loss_scalar(cfg.max_temp_k, mag * 0.1)
        if name in {"ThermalResistanceIncrease", "HeatPathBlocked", "InsulationDegradation", "ThermalCouplingFailure"} and hasattr(cfg, "tau_s"):
            updates["tau_s"] = _increase_scalar(cfg.tau_s, mag)
        if name == "HeatInputLoss" and hasattr(cfg, "heat_gain_k_per_w"):
            updates["heat_gain_k_per_w"] = _loss_scalar(cfg.heat_gain_k_per_w, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_thermal_node_fault(node: object, spec: FaultSpec, *, set_attr) -> bool:
    from dataclasses import replace

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if fault_value != "thermal_node_heat_bias":
        return False
    magnitude = float(getattr(spec, "magnitude", 0.0))
    if hasattr(node, "fault_heat_bias_w"):
        set_attr(node, "fault_heat_bias_w", magnitude)
        return True
    if hasattr(node, "params") and hasattr(node.params, "internal_heat_w"):
        set_attr(node, "params", replace(node.params, internal_heat_w=float(node.params.internal_heat_w) + magnitude))
        return True
    return False

