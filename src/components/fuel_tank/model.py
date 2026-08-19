"""Pure-Python reference model for the fuel tank component."""
from __future__ import annotations

from dataclasses import replace

from .degradation import FuelTankDegradation
from .faults import FaultSpec
from .faults import FuelTankFaultType
from .schemas import FuelTankConfig, FuelTankProfileResult, FuelTankState


def pressure_from_mass(propellant_mass_kg: float, config: FuelTankConfig) -> float:
    return config.dry_pressure_pa + max(0.0, min(1.0, propellant_mass_kg / max(config.capacity_kg, 1e-12))) * (
        config.full_pressure_pa - config.dry_pressure_pa
    )


def initialize_fuel_tank(config: FuelTankConfig) -> FuelTankState:
    mass_kg = max(0.0, min(config.capacity_kg, config.initial_mass_kg))
    return FuelTankState(mass_kg, pressure_from_mass(mass_kg, config))


def step_fuel_tank(state: FuelTankState, config: FuelTankConfig, mass_flow_kg_s: float, dt_s: float) -> FuelTankState:
    if dt_s < 0:
        raise ValueError("dt_s")
    propellant_mass_kg = max(0.0, min(config.capacity_kg, state.propellant_mass_kg - max(0.0, mass_flow_kg_s) * dt_s))
    return FuelTankState(propellant_mass_kg, pressure_from_mass(propellant_mass_kg, config))


def simulate_mass_flow_profile(
    state: FuelTankState,
    config: FuelTankConfig,
    profile,
    dt_s: float,
) -> FuelTankProfileResult:
    time_s = [0.0]
    propellant_mass_kg = [state.propellant_mass_kg]
    pressure_pa = [state.pressure_pa]
    current_state = state
    for i, mass_flow in enumerate(profile):
        current_state = step_fuel_tank(current_state, config, mass_flow, dt_s)
        time_s.append((i + 1) * dt_s)
        propellant_mass_kg.append(current_state.propellant_mass_kg)
        pressure_pa.append(current_state.pressure_pa)
    return FuelTankProfileResult(tuple(time_s), tuple(propellant_mass_kg), tuple(pressure_pa))


def _build_nominal_fuel_tank_config_base_impl(
    capacity_kg: float = 10.0,
    initial_mass_kg: float = 5.0,
    full_pressure_pa: float = 2.5e6,
    dry_pressure_pa: float = 1.5e5,
    degradation: FuelTankDegradation | None = None,
    fault_specs: list[FaultSpec] | None = None,
) -> FuelTankConfig:
    config = FuelTankConfig(
        capacity_kg=capacity_kg,
        initial_mass_kg=initial_mass_kg,
        full_pressure_pa=full_pressure_pa,
        dry_pressure_pa=dry_pressure_pa,
    )
    if degradation is not None:
        config = replace(
            config,
            capacity_kg=config.capacity_kg * (1.0 - degradation.fuel_leak_pct / 100.0),
            full_pressure_pa=config.full_pressure_pa * (1.0 - degradation.pressure_loss_pct / 100.0),
        )
    if fault_specs:
        config = apply_fuel_tank_config_faults(config, fault_specs)
    return config


def apply_fuel_tank_config_faults(config: FuelTankConfig, fault_specs: list[FaultSpec]) -> FuelTankConfig:
    updated = config
    for spec in fault_specs:
        fault_type = spec.fault_type if isinstance(spec.fault_type, FuelTankFaultType) else FuelTankFaultType(spec.fault_type)
        if fault_type == FuelTankFaultType.RapidLeak:
            updated = replace(updated, capacity_kg=updated.capacity_kg * (1.0 - min(1.0, float(spec.magnitude) / 10.0)))
        elif fault_type == FuelTankFaultType.PressureLoss:
            updated = replace(updated, full_pressure_pa=updated.full_pressure_pa * (1.0 - min(1.0, float(spec.magnitude))))
        elif fault_type == FuelTankFaultType.Overpressure:
            updated = replace(updated, full_pressure_pa=updated.full_pressure_pa * (1.0 + float(spec.magnitude)))
        elif fault_type == FuelTankFaultType.ValveStuck:
            updated = replace(updated, dry_pressure_pa=updated.dry_pressure_pa * (1.0 + float(spec.magnitude)))
    return updated


# Component fault/degradation compatibility wrappers
from .degradation import FuelTankDegradationRate
from .degradation import apply_fuel_tank_degradation, compute_degradation_state
from .faults import apply_fuel_tank_faults
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_fuel_tank_config_base = _build_nominal_fuel_tank_config_base_impl

def build_nominal_fuel_tank_config(
    *args,
    degradation: FuelTankDegradation | None = None,
    degradation_rate: FuelTankDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_fuel_tank_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_fuel_tank_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_fuel_tank_faults(config, fault_specs)
    return config
