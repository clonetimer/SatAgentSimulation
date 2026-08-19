"""Thermal subsystem schemas.

The thermal subsystem composes thermal-node component models into a multi-node
spacecraft thermal management layer.  It is pure Python and intentionally does
not import Basilisk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from components.thermal_node.builder import ThermalNodeConfig, ThermalNodeState


@dataclass(frozen=True)
class HeaterConfig:
    """Simple thermostatic heater configuration for one thermal node.

    Attributes:
        power_w: Heater power applied to the node when enabled.
        on_below_k: Heater requests on when node temperature is below this
            threshold.
        off_above_k: Heater turns off when node temperature exceeds this
            threshold.  Values <= ``on_below_k`` disable hysteresis and use the
            on threshold as a simple switch.
    """

    power_w: float = 0.0
    on_below_k: float = 0.0
    off_above_k: float = 0.0


@dataclass(frozen=True)
class ThermalConfig:
    """Thermal subsystem configuration.

    ``component_node_map`` maps power-producing components/subsystems to the
    thermal node that receives their heat.  If a component is not listed, the
    component name itself is treated as the node name when such node exists.
    """

    nodes: Mapping[str, ThermalNodeConfig] = field(default_factory=dict)
    initial_temp_k_by_node: Mapping[str, float] = field(default_factory=dict)
    component_node_map: Mapping[str, str] = field(default_factory=dict)
    heat_efficiency_by_component: Mapping[str, float] = field(default_factory=dict)
    mode_power_w_by_node: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    sunlight_heat_w_by_node: Mapping[str, float] = field(default_factory=dict)
    heaters: Mapping[str, HeaterConfig] = field(default_factory=dict)
    under_temp_limit_k_by_node: Mapping[str, float] = field(default_factory=dict)
    over_temp_limit_k_by_node: Mapping[str, float] = field(default_factory=dict)
    safe_request_over_temp_nodes: tuple[str, ...] = ()
    safe_request_under_temp_nodes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ThermalBasiliskConfig:
    """Basilisk-scheduled builder configuration for the thermal subsystem."""

    duration_s: float = 300.0
    step_s: float = 10.0
    node_name: str = "thermal_subsystem_node"
    initial_temp_c: float = 22.0
    ambient_temp_c: float = 18.0
    min_safe_temp_c: float = -5.0
    max_safe_temp_c: float = 45.0
    thermal_capacity_j_per_c: float = 900.0
    conductance_w_per_c: float = 0.35
    heat_power_w: float = 25.0
    heater_power_w: float = 18.0
    cooling_power_w: float = 12.0
    heater_on_below_c: float = 2.0
    heater_off_above_c: float = 6.0
    cooling_on_above_c: float = 40.0
    cooling_off_below_c: float = 36.0


@dataclass(frozen=True)
class ThermalState:
    """Thermal subsystem dynamic state."""

    nodes: Mapping[str, ThermalNodeState]
    heater_on_by_node: Mapping[str, bool] = field(default_factory=dict)
    time_s: float = 0.0


@dataclass(frozen=True)
class ThermalStepInput:
    """One thermal propagation input sample."""

    dt_s: float
    mode: str = "nominal"
    shadow_factor: float = 1.0
    component_power_w: Mapping[str, float] = field(default_factory=dict)
    heater_enabled: bool = True
    ambient_k_by_node: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ThermalStepResult:
    """One thermal propagation output sample."""

    time_s: float
    mode: str
    node_temperature_k: dict[str, float]
    node_heat_power_w: dict[str, float]
    component_heat_power_w: dict[str, float]
    heater_power_w: dict[str, float]
    heater_on_by_node: dict[str, bool]
    under_temp_flags: tuple[str, ...]
    over_temp_flags: tuple[str, ...]
    thermal_safe_request: bool


@dataclass(frozen=True)
class ThermalProfileResult:
    """Multi-step thermal history."""

    time_s: tuple[float, ...]
    node_temperature_k: tuple[dict[str, float], ...]
    node_heat_power_w: tuple[dict[str, float], ...]
    heater_power_w: tuple[dict[str, float], ...]
    under_temp_flags: tuple[tuple[str, ...], ...]
    over_temp_flags: tuple[tuple[str, ...], ...]
    thermal_safe_request: tuple[bool, ...]


__all__ = [
    "HeaterConfig",
    "ThermalConfig",
    "ThermalBasiliskConfig",
    "ThermalState",
    "ThermalStepInput",
    "ThermalStepResult",
    "ThermalProfileResult",
]
