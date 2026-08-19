from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
磁力矩器故障类型模块

定义磁力矩器(MTB)部件可能发生的故障类型。

文献支撑:
- 基于磁力矩器的卫星姿态容错控制方法[P]. 中国专利, CN202511413583.
- Khan A, et al. Reconfigurable Asymmetric Embedded Magnetorquers for Attitude Control of Nanosatellites[J]. IEEE Transactions on Aerospace and Electronic Systems, 2021.
- Mohammadi A, et al. Adaptive Attitude Control of a Satellite by Considering Magnetorquer Faults[J]. ISA Transactions, 2022.
"""

from enum import Enum


class MTBFaultType(Enum):
    """
    磁力矩器故障类型枚举

    磁力矩器故障主要包括线圈失效、退磁、短路和开路等模式。

    枚举值:
        CoilOpenCircuit: 线圈开路
            - 磁力矩器线圈断开，无法产生磁矩
            - magnitude 表示失效线圈数量比例（0-1）

        CoilShortCircuit: 线圈短路
            - 磁力矩器线圈短路，产生异常电流
            - magnitude 表示短路程度（0-1）

        Demagnetization: 退磁
            - 永磁体退磁，磁力矩输出能力下降
            - magnitude 表示剩余磁通量比例（0-1）

        PowerLoss: 功率损失
            - 供电故障导致磁力矩输出下降
            - magnitude 表示功率损失比例（0-1）

        Saturation: 磁饱和
            - 铁芯磁饱和，无法进一步增加磁矩
            - magnitude 表示饱和程度（0-1）

        TemperatureDrift: 温度漂移
            - 温度变化导致磁矩输出偏差
            - magnitude 表示漂移系数

        CommunicationLoss: 通信丢失
            - 磁力矩器与控制器通信中断
            - magnitude 表示通信中断持续时间（秒）

        CoilBurnout: 线圈烧毁
            - 线圈过热烧毁
            - magnitude 表示烧毁线圈数量比例（0-1）
    """
    CoilOpenCircuit = "coil_open_circuit"
    CoilShortCircuit = "coil_short_circuit"
    Demagnetization = "demagnetization"
    PowerLoss = "power_loss"
    Saturation = "saturation"
    TemperatureDrift = "temperature_drift"
    CommunicationLoss = "communication_loss"
    CoilBurnout = "coil_burnout"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class MtbL2FaultType(str, Enum):
    COIL_OPEN = 'coil_open'
    COIL_SHORT = 'coil_short'
    DIPOLE_SATURATION = 'dipole_saturation'


def _effects_for(kind: MtbL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        MtbL2FaultType.COIL_OPEN: (
            ParameterEffect('current_factor', 'override', 0.0, 'open coil prevents commanded dipole generation'),
            ParameterEffect('magnetic_moment_factor', 'override', 0.0, 'open coil prevents commanded dipole generation'),
        ),
        MtbL2FaultType.COIL_SHORT: (
            ParameterEffect('coil_resistance_ohm', 'multiply', 0.25, 'shorted winding changes resistance and heats coil'),
            ParameterEffect('current_factor', 'multiply', 1.5, 'shorted winding changes resistance and heats coil'),
            ParameterEffect('magnetic_moment_factor', 'multiply', 0.45, 'shorted winding changes resistance and heats coil'),
        ),
        MtbL2FaultType.DIPOLE_SATURATION: (
            ParameterEffect('dipole_limit_am2', 'multiply', 0.5, 'magnetic core/electronics saturation limits dipole'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class MtbFault(TimeWindowFault):
    """Base L2-light fault for mtb."""
    fault_type_enum: MtbL2FaultType = MtbL2FaultType.COIL_OPEN
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
    MtbL2FaultType.COIL_OPEN: 'open coil prevents commanded dipole generation',
    MtbL2FaultType.COIL_SHORT: 'shorted winding changes resistance and heats coil',
    MtbL2FaultType.DIPOLE_SATURATION: 'magnetic core/electronics saturation limits dipole',
}


@dataclass
class MtbCoilOpenFault(MtbFault):
    """open coil prevents commanded dipole generation."""
    fault_type_enum: MtbL2FaultType = MtbL2FaultType.COIL_OPEN
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class MtbCoilShortFault(MtbFault):
    """shorted winding changes resistance and heats coil."""
    fault_type_enum: MtbL2FaultType = MtbL2FaultType.COIL_SHORT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class MtbDipoleSaturationFault(MtbFault):
    """magnetic core/electronics saturation limits dipole."""
    fault_type_enum: MtbL2FaultType = MtbL2FaultType.DIPOLE_SATURATION
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[MtbFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        MtbCoilOpenFault(),
        MtbCoilShortFault(),
    ]

__all__ = [
    'MTBFaultType', 'MtbFault', "default_faults",
    'MtbCoilOpenFault',
    'MtbCoilShortFault',
    'MtbDipoleSaturationFault',
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

def apply_mtb_faults(mtb_config, fault_specs: list[FaultSpec]) -> object:
    cfg = mtb_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"CoilOpenCircuit", "CoilFailure", "PowerLoss", "CoilBurnout"} and hasattr(cfg, "dipole_limit_am2"):
            updates["dipole_limit_am2"] = _zero_like(cfg.dipole_limit_am2)
        if name in {"CoilShortCircuit", "Demagnetization", "Saturation", "TemperatureDrift"} and hasattr(cfg, "dipole_limit_am2"):
            updates["dipole_limit_am2"] = _loss_like(cfg.dipole_limit_am2, mag)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg



# --- Basilisk runtime fault application (component-owned) ---
_OPEN_FAULTS = {"coil_open", "coil_open_circuit", "coil_burnout", "power_loss", "communication_loss"}
_DERATE_FAULTS = {"coil_short", "coil_short_circuit", "demagnetization", "dipole_saturation", "saturation", "temperature_drift"}


def _fault_value(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "value", spec.fault_type))


def _magnitude(spec: FaultSpec, default: float = 1.0) -> float:
    try:
        return max(0.0, float(getattr(spec, "magnitude", default)))
    except Exception:
        return default


def _select_axis(target_id: str, count: int) -> int | None:
    target = target_id.lower()
    digits = "".join(ch if ch.isdigit() else " " for ch in target).split()
    if digits:
        idx = int(digits[-1])
        if 0 <= idx < count:
            return idx
    for name, idx in (("x", 0), ("y", 1), ("z", 2)):
        if f"axis_{name}" in target or target.endswith(f".{name}"):
            return idx if idx < count else None
    return None


def apply_runtime_mtb_fault(
    target: object,
    spec: FaultSpec,
    *,
    set_attr=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Apply component-owned runtime MTB fault effects via ``MTBCmdMsg``.

    Basilisk MTB exposes runtime actuation through native command messages; the
    helper therefore gates or derates commanded dipoles without changing an
    output message or reimplementing torque physics at subsystem/whole-spacecraft
    level.
    """
    if not (hasattr(target, "commanded_dipoles_am2") and hasattr(target, "bar_count") and hasattr(target, "effector")):
        return ()
    from .builder import write_mtb_command_message
    base = list(target.commanded_dipoles_am2 or [0.0] * int(target.bar_count))
    if len(base) < int(target.bar_count):
        base.extend([0.0] * (int(target.bar_count) - len(base)))
    fault = _fault_value(spec)
    magnitude = min(1.0, _magnitude(spec, 1.0))
    target_id = str(getattr(spec, "target_id", "") or "")
    axis = _select_axis(target_id, len(base))
    affected = range(len(base)) if axis is None else (axis,)
    after = list(base)
    if fault in _OPEN_FAULTS:
        for idx in affected:
            after[idx] = 0.0
    elif fault in _DERATE_FAULTS:
        factor = max(0.0, 1.0 - magnitude)
        for idx in affected:
            after[idx] *= factor
    else:
        return ()

    old_msg = target.cmd_msg
    old_values = tuple(target.commanded_dipoles_am2)
    msg = write_mtb_command_message(after)
    if set_attr is not None:
        # Register reversible bundle fields for the whole-spacecraft injector.
        set_attr(target, "cmd_msg", msg)
        set_attr(target, "commanded_dipoles_am2", tuple(after))
    else:
        target.cmd_msg = msg
        target.commanded_dipoles_am2 = tuple(after)
    target.effector.mtbCmdInMsg.subscribeTo(msg)
    if register_post_restore is not None:
        def _restore_subscription() -> None:
            target.effector.mtbCmdInMsg.subscribeTo(target.cmd_msg)
        register_post_restore(_restore_subscription)
    return ({
        "component": "mtb",
        "target_id": target_id,
        "changed": ("MTBCmdMsg.mtbDipoleCmds",),
        "before": old_values,
        "after": tuple(after),
        "message_replaced": old_msg is not msg,
    },)

