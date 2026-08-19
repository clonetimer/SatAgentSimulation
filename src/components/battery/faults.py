from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
电池故障类型模块

定义电池部件可能发生的故障类型。
"""

from enum import Enum


class BatteryFaultType(Enum):
    """
    电池故障类型枚举
    
    定义电池部件可能发生的各种故障类型。
    
    枚举值:
        SuddenCapacityLoss: 突发容量损失
            - 电池容量突然下降
            - magnitude 表示损失的比例（0-1）
            
        OpenCircuit: 开路故障
            - 电池内部连接断开
            - magnitude 表示故障严重程度（0-1）
            
        ThermalRunaway: 热失控
            - 电池温度失控上升
            - magnitude 表示热失控速率倍数（1.0 为正常速率）
    
    示例:
        # 电池容量突降30%
        fault = FaultSpec(
            fault_type=BatteryFaultType.SuddenCapacityLoss,
            onset_time_s=1000.0,
            duration_s=-1.0,
            magnitude=0.3,
            target_id="battery_1"
        )
    """
    SuddenCapacityLoss = "sudden_capacity_loss"  # 突发容量损失
    OpenCircuit = "open_circuit"  # 开路故障
    ThermalRunaway = "thermal_runaway"  # 热失控

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class BatteryL2FaultType(str, Enum):
    OPEN_CIRCUIT = 'open_circuit'
    INTERNAL_SHORT = 'internal_short'
    OVER_TEMPERATURE_LIMIT = 'over_temperature_limit'


def _effects_for(kind: BatteryL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        BatteryL2FaultType.OPEN_CIRCUIT: (
            ParameterEffect('available_current_factor', 'override', 0.0, 'open circuit interrupts discharge path'),
        ),
        BatteryL2FaultType.INTERNAL_SHORT: (
            ParameterEffect('internal_resistance_ohm', 'multiply', 0.35, 'internal short adds parasitic current and heat'),
            ParameterEffect('heat_generation_w', 'add', 25.0, 'internal short adds parasitic current and heat'),
            ParameterEffect('available_current_factor', 'multiply', 0.65, 'internal short adds parasitic current and heat'),
        ),
        BatteryL2FaultType.OVER_TEMPERATURE_LIMIT: (
            ParameterEffect('available_current_factor', 'multiply', 0.45, 'thermal protection derates available current'),
            ParameterEffect('heat_generation_w', 'add', 8.0, 'thermal protection derates available current'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class BatteryFault(TimeWindowFault):
    """Base L2-light fault for battery."""
    fault_type_enum: BatteryL2FaultType = BatteryL2FaultType.OPEN_CIRCUIT
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
    BatteryL2FaultType.OPEN_CIRCUIT: 'open circuit interrupts discharge path',
    BatteryL2FaultType.INTERNAL_SHORT: 'internal short adds parasitic current and heat',
    BatteryL2FaultType.OVER_TEMPERATURE_LIMIT: 'thermal protection derates available current',
}


@dataclass
class BatteryOpenCircuitFault(BatteryFault):
    """open circuit interrupts discharge path."""
    fault_type_enum: BatteryL2FaultType = BatteryL2FaultType.OPEN_CIRCUIT
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class BatteryInternalShortFault(BatteryFault):
    """internal short adds parasitic current and heat."""
    fault_type_enum: BatteryL2FaultType = BatteryL2FaultType.INTERNAL_SHORT
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class BatteryOverTemperatureLimitFault(BatteryFault):
    """thermal protection derates available current."""
    fault_type_enum: BatteryL2FaultType = BatteryL2FaultType.OVER_TEMPERATURE_LIMIT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[BatteryFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        BatteryOpenCircuitFault(),
        BatteryInternalShortFault(),
    ]

__all__ = [
    'BatteryFaultType', 'BatteryFault', "default_faults",
    'BatteryOpenCircuitFault',
    'BatteryInternalShortFault',
    'BatteryOverTemperatureLimitFault',
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

def apply_battery_faults(battery_config, fault_specs: list[FaultSpec]) -> object:
    cfg = battery_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"SuddenCapacityLoss", "CapacityFade"} and hasattr(cfg, "capacity_wh"):
            updates["capacity_wh"] = _loss_scalar(cfg.capacity_wh, mag)
        if name in {"OpenCircuit", "InternalShort"}:
            if hasattr(cfg, "charge_efficiency"):
                updates["charge_efficiency"] = 0.0 if name == "OpenCircuit" else _loss_scalar(cfg.charge_efficiency, mag * 0.5)
            if hasattr(cfg, "discharge_efficiency"):
                updates["discharge_efficiency"] = 0.0 if name == "OpenCircuit" else _loss_scalar(cfg.discharge_efficiency, mag * 0.5)
        if name == "ThermalRunaway":
            if hasattr(cfg, "max_temp_c"):
                updates["max_temp_c"] = 100.0
            if hasattr(cfg, "optimal_temp_c"):
                updates["optimal_temp_c"] = 60.0
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_battery_fault(battery: object, spec: FaultSpec, *, set_attr) -> bool:
    """Apply currently supported battery runtime hooks.

    Capacity mutation remains a transitional direct hook.  The native
    ``batteryFaultInMsg`` route is the target of EPS-NATIVE-POWER-1.
    """

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    if fault_value == "sudden_capacity_loss" and hasattr(battery, "storageCapacity"):
        set_attr(battery, "storageCapacity", float(getattr(battery, "storageCapacity")) * max(0.0, 1.0 - magnitude))
        return True
    if fault_value == "open_circuit" and hasattr(battery, "storageCapacity"):
        set_attr(battery, "storageCapacity", max(1.0, float(getattr(battery, "storageCapacity")) * 0.01))
        return True
    if fault_value == "thermal_runaway" and hasattr(battery, "tempC"):
        set_attr(battery, "tempC", float(getattr(battery, "tempC", 25.0)) + magnitude)
        return True
    return False

