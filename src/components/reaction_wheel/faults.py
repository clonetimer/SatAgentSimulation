from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
反作用轮故障类型模块

定义反作用轮部件可能发生的故障类型。
"""

from enum import Enum


class RWFaultType(Enum):
    """
    反作用轮故障类型枚举

    定义反作用轮部件可能发生的各种故障类型。

    枚举值:
        Jamming: 卡死故障
            - 反作用轮无法旋转，完全卡死
            - magnitude 表示卡死程度（0-1），1.0为完全卡死

        MotorFailure: 电机故障
            - 反作用轮电机故障
            - magnitude 表示故障程度（0-1）

    示例:
        # 反作用轮完全卡死
        fault = FaultSpec(
            fault_type=RWFaultType.Jamming,
            onset_time_s=1500.0,
            duration_s=-1.0,
            magnitude=1.0,  # 完全卡死
            target_id="reaction_wheel_1"
        )
    """
    Jamming = "rw_jamming"  # 卡死故障
    MotorFailure = "rw_motor_failure"  # 电机故障
    BearingSeizure = "rw_bearing_seizure"  # 轴承咬死/摩擦突增（Basilisk 原生摩擦参数）

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class ReactionWheelL2FaultType(str, Enum):
    BEARING_SEIZURE = 'bearing_seizure'
    MOTOR_OPEN = 'motor_open'
    SPEED_SENSOR_FAULT = 'speed_sensor_fault'


def _effects_for(kind: ReactionWheelL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        ReactionWheelL2FaultType.BEARING_SEIZURE: (
            ParameterEffect('friction_torque_nm', 'add', 0.02, 'bearing seizure raises friction and removes torque authority'),
            ParameterEffect('max_torque_nm', 'multiply', 0.25, 'bearing seizure raises friction and removes torque authority'),
            ParameterEffect('availability', 'multiply', 0.3, 'bearing seizure raises friction and removes torque authority'),
        ),
        ReactionWheelL2FaultType.MOTOR_OPEN: (
            ParameterEffect('max_torque_nm', 'override', 0.0, 'motor phase open removes commanded torque path'),
            ParameterEffect('availability', 'override', 0.0, 'motor phase open removes commanded torque path'),
        ),
        ReactionWheelL2FaultType.SPEED_SENSOR_FAULT: (
            ParameterEffect('speed_sensor_bias_rad_s', 'add', 25.0, 'tachometer fault biases speed feedback'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class ReactionWheelFault(TimeWindowFault):
    """Base L2-light fault for reaction_wheel."""
    fault_type_enum: ReactionWheelL2FaultType = ReactionWheelL2FaultType.BEARING_SEIZURE
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
    ReactionWheelL2FaultType.BEARING_SEIZURE: 'bearing seizure raises friction and removes torque authority',
    ReactionWheelL2FaultType.MOTOR_OPEN: 'motor phase open removes commanded torque path',
    ReactionWheelL2FaultType.SPEED_SENSOR_FAULT: 'tachometer fault biases speed feedback',
}


@dataclass
class ReactionWheelBearingSeizureFault(ReactionWheelFault):
    """bearing seizure raises friction and removes torque authority."""
    fault_type_enum: ReactionWheelL2FaultType = ReactionWheelL2FaultType.BEARING_SEIZURE
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class ReactionWheelMotorOpenFault(ReactionWheelFault):
    """motor phase open removes commanded torque path."""
    fault_type_enum: ReactionWheelL2FaultType = ReactionWheelL2FaultType.MOTOR_OPEN
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class ReactionWheelSpeedSensorFaultFault(ReactionWheelFault):
    """tachometer fault biases speed feedback."""
    fault_type_enum: ReactionWheelL2FaultType = ReactionWheelL2FaultType.SPEED_SENSOR_FAULT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[ReactionWheelFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        ReactionWheelBearingSeizureFault(),
        ReactionWheelMotorOpenFault(),
    ]

ReactionWheelFaultType = RWFaultType

__all__ = [
    'RWFaultType', 'ReactionWheelFaultType', 'ReactionWheelFault', "default_faults",
    'ReactionWheelBearingSeizureFault',
    'ReactionWheelMotorOpenFault',
    'ReactionWheelSpeedSensorFaultFault',
    'build_reaction_wheel_fault_spec',
    'reaction_wheel_fault_friction_factor',
    'apply_reaction_wheel_spec_faults',
    'apply_runtime_reaction_wheel_fault',
]


# --- Basilisk-native component fault mapping ---

def build_reaction_wheel_fault_spec(
    fault_type: RWFaultType,
    *,
    onset_time_s: float = 0.0,
    duration_s: float = -1.0,
    magnitude: float = 1.0,
    target_id: str = "rw_0",
) -> FaultSpec:
    """Build a component-owned RW ``FaultSpec`` for subsystem/spacecraft callers."""

    return FaultSpec(
        fault_type=fault_type,
        onset_time_s=float(onset_time_s),
        duration_s=float(duration_s),
        magnitude=float(magnitude),
        target_id=target_id,
    )


def _fault_key(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "value", spec.fault_type))


def reaction_wheel_fault_friction_factor(fault_specs: Sequence[FaultSpec]) -> float:
    """Return a build-time native-friction multiplier implied by RW faults.

    The subsystem may use this scalar when converting its configuration into
    component ``ReactionWheelSpec`` values.  The fault physics remain defined
    here in the component module.
    """

    factor = 0.0
    for spec in fault_specs:
        key = _fault_key(spec)
        magnitude = max(0.0, float(getattr(spec, "magnitude", 1.0)))
        if key == RWFaultType.BearingSeizure.value:
            factor = max(factor, 10.0 * magnitude)
        elif key == RWFaultType.Jamming.value:
            factor = max(factor, 25.0 * magnitude)
    return factor


def apply_reaction_wheel_spec_faults(wheel_specs, fault_specs: Sequence[FaultSpec]):
    """Apply component RW faults to immutable ``ReactionWheelSpec`` values.

    This is a build-time path. Runtime faults use
    :func:`apply_runtime_reaction_wheel_fault` against Basilisk RW config data.
    """

    from dataclasses import replace as _replace

    specs = list(wheel_specs)
    for fault in fault_specs:
        key = _fault_key(fault)
        target_index = _target_wheel_index(getattr(fault, "target_id", None), len(specs))
        indices = range(len(specs)) if target_index is None else (target_index,)
        magnitude = max(0.0, min(1.0, float(getattr(fault, "magnitude", 1.0))))
        for idx in indices:
            spec = specs[idx]
            if key in {RWFaultType.BearingSeizure.value, RWFaultType.Jamming.value}:
                coulomb = max(float(spec.fCoulomb), 0.02 * max(0.1, magnitude))
                specs[idx] = _replace(
                    spec,
                    use_rw_friction=True,
                    fCoulomb=coulomb,
                    fStatic=max(float(spec.fStatic), 1.5 * coulomb),
                    betaStatic=float(spec.betaStatic) if float(spec.betaStatic) > 0.0 else 10.0,
                    cViscous=max(float(spec.cViscous), 1.0e-5 * max(0.1, magnitude)),
                    u_max_nm=(0.0 if key == RWFaultType.Jamming.value else float(spec.u_max_nm) * max(0.0, 1.0 - 0.75 * magnitude)),
                )
            elif key == RWFaultType.MotorFailure.value:
                specs[idx] = _replace(spec, u_max_nm=float(spec.u_max_nm) * max(0.0, 1.0 - magnitude))
    return tuple(specs)


def _target_wheel_index(target_id: str | None, wheel_count: int) -> int | None:
    text = str(target_id or "").lower()
    if not text or text.endswith(".all") or text.endswith("_all"):
        return None
    import re

    match = re.search(r"(?:rw|wheel|reaction_wheel)[._-]?(\d+)$", text)
    if match:
        index = int(match.group(1))
        return index if 0 <= index < wheel_count else None
    if text.endswith(".primary"):
        return 0 if wheel_count else None
    return 0 if wheel_count else None


def _rw_config_entries(rw_target) -> list[object]:
    effector = getattr(rw_target, "effector", rw_target)
    data = getattr(effector, "ReactionWheelData", None)
    if data is None:
        return []
    try:
        return [data[idx] for idx in range(len(data))]
    except Exception:
        return []


def apply_runtime_reaction_wheel_fault(
    rw_target,
    spec: FaultSpec,
    *,
    set_attr=None,
) -> tuple[dict[str, object], ...]:
    # Legacy v0.5.3.5 documents may still route wheel-speed saturation
    # through the historical fault API.  Delegate it to the operational
    # constraint implementation while keeping it out of fault classification.
    if _fault_key(spec) in {"rw_speed_saturation", "reaction_wheel_speed_limit"}:
        from .constraints import apply_runtime_reaction_wheel_constraint
        return apply_runtime_reaction_wheel_constraint(rw_target, spec, set_attr=set_attr)
    """Mutate Basilisk ``RWConfigPayload`` fields for a runtime RW fault.

    ``set_attr`` is an optional callback ``(obj, attr, value)`` used by the
    whole-spacecraft scheduler to capture originals for transient recovery.
    The component owns the fault-to-parameter mapping; higher layers only
    locate the RW target and delegate here.
    """

    entries = _rw_config_entries(rw_target)
    if not entries:
        return ()
    target_index = _target_wheel_index(getattr(spec, "target_id", None), len(entries))
    indices = range(len(entries)) if target_index is None else (target_index,)
    key = _fault_key(spec)
    magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 1.0))))
    setter = set_attr or (lambda obj, attr, value: setattr(obj, attr, value))
    mutations: list[dict[str, object]] = []

    def mutate(obj, attr: str, value: float, index: int) -> None:
        if not hasattr(obj, attr):
            return
        before = getattr(obj, attr)
        setter(obj, attr, value)
        mutations.append({"wheel_index": index, "attr": attr, "before": before, "after": value})

    for idx in indices:
        cfg = entries[idx]
        if key in {RWFaultType.BearingSeizure.value, RWFaultType.Jamming.value}:
            coulomb = max(float(getattr(cfg, "fCoulomb", 0.0)), 0.02 * max(0.1, magnitude))
            mutate(cfg, "fCoulomb", coulomb, idx)
            mutate(cfg, "fStatic", max(float(getattr(cfg, "fStatic", 0.0)), 1.5 * coulomb), idx)
            mutate(cfg, "betaStatic", float(getattr(cfg, "betaStatic", -1.0)) if float(getattr(cfg, "betaStatic", -1.0)) > 0.0 else 10.0, idx)
            mutate(cfg, "cViscous", max(float(getattr(cfg, "cViscous", 0.0)), 1.0e-5 * max(0.1, magnitude)), idx)
            current_u_max = float(getattr(cfg, "u_max", 0.0))
            target_u_max = 0.0 if key == RWFaultType.Jamming.value else current_u_max * max(0.0, 1.0 - 0.75 * magnitude)
            mutate(cfg, "u_max", target_u_max, idx)
        elif key == RWFaultType.MotorFailure.value:
            mutate(cfg, "u_max", float(getattr(cfg, "u_max", 0.0)) * max(0.0, 1.0 - magnitude), idx)
    return tuple(mutations)

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

def apply_reaction_wheel_faults(rw_config, fault_specs: list[FaultSpec]) -> object:
    # Compatibility only: historical saturation entries are normalized as
    # operational constraints and never counted as faults.
    from .constraints import apply_reaction_wheel_constraints
    constraint_specs = [spec for spec in fault_specs if _fault_value(spec) in {"rw_speed_saturation", "reaction_wheel_speed_limit"}]
    fault_specs = [spec for spec in fault_specs if _fault_value(spec) not in {"rw_speed_saturation", "reaction_wheel_speed_limit"}]
    cfg = apply_reaction_wheel_constraints(rw_config, constraint_specs)
    for spec in fault_specs:
        name = _fault_name(spec)
        value = _fault_value(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"Jamming", "MotorFailure", "BearingSeizure"} or value in {RWFaultType.Jamming.value, RWFaultType.MotorFailure.value, RWFaultType.BearingSeizure.value}:
            if hasattr(cfg, "max_speed_rad_s"):
                updates["max_speed_rad_s"] = _zero_like(cfg.max_speed_rad_s)
            if hasattr(cfg, "max_motor_torque_nm"):
                updates["max_motor_torque_nm"] = _zero_like(cfg.max_motor_torque_nm)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

