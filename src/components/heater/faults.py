from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
加热器故障类型模块

定义加热器部件可能发生的故障类型。
"""

from enum import Enum


class HeaterFaultType(Enum):
    """
    加热器故障类型枚举

    定义加热器部件可能发生的各种故障类型。

    枚举值:
        Failure: 加热器失效
            - 加热器完全失效，无法加热
            - magnitude 表示失效程度（0-1），1.0为完全失效

        Overheating: 过热故障
            - 加热器过热，温度控制失效
            - magnitude 表示过热程度（温度倍数）

        Stuck: 卡滞故障
            - 加热器卡滞在某个状态，无法切换
            - magnitude 表示卡滞程度（0-1）

    示例:
        # 加热器完全失效
        fault = FaultSpec(
            fault_type=HeaterFaultType.Failure,
            onset_time_s=2000.0,
            duration_s=-1.0,
            magnitude=1.0,  # 完全失效
            target_id="heater_1"
        )
    """
    Failure = "heater_failure"  # 加热器失效
    Overheating = "heater_overheating"  # 过热故障
    Stuck = "heater_stuck"  # 卡滞故障

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class HeaterL2FaultType(str, Enum):
    STUCK_OFF = 'stuck_off'
    STUCK_ON = 'stuck_on'
    RELAY_CHATTER = 'relay_chatter'


def _effects_for(kind: HeaterL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        HeaterL2FaultType.STUCK_OFF: (
            ParameterEffect('commanded_on', 'override', 0.0, 'relay/open fault prevents heater power delivery'),
            ParameterEffect('heater_power_w', 'override', 0.0, 'relay/open fault prevents heater power delivery'),
        ),
        HeaterL2FaultType.STUCK_ON: (
            ParameterEffect('commanded_on', 'override', 1.0, 'relay welded closed forces heater on'),
            ParameterEffect('thermal_overshoot_w', 'add', 15.0, 'relay welded closed forces heater on'),
        ),
        HeaterL2FaultType.RELAY_CHATTER: (
            ParameterEffect('efficiency', 'multiply', 0.55, 'relay chatter reduces duty-cycle and adds thermal uncertainty'),
            ParameterEffect('thermal_overshoot_w', 'add', 4.0, 'relay chatter reduces duty-cycle and adds thermal uncertainty'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class HeaterFault(TimeWindowFault):
    """Base L2-light fault for heater."""
    fault_type_enum: HeaterL2FaultType = HeaterL2FaultType.STUCK_OFF
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
    HeaterL2FaultType.STUCK_OFF: 'relay/open fault prevents heater power delivery',
    HeaterL2FaultType.STUCK_ON: 'relay welded closed forces heater on',
    HeaterL2FaultType.RELAY_CHATTER: 'relay chatter reduces duty-cycle and adds thermal uncertainty',
}


@dataclass
class HeaterStuckOffFault(HeaterFault):
    """relay/open fault prevents heater power delivery."""
    fault_type_enum: HeaterL2FaultType = HeaterL2FaultType.STUCK_OFF
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class HeaterStuckOnFault(HeaterFault):
    """relay welded closed forces heater on."""
    fault_type_enum: HeaterL2FaultType = HeaterL2FaultType.STUCK_ON
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class HeaterRelayChatterFault(HeaterFault):
    """relay chatter reduces duty-cycle and adds thermal uncertainty."""
    fault_type_enum: HeaterL2FaultType = HeaterL2FaultType.RELAY_CHATTER
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[HeaterFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        HeaterStuckOffFault(),
        HeaterStuckOnFault(),
    ]

__all__ = [
    'HeaterFaultType', 'HeaterFault', "default_faults",
    'HeaterStuckOffFault',
    'HeaterStuckOnFault',
    'HeaterRelayChatterFault',
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

def apply_heater_faults(heater_config, fault_specs: list[FaultSpec]) -> object:
    cfg = heater_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"Failure", "OpenCircuit"} and hasattr(cfg, "max_power_w"):
            updates["max_power_w"] = 0.0
        if name in {"Overheating", "Stuck"}:
            if hasattr(cfg, "max_power_w"):
                updates["max_power_w"] = _increase_scalar(cfg.max_power_w, mag)
            if hasattr(cfg, "setpoint_k"):
                updates["setpoint_k"] = _increase_scalar(cfg.setpoint_k, mag * 0.02)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_heater_fault(heater: object, spec: FaultSpec, *, set_attr) -> bool:
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    if fault_value in {"thermal_heater_stuck_off", "heater_failure"}:
        changed = False
        for attr, value in (("forced_state", False), ("power_scale", 0.0), ("heater_forced_state", False), ("heater_power_scale", 0.0)):
            if hasattr(heater, attr):
                set_attr(heater, attr, value); changed = True
        return changed
    if fault_value in {"thermal_heater_stuck_on", "heater_stuck"}:
        changed = False
        for attr, value in (("forced_state", True), ("power_scale", magnitude), ("heater_forced_state", True), ("heater_power_scale", magnitude)):
            if hasattr(heater, attr):
                set_attr(heater, attr, value); changed = True
        return changed
    if fault_value in {"thermal_heater_overheat", "heater_overheating"}:
        changed = False
        factor = 1.0 + magnitude
        for attr, value in (("forced_state", True), ("power_scale", factor), ("heater_forced_state", True), ("heater_power_scale", factor)):
            if hasattr(heater, attr):
                set_attr(heater, attr, value); changed = True
        return changed
    return False

