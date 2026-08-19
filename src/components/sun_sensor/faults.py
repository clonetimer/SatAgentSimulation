from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
Sun Sensor故障类型模块

定义太阳敏感器部件可能发生的故障类型。
"""

from enum import Enum


class SunSensorFaultType(Enum):
    """
    Sun Sensor故障类型枚举

    定义太阳敏感器部件可能发生的各种故障类型。

    枚举值:
        SignalLoss: 信号丢失
            - 太阳敏感器完全失去信号输出
            - magnitude 表示丢失程度（0-1），1.0为完全丢失

        BiasDrift: 偏置漂移
            - 太阳敏感器测量值产生固定偏移
            - magnitude 表示偏置值（单位取决于传感器类型）

        NoiseIncrease: 噪声增加
            - 太阳敏感器输出噪声异常增大
            - magnitude 表示噪声增大的倍数（1.0 为正常噪声水平）

        Saturation: 饱和故障
            - 太阳敏感器输出饱和
            - magnitude 表示饱和程度

        EclipseBlindness: 日食盲
            - 太阳敏感器在日食期间无法正常工作
            - magnitude 无意义

    示例:
        # 太阳敏感器噪声增加故障
        from ..fault_spec import FaultSpec
        fault = FaultSpec(
            fault_type=SunSensorFaultType.NoiseIncrease,
            onset_time_s=1000.0,
            duration_s=-1.0,
            magnitude=3.0,  # 噪声增大3倍
            target_id="sun_sensor_1"
        )
    """
    SignalLoss = "signal_loss"          # 信号丢失
    BiasDrift = "bias_drift"            # 偏置漂移
    NoiseIncrease = "noise_increase"    # 噪声增加
    Saturation = "saturation"          # 饱和故障
    EclipseBlindness = "eclipse_blindness"  # 日食盲

    @property
    def severity_level(self) -> int:
        """
        获取故障严重程度等级

        返回:
            int: 故障严重程度等级（1-3级）
                - 1级: 轻微故障，影响有限
                - 2级: 中等故障，影响较大
                - 3级: 严重故障，影响重大
        """
        severity_mapping = {
            SunSensorFaultType.SignalLoss: 3,
            SunSensorFaultType.EclipseBlindness: 2,
            SunSensorFaultType.Saturation: 2,
            SunSensorFaultType.BiasDrift: 1,
            SunSensorFaultType.NoiseIncrease: 1,
        }
        return severity_mapping[self]


# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class SunSensorL2FaultType(str, Enum):
    FALSE_ECLIPSE = 'false_eclipse'
    CELL_FAILURE = 'cell_failure'
    CONTAMINATION = 'contamination'


def _effects_for(kind: SunSensorL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        SunSensorL2FaultType.FALSE_ECLIPSE: (
            ParameterEffect('availability', 'override', 0.0, 'electronics/threshold fault reports invalid sun vector'),
            ParameterEffect('sensitivity_factor', 'multiply', 0.0, 'electronics/threshold fault reports invalid sun vector'),
        ),
        SunSensorL2FaultType.CELL_FAILURE: (
            ParameterEffect('sensitivity_factor', 'multiply', 0.45, 'sensor cell failure reduces sensitivity'),
            ParameterEffect('sun_vector_sigma_deg', 'multiply', 3.0, 'sensor cell failure reduces sensitivity'),
        ),
        SunSensorL2FaultType.CONTAMINATION: (
            ParameterEffect('sensitivity_factor', 'multiply', 0.6, 'cover contamination lowers illumination sensitivity'),
            ParameterEffect('bias_deg', 'add', 0.5, 'cover contamination lowers illumination sensitivity'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class SunSensorFault(TimeWindowFault):
    """Base L2-light fault for sun_sensor."""
    fault_type_enum: SunSensorL2FaultType = SunSensorL2FaultType.FALSE_ECLIPSE
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
    SunSensorL2FaultType.FALSE_ECLIPSE: 'electronics/threshold fault reports invalid sun vector',
    SunSensorL2FaultType.CELL_FAILURE: 'sensor cell failure reduces sensitivity',
    SunSensorL2FaultType.CONTAMINATION: 'cover contamination lowers illumination sensitivity',
}


@dataclass
class SunSensorFalseEclipseFault(SunSensorFault):
    """electronics/threshold fault reports invalid sun vector."""
    fault_type_enum: SunSensorL2FaultType = SunSensorL2FaultType.FALSE_ECLIPSE
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class SunSensorCellFailureFault(SunSensorFault):
    """sensor cell failure reduces sensitivity."""
    fault_type_enum: SunSensorL2FaultType = SunSensorL2FaultType.CELL_FAILURE
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class SunSensorContaminationFault(SunSensorFault):
    """cover contamination lowers illumination sensitivity."""
    fault_type_enum: SunSensorL2FaultType = SunSensorL2FaultType.CONTAMINATION
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[SunSensorFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        SunSensorFalseEclipseFault(),
        SunSensorCellFailureFault(),
    ]

__all__ = [
    'SunSensorFaultType', 'SunSensorFault', "default_faults",
    'SunSensorFalseEclipseFault',
    'SunSensorCellFailureFault',
    'SunSensorContaminationFault',
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

def apply_sun_sensor_faults(ss_config, fault_specs: list[FaultSpec]) -> object:
    cfg = ss_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SignalLoss", "EclipseBlindness", "PhotoCellFailure"} and hasattr(cfg, "accuracy"):
            updates["accuracy"] = 0.0
        if name in {"BiasDrift", "NoiseIncrease"} and hasattr(cfg, "noise_std"):
            updates["noise_std"] = _increase_scalar(cfg.noise_std, mag)
        if name == "Saturation" and hasattr(cfg, "min_intensity"):
            updates["min_intensity"] = _increase_scalar(cfg.min_intensity, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

