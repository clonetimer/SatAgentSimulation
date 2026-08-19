"""Schema definitions for the battery component."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BatteryConfig:
    capacity_wh: float = 100.0
    initial_soc: float = 0.5
    charge_efficiency: float = 1.0
    discharge_efficiency: float = 1.0
    min_soc: float = 0.0
    max_soc: float = 1.0
    cv_soc_threshold: float = 0.7
    cv_voltage_factor: float = 0.95
    max_charge_current_a: float = 10.0
    nominal_voltage_v: float = 28.0
    min_temp_c: float = 0.0
    max_temp_c: float = 45.0
    optimal_temp_c: float = 25.0


@dataclass(frozen=True)
class BatteryState:
    storage_wh: float
    capacity_wh: float
    soc: float
    shunt_dissipated_wh: float = 0.0


@dataclass(frozen=True)
class BatteryProfileResult:
    time_s: tuple
    soc: tuple
    storage_wh: tuple
    shunt_dissipated_wh: tuple

@dataclass(frozen=True)
class BatteryNativeConfig:
    duration_s: float = 120.0
    step_s: float = 10.0
    capacity_wh: float = 160.0
    initial_soc: float = 0.62
    power_nodes_w: tuple[float, ...] = (95.0, -38.0, -18.0)
    discharge_efficiency: float = 1.0
    charge_efficiency: float = 1.0
    fault_capacity_ratio: float | None = None
