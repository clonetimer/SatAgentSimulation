from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
配电单元故障类型模块

定义配电单元(PDU)部件可能发生的故障类型。

文献支撑:
- Ye Z, Yin J, Zhang T, et al. Fault reconstruction method of high redundancy satellite power distribution unit[J]. IET Power Electronics, 2023, 16(8): 1443-1454.
- 全电推卫星能源全自主管理方法及系统[P]. 中国专利, CN202511344112.
- NASA. A Hybrid Model and Data Driven Approach for Anomaly Detection in Space Power Systems[R]. ASCEND, 2025.
"""

from enum import Enum


class PDUFaultType(Enum):
    """
    配电单元故障类型枚举

    PDU故障主要包括过流保护、短路、开路、过热和监控系统故障等模式。

    枚举值:
        OvercurrentProtection: 过流保护触发
            - 负载电流超过保护阈值，保护开关动作
            - magnitude 表示过流倍数

        ShortCircuit: 短路故障
            - 输出端口短路，导致保护开关断开
            - magnitude 表示短路严重程度（0-1）

        OpenCircuit: 开路故障
            - 输出端口开路，负载无法获得电力
            - magnitude 表示开路端口数量比例（0-1）

        Overheating: 过热故障
            - PDU温度超过安全阈值
            - magnitude 表示温度超阈值比例

        VoltageDrop: 电压下降
            - 母线电压低于额定值
            - magnitude 表示电压下降比例（0-1）

        LoadImbalance: 负载不平衡
            - 各输出端口负载分配不均
            - magnitude 表示不平衡度（0-1）

        FuseBlown: 熔断器熔断
            - 熔断器熔断，切断电路保护
            - magnitude 表示熔断熔断器数量比例（0-1）

        MonitoringFailure: 监控系统故障
            - PDU监测传感器或通信故障
            - magnitude 表示故障严重程度（0-1）

        PowerModuleFailure: 功率模块失效
            - 内部功率变换模块故障
            - magnitude 表示失效模块数量比例（0-1）

        MOSFETFailure: MOS管故障
            - 功率开关管故障（短路或开路）
            - magnitude 表示故障MOS管数量比例（0-1）
    """
    OvercurrentProtection = "overcurrent_protection"
    ShortCircuit = "short_circuit"
    OpenCircuit = "open_circuit"
    Overheating = "overheating"
    VoltageDrop = "voltage_drop"
    LoadImbalance = "load_imbalance"
    FuseBlown = "fuse_blown"
    MonitoringFailure = "monitoring_failure"
    PowerModuleFailure = "power_module_failure"
    MOSFETFailure = "mosfet_failure"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class PduL2FaultType(str, Enum):
    CHANNEL_TRIP = 'channel_trip'
    OVER_CURRENT = 'over_current'
    SWITCH_STUCK = 'switch_stuck'


def _effects_for(kind: PduL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PduL2FaultType.CHANNEL_TRIP: (
            ParameterEffect('trip_state', 'override', 1.0, 'protective trip opens one load channel'),
            ParameterEffect('availability', 'multiply', 0.4, 'protective trip opens one load channel'),
        ),
        PduL2FaultType.OVER_CURRENT: (
            ParameterEffect('path_resistance_ohm', 'multiply', 2.0, 'load short increases current and voltage drop'),
            ParameterEffect('bus_voltage_v', 'add', -3.0, 'load short increases current and voltage drop'),
        ),
        PduL2FaultType.SWITCH_STUCK: (
            ParameterEffect('availability', 'multiply', 0.65, 'solid-state switch stuck prevents commanded switching'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class PduFault(TimeWindowFault):
    """Base L2-light fault for pdu."""
    fault_type_enum: PduL2FaultType = PduL2FaultType.CHANNEL_TRIP
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
    PduL2FaultType.CHANNEL_TRIP: 'protective trip opens one load channel',
    PduL2FaultType.OVER_CURRENT: 'load short increases current and voltage drop',
    PduL2FaultType.SWITCH_STUCK: 'solid-state switch stuck prevents commanded switching',
}


@dataclass
class PduChannelTripFault(PduFault):
    """protective trip opens one load channel."""
    fault_type_enum: PduL2FaultType = PduL2FaultType.CHANNEL_TRIP
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class PduOverCurrentFault(PduFault):
    """load short increases current and voltage drop."""
    fault_type_enum: PduL2FaultType = PduL2FaultType.OVER_CURRENT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class PduSwitchStuckFault(PduFault):
    """solid-state switch stuck prevents commanded switching."""
    fault_type_enum: PduL2FaultType = PduL2FaultType.SWITCH_STUCK
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[PduFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        PduChannelTripFault(),
        PduOverCurrentFault(),
    ]

__all__ = [
    'PDUFaultType', 'PduFault', "default_faults",
    'PduChannelTripFault',
    'PduOverCurrentFault',
    'PduSwitchStuckFault',
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

def apply_pdu_faults(pdu_config, fault_specs: list[FaultSpec]) -> object:
    cfg = pdu_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        if hasattr(cfg, "bus_max_w") and name in {"OvercurrentProtection", "ShortCircuit", "OpenCircuit", "FuseBlown", "PowerModuleFailure", "MOSFETFailure", "VoltageDrop", "LoadImbalance", "Overheating"}:
            cfg = _safe_replace_fault(cfg, bus_max_w=_loss_scalar(cfg.bus_max_w, mag))
    return cfg

