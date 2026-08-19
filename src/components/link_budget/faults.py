from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
链路预算故障类型模块

定义链路预算(LinkBudget)部件可能发生的故障类型。

文献支撑:
- 航天器多因素可靠性-洞察研究[R]. 飞鸽文档, 2024.
- Shao R, You W, Nie Y. Reliability modeling framework of satellite constellation based on three-parameter interval grey number Lz transformation[J]. Scientific Reports, 2025, 15: 21022.
"""

from enum import Enum


class LinkBudgetFaultType(Enum):
    """
    链路预算故障类型枚举

    链路预算故障主要包括信号衰减、误码率上升和链路中断等模式。

    枚举值:
        SignalAttenuation: 信号衰减
            - 信号强度下降超过预期
            - magnitude 表示衰减倍数

        BitErrorRateIncrease: 误码率上升
            - 数据传输误码率增加
            - magnitude 表示误码率增加倍数

        LinkInterruption: 链路中断
            - 通信链路完全中断
            - magnitude 表示中断持续时间（秒）

        NoiseFloorIncrease: 噪声基底上升
            - 接收机噪声水平上升
            - magnitude 表示噪声增加倍数

        Interference: 干扰
            - 外部干扰影响通信质量
            - magnitude 表示干扰强度

        PolarizationMismatch: 极化失配
            - 收发极化不匹配
            - magnitude 表示极化失配程度（0-1）

        DopplerShiftError: 多普勒频移误差
            - 多普勒频移补偿错误
            - magnitude 表示频移误差（Hz）

        Fading: 信号衰落
            - 信号强度波动
            - magnitude 表示衰落深度（dB）

        BandwidthReduction: 带宽缩减
            - 可用带宽减少
            - magnitude 表示带宽减少比例（0-1）
    """
    SignalAttenuation = "signal_attenuation"
    BitErrorRateIncrease = "bit_error_rate_increase"
    LinkInterruption = "link_interruption"
    NoiseFloorIncrease = "noise_floor_increase"
    Interference = "interference"
    PolarizationMismatch = "polarization_mismatch"
    DopplerShiftError = "doppler_shift_error"
    Fading = "fading"
    BandwidthReduction = "bandwidth_reduction"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class LinkBudgetL2FaultType(str, Enum):
    JAMMING = 'jamming'
    FADE = 'fade'
    ANTENNA_MISPOINTING = 'antenna_mispointing'


def _effects_for(kind: LinkBudgetL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        LinkBudgetL2FaultType.JAMMING: (
            ParameterEffect('interference_db', 'add', 12.0, 'RF interference raises noise/interference term'),
            ParameterEffect('margin_db', 'add', -12.0, 'RF interference raises noise/interference term'),
            ParameterEffect('ber', 'multiply', 1000.0, 'RF interference raises noise/interference term'),
        ),
        LinkBudgetL2FaultType.FADE: (
            ParameterEffect('path_loss_db', 'add', 8.0, 'atmospheric/body fade increases path loss'),
            ParameterEffect('margin_db', 'add', -8.0, 'atmospheric/body fade increases path loss'),
        ),
        LinkBudgetL2FaultType.ANTENNA_MISPOINTING: (
            ParameterEffect('margin_db', 'add', -6.0, 'mispointing reduces EIRP/G/T and margin'),
            ParameterEffect('ber', 'multiply', 100.0, 'mispointing reduces EIRP/G/T and margin'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class LinkBudgetFault(TimeWindowFault):
    """Base L2-light fault for link_budget."""
    fault_type_enum: LinkBudgetL2FaultType = LinkBudgetL2FaultType.JAMMING
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
    LinkBudgetL2FaultType.JAMMING: 'RF interference raises noise/interference term',
    LinkBudgetL2FaultType.FADE: 'atmospheric/body fade increases path loss',
    LinkBudgetL2FaultType.ANTENNA_MISPOINTING: 'mispointing reduces EIRP/G/T and margin',
}


@dataclass
class LinkBudgetJammingFault(LinkBudgetFault):
    """RF interference raises noise/interference term."""
    fault_type_enum: LinkBudgetL2FaultType = LinkBudgetL2FaultType.JAMMING
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class LinkBudgetFadeFault(LinkBudgetFault):
    """atmospheric/body fade increases path loss."""
    fault_type_enum: LinkBudgetL2FaultType = LinkBudgetL2FaultType.FADE
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class LinkBudgetAntennaMispointingFault(LinkBudgetFault):
    """mispointing reduces EIRP/G/T and margin."""
    fault_type_enum: LinkBudgetL2FaultType = LinkBudgetL2FaultType.ANTENNA_MISPOINTING
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[LinkBudgetFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        LinkBudgetJammingFault(),
        LinkBudgetFadeFault(),
    ]

__all__ = [
    'LinkBudgetFaultType', 'LinkBudgetFault', "default_faults",
    'LinkBudgetJammingFault',
    'LinkBudgetFadeFault',
    'LinkBudgetAntennaMispointingFault',
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

def apply_link_budget_faults(lb_config, fault_specs: list[FaultSpec]) -> object:
    cfg = lb_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SignalAttenuation", "Interference", "Fading", "PolarizationMismatch"} and hasattr(cfg, "misc_loss_db"):
            updates["misc_loss_db"] = float(cfg.misc_loss_db) + mag * 10.0
        if name in {"NoiseFloorIncrease", "BitErrorRateIncrease"} and hasattr(cfg, "noise_temp_k"):
            updates["noise_temp_k"] = _increase_scalar(cfg.noise_temp_k, mag)
        if name in {"LinkInterruption", "BandwidthReduction"} and hasattr(cfg, "downlink_eff"):
            updates["downlink_eff"] = _loss_scalar(cfg.downlink_eff, 1.0 if name == "LinkInterruption" else mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

