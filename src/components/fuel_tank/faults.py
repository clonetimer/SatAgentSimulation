from __future__ import annotations
from ..fault_spec import FaultSpec

# Local component fault declarations; component-local module is the canonical source.
"""
燃料箱故障类型模块

定义燃料箱部件可能发生的故障类型。
"""

from enum import Enum


class FuelTankFaultType(Enum):
    """
    燃料箱故障类型枚举

    定义燃料箱部件可能发生的各种故障类型。

    枚举值:
        RapidLeak: 快速泄漏
            - 燃料箱发生快速泄漏
            - magnitude 表示泄漏速率倍数（1.0 为正常消耗速率）

        PressureLoss: 压力损失
            - 燃料箱压力异常下降
            - magnitude 表示压力损失百分比（0-1）

        Overpressure: 过压
            - 燃料箱压力过高
            - magnitude 表示超过正常压力的倍数

        ValveStuck: 阀门卡死
            - 燃料箱阀门无法正常开关
            - magnitude 表示卡死程度（0-1），1.0为完全卡死

    示例:
        # 燃料箱快速泄漏
        fault = FaultSpec(
            fault_type=FuelTankFaultType.RapidLeak,
            onset_time_s=2000.0,
            duration_s=-1.0,
            magnitude=5.0,  # 5倍正常泄漏速率
            target_id="fuel_tank_1"
        )
    """
    RapidLeak = "rapid_leak"
    PressureLoss = "pressure_loss"
    Overpressure = "overpressure"
    ValveStuck = "valve_stuck"

# L2-light local time-window fault mechanism declarations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from ..fault_base import ParameterEffect, TimeWindowFault


class FuelTankL2FaultType(str, Enum):
    LEAK = 'leak'
    OUTLET_BLOCKAGE = 'outlet_blockage'
    PRESSURE_SENSOR_FAULT = 'pressure_sensor_fault'


def _effects_for(kind: FuelTankL2FaultType) -> tuple[ParameterEffect, ...]:
    effects_by_type = {
        FuelTankL2FaultType.LEAK: (
            ParameterEffect('leak_rate_kg_s', 'add', 0.0002, 'tank leak introduces parasitic mass loss and pressure decay'),
            ParameterEffect('tank_pressure_pa', 'multiply', 0.85, 'tank leak introduces parasitic mass loss and pressure decay'),
        ),
        FuelTankL2FaultType.OUTLET_BLOCKAGE: (
            ParameterEffect('outlet_flow_factor', 'multiply', 0.35, 'outlet blockage restricts feed flow'),
        ),
        FuelTankL2FaultType.PRESSURE_SENSOR_FAULT: (
            ParameterEffect('tank_pressure_pa', 'multiply', 0.75, 'pressure readout bias corrupts feed estimates'),
        ),
    }
    return effects_by_type[kind]


@dataclass
class FuelTankFault(TimeWindowFault):
    """Base L2-light fault for fuel_tank."""
    fault_type_enum: FuelTankL2FaultType = FuelTankL2FaultType.LEAK
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
    FuelTankL2FaultType.LEAK: 'tank leak introduces parasitic mass loss and pressure decay',
    FuelTankL2FaultType.OUTLET_BLOCKAGE: 'outlet blockage restricts feed flow',
    FuelTankL2FaultType.PRESSURE_SENSOR_FAULT: 'pressure readout bias corrupts feed estimates',
}


@dataclass
class FuelTankLeakFault(FuelTankFault):
    """tank leak introduces parasitic mass loss and pressure decay."""
    fault_type_enum: FuelTankL2FaultType = FuelTankL2FaultType.LEAK
    start_s: float = 120.0
    end_s: float = 300.0


@dataclass
class FuelTankOutletBlockageFault(FuelTankFault):
    """outlet blockage restricts feed flow."""
    fault_type_enum: FuelTankL2FaultType = FuelTankL2FaultType.OUTLET_BLOCKAGE
    start_s: float = 180.0
    end_s: float = 360.0


@dataclass
class FuelTankPressureSensorFaultFault(FuelTankFault):
    """pressure readout bias corrupts feed estimates."""
    fault_type_enum: FuelTankL2FaultType = FuelTankL2FaultType.PRESSURE_SENSOR_FAULT
    start_s: float = 240.0
    end_s: float = 420.0


def default_faults() -> list[FuelTankFault]:
    """Return representative L2-light fault events for sparse runner checks."""
    return [
        FuelTankLeakFault(),
        FuelTankOutletBlockageFault(),
    ]

__all__ = [
    'FuelTankFaultType', 'FuelTankFault', "default_faults",
    'FuelTankLeakFault',
    'FuelTankOutletBlockageFault',
    'FuelTankPressureSensorFaultFault',
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

def apply_fuel_tank_faults(ft_config, fault_specs: list[FaultSpec]) -> object:
    cfg = ft_config
    for spec in fault_specs:
        name = _fault_name(spec)
        mag = min(1.0, _mag(spec))
        updates = {}
        if name in {"RapidLeak", "PressureLoss", "ValveStuck"} and hasattr(cfg, "initial_mass_kg"):
            updates["initial_mass_kg"] = _loss_scalar(cfg.initial_mass_kg, mag)
        if name in {"PressureLoss", "Overpressure"} and hasattr(cfg, "capacity_kg"):
            updates["capacity_kg"] = _loss_scalar(cfg.capacity_kg, mag * 0.2)
        cfg = _safe_replace_fault(cfg, **updates)
    return cfg

# --- Basilisk runtime fault application (component-owned) ---
def apply_runtime_fuel_tank_fault(
    fuel_tank: object,
    spec: FaultSpec,
    *,
    set_attr,
    record_mutation=None,
    register_post_restore=None,
) -> bool:
    """Apply fuel-tank runtime faults through native Basilisk hooks when available.

    Leakage is represented by the native ``FuelTank.setFuelLeakRate``/
    ``fuelLeakRateInMsg`` path.  Pressure and outlet-flow quantities remain
    project proxy fields and are only changed when a non-native tank object
    explicitly exposes them.
    """

    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    if fault_value in {"rapid_leak", "fuel_leak", "leak"} and hasattr(fuel_tank, "setFuelLeakRate"):
        getter = getattr(fuel_tank, "getFuelLeakRate", None)
        try:
            before = float(getter()) if callable(getter) else float(getattr(fuel_tank, "fuelLeakRate", 0.0))
        except Exception:
            before = 0.0
        # For runtime FaultSpec, treat magnitude as kg/s when it is a small
        # engineering value.  Larger severity-style magnitudes use a bounded
        # representative native leak rate.
        after = magnitude if 0.0 < magnitude < 1.0 else max(before, 1.0e-4 * max(1.0, magnitude))
        fuel_tank.setFuelLeakRate(float(after))
        if record_mutation is not None:
            record_mutation(fuel_tank, "setFuelLeakRate", before, after)
        if register_post_restore is not None:
            register_post_restore(lambda tank=fuel_tank, value=before: tank.setFuelLeakRate(float(value)))
        return True
    if fault_value == "pressure_loss" and hasattr(fuel_tank, "pressure"):
        set_attr(fuel_tank, "pressure", float(getattr(fuel_tank, "pressure")) * max(0.0, 1.0 - min(1.0, magnitude)))
        return True
    if fault_value == "overpressure" and hasattr(fuel_tank, "pressure"):
        set_attr(fuel_tank, "pressure", float(getattr(fuel_tank, "pressure")) * (1.0 + magnitude))
        return True
    if fault_value in {"valve_stuck", "outlet_blockage"} and hasattr(fuel_tank, "valveOpen"):
        set_attr(fuel_tank, "valveOpen", not bool(magnitude))
        return True
    return False

