from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
载荷故障类型模块

定义载荷(Payload)部件可能发生的故障类型。

文献支撑:
- 基于MBSE的中继卫星捕获跟踪系统故障建模分析[J]. 航天器工程, 2025, 34(4): 71-79.
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- Shao R, You W, Nie Y. Reliability modeling framework of satellite constellation based on three-parameter interval grey number Lz transformation[J]. Scientific Reports, 2025, 15: 21022.
"""

from enum import Enum


class PayloadFaultType(Enum):
    """
    载荷故障类型枚举

    载荷故障主要包括传感器失效、通信中断、数据丢失和功率异常等模式。

    枚举值:
        SensorFailure: 传感器失效
            - 载荷传感器无法正常工作
            - magnitude 表示失效传感器数量比例（0-1）

        CommunicationLoss: 通信丢失
            - 载荷与星上计算机通信中断
            - magnitude 表示通信中断持续时间（秒）

        DataCorruption: 数据损坏
            - 采集数据出现错误或损坏
            - magnitude 表示数据损坏比例（0-1）

        PowerLoss: 功率损失
            - 载荷供电故障
            - magnitude 表示功率损失比例（0-1）

        Overheating: 过热故障
            - 载荷温度超过安全阈值
            - magnitude 表示温度超阈值比例

        StuckOn: 永久开启
            - 载荷无法关闭，持续工作
            - magnitude 表示持续工作时间（秒）

        StuckOff: 永久关闭
            - 载荷无法开启
            - magnitude 表示关闭持续时间（秒）

        CalibrationDrift: 标定漂移
            - 传感器标定参数随时间漂移
            - magnitude 表示漂移量

        ImageDegradation: 图像退化
            - 成像载荷图像质量下降
            - magnitude 表示图像质量下降比例（0-1）

        RadiationDamage: 辐射损伤
            - 空间辐射导致电子器件损伤
            - magnitude 表示损伤程度（0-1）
    """
    SensorFailure = "sensor_failure"
    CommunicationLoss = "communication_loss"
    DataCorruption = "data_corruption"
    PowerLoss = "power_loss"
    Overheating = "overheating"
    StuckOn = "stuck_on"
    StuckOff = "stuck_off"
    CalibrationDrift = "calibration_drift"
    ImageDegradation = "image_degradation"
    RadiationDamage = "radiation_damage"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class PayloadL2FaultType(str, Enum):
    INSTRUMENT_OFF = 'instrument_off'
    SATURATION = 'saturation'
    POINTING_LIMIT = 'pointing_limit'


def _effects_for(kind: PayloadL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PayloadL2FaultType.INSTRUMENT_OFF: (
            ParameterEffect('availability', 'override', 0.0, 'instrument power fault disables science output'),
            ParameterEffect('data_rate_mbps', 'override', 0.0, 'instrument power fault disables science output'),
        ),
        PayloadL2FaultType.SATURATION: (
            ParameterEffect('sensitivity_factor', 'multiply', 0.35, 'detector saturation lowers usable sensitivity/data quality'),
            ParameterEffect('data_rate_mbps', 'multiply', 0.5, 'detector saturation lowers usable sensitivity/data quality'),
        ),
        PayloadL2FaultType.POINTING_LIMIT: (
            ParameterEffect('pointing_limit_deg', 'multiply', 0.4, 'pointing constraint violation reduces observing quality'),
            ParameterEffect('data_rate_mbps', 'multiply', 0.6, 'pointing constraint violation reduces observing quality'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class PayloadFault(TimeWindowFault):
    """Base L2-light fault for payload."""
    fault_type_enum: PayloadL2FaultType = PayloadL2FaultType.INSTRUMENT_OFF
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
    PayloadL2FaultType.INSTRUMENT_OFF: 'instrument power fault disables science output',
    PayloadL2FaultType.SATURATION: 'detector saturation lowers usable sensitivity/data quality',
    PayloadL2FaultType.POINTING_LIMIT: 'pointing constraint violation reduces observing quality',
}


@dataclass
class PayloadInstrumentOffFault(PayloadFault):
    """instrument power fault disables science output."""
    fault_type_enum: PayloadL2FaultType = PayloadL2FaultType.INSTRUMENT_OFF
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class PayloadSaturationFault(PayloadFault):
    """detector saturation lowers usable sensitivity/data quality."""
    fault_type_enum: PayloadL2FaultType = PayloadL2FaultType.SATURATION
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class PayloadPointingLimitFault(PayloadFault):
    """pointing constraint violation reduces observing quality."""
    fault_type_enum: PayloadL2FaultType = PayloadL2FaultType.POINTING_LIMIT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[PayloadFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        PayloadInstrumentOffFault(),
        PayloadSaturationFault(),
    ]

__all__ = [
    'PayloadFaultType', 'PayloadFault', "default_faults",
    'PayloadInstrumentOffFault',
    'PayloadSaturationFault',
    'PayloadPointingLimitFault',
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

def apply_payload_faults(payload_config, fault_specs: list[FaultSpec]) -> object:
    cfg = payload_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SensorFailure", "CommunicationLoss", "PowerLoss", "StuckOff"}:
            if hasattr(cfg, "data_rate_bps"):
                updates["data_rate_bps"] = 0.0
            if hasattr(cfg, "observation_power_w") and name == "PowerLoss":
                updates["observation_power_w"] = 0.0
        if name in {"DataCorruption", "CalibrationDrift", "ImageDegradation", "RadiationDamage"} and hasattr(cfg, "data_rate_bps"):
            updates["data_rate_bps"] = _loss_scalar(cfg.data_rate_bps, mag)
        if name in {"Overheating", "StuckOn"} and hasattr(cfg, "observation_power_w"):
            updates["observation_power_w"] = _increase_scalar(cfg.observation_power_w, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_payload_instrument_fault(instrument: object, spec: FaultSpec, *, set_attr) -> bool:
    """Apply a runtime payload data-generation fault to the instrument."""

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    if not hasattr(instrument, "nodeBaudRate"):
        return False
    if fault_value in {"instrument_off", "stuck_off", "power_loss"}:
        set_attr(instrument, "nodeBaudRate", 0.0)
        return True
    if fault_value in {"image_degradation", "saturation", "calibration_drift"}:
        factor = max(0.0, 1.0 - min(1.0, magnitude))
        set_attr(instrument, "nodeBaudRate", float(getattr(instrument, "nodeBaudRate")) * factor)
        return True
    return False

