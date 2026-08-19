"""Unified configuration module for satellite simulation.

This module provides a centralized entry point for all configuration classes
used in the satellite simulation library. It re-exports configuration classes
from their original locations for easier access.

Configuration categories:
    - Component configurations (battery, thermal node, solar panel, etc.)
    - Subsystem configurations (thermal network, EPS, ADCS, etc.)
    - Whole spacecraft configurations
    - Simulation run configurations
"""
from __future__ import annotations

from typing import Optional, Dict, List, Tuple

from components.dynamic_models import (
    ThermalNodeConfig,
    DataQueueConfig,
    SolarPanelConfig,
    ThrusterCommandConfig,
    MtbConfig,
    SingleGimbalCmgConfig,
    CmgConfig,
    ImuConfig,
    MagnetometerConfig,
    StarTrackerConfig,
    SunSensorConfig,
    PowerSinkConfig,
    PduConfig,
    LinkBudgetConfig,
    GroundStationConfig,
)
from components.battery import BatteryConfig
from components.fuel_tank import FuelTankConfig
from components.reaction_wheel import ReactionWheelCommandConfig, ReactionWheelDynamicsConfig
from components.thruster import ThrusterPhysicalConfig

from subsystems.thermal.network_config import (
    ThermalNodeParams,
    ThermalConductionPath,
    HeaterParams,
    RadiatorParams,
    SolarPanelParams,
    ThermalNetworkConfig,
    build_nominal_thermal_network_config,
    apply_degradation_to_network_config,
)

from subsystems.eps.schemas import EpsConfig

from subsystems.adcs.schemas import AdcsConfig

from subsystems.propulsion.schemas import PropulsionConfig

from subsystems.thermal.schemas import ThermalConfig

from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig

from subsystems.eps.degradation import EPSDegradation, BatteryDegradation, SolarPanelDegradation

from subsystems.adcs.degradation import ADCSDegradation, ReactionWheelDegradation, SensorDegradation

from subsystems.propulsion.degradation import PropulsionDegradation, ThrusterDegradation, FuelTankDegradation

from subsystems.thermal.degradation import ThermalDegradation

from components.heater.degradation import HeaterDegradation
from components.radiator.degradation import RadiatorDegradation

COMPONENT_CONFIGS = [
    BatteryConfig,
    ThermalNodeConfig,
    DataQueueConfig,
    SolarPanelConfig,
    ReactionWheelCommandConfig,
    ReactionWheelDynamicsConfig,
    ThrusterCommandConfig,
    ThrusterPhysicalConfig,
    FuelTankConfig,
    MtbConfig,
    SingleGimbalCmgConfig,
    CmgConfig,
    ImuConfig,
    MagnetometerConfig,
    StarTrackerConfig,
    SunSensorConfig,
    PowerSinkConfig,
    PduConfig,
    LinkBudgetConfig,
    GroundStationConfig,
]

SUBSYSTEM_CONFIGS = [
    ThermalNetworkConfig,
    ThermalConfig,
    EpsConfig,
    AdcsConfig,
    PropulsionConfig,
]

WHOLE_SPACECRAFT_CONFIGS = [
    WholeSpacecraftConfig,
    WholeSpacecraftRunConfig,
]

DEGRADATION_CONFIGS = [
    EPSDegradation,
    ADCSDegradation,
    PropulsionDegradation,
    ThermalDegradation,
    BatteryDegradation,
    SolarPanelDegradation,
    ReactionWheelDegradation,
    SensorDegradation,
    ThrusterDegradation,
    FuelTankDegradation,
    HeaterDegradation,
    RadiatorDegradation,
]


def get_config_class(name: str) -> Optional[type]:
    """Get a configuration class by name."""
    for cfg in COMPONENT_CONFIGS + SUBSYSTEM_CONFIGS + WHOLE_SPACECRAFT_CONFIGS + DEGRADATION_CONFIGS:
        if cfg.__name__ == name:
            return cfg
    return None


def list_config_classes() -> Dict[str, List[str]]:
    """List all available configuration classes by category."""
    return {
        "components": [c.__name__ for c in COMPONENT_CONFIGS],
        "subsystems": [c.__name__ for c in SUBSYSTEM_CONFIGS],
        "whole_spacecraft": [c.__name__ for c in WHOLE_SPACECRAFT_CONFIGS],
        "degradation": [c.__name__ for c in DEGRADATION_CONFIGS],
    }
