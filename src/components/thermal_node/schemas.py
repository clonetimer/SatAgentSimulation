"""Schema contracts for the thermal node component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class ThermalNodeConfig:
    ambient_k: float = 300.0
    tau_s: float = 10.0
    heat_gain_k_per_w: float = 1.0
    min_temp_k: float = 0.0
    max_temp_k: float = 1000.0

@dataclass(frozen=True)
class ThermalNodeScheduledConfig:
    node_name: str = "payload_thermal_node"
    initial_temp_c: float = 22.0
    ambient_temp_c: float = 18.0
    min_safe_temp_c: float = -5.0
    max_safe_temp_c: float = 45.0
    hysteresis_c: float = 2.0
    thermal_capacity_j_per_c: float = 900.0
    conductance_w_per_c: float = 0.35
    heater_power_w: float = 18.0
    cooling_power_w: float = 12.0
    heater_on_below_c: float = 2.0
    heater_off_above_c: float = 6.0
    cooling_on_above_c: float = 40.0
    cooling_off_below_c: float = 36.0
