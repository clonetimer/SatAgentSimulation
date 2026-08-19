"""Thermal network configuration for multi-node spacecraft thermal modeling.

This module defines the configuration classes for a comprehensive thermal network
that supports:
- Multiple thermal nodes (battery, electronics, payload, ADCS, structure, solar_panel)
- Thermal conduction paths between nodes
- Solar heating with eclipse modulation
- Active heaters and radiators with control logic
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sat_sim.exceptions import (
    ConfigRangeError, ConfigValueError, ConfigMissingError,
    validate_positive, validate_non_negative, validate_between_zero_one,
    validate_not_none, validate_range
)


@dataclass(frozen=True)
class ThermalNodeParams:
    """Thermal node physical parameters.
    
    Attributes:
        mass_kg: Node mass in kilograms
        specific_heat_j_per_kg_k: Specific heat capacity (J/kg/K)
        surface_area_m2: Surface area for radiation (m²)
        emissivity: Surface emissivity (0-1)
        absorptivity: Solar absorptivity (0-1)
        initial_temp_k: Initial temperature (K)
        min_temp_k: Minimum safe temperature (K)
        max_temp_k: Maximum safe temperature (K)
        internal_heat_w: Internal heat generation (W) from component power dissipation
    """
    mass_kg: float = 1.0
    specific_heat_j_per_kg_k: float = 900.0
    surface_area_m2: float = 0.1
    emissivity: float = 0.8
    absorptivity: float = 0.3
    initial_temp_k: float = 290.0
    min_temp_k: float = 220.0
    max_temp_k: float = 360.0
    internal_heat_w: float = 0.0

    def __post_init__(self) -> None:
        validate_positive(self.mass_kg, "mass_kg")
        validate_positive(self.specific_heat_j_per_kg_k, "specific_heat_j_per_kg_k")
        validate_non_negative(self.surface_area_m2, "surface_area_m2")
        validate_between_zero_one(self.emissivity, "emissivity")
        validate_between_zero_one(self.absorptivity, "absorptivity")
        validate_positive(self.initial_temp_k, "initial_temp_k")
        validate_positive(self.min_temp_k, "min_temp_k")
        validate_positive(self.max_temp_k, "max_temp_k")
        validate_non_negative(self.internal_heat_w, "internal_heat_w")
        if self.min_temp_k > self.max_temp_k:
            raise ConfigRangeError("min_temp_k", self.min_temp_k, 0.0, self.max_temp_k)
        if self.initial_temp_k < self.min_temp_k or self.initial_temp_k > self.max_temp_k:
            raise ConfigRangeError("initial_temp_k", self.initial_temp_k, self.min_temp_k, self.max_temp_k)

    @property
    def thermal_capacity_j_per_k(self) -> float:
        return self.mass_kg * self.specific_heat_j_per_kg_k


@dataclass(frozen=True)
class ThermalConductionPath:
    """Thermal conduction path between two nodes.
    
    Attributes:
        from_node: Source node name
        to_node: Target node name
        conductance_w_per_k: Thermal conductance (W/K)
    """
    from_node: str
    to_node: str
    conductance_w_per_k: float

    def __post_init__(self) -> None:
        validate_not_none(self.from_node, "from_node")
        validate_not_none(self.to_node, "to_node")
        validate_non_negative(self.conductance_w_per_k, "conductance_w_per_k")


@dataclass(frozen=True)
class HeaterParams:
    """Heater parameters for active thermal control.
    
    Attributes:
        power_w: Heater power when active (W)
        setpoint_k: Target temperature setpoint (K)
        hysteresis_k: Hysteresis band (K)
        efficiency: Heater efficiency (0-1)
    """
    power_w: float = 12.0
    setpoint_k: float = 286.0
    hysteresis_k: float = 5.0
    efficiency: float = 0.95

    def __post_init__(self) -> None:
        validate_non_negative(self.power_w, "power_w")
        validate_positive(self.setpoint_k, "setpoint_k")
        validate_non_negative(self.hysteresis_k, "hysteresis_k")
        validate_between_zero_one(self.efficiency, "efficiency")

    @property
    def on_below_k(self) -> float:
        return self.setpoint_k - self.hysteresis_k / 2.0

    @property
    def off_above_k(self) -> float:
        return self.setpoint_k + self.hysteresis_k / 2.0


@dataclass(frozen=True)
class RadiatorParams:
    """Radiator parameters for active thermal control.
    
    Attributes:
        area_m2: Radiator surface area (m²)
        emissivity: Radiator emissivity (0-1)
        view_factor: View factor to space
        min_temp_k: Minimum temperature for radiator activation (K)
        max_temp_k: Maximum temperature for radiator activation (K)
        deployable: Whether radiator can be deployed/retracted
    """
    area_m2: float = 0.35
    emissivity: float = 0.82
    view_factor: float = 0.9
    min_temp_k: float = 290.0
    max_temp_k: float = 360.0
    deployable: bool = True

    def __post_init__(self) -> None:
        validate_non_negative(self.area_m2, "area_m2")
        validate_between_zero_one(self.emissivity, "emissivity")
        validate_between_zero_one(self.view_factor, "view_factor")
        validate_positive(self.min_temp_k, "min_temp_k")
        validate_positive(self.max_temp_k, "max_temp_k")
        if self.min_temp_k > self.max_temp_k:
            raise ConfigRangeError("min_temp_k", self.min_temp_k, 0.0, self.max_temp_k)


@dataclass(frozen=True)
class SolarPanelParams:
    """Solar panel thermal parameters.
    
    Attributes:
        area_m2: Panel area (m²)
        absorptivity: Solar absorptivity
        emissivity: Thermal emissivity
        efficiency: Electrical efficiency (affects heat generation)
    """
    area_m2: float = 1.0
    absorptivity: float = 0.7
    emissivity: float = 0.25
    electrical_efficiency: float = 0.28

    def __post_init__(self) -> None:
        validate_non_negative(self.area_m2, "area_m2")
        validate_between_zero_one(self.absorptivity, "absorptivity")
        validate_between_zero_one(self.emissivity, "emissivity")
        validate_between_zero_one(self.electrical_efficiency, "electrical_efficiency")


@dataclass(frozen=True)
class ThermalNetworkConfig:
    """Comprehensive thermal network configuration.
    
    Attributes:
        nodes: Thermal nodes with their parameters
        conduction_paths: Thermal conduction paths between nodes
        heaters: Heaters for each node
        radiators: Radiators for each node
        solar_panels: Solar panel parameters
        sun_power_w_per_m2: Solar constant at spacecraft location (W/m²)
        sink_temp_k: Effective sink temperature (space) (K)
        duration_s: Simulation duration (s)
        step_s: Simulation time step (s)
    """
    nodes: dict[str, ThermalNodeParams] = field(default_factory=dict)
    conduction_paths: list[ThermalConductionPath] = field(default_factory=list)
    heaters: dict[str, HeaterParams] = field(default_factory=dict)
    radiators: dict[str, RadiatorParams] = field(default_factory=dict)
    solar_panels: Optional[SolarPanelParams] = None
    sun_power_w_per_m2: float = 1367.0
    sink_temp_k: float = 2.7
    duration_s: float = 3600.0
    step_s: float = 10.0

    def __post_init__(self) -> None:
        validate_non_negative(self.sun_power_w_per_m2, "sun_power_w_per_m2")
        validate_non_negative(self.sink_temp_k, "sink_temp_k")
        validate_positive(self.duration_s, "duration_s")
        validate_positive(self.step_s, "step_s")
        if self.step_s > self.duration_s:
            raise ConfigRangeError("step_s", self.step_s, 0.0, self.duration_s)
        for heater_node in self.heaters:
            if heater_node not in self.nodes:
                raise ConfigMissingError(heater_node, "heaters (node not found in nodes)")
        for radiator_node in self.radiators:
            if radiator_node not in self.nodes:
                raise ConfigMissingError(radiator_node, "radiators (node not found in nodes)")
        for path in self.conduction_paths:
            if path.from_node not in self.nodes:
                raise ConfigMissingError(path.from_node, "conduction_paths (from_node not found)")
            if path.to_node not in self.nodes:
                raise ConfigMissingError(path.to_node, "conduction_paths (to_node not found)")


def build_nominal_thermal_network_config() -> ThermalNetworkConfig:
    """Build a nominal thermal network configuration with multiple nodes.
    
    Includes:
    - Battery, electronics, payload, ADCS, structure, and solar panel nodes
    - Thermal conduction paths between adjacent nodes
    - Heaters for battery and electronics
    - Radiators for electronics and payload
    - Solar panel for dynamic solar heating
    """
    return ThermalNetworkConfig(
        nodes={
            "battery": ThermalNodeParams(
                mass_kg=10.0,
                specific_heat_j_per_kg_k=800.0,
                surface_area_m2=0.08,
                emissivity=0.85,
                absorptivity=0.25,
                initial_temp_k=290.0,
                min_temp_k=220.0,
                max_temp_k=320.0,
                internal_heat_w=2.0,
            ),
            "electronics": ThermalNodeParams(
                mass_kg=5.0,
                specific_heat_j_per_kg_k=700.0,
                surface_area_m2=0.12,
                emissivity=0.80,
                absorptivity=0.30,
                initial_temp_k=295.0,
                min_temp_k=220.0,
                max_temp_k=330.0,
                internal_heat_w=8.0,
            ),
            "payload": ThermalNodeParams(
                mass_kg=3.0,
                specific_heat_j_per_kg_k=600.0,
                surface_area_m2=0.06,
                emissivity=0.75,
                absorptivity=0.35,
                initial_temp_k=292.0,
                min_temp_k=220.0,
                max_temp_k=340.0,
                internal_heat_w=25.0,
            ),
            "adcs": ThermalNodeParams(
                mass_kg=2.0,
                specific_heat_j_per_kg_k=750.0,
                surface_area_m2=0.04,
                emissivity=0.82,
                absorptivity=0.28,
                initial_temp_k=293.0,
                min_temp_k=210.0,
                max_temp_k=350.0,
                internal_heat_w=3.0,
            ),
            "structure": ThermalNodeParams(
                mass_kg=20.0,
                specific_heat_j_per_kg_k=500.0,
                surface_area_m2=0.5,
                emissivity=0.70,
                absorptivity=0.40,
                initial_temp_k=285.0,
                min_temp_k=200.0,
                max_temp_k=370.0,
                internal_heat_w=0.0,
            ),
            "solar_panel": ThermalNodeParams(
                mass_kg=4.0,
                specific_heat_j_per_kg_k=650.0,
                surface_area_m2=2.0,
                emissivity=0.85,
                absorptivity=0.35,
                initial_temp_k=300.0,
                min_temp_k=200.0,
                max_temp_k=390.0,
                internal_heat_w=5.0,
            ),
        },
        conduction_paths=[
            ThermalConductionPath("battery", "structure", 0.5),
            ThermalConductionPath("battery", "electronics", 0.3),
            ThermalConductionPath("electronics", "structure", 0.8),
            ThermalConductionPath("electronics", "payload", 0.4),
            ThermalConductionPath("electronics", "adcs", 0.2),
            ThermalConductionPath("payload", "structure", 0.3),
            ThermalConductionPath("adcs", "structure", 0.4),
            ThermalConductionPath("solar_panel", "structure", 2.0),
        ],
        heaters={
            "battery": HeaterParams(
                power_w=12.0,
                setpoint_k=286.0,
                hysteresis_k=5.0,
                efficiency=0.95,
            ),
            "electronics": HeaterParams(
                power_w=8.0,
                setpoint_k=280.0,
                hysteresis_k=4.0,
                efficiency=0.95,
            ),
        },
        radiators={
            "electronics": RadiatorParams(
                area_m2=0.35,
                emissivity=0.82,
                view_factor=0.9,
                min_temp_k=290.0,
                max_temp_k=330.0,
                deployable=True,
            ),
            "payload": RadiatorParams(
                area_m2=0.25,
                emissivity=0.80,
                view_factor=0.85,
                min_temp_k=295.0,
                max_temp_k=340.0,
                deployable=True,
            ),
        },
        solar_panels=SolarPanelParams(
            area_m2=2.0,
            absorptivity=0.7,
            emissivity=0.25,
            electrical_efficiency=0.28,
        ),
        sun_power_w_per_m2=1367.0,
        sink_temp_k=2.7,
        duration_s=3600.0,
        step_s=10.0,
    )


def apply_degradation_to_network_config(
    config: ThermalNetworkConfig,
    heater_efficiency_loss_pct: float = 0.0,
    radiator_efficiency_loss_pct: float = 0.0,
    radiator_emissivity_loss_pct: float = 0.0,
) -> ThermalNetworkConfig:
    """Apply degradation to thermal network configuration."""
    from dataclasses import replace

    degraded_heaters = {}
    for node_name, heater in config.heaters.items():
        degraded_heaters[node_name] = replace(
            heater,
            efficiency=heater.efficiency * (1.0 - heater_efficiency_loss_pct / 100.0),
        )

    degraded_radiators = {}
    for node_name, radiator in config.radiators.items():
        degraded_radiators[node_name] = replace(
            radiator,
            emissivity=radiator.emissivity * (1.0 - radiator_emissivity_loss_pct / 100.0),
            area_m2=radiator.area_m2 * (1.0 - radiator_efficiency_loss_pct / 100.0),
        )

    return replace(
        config,
        heaters=degraded_heaters,
        radiators=degraded_radiators,
    )
