"""Simulation scenario templates for common satellite mission profiles.

This module provides pre-defined scenario templates that can be used to quickly
set up and run common simulation cases. Each template returns a complete
configuration and can be customized as needed.

Available scenarios:
    - battery_discharge_test: Battery discharge under constant load
    - battery_charge_test: Battery charging from solar panels
    - thermal_network_simulation: Multi-node thermal network simulation
    - solar_eclipse_cycle: Solar panel output through eclipse cycles
    - combined_power_thermal: Combined EPS and thermal simulation
"""
from __future__ import annotations

from typing import Tuple, List, Dict, Any, Optional

from components.dynamic_models import (
    ThermalNodeConfig,
    ThermalNodeState,
    SolarPanelConfig,
    SolarPanelState,
)
from components.battery import BatteryConfig, BatteryState

from subsystems.thermal.network_config import (
    ThermalNetworkConfig,
    build_nominal_thermal_network_config,
)


def battery_discharge_test(
    capacity_wh: float = 100.0,
    initial_soc: float = 1.0,
    discharge_power_w: float = 20.0,
    dt_s: float = 60.0,
    duration_hours: float = 10.0,
) -> Dict[str, Any]:
    """Create a battery discharge test scenario.
    
    Args:
        capacity_wh: Battery capacity in watt-hours
        initial_soc: Initial state of charge (0-1)
        discharge_power_w: Constant discharge power in watts
        dt_s: Time step in seconds
        duration_hours: Simulation duration in hours
    
    Returns:
        Dictionary with config, initial state, power profile, and parameters
    """
    config = BatteryConfig(
        capacity_wh=capacity_wh,
        initial_soc=initial_soc,
        min_soc=0.05,
        max_soc=1.0,
        discharge_efficiency=0.95,
    )
    
    initial_state = BatteryState(
        storage_wh=capacity_wh * initial_soc,
        capacity_wh=capacity_wh,
        soc=initial_soc,
    )
    
    num_steps = int(duration_hours * 3600 / dt_s)
    power_profile = [-discharge_power_w] * num_steps
    
    return {
        "config": config,
        "initial_state": initial_state,
        "power_profile": power_profile,
        "dt_s": dt_s,
        "duration_s": duration_hours * 3600,
        "scenario_name": "battery_discharge_test",
        "description": f"Battery discharge at {discharge_power_w}W from {initial_soc*100}% SOC",
    }


def battery_charge_test(
    capacity_wh: float = 100.0,
    initial_soc: float = 0.2,
    charge_power_w: float = 50.0,
    dt_s: float = 60.0,
    duration_hours: float = 5.0,
) -> Dict[str, Any]:
    """Create a battery charge test scenario.
    
    Args:
        capacity_wh: Battery capacity in watt-hours
        initial_soc: Initial state of charge (0-1)
        charge_power_w: Constant charge power in watts
        dt_s: Time step in seconds
        duration_hours: Simulation duration in hours
    
    Returns:
        Dictionary with config, initial state, power profile, and parameters
    """
    config = BatteryConfig(
        capacity_wh=capacity_wh,
        initial_soc=initial_soc,
        min_soc=0.0,
        max_soc=1.0,
        charge_efficiency=0.95,
        cv_soc_threshold=0.8,
        max_charge_current_a=10.0,
        nominal_voltage_v=28.0,
    )
    
    initial_state = BatteryState(
        storage_wh=capacity_wh * initial_soc,
        capacity_wh=capacity_wh,
        soc=initial_soc,
    )
    
    num_steps = int(duration_hours * 3600 / dt_s)
    power_profile = [charge_power_w] * num_steps
    
    return {
        "config": config,
        "initial_state": initial_state,
        "power_profile": power_profile,
        "dt_s": dt_s,
        "duration_s": duration_hours * 3600,
        "scenario_name": "battery_charge_test",
        "description": f"Battery charge at {charge_power_w}W from {initial_soc*100}% SOC",
    }


def thermal_node_test(
    ambient_k: float = 290.0,
    tau_s: float = 300.0,
    heat_gain_k_per_w: float = 0.5,
    power_w: float = 10.0,
    dt_s: float = 10.0,
    duration_hours: float = 2.0,
) -> Dict[str, Any]:
    """Create a single thermal node test scenario.
    
    Args:
        ambient_k: Ambient temperature in Kelvin
        tau_s: Thermal time constant in seconds
        heat_gain_k_per_w: Temperature rise per watt (K/W)
        power_w: Constant power input in watts
        dt_s: Time step in seconds
        duration_hours: Simulation duration in hours
    
    Returns:
        Dictionary with config, initial state, power profile, and parameters
    """
    config = ThermalNodeConfig(
        ambient_k=ambient_k,
        tau_s=tau_s,
        heat_gain_k_per_w=heat_gain_k_per_w,
        min_temp_k=200.0,
        max_temp_k=400.0,
    )
    
    initial_state = ThermalNodeState(temp_k=ambient_k)
    
    num_steps = int(duration_hours * 3600 / dt_s)
    power_profile = [power_w] * num_steps
    
    return {
        "config": config,
        "initial_state": initial_state,
        "power_profile": power_profile,
        "dt_s": dt_s,
        "duration_s": duration_hours * 3600,
        "scenario_name": "thermal_node_test",
        "description": f"Thermal node with {power_w}W input, tau={tau_s}s",
    }


def thermal_network_simulation(
    duration_hours: float = 24.0,
    dt_s: float = 60.0,
    eclipse_fraction: float = 0.3,
) -> Dict[str, Any]:
    """Create a multi-node thermal network simulation scenario.
    
    Args:
        duration_hours: Simulation duration in hours
        dt_s: Time step in seconds
        eclipse_fraction: Fraction of time in eclipse (0-1)
    
    Returns:
        Dictionary with thermal network config and simulation parameters
    """
    config = build_nominal_thermal_network_config()
    
    num_steps = int(duration_hours * 3600 / dt_s)
    eclipse_steps = int(num_steps * eclipse_fraction)
    
    shadow_profile = []
    for i in range(num_steps):
        if i % int(1 / eclipse_fraction) < eclipse_steps / int(1 / eclipse_fraction):
            shadow_profile.append(0.0)
        else:
            shadow_profile.append(1.0)
    
    return {
        "config": config,
        "shadow_profile": shadow_profile,
        "dt_s": dt_s,
        "duration_s": duration_hours * 3600,
        "scenario_name": "thermal_network_simulation",
        "description": f"{len(config.nodes)}-node thermal network over {duration_hours}h with {eclipse_fraction*100}% eclipse",
    }


def solar_eclipse_cycle(
    max_power_w: float = 120.0,
    orbit_period_minutes: float = 90.0,
    eclipse_duration_minutes: float = 30.0,
    dt_s: float = 10.0,
    num_orbits: int = 3,
) -> Dict[str, Any]:
    """Create a solar panel eclipse cycle scenario.
    
    Args:
        max_power_w: Maximum solar panel power
        orbit_period_minutes: Orbit period in minutes
        eclipse_duration_minutes: Eclipse duration in minutes
        dt_s: Time step in seconds
        num_orbits: Number of orbits to simulate
    
    Returns:
        Dictionary with solar panel config, sun profile, shadow profile, and parameters
    """
    config = SolarPanelConfig(
        max_power_w=max_power_w,
        efficiency=0.28,
        max_slew_rate_rad_s=0.05,
    )
    
    initial_state = SolarPanelState(normal_b=(1.0, 0.0, 0.0))
    
    orbit_period_s = orbit_period_minutes * 60
    eclipse_duration_s = eclipse_duration_minutes * 60
    total_duration_s = orbit_period_s * num_orbits
    
    num_steps = int(total_duration_s / dt_s)
    
    sun_profile = [(1.0, 0.0, 0.0)] * num_steps
    
    shadow_profile = []
    for i in range(num_steps):
        time_s = i * dt_s
        orbit_time_s = time_s % orbit_period_s
        if orbit_time_s < eclipse_duration_s:
            shadow_profile.append(0.0)
        else:
            shadow_profile.append(1.0)
    
    return {
        "config": config,
        "initial_state": initial_state,
        "sun_profile": sun_profile,
        "shadow_profile": shadow_profile,
        "dt_s": dt_s,
        "duration_s": total_duration_s,
        "scenario_name": "solar_eclipse_cycle",
        "description": f"Solar panel through {num_orbits} orbits ({orbit_period_minutes}min each, {eclipse_duration_minutes}min eclipse)",
    }


def combined_power_thermal(
    capacity_wh: float = 100.0,
    solar_power_w: float = 120.0,
    load_power_w: float = 20.0,
    orbit_period_minutes: float = 90.0,
    eclipse_duration_minutes: float = 30.0,
    dt_s: float = 60.0,
    num_orbits: int = 5,
) -> Dict[str, Any]:
    """Create a combined power and thermal simulation scenario.
    
    Args:
        capacity_wh: Battery capacity in watt-hours
        solar_power_w: Maximum solar panel power
        load_power_w: Constant load power
        orbit_period_minutes: Orbit period in minutes
        eclipse_duration_minutes: Eclipse duration in minutes
        dt_s: Time step in seconds
        num_orbits: Number of orbits to simulate
    
    Returns:
        Dictionary with combined EPS and thermal configuration
    """
    battery_config = BatteryConfig(
        capacity_wh=capacity_wh,
        initial_soc=0.5,
        min_soc=0.1,
        max_soc=1.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
    )
    
    battery_initial = BatteryState(
        storage_wh=capacity_wh * 0.5,
        capacity_wh=capacity_wh,
        soc=0.5,
    )
    
    thermal_config = ThermalNodeConfig(
        ambient_k=290.0,
        tau_s=600.0,
        heat_gain_k_per_w=0.3,
        min_temp_k=250.0,
        max_temp_k=350.0,
    )
    
    thermal_initial = ThermalNodeState(temp_k=290.0)
    
    orbit_period_s = orbit_period_minutes * 60
    total_duration_s = orbit_period_s * num_orbits
    num_steps = int(total_duration_s / dt_s)
    
    net_power_profile = []
    for i in range(num_steps):
        time_s = i * dt_s
        orbit_time_s = time_s % orbit_period_s
        if orbit_time_s < eclipse_duration_minutes * 60:
            net_power_profile.append(-load_power_w)
        else:
            net_power_profile.append(solar_power_w - load_power_w)
    
    return {
        "battery_config": battery_config,
        "battery_initial": battery_initial,
        "thermal_config": thermal_config,
        "thermal_initial": thermal_initial,
        "power_profile": net_power_profile,
        "dt_s": dt_s,
        "duration_s": total_duration_s,
        "scenario_name": "combined_power_thermal",
        "description": f"Combined EPS+thermal simulation over {num_orbits} orbits",
    }


SCENARIOS = {
    "battery_discharge_test": battery_discharge_test,
    "battery_charge_test": battery_charge_test,
    "thermal_node_test": thermal_node_test,
    "thermal_network_simulation": thermal_network_simulation,
    "solar_eclipse_cycle": solar_eclipse_cycle,
    "combined_power_thermal": combined_power_thermal,
}


def list_scenarios() -> List[str]:
    """List all available scenarios."""
    return list(SCENARIOS.keys())


def get_scenario(name: str, **kwargs) -> Dict[str, Any]:
    """Get a scenario by name with optional customization.
    
    Args:
        name: Scenario name from list_scenarios()
        **kwargs: Parameters to override default scenario settings
    
    Returns:
        Scenario dictionary with configuration and parameters
    
    Raises:
        ValueError: If scenario name is not found
    """
    if name not in SCENARIOS:
        raise ValueError(f"Scenario '{name}' not found. Available: {list(SCENARIOS.keys())}")
    return SCENARIOS[name](**kwargs)
