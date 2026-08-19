"""Propulsion subsystem schemas.

The v3.2 propulsion subsystem is a pure-Python subsystem-level composition of
thruster and fuel-tank component models.  It intentionally does not import
Basilisk.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from components.thruster import ThrusterCommandConfig, ThrusterPhysicalConfig, ThrusterPulseResult
from components.fuel_tank import FuelTankConfig, FuelTankState


@dataclass(frozen=True)
class PropulsionConfig:
    """Propulsion subsystem configuration.

    Attributes:
        thruster_command: On-time command shaping configuration.
        thruster_physical: Physical thrust/Isp/geometry configuration.
        fuel_tank: Fuel tank configuration.
        min_soc_for_burn: Below this battery SOC, burns are inhibited.
        require_eps_permission: If true, ``PropulsionStepInput.eps_allows_burn``
            must be true for a burn to occur.
    """

    thruster_command: ThrusterCommandConfig = field(default_factory=ThrusterCommandConfig)
    thruster_physical: ThrusterPhysicalConfig = field(default_factory=ThrusterPhysicalConfig)
    fuel_tank: FuelTankConfig = field(default_factory=FuelTankConfig)
    min_soc_for_burn: float = 0.3
    require_eps_permission: bool = True


@dataclass(frozen=True)
class PropulsionState:
    """Propulsion dynamic state."""

    fuel_tank: FuelTankState
    time_s: float = 0.0


@dataclass(frozen=True)
class PropulsionStepInput:
    """One propulsion propagation input sample."""

    dt_s: float
    burn_requested: bool = False
    battery_soc: float = 1.0
    eps_allows_burn: bool = True
    mode: str = "nominal"


@dataclass(frozen=True)
class PropulsionStepResult:
    """One propulsion step output sample."""

    time_s: float
    mode: str
    burn_requested: bool
    burn_allowed: bool
    inhibition_reason: str | None
    on_time_s: tuple[float, ...]
    impulse_ns: tuple[float, ...]
    total_impulse_ns: float
    total_force_impulse_b_ns: tuple[float, float, float]
    total_torque_impulse_b_nms: tuple[float, float, float]
    propellant_used_kg: float
    propellant_remaining_kg: float
    tank_pressure_pa: float
    battery_soc: float
    eps_allows_burn: bool


@dataclass(frozen=True)
class PropulsionProfileResult:
    """Multi-step propulsion history."""

    time_s: tuple[float, ...]
    burn_allowed: tuple[bool, ...]
    inhibition_reason: tuple[str | None, ...]
    total_impulse_ns: tuple[float, ...]
    cumulative_impulse_ns: tuple[float, ...]
    propellant_used_kg: tuple[float, ...]
    propellant_remaining_kg: tuple[float, ...]
    tank_pressure_pa: tuple[float, ...]



@dataclass(frozen=True)
class PropulsionBasiliskConfig:
    """Basilisk-backed propulsion run configuration.

    The dataclass is schema-only and intentionally does not import Basilisk.
    """

    duration_s: float = 2.0
    step_s: float = 0.1
    spacecraft_mass_kg: float = 10.0
    initial_propellant_kg: float | None = None
    tank_capacity_kg: float | None = None
    on_time_s: tuple[float, ...] | None = None
    burn_start_s: float = 0.0


@dataclass(frozen=True)
class PropulsionBasiliskTraceRow:
    time_s: float
    position_x_m: float
    velocity_x_m_s: float
    fuel_mass_kg: float
    fuel_mass_dot_kg_s: float
    thrust_force_n: float
    thrust_force_b_x_n: float
    thrust_factor: float


@dataclass(frozen=True)
class PropulsionBasiliskSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    messages: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    initial_fuel_mass_kg: float
    final_fuel_mass_kg: float
    propellant_used_kg: float
    final_velocity_x_m_s: float
    status: str


# Backward-compatible names for callers that previously imported from the
# removed legacy Basilisk compatibility module.
PropulsionNativeConfig = PropulsionBasiliskConfig
PropulsionNativeTraceRow = PropulsionBasiliskTraceRow
PropulsionNativeSummary = PropulsionBasiliskSummary


__all__ = [
    "PropulsionConfig",
    "PropulsionState",
    "PropulsionStepInput",
    "PropulsionStepResult",
    "PropulsionProfileResult",

    "PropulsionBasiliskConfig",
    "PropulsionBasiliskTraceRow",
    "PropulsionBasiliskSummary",
    "PropulsionNativeConfig",
    "PropulsionNativeTraceRow",
    "PropulsionNativeSummary",
]
