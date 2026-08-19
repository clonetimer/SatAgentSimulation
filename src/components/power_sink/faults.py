from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
功率负载故障类型模块

定义功率负载(PowerSink)部件可能发生的故障类型。

文献支撑:
- Rezaee A, Nobahari N, Asgarifar A, et al. Smart Fault Detection in Nanosatellite Electrical Power System[J]. arXiv, 2026.
- NASA. A Hybrid Model and Data Driven Approach for Anomaly Detection in Space Power Systems[R]. ASCEND, 2025.
"""

from enum import Enum


class PowerSinkFaultType(Enum):
    """
    功率负载故障类型枚举

    功率负载故障主要包括功耗异常、短路和开路等模式。

    枚举值:
        OverpowerConsumption: 功耗过载
            - 负载功耗超过额定值
            - magnitude 表示功耗超量比例（0-1）

        UnderpowerConsumption: 功耗不足
            - 负载功耗低于预期
            - magnitude 表示功耗不足比例（0-1）

        ShortCircuit: 短路
            - 负载内部短路
            - magnitude 表示短路程度（0-1）

        OpenCircuit: 开路
            - 负载电路断开
            - magnitude 表示开路程度（0-1）

        PowerSurge: 功率浪涌
            - 瞬时功率突增
            - magnitude 表示浪涌倍数

        PowerDrop: 功率骤降
            - 瞬时功率突降
            - magnitude 表示骤降比例（0-1）

        ThermalOverload: 热过载
            - 负载温度超过安全阈值
            - magnitude 表示温度超阈值比例

        FailureToStart: 启动失败
            - 负载无法启动
            - magnitude 表示启动失败次数比例（0-1）

        UnexpectedShutdown: 意外关机
            - 负载意外关闭
            - magnitude 表示关机持续时间（秒）
    """
    OverpowerConsumption = "overpower_consumption"
    UnderpowerConsumption = "underpower_consumption"
    ShortCircuit = "short_circuit"
    OpenCircuit = "open_circuit"
    PowerSurge = "power_surge"
    PowerDrop = "power_drop"
    ThermalOverload = "thermal_overload"
    FailureToStart = "failure_to_start"
    UnexpectedShutdown = "unexpected_shutdown"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class PowerSinkL2FaultType(str, Enum):
    OVERLOAD = 'overload'
    INTERMITTENT_LOAD = 'intermittent_load'
    OPEN_LOAD = 'open_load'


def _effects_for(kind: PowerSinkL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PowerSinkL2FaultType.OVERLOAD: (
            ParameterEffect('load_power_w', 'multiply', 1.8, 'load fault draws excess power'),
            ParameterEffect('surge_power_w', 'add', 15.0, 'load fault draws excess power'),
        ),
        PowerSinkL2FaultType.INTERMITTENT_LOAD: (
            ParameterEffect('availability', 'multiply', 0.7, 'intermittent connection lowers availability and adds surge'),
            ParameterEffect('surge_power_w', 'add', 5.0, 'intermittent connection lowers availability and adds surge'),
        ),
        PowerSinkL2FaultType.OPEN_LOAD: (
            ParameterEffect('load_power_w', 'override', 0.0, 'open circuit removes load draw'),
            ParameterEffect('availability', 'override', 0.0, 'open circuit removes load draw'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class PowerSinkFault(TimeWindowFault):
    """Base L2-light fault for power_sink."""
    fault_type_enum: PowerSinkL2FaultType = PowerSinkL2FaultType.OVERLOAD
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
    PowerSinkL2FaultType.OVERLOAD: 'load fault draws excess power',
    PowerSinkL2FaultType.INTERMITTENT_LOAD: 'intermittent connection lowers availability and adds surge',
    PowerSinkL2FaultType.OPEN_LOAD: 'open circuit removes load draw',
}


@dataclass
class PowerSinkOverloadFault(PowerSinkFault):
    """load fault draws excess power."""
    fault_type_enum: PowerSinkL2FaultType = PowerSinkL2FaultType.OVERLOAD
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class PowerSinkIntermittentLoadFault(PowerSinkFault):
    """intermittent connection lowers availability and adds surge."""
    fault_type_enum: PowerSinkL2FaultType = PowerSinkL2FaultType.INTERMITTENT_LOAD
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class PowerSinkOpenLoadFault(PowerSinkFault):
    """open circuit removes load draw."""
    fault_type_enum: PowerSinkL2FaultType = PowerSinkL2FaultType.OPEN_LOAD
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[PowerSinkFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        PowerSinkOverloadFault(),
        PowerSinkIntermittentLoadFault(),
    ]

__all__ = [
    'PowerSinkFaultType', 'PowerSinkFault', "default_faults",
    'PowerSinkOverloadFault',
    'PowerSinkIntermittentLoadFault',
    'PowerSinkOpenLoadFault',
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

def apply_power_sink_faults(ps_config, fault_specs: list[FaultSpec]) -> object:
    cfg = ps_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"ShortCircuit", "OverpowerConsumption", "Overpower", "PowerSurge", "ThermalOverload"} and hasattr(cfg, "base_w"):
            updates["base_w"] = _increase_scalar(cfg.base_w, mag)
        if name in {"OpenCircuit", "PowerLoss", "UnderpowerConsumption", "PowerDrop", "UnexpectedShutdown"} and hasattr(cfg, "base_w"):
            updates["base_w"] = 0.0
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

