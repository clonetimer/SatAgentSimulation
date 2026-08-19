from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
数据队列故障类型模块

定义数据队列(DataQueue)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- 新型故障树分析方法在航天可靠性领域的革新与实践[R]. 2025.
"""

from enum import Enum


class DataQueueFaultType(Enum):
    """
    数据队列故障类型枚举

    数据队列故障主要包括队列溢出、数据丢失、死锁和延迟等模式。

    枚举值:
        QueueOverflow: 队列溢出
            - 数据写入速度超过处理速度，队列满溢
            - magnitude 表示溢出数据量比例（0-1）

        DataLoss: 数据丢失
            - 队列数据丢失
            - magnitude 表示丢失数据比例（0-1）

        Deadlock: 死锁
            - 队列读写操作死锁
            - magnitude 表示死锁持续时间（秒）

        ReadFailure: 读取失败
            - 无法从队列读取数据
            - magnitude 表示读取失败次数比例（0-1）

        WriteFailure: 写入失败
            - 无法向队列写入数据
            - magnitude 表示写入失败次数比例（0-1）

        DataCorruption: 数据损坏
            - 队列中数据损坏
            - magnitude 表示损坏数据比例（0-1）

        ProcessingDelay: 处理延迟
            - 数据处理延迟超过阈值
            - magnitude 表示延迟时间（秒）

        MemoryExhaustion: 内存耗尽
            - 队列占用内存超出限制
            - magnitude 表示内存超限制比例（0-1）

        SynchronizationFailure: 同步失败
            - 队列读写同步机制失效
            - magnitude 表示同步失败次数比例（0-1）
    """
    QueueOverflow = "queue_overflow"
    DataLoss = "data_loss"
    Deadlock = "deadlock"
    ReadFailure = "read_failure"
    WriteFailure = "write_failure"
    DataCorruption = "data_corruption"
    ProcessingDelay = "processing_delay"
    MemoryExhaustion = "memory_exhaustion"
    SynchronizationFailure = "synchronization_failure"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class DataQueueL2FaultType(str, Enum):
    OVERFLOW = 'overflow'
    READ_STALL = 'read_stall'
    PRIORITY_INVERSION = 'priority_inversion'


def _effects_for(kind: DataQueueL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        DataQueueL2FaultType.OVERFLOW: (
            ParameterEffect('packet_loss_rate', 'add', 0.2, 'write/read imbalance causes queue overflow and packet loss'),
            ParameterEffect('read_rate_bps', 'multiply', 0.65, 'write/read imbalance causes queue overflow and packet loss'),
        ),
        DataQueueL2FaultType.READ_STALL: (
            ParameterEffect('read_rate_bps', 'multiply', 0.15, 'downlink reader stalls and drain rate collapses'),
        ),
        DataQueueL2FaultType.PRIORITY_INVERSION: (
            ParameterEffect('read_rate_bps', 'multiply', 0.55, 'scheduler misorders packets reducing effective throughput'),
            ParameterEffect('packet_loss_rate', 'add', 0.05, 'scheduler misorders packets reducing effective throughput'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class DataQueueFault(TimeWindowFault):
    """Base L2-light fault for data_queue."""
    fault_type_enum: DataQueueL2FaultType = DataQueueL2FaultType.OVERFLOW
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
    DataQueueL2FaultType.OVERFLOW: 'write/read imbalance causes queue overflow and packet loss',
    DataQueueL2FaultType.READ_STALL: 'downlink reader stalls and drain rate collapses',
    DataQueueL2FaultType.PRIORITY_INVERSION: 'scheduler misorders packets reducing effective throughput',
}


@dataclass
class DataQueueOverflowFault(DataQueueFault):
    """write/read imbalance causes queue overflow and packet loss."""
    fault_type_enum: DataQueueL2FaultType = DataQueueL2FaultType.OVERFLOW
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class DataQueueReadStallFault(DataQueueFault):
    """downlink reader stalls and drain rate collapses."""
    fault_type_enum: DataQueueL2FaultType = DataQueueL2FaultType.READ_STALL
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class DataQueuePriorityInversionFault(DataQueueFault):
    """scheduler misorders packets reducing effective throughput."""
    fault_type_enum: DataQueueL2FaultType = DataQueueL2FaultType.PRIORITY_INVERSION
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[DataQueueFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        DataQueueOverflowFault(),
        DataQueueReadStallFault(),
    ]

__all__ = [
    'DataQueueFaultType', 'DataQueueFault', "default_faults",
    'DataQueueOverflowFault',
    'DataQueueReadStallFault',
    'DataQueuePriorityInversionFault',
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

def apply_data_queue_faults(dq_config, fault_specs: list[FaultSpec]) -> object:
    cfg = dq_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        if hasattr(cfg, "capacity_bits") and name in {"QueueOverflow", "DataLoss", "ReadFailure", "WriteFailure", "DataCorruption", "MemoryExhaustion"}:
            cfg = _safe_replace_fault(cfg, capacity_bits=_loss_scalar(cfg.capacity_bits, 0.5 if name == "QueueOverflow" else mag))
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_storage_fault(storage: object, spec: FaultSpec, *, set_attr) -> bool:
    """Apply runtime storage capacity faults to ``SimpleStorageUnit``."""

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if fault_value not in {"capacity_loss", "bad_block", "queue_overflow", "read_only"}:
        return False
    if not hasattr(storage, "storageCapacity"):
        return False
    magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 0.0))))
    set_attr(storage, "storageCapacity", float(getattr(storage, "storageCapacity")) * (1.0 - magnitude))
    return True

