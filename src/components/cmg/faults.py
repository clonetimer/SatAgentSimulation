from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
控制力矩陀螺故障类型模块

定义控制力矩陀螺(CMG)部件可能发生的故障类型。

文献支撑:
- Inampudi R, Gordeuk J. Simulation of Malfunctions for the ISS Double-Gimbal Control Moment Gyroscope[C]. AIAA, 2015.
- Shen Q, Yue C, Yu X, et al. Fault Modeling, Estimation, and Fault-Tolerant Steering Logic Design for Single-Gimbal Control Moment Gyro[J]. IEEE Transactions on Control Systems Technology, 2020.
- Yue C, Shen Q, Cao X, et al. Fault Modeling of General Momentum Exchange Devices in Spacecraft Attitude Control Systems[J]. Journal of the Franklin Institute, 2020.
"""

from enum import Enum


class CMGFaultType(Enum):
    """
    控制力矩陀螺故障类型枚举

    CMG故障分为机械、热和电气三大类，参考ISS双框架CMG故障建模方法。

    枚举值:
        GimbalMotorFailure: 框架电机失效
            - 框架驱动电机故障，导致框架无法旋转
            - magnitude 表示故障严重程度（0-1）

        SpinMotorFailure: 飞轮电机失效
            - 飞轮驱动电机故障，导致飞轮转速下降或停止
            - magnitude 表示转速下降比例（0-1）

        BearingWear: 轴承磨损
            - 旋转轴承磨损，增加摩擦阻力
            - magnitude 表示摩擦系数增加倍数（1.0为正常）

        GimbalLock: 框架锁死
            - 框架被锁定在当前位置
            - magnitude 表示锁死角度偏差（度）

        TorqueDecay: 力矩衰减
            - CMG输出力矩能力下降
            - magnitude 表示力矩损失比例（0-1）

        ThermalOverload: 热过载
            - CMG温度超过安全阈值
            - magnitude 表示温度超阈值比例

        CommunicationLoss: 通信丢失
            - CMG与控制器通信中断
            - magnitude 表示通信中断持续时间（秒）

        SingularityLock: 奇异锁死
            - CMG组合进入奇异状态，无法输出所需力矩
            - magnitude 表示奇异程度（0-1）
    """
    GimbalMotorFailure = "gimbal_motor_failure"
    SpinMotorFailure = "spin_motor_failure"
    BearingWear = "bearing_wear"
    GimbalLock = "gimbal_lock"
    TorqueDecay = "torque_decay"
    ThermalOverload = "thermal_overload"
    CommunicationLoss = "communication_loss"
    SingularityLock = "singularity_lock"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class CmgL2FaultType(str, Enum):
    GIMBAL_STUCK = 'gimbal_stuck'
    WHEEL_DRIVE_FAULT = 'wheel_drive_fault'
    RATE_LIMIT_FAULT = 'rate_limit_fault'


def _effects_for(kind: CmgL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        CmgL2FaultType.GIMBAL_STUCK: (
            ParameterEffect('gimbal_rate_limit_rad_s', 'override', 0.0, 'gimbal bearing/jam blocks commanded steering'),
            ParameterEffect('torque_authority_factor', 'multiply', 0.2, 'gimbal bearing/jam blocks commanded steering'),
        ),
        CmgL2FaultType.WHEEL_DRIVE_FAULT: (
            ParameterEffect('wheel_momentum_nms', 'multiply', 0.45, 'wheel drive fault reduces stored momentum'),
            ParameterEffect('torque_authority_factor', 'multiply', 0.55, 'wheel drive fault reduces stored momentum'),
        ),
        CmgL2FaultType.RATE_LIMIT_FAULT: (
            ParameterEffect('gimbal_rate_limit_rad_s', 'multiply', 0.35, 'drive electronics limits gimbal slew rate'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class CmgFault(TimeWindowFault):
    """Base L2-light fault for cmg."""
    fault_type_enum: CmgL2FaultType = CmgL2FaultType.GIMBAL_STUCK
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
    CmgL2FaultType.GIMBAL_STUCK: 'gimbal bearing/jam blocks commanded steering',
    CmgL2FaultType.WHEEL_DRIVE_FAULT: 'wheel drive fault reduces stored momentum',
    CmgL2FaultType.RATE_LIMIT_FAULT: 'drive electronics limits gimbal slew rate',
}


@dataclass
class CmgGimbalStuckFault(CmgFault):
    """gimbal bearing/jam blocks commanded steering."""
    fault_type_enum: CmgL2FaultType = CmgL2FaultType.GIMBAL_STUCK
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class CmgWheelDriveFaultFault(CmgFault):
    """wheel drive fault reduces stored momentum."""
    fault_type_enum: CmgL2FaultType = CmgL2FaultType.WHEEL_DRIVE_FAULT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class CmgRateLimitFaultFault(CmgFault):
    """drive electronics limits gimbal slew rate."""
    fault_type_enum: CmgL2FaultType = CmgL2FaultType.RATE_LIMIT_FAULT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[CmgFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        CmgGimbalStuckFault(),
        CmgWheelDriveFaultFault(),
    ]

__all__ = [
    'CMGFaultType', 'CmgFault', "default_faults",
    'CmgGimbalStuckFault',
    'CmgWheelDriveFaultFault',
    'CmgRateLimitFaultFault',
]

# --- Local config-level fault application support migrated from root legacy module ---
from dataclasses import replace
from dataclasses import fields as _fault_dc_fields, is_dataclass as _fault_is_dataclass

def _fault_name(spec: FaultSpec) -> str:
    return getattr(spec.fault_type, "name", str(spec.fault_type))

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

def apply_cmg_faults(cmg_config, fault_specs: list[FaultSpec]) -> object:
    cfg = cmg_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"GimbalMotorFailure", "GimbalLock", "SingularityLock", "CommunicationLoss"} and hasattr(cfg, "gimbal_rate_limit_rad_s"):
            updates["gimbal_rate_limit_rad_s"] = 0.0
        if name in {"SpinMotorFailure", "BearingWear", "ThermalOverload"}:
            if hasattr(cfg, "wheel_speed_rad_s"):
                updates["wheel_speed_rad_s"] = _loss_scalar(cfg.wheel_speed_rad_s, mag)
            if hasattr(cfg, "wheel_speed_limit_rad_s") and cfg.wheel_speed_limit_rad_s is not None:
                updates["wheel_speed_limit_rad_s"] = _loss_scalar(cfg.wheel_speed_limit_rad_s, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg



# --- Basilisk runtime fault application (component-owned) ---
_CMG_LOCK_FAULTS = {
    "gimbal_motor_failure",
    "gimbal_lock",
    "singularity_lock",
    "communication_loss",
    "gimbal_stuck",
}
_CMG_SPIN_FAULTS = {
    "spin_motor_failure",
    "torque_decay",
    "wheel_drive_fault",
}
_CMG_WEAR_FAULTS = {
    "bearing_wear",
    "gimbal_friction_growth",
}
_CMG_RATE_FAULTS = {"rate_limit_fault"}


def _fault_value(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "value", spec.fault_type))


def _magnitude(spec: FaultSpec, default: float = 1.0) -> float:
    try:
        return max(0.0, float(getattr(spec, "magnitude", default)))
    except Exception:
        return default


def _iter_vscmg_data(target: object) -> list[Any]:
    data = getattr(target, "VSCMGData", None)
    if data is None and hasattr(target, "effector"):
        data = getattr(target.effector, "VSCMGData", None)
    if data is None:
        return []
    try:
        count = len(data)
    except Exception:
        return []
    out: list[Any] = []
    for idx in range(int(count)):
        try:
            out.append(data[idx])
        except Exception:
            continue
    return out


def _select_indices(target_id: str, count: int) -> tuple[int, ...]:
    target = target_id.lower()
    digits = "".join(ch if ch.isdigit() else " " for ch in target).split()
    if digits:
        idx = int(digits[-1])
        if 0 <= idx < count:
            return (idx,)
    return tuple(range(count))


def _set(obj: object, attr: str, value: Any, *, set_attr=None) -> bool:
    if obj is None or not hasattr(obj, attr):
        return False
    if set_attr is not None:
        set_attr(obj, attr, value)
    else:
        setattr(obj, attr, value)
    return True


def apply_runtime_cmg_fault(target: object, spec: FaultSpec, *, set_attr=None) -> tuple[dict[str, object], ...]:
    """Apply component-owned native VSCMG runtime fault effects.

    Effects are restricted to Basilisk ``VSCMGConfigMsgPayload`` fields already
    present in ``VSCMGStateEffector.VSCMGData``.  The subsystem supplies the
    scheduling/recovery callback; this helper only owns field semantics.
    """
    data = _iter_vscmg_data(target)
    if not data:
        return ()
    fault = _fault_value(spec)
    mag = min(1.0, _magnitude(spec, 1.0))
    target_id = str(getattr(spec, "target_id", "") or "")
    mutations: list[dict[str, object]] = []
    for idx in _select_indices(target_id, len(data)):
        unit = data[idx]
        changed: list[str] = []
        if fault in _CMG_LOCK_FAULTS:
            if _set(unit, "gammaDot_max", 0.0, set_attr=set_attr):
                changed.append("gammaDot_max")
            if _set(unit, "u_g_max", 0.0, set_attr=set_attr):
                changed.append("u_g_max")
        elif fault in _CMG_SPIN_FAULTS:
            factor = max(0.0, 1.0 - mag)
            for attr in ("u_s_max", "Omega_max"):
                if hasattr(unit, attr) and _set(unit, attr, float(getattr(unit, attr)) * factor, set_attr=set_attr):
                    changed.append(attr)
        elif fault in _CMG_RATE_FAULTS:
            factor = max(0.0, 1.0 - mag)
            if hasattr(unit, "gammaDot_max") and _set(unit, "gammaDot_max", float(getattr(unit, "gammaDot_max")) * factor, set_attr=set_attr):
                changed.append("gammaDot_max")
        elif fault in _CMG_WEAR_FAULTS:
            growth = max(0.0, mag)
            for attr in ("wheelLinearFrictionRatio", "gimbalLinearFrictionRatio"):
                base = float(getattr(unit, attr, 0.0))
                new_value = base + growth if base <= 0.0 else base * (1.0 + growth)
                if _set(unit, attr, new_value, set_attr=set_attr):
                    changed.append(attr)
        if changed:
            mutations.append({"component": "cmg", "target_id": target_id, "index": idx, "changed": tuple(changed)})
    return tuple(mutations)

