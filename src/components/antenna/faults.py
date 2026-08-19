from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
天线故障类型模块

定义天线(Antenna)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- Shao R, You W, Nie Y. Reliability modeling framework of satellite constellation based on three-parameter interval grey number Lz transformation[J]. Scientific Reports, 2025, 15: 21022.
"""

from enum import Enum


class AntennaFaultType(Enum):
    """
    天线故障类型枚举

    天线故障主要包括增益下降、指向偏差和短路开路等模式。

    枚举值:
        GainDegradation: 增益下降
            - 天线增益降低
            - magnitude 表示增益下降比例（0-1）

        PointingError: 指向偏差
            - 天线指向偏离目标
            - magnitude 表示指向偏差角度（度）

        ShortCircuit: 短路
            - 天线馈电线路短路
            - magnitude 表示短路严重程度（0-1）

        OpenCircuit: 开路
            - 天线馈电线路断开
            - magnitude 表示开路严重程度（0-1）

        NoiseFigureIncrease: 噪声系数增加
            - 接收机噪声系数上升
            - magnitude 表示噪声系数增加倍数

        PolarizationMismatch: 极化失配
            - 天线极化与信号极化不匹配
            - magnitude 表示失配程度（0-1）

        ImpedanceMismatch: 阻抗失配
            - 天线阻抗与传输线不匹配
            - magnitude 表示失配程度（0-1）

        MechanicalFailure: 机械故障
            - 天线展开或转动机构故障
            - magnitude 表示故障严重程度（0-1）

        SignalLoss: 信号丢失
            - 接收或发射信号完全丢失
            - magnitude 表示丢失持续时间（秒）
    """
    GainDegradation = "gain_degradation"
    PointingError = "pointing_error"
    ShortCircuit = "short_circuit"
    OpenCircuit = "open_circuit"
    NoiseFigureIncrease = "noise_figure_increase"
    PolarizationMismatch = "polarization_mismatch"
    ImpedanceMismatch = "impedance_mismatch"
    MechanicalFailure = "mechanical_failure"
    SignalLoss = "signal_loss"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class AntennaL2FaultType(str, Enum):
    POINTING_LOSS = 'pointing_loss'
    FEED_OPEN = 'feed_open'
    GAIN_DROP = 'gain_drop'


def _effects_for(kind: AntennaL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        AntennaL2FaultType.POINTING_LOSS: (
            ParameterEffect('pointing_error_deg', 'add', 6.0, 'boresight misalignment increases pointing error and pattern loss'),
            ParameterEffect('gain_dbi', 'add', -4.0, 'boresight misalignment increases pointing error and pattern loss'),
            ParameterEffect('rf_path_factor', 'multiply', 0.65, 'boresight misalignment increases pointing error and pattern loss'),
        ),
        AntennaL2FaultType.FEED_OPEN: (
            ParameterEffect('availability', 'override', 0.0, 'feed/open-circuit fault disconnects RF path'),
            ParameterEffect('rf_path_factor', 'override', 0.0, 'feed/open-circuit fault disconnects RF path'),
        ),
        AntennaL2FaultType.GAIN_DROP: (
            ParameterEffect('gain_dbi', 'add', -5.0, 'feed/network mismatch reduces realized gain'),
            ParameterEffect('rf_path_factor', 'multiply', 0.55, 'feed/network mismatch reduces realized gain'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class AntennaFault(TimeWindowFault):
    """Base L2-light fault for antenna."""
    fault_type_enum: AntennaL2FaultType = AntennaL2FaultType.POINTING_LOSS
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
    AntennaL2FaultType.POINTING_LOSS: 'boresight misalignment increases pointing error and pattern loss',
    AntennaL2FaultType.FEED_OPEN: 'feed/open-circuit fault disconnects RF path',
    AntennaL2FaultType.GAIN_DROP: 'feed/network mismatch reduces realized gain',
}


@dataclass
class AntennaPointingLossFault(AntennaFault):
    """boresight misalignment increases pointing error and pattern loss."""
    fault_type_enum: AntennaL2FaultType = AntennaL2FaultType.POINTING_LOSS
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class AntennaFeedOpenFault(AntennaFault):
    """feed/open-circuit fault disconnects RF path."""
    fault_type_enum: AntennaL2FaultType = AntennaL2FaultType.FEED_OPEN
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class AntennaGainDropFault(AntennaFault):
    """feed/network mismatch reduces realized gain."""
    fault_type_enum: AntennaL2FaultType = AntennaL2FaultType.GAIN_DROP
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[AntennaFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        AntennaPointingLossFault(),
        AntennaFeedOpenFault(),
    ]

__all__ = [
    'AntennaFaultType', 'AntennaFault', "default_faults",
    'AntennaPointingLossFault',
    'AntennaFeedOpenFault',
    'AntennaGainDropFault',
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

def apply_antenna_faults(ant_config, fault_specs: list[FaultSpec]) -> object:
    cfg = ant_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"GainDegradation", "ShortCircuit", "OpenCircuit", "MechanicalFailure", "SignalLoss"} and hasattr(cfg, "peak_gain_dbi"):
            updates["peak_gain_dbi"] = 0.0 if name in {"OpenCircuit", "SignalLoss"} else _loss_scalar(cfg.peak_gain_dbi, mag)
        if name in {"PointingError", "MechanicalFailure"} and hasattr(cfg, "half_power_beamwidth_deg"):
            updates["half_power_beamwidth_deg"] = _increase_scalar(cfg.half_power_beamwidth_deg, mag)
        if name in {"NoiseFigureIncrease", "PolarizationMismatch", "ImpedanceMismatch"} and hasattr(cfg, "efficiency"):
            updates["efficiency"] = _loss_scalar(cfg.efficiency, min(1.0, mag * 0.5))
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

