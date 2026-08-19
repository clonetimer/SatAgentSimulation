"""Schema definitions for the fuel tank component."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuelTankConfig:
    capacity_kg: float = 3.0
    initial_mass_kg: float = 2.0
    full_pressure_pa: float = 2.5e6
    dry_pressure_pa: float = 1.5e5
    tank_model: str = "constant_volume"
    radius_tank_m: float = 0.1
    rho_fuel_kg_m3: float = 1000.0
    length_tank_m: float = 0.3
    radius_inner_m: float = 0.02
    r_tb_b_m: tuple = (0.0, 0.0, 0.0)
    dcm_tb: tuple = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    leak_rate_kg_s: float = 0.0


@dataclass(frozen=True)
class FuelTankState:
    propellant_mass_kg: float
    pressure_pa: float


@dataclass(frozen=True)
class FuelTankProfileResult:
    time_s: tuple
    propellant_mass_kg: tuple
    pressure_pa: tuple
