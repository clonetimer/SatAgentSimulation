from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
Magnetometer故障类型模块

定义磁强计部件可能发生的故障类型。
"""

from enum import Enum


class MagnetometerFaultType(Enum):
    """
    Magnetometer故障类型枚举

    定义磁强计部件可能发生的各种故障类型。

    枚举值:
        SignalLoss: 信号丢失
            - 磁强计完全失去信号输出
            - magnitude 表示丢失程度（0-1），1.0为完全丢失

        BiasDrift: 偏置漂移
            - 磁强计测量值产生固定偏移
            - magnitude 表示偏置值（单位：nT）

        NoiseIncrease: 噪声增加
            - 磁强计输出噪声异常增大
            - magnitude 表示噪声增大的倍数（1.0 为正常噪声水平）

        ScaleFactorError: 标度因子误差
            - 磁强计标度因子发生变化
            - magnitude 表示标度因子变化百分比

        Hysteresis: 迟滞故障
            - 磁强计出现迟滞现象
            - magnitude 表示迟滞程度

    示例:
        # 磁强计偏置漂移故障
        from ..fault_spec import FaultSpec
        fault = FaultSpec(
            fault_type=MagnetometerFaultType.BiasDrift,
            onset_time_s=500.0,
            duration_s=-1.0,
            magnitude=10.0,  # 偏置值 10 nT
            target_id="magnetometer_1"
        )
    """
    SignalLoss = "signal_loss"          # 信号丢失
    BiasDrift = "bias_drift"            # 偏置漂移
    NoiseIncrease = "noise_increase"    # 噪声增加
    ScaleFactorError = "scale_factor_error"  # 标度因子误差
    Hysteresis = "hysteresis"          # 迟滞故障

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
            MagnetometerFaultType.SignalLoss: 3,
            MagnetometerFaultType.ScaleFactorError: 2,
            MagnetometerFaultType.BiasDrift: 2,
            MagnetometerFaultType.Hysteresis: 1,
            MagnetometerFaultType.NoiseIncrease: 1,
        }
        return severity_mapping[self]


# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class MagnetometerL2FaultType(str, Enum):
    AXIS_BIAS = 'axis_bias'
    AXIS_DROPOUT = 'axis_dropout'
    SATURATION = 'saturation'


def _effects_for(kind: MagnetometerL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        MagnetometerL2FaultType.AXIS_BIAS: (
            ParameterEffect('bias_nt', 'add', 120.0, 'axis electronics fault adds magnetic field bias'),
        ),
        MagnetometerL2FaultType.AXIS_DROPOUT: (
            ParameterEffect('availability', 'override', 0.0, 'axis dropout invalidates magnetic field measurement'),
        ),
        MagnetometerL2FaultType.SATURATION: (
            ParameterEffect('scale_factor', 'multiply', 0.2, 'front-end saturation clamps useful scale factor'),
            ParameterEffect('noise_sigma_nt', 'multiply', 5.0, 'front-end saturation clamps useful scale factor'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class MagnetometerFault(TimeWindowFault):
    """Base L2-light fault for magnetometer."""
    fault_type_enum: MagnetometerL2FaultType = MagnetometerL2FaultType.AXIS_BIAS
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
    MagnetometerL2FaultType.AXIS_BIAS: 'axis electronics fault adds magnetic field bias',
    MagnetometerL2FaultType.AXIS_DROPOUT: 'axis dropout invalidates magnetic field measurement',
    MagnetometerL2FaultType.SATURATION: 'front-end saturation clamps useful scale factor',
}


@dataclass
class MagnetometerAxisBiasFault(MagnetometerFault):
    """axis electronics fault adds magnetic field bias."""
    fault_type_enum: MagnetometerL2FaultType = MagnetometerL2FaultType.AXIS_BIAS
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class MagnetometerAxisDropoutFault(MagnetometerFault):
    """axis dropout invalidates magnetic field measurement."""
    fault_type_enum: MagnetometerL2FaultType = MagnetometerL2FaultType.AXIS_DROPOUT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class MagnetometerSaturationFault(MagnetometerFault):
    """front-end saturation clamps useful scale factor."""
    fault_type_enum: MagnetometerL2FaultType = MagnetometerL2FaultType.SATURATION
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[MagnetometerFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        MagnetometerAxisBiasFault(),
        MagnetometerAxisDropoutFault(),
    ]

__all__ = [
    'MagnetometerFaultType', 'MagnetometerFault', "default_faults",
    'MagnetometerAxisBiasFault',
    'MagnetometerAxisDropoutFault',
    'MagnetometerSaturationFault',
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

def apply_magnetometer_faults(mag_config, fault_specs: list[FaultSpec]) -> object:
    cfg = mag_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = _mag(spec)
        updates = {}
        if name == "SignalLoss" and hasattr(cfg, "axis_scale"):
            updates["axis_scale"] = _zero_like(cfg.axis_scale)
        if name in {"BiasDrift", "BiasShift"} and hasattr(cfg, "bias_t"):
            updates["bias_t"] = tuple(float(v) + mag for v in cfg.bias_t)
        if name == "NoiseIncrease" and hasattr(cfg, "noise_std_t"):
            updates["noise_std_t"] = _increase_like(cfg.noise_std_t, mag)
        if name == "ScaleFactorError" and hasattr(cfg, "axis_scale"):
            updates["axis_scale"] = _increase_like(cfg.axis_scale, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

