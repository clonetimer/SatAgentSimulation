from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
载荷传感器故障类型模块

定义载荷传感器(PayloadSensor)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
"""

from enum import Enum


class PayloadSensorFaultType(Enum):
    """
    载荷传感器故障类型枚举

    载荷传感器故障主要包括灵敏度下降、噪声增加和标定漂移等模式。

    枚举值:
        SensitivityLoss: 灵敏度损失
            - 传感器灵敏度下降
            - magnitude 表示灵敏度损失比例（0-1）

        NoiseIncrease: 噪声增加
            - 传感器噪声水平上升
            - magnitude 表示噪声增加倍数

        CalibrationDrift: 标定漂移
            - 传感器标定参数漂移
            - magnitude 表示漂移量

        SignalLoss: 信号丢失
            - 传感器信号完全丢失
            - magnitude 表示丢失持续时间（秒）

        Saturation: 饱和
            - 传感器输出饱和
            - magnitude 表示饱和程度（0-1）

        BiasError: 偏置误差
            - 传感器输出存在固定偏置
            - magnitude 表示偏置值

        StuckValue: 数值卡滞
            - 传感器输出固定在某个值
            - magnitude 表示卡滞值

        PowerLoss: 功率损失
            - 传感器供电故障
            - magnitude 表示功率损失比例（0-1）
    """
    SensitivityLoss = "sensitivity_loss"
    NoiseIncrease = "noise_increase"
    CalibrationDrift = "calibration_drift"
    SignalLoss = "signal_loss"
    Saturation = "saturation"
    BiasError = "bias_error"
    StuckValue = "stuck_value"
    PowerLoss = "power_loss"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class PayloadSensorL2FaultType(str, Enum):
    PIXEL_DROPOUT = 'pixel_dropout'
    BLUR = 'blur'
    RADIATION_HIT = 'radiation_hit'


def _effects_for(kind: PayloadSensorL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        PayloadSensorL2FaultType.PIXEL_DROPOUT: (
            ParameterEffect('bad_pixel_fraction', 'add', 0.15, 'pixel cluster dropout reduces usable focal plane area'),
            ParameterEffect('image_quality', 'multiply', 0.75, 'pixel cluster dropout reduces usable focal plane area'),
        ),
        PayloadSensorL2FaultType.BLUR: (
            ParameterEffect('image_quality', 'multiply', 0.55, 'focus/pointing blur lowers image quality'),
        ),
        PayloadSensorL2FaultType.RADIATION_HIT: (
            ParameterEffect('noise_sigma', 'multiply', 6.0, 'radiation event raises transient noise and bad pixels'),
            ParameterEffect('bad_pixel_fraction', 'add', 0.03, 'radiation event raises transient noise and bad pixels'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class PayloadSensorFault(TimeWindowFault):
    """Base L2-light fault for payload_sensor."""
    fault_type_enum: PayloadSensorL2FaultType = PayloadSensorL2FaultType.PIXEL_DROPOUT
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
    PayloadSensorL2FaultType.PIXEL_DROPOUT: 'pixel cluster dropout reduces usable focal plane area',
    PayloadSensorL2FaultType.BLUR: 'focus/pointing blur lowers image quality',
    PayloadSensorL2FaultType.RADIATION_HIT: 'radiation event raises transient noise and bad pixels',
}


@dataclass
class PayloadSensorPixelDropoutFault(PayloadSensorFault):
    """pixel cluster dropout reduces usable focal plane area."""
    fault_type_enum: PayloadSensorL2FaultType = PayloadSensorL2FaultType.PIXEL_DROPOUT
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class PayloadSensorBlurFault(PayloadSensorFault):
    """focus/pointing blur lowers image quality."""
    fault_type_enum: PayloadSensorL2FaultType = PayloadSensorL2FaultType.BLUR
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class PayloadSensorRadiationHitFault(PayloadSensorFault):
    """radiation event raises transient noise and bad pixels."""
    fault_type_enum: PayloadSensorL2FaultType = PayloadSensorL2FaultType.RADIATION_HIT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[PayloadSensorFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        PayloadSensorPixelDropoutFault(),
        PayloadSensorBlurFault(),
    ]

__all__ = [
    'PayloadSensorFaultType', 'PayloadSensorFault', "default_faults",
    'PayloadSensorPixelDropoutFault',
    'PayloadSensorBlurFault',
    'PayloadSensorRadiationHitFault',
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

def apply_payload_sensor_faults(ps_config, fault_specs: list[FaultSpec]) -> object:
    cfg = ps_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SignalLoss", "StuckValue"} and hasattr(cfg, "nominal_data_rate_bps"):
            updates["nominal_data_rate_bps"] = 0.0
        if name in {"SensitivityLoss", "NoiseIncrease", "CalibrationDrift", "BiasError", "BiasShift"} and hasattr(cfg, "min_quality_score"):
            updates["min_quality_score"] = min(1.0, _increase_scalar(cfg.min_quality_score, mag))
        if name == "SensitivityLoss" and hasattr(cfg, "nominal_data_rate_bps"):
            updates["nominal_data_rate_bps"] = _loss_scalar(cfg.nominal_data_rate_bps, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

