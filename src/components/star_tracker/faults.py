from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
Star Tracker故障类型模块

定义星敏感器部件可能发生的故障类型。
"""

from enum import Enum


class StarTrackerFaultType(Enum):
    """
    Star Tracker故障类型枚举

    定义星敏感器部件可能发生的各种故障类型。

    枚举值:
        SignalLoss: 信号丢失
            - 星敏感器完全失去信号输出
            - magnitude 表示丢失程度（0-1），1.0为完全丢失

        BiasDrift: 偏置漂移
            - 星敏感器测量值产生固定偏移
            - magnitude 表示偏置值（MRP格式）

        AccuracyLoss: 精度下降
            - 星敏感器测量精度显著下降
            - magnitude 表示精度下降的倍数

        FieldOfViewObstruction: 视场遮挡
            - 星敏感器视场被遮挡
            - magnitude 表示遮挡程度（0-1）

        StuckAtLast: 卡滞在最后值
            - 星敏感器输出始终为最后一次有效测量值
            - magnitude 无意义

    示例:
        # 星敏感器视场遮挡故障
        from ..fault_spec import FaultSpec
        fault = FaultSpec(
            fault_type=StarTrackerFaultType.FieldOfViewObstruction,
            onset_time_s=2000.0,
            duration_s=300.0,
            magnitude=0.5,  # 50%遮挡
            target_id="star_tracker_1"
        )
    """
    SignalLoss = "signal_loss"                # 信号丢失
    BiasDrift = "bias_drift"                  # 偏置漂移
    AccuracyLoss = "accuracy_loss"            # 精度下降
    FieldOfViewObstruction = "fov_obstruction"  # 视场遮挡
    StuckAtLast = "stuck_at_last"            # 卡滞在最后值

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
            StarTrackerFaultType.SignalLoss: 3,
            StarTrackerFaultType.StuckAtLast: 3,
            StarTrackerFaultType.FieldOfViewObstruction: 2,
            StarTrackerFaultType.BiasDrift: 2,
            StarTrackerFaultType.AccuracyLoss: 1,
        }
        return severity_mapping[self]


# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class StarTrackerL2FaultType(str, Enum):
    BLINDING = 'blinding'
    DROPOUT = 'dropout'
    MISALIGNMENT = 'misalignment'


def _effects_for(kind: StarTrackerL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        StarTrackerL2FaultType.BLINDING: (
            ParameterEffect('availability', 'override', 0.0, 'sun/moon blinding invalidates star solution'),
            ParameterEffect('dropout_probability', 'override', 1.0, 'sun/moon blinding invalidates star solution'),
        ),
        StarTrackerL2FaultType.DROPOUT: (
            ParameterEffect('availability', 'multiply', 0.3, 'tracking dropout lowers measurement availability'),
            ParameterEffect('dropout_probability', 'add', 0.6, 'tracking dropout lowers measurement availability'),
        ),
        StarTrackerL2FaultType.MISALIGNMENT: (
            ParameterEffect('boresight_bias_arcsec', 'add', 80.0, 'mount/thermal shift adds boresight bias'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class StarTrackerFault(TimeWindowFault):
    """Base L2-light fault for star_tracker."""
    fault_type_enum: StarTrackerL2FaultType = StarTrackerL2FaultType.BLINDING
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
    StarTrackerL2FaultType.BLINDING: 'sun/moon blinding invalidates star solution',
    StarTrackerL2FaultType.DROPOUT: 'tracking dropout lowers measurement availability',
    StarTrackerL2FaultType.MISALIGNMENT: 'mount/thermal shift adds boresight bias',
}


@dataclass
class StarTrackerBlindingFault(StarTrackerFault):
    """sun/moon blinding invalidates star solution."""
    fault_type_enum: StarTrackerL2FaultType = StarTrackerL2FaultType.BLINDING
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class StarTrackerDropoutFault(StarTrackerFault):
    """tracking dropout lowers measurement availability."""
    fault_type_enum: StarTrackerL2FaultType = StarTrackerL2FaultType.DROPOUT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class StarTrackerMisalignmentFault(StarTrackerFault):
    """mount/thermal shift adds boresight bias."""
    fault_type_enum: StarTrackerL2FaultType = StarTrackerL2FaultType.MISALIGNMENT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[StarTrackerFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        StarTrackerBlindingFault(),
        StarTrackerDropoutFault(),
    ]

__all__ = [
    'StarTrackerFaultType', 'StarTrackerFault', "default_faults",
    'StarTrackerBlindingFault',
    'StarTrackerDropoutFault',
    'StarTrackerMisalignmentFault',
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

def apply_star_tracker_faults(st_config, fault_specs: list[FaultSpec]) -> object:
    cfg = st_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SignalLoss", "StuckAtLast"} and hasattr(cfg, "max_drift_norm"):
            updates["max_drift_norm"] = 0.0
        if name in {"BiasDrift", "AccuracyLoss", "FieldOfViewObstruction", "StarIdentificationFailure"} and hasattr(cfg, "drift_rate_mrp_s"):
            updates["drift_rate_mrp_s"] = _increase_like(cfg.drift_rate_mrp_s, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

