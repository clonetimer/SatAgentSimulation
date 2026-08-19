from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
星上存储故障类型模块

定义星上存储(OnboardStorage)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""

from enum import Enum


class OnboardStorageFaultType(Enum):
    """
    星上存储故障类型枚举

    星上存储故障主要包括读写故障、数据损坏和存储容量异常等模式。

    枚举值:
        ReadFailure: 读取失败
            - 无法从存储读取数据
            - magnitude 表示读取失败次数比例（0-1）

        WriteFailure: 写入失败
            - 无法向存储写入数据
            - magnitude 表示写入失败次数比例（0-1）

        DataCorruption: 数据损坏
            - 存储数据损坏
            - magnitude 表示损坏数据比例（0-1）

        CapacityLoss: 容量损失
            - 可用存储容量减少
            - magnitude 表示容量损失比例（0-1）

        PowerLoss: 功率损失
            - 存储供电故障
            - magnitude 表示功率损失比例（0-1）

        InterfaceFailure: 接口故障
            - 数据接口通信故障
            - magnitude 表示故障持续时间（秒）

        Wearout: 磨损耗尽
            - 闪存等磨损型存储耗尽
            - magnitude 表示剩余寿命比例（0-1）

        Overheating: 过热故障
            - 存储温度超过安全阈值
            - magnitude 表示温度超阈值比例
    """
    ReadFailure = "read_failure"
    WriteFailure = "write_failure"
    DataCorruption = "data_corruption"
    CapacityLoss = "capacity_loss"
    PowerLoss = "power_loss"
    InterfaceFailure = "interface_failure"
    Wearout = "wearout"
    Overheating = "overheating"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class OnboardStorageL2FaultType(str, Enum):
    BAD_BLOCK = 'bad_block'
    READ_ONLY = 'read_only'
    CORRUPTION = 'corruption'


def _effects_for(kind: OnboardStorageL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        OnboardStorageL2FaultType.BAD_BLOCK: (
            ParameterEffect('capacity_gb', 'multiply', 0.8, 'bad-block cluster reduces usable capacity and raises errors'),
            ParameterEffect('bit_error_rate', 'multiply', 1000000.0, 'bad-block cluster reduces usable capacity and raises errors'),
        ),
        OnboardStorageL2FaultType.READ_ONLY: (
            ParameterEffect('write_rate_mbps', 'override', 0.0, 'controller read-only mode blocks writes'),
        ),
        OnboardStorageL2FaultType.CORRUPTION: (
            ParameterEffect('bit_error_rate', 'multiply', 100000000.0, 'data corruption raises bit error probability'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class OnboardStorageFault(TimeWindowFault):
    """Base L2-light fault for onboard_storage."""
    fault_type_enum: OnboardStorageL2FaultType = OnboardStorageL2FaultType.BAD_BLOCK
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
    OnboardStorageL2FaultType.BAD_BLOCK: 'bad-block cluster reduces usable capacity and raises errors',
    OnboardStorageL2FaultType.READ_ONLY: 'controller read-only mode blocks writes',
    OnboardStorageL2FaultType.CORRUPTION: 'data corruption raises bit error probability',
}


@dataclass
class OnboardStorageBadBlockFault(OnboardStorageFault):
    """bad-block cluster reduces usable capacity and raises errors."""
    fault_type_enum: OnboardStorageL2FaultType = OnboardStorageL2FaultType.BAD_BLOCK
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class OnboardStorageReadOnlyFault(OnboardStorageFault):
    """controller read-only mode blocks writes."""
    fault_type_enum: OnboardStorageL2FaultType = OnboardStorageL2FaultType.READ_ONLY
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class OnboardStorageCorruptionFault(OnboardStorageFault):
    """data corruption raises bit error probability."""
    fault_type_enum: OnboardStorageL2FaultType = OnboardStorageL2FaultType.CORRUPTION
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[OnboardStorageFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        OnboardStorageBadBlockFault(),
        OnboardStorageReadOnlyFault(),
    ]

__all__ = [
    'OnboardStorageFaultType', 'OnboardStorageFault', "default_faults",
    'OnboardStorageBadBlockFault',
    'OnboardStorageReadOnlyFault',
    'OnboardStorageCorruptionFault',
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

def apply_onboard_storage_faults(os_config, fault_specs: list[FaultSpec]) -> object:
    cfg = os_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"ReadFailure", "WriteFailure", "DataCorruption", "Corruption", "CapacityLoss", "Wearout"} and hasattr(cfg, "capacity_bits"):
            updates["capacity_bits"] = _loss_scalar(cfg.capacity_bits, mag)
        if name in {"PowerLoss", "InterfaceFailure"} and hasattr(cfg, "high_watermark"):
            updates["high_watermark"] = 0.0
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

