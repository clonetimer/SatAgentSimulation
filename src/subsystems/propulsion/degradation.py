"""Propulsion subsystem degradation aggregation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from components.thruster import degradation as _thruster_degradation
from components.fuel_tank import degradation as _fuel_tank_degradation
from components.thruster.degradation import ThrusterDegradation, apply_thruster_degradation
from components.fuel_tank.degradation import FuelTankDegradation

from ..degradation_base import (
    ComponentDegradationBinding,
    SubsystemDegradationScenario,
    bind_degradation,
    build_degradation_update_specs,
    pick_default,
    register_degradation_events,
)


COMPONENT_COVERAGE: tuple[str, ...] = (
    "thruster",
    "fuel_tank",
)


@dataclass(frozen=True)
class PropulsionDegradation:
    thruster_degradation: ThrusterDegradation
    fuel_tank_degradation: FuelTankDegradation

    def total_delta_v_loss_pct(self) -> float:
        thrust_loss = self.thruster_degradation.thrust_loss_pct
        isp_loss = self.thruster_degradation.isp_loss_pct
        fuel_leak = self.fuel_tank_degradation.fuel_leak_pct
        total_loss = thrust_loss * 0.4 + isp_loss * 0.3 + fuel_leak * 0.3
        return min(total_loss, 100.0)


_COMPONENT_DEGRADATION_MODULES = {
    "thruster": _thruster_degradation,
    "fuel_tank": _fuel_tank_degradation,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_DEGRADATION_MODULES[component], "default_degradations", component, index)


def default_degradation_scenarios() -> dict[str, SubsystemDegradationScenario]:
    """Return representative propulsion degradation scenarios."""

    isp_decay = bind_degradation(
        "thruster",
        _pick("thruster", 0),
        target_id="thruster.primary",
        mapping={"module": "thruster_effector", "parameter": "isp_s/mass_flow", "injection": "periodic_command_or_module_parameter"},
    )
    thrust_drift = bind_degradation(
        "thruster",
        _pick("thruster", 1),
        target_id="thruster.primary",
        mapping={"module": "thruster_effector", "parameter": "thrust_n/mass_flow_factor", "injection": "periodic_command_or_module_parameter"},
    )
    pressure_decay = bind_degradation(
        "fuel_tank",
        _pick("fuel_tank", 0),
        target_id="fuel_tank.main",
        mapping={"module": "fuel_tank", "parameter": "tank_pressure", "injection": "periodic_module_parameter"},
    )
    outlet_aging = bind_degradation(
        "fuel_tank",
        _pick("fuel_tank", 1),
        target_id="fuel_tank.outlet",
        mapping={"module": "fuel_tank", "parameter": "outlet_flow_factor", "injection": "periodic_module_parameter"},
    )

    return {
        "thruster_aging": SubsystemDegradationScenario(
            name="thruster_aging",
            component_degradations=(isp_decay, thrust_drift),
            description="Thruster specific impulse and thrust coefficient degrade delivered impulse.",
        ),
        "fuel_tank_aging": SubsystemDegradationScenario(
            name="fuel_tank_aging",
            component_degradations=(pressure_decay, outlet_aging),
            description="Fuel tank pressure decay and outlet aging reduce feed-system performance.",
        ),
        "combined_propulsion_aging": SubsystemDegradationScenario(
            name="combined_propulsion_aging",
            component_degradations=(isp_decay, thrust_drift, pressure_decay, outlet_aging),
            description="Coverage scenario exercising every Propulsion component degradation binding.",
        ),
    }


def build_propulsion_degradation(
    *,
    thrust_loss_pct: float = 0.0,
    isp_loss_pct: float = 0.0,
    fuel_leak_pct: float = 0.0,
    pressure_loss_pct: float = 0.0,
) -> PropulsionDegradation:
    """Build the compact Propulsion degradation object for higher-level callers."""

    return PropulsionDegradation(
        thruster_degradation=ThrusterDegradation(
            thrust_loss_pct=float(thrust_loss_pct),
            isp_loss_pct=float(isp_loss_pct),
        ),
        fuel_tank_degradation=FuelTankDegradation(
            fuel_leak_pct=float(fuel_leak_pct),
            pressure_loss_pct=float(pressure_loss_pct),
        ),
    )


def default_propulsion_degradation() -> PropulsionDegradation:
    return build_propulsion_degradation()


def apply_propulsion_degradation(prop_config, prop_degradation: PropulsionDegradation):
    degraded_thruster = apply_thruster_degradation(prop_config.thruster_physical, prop_degradation.thruster_degradation)
    original_mass = prop_config.fuel_tank.initial_mass_kg
    leaked_mass = original_mass * (prop_degradation.fuel_tank_degradation.fuel_leak_pct / 100.0)
    degraded_initial_mass = max(0.0, original_mass - leaked_mass)
    pressure_loss_factor = prop_degradation.fuel_tank_degradation.pressure_loss_pct / 100.0
    degraded_fuel_tank = replace(
        prop_config.fuel_tank,
        initial_mass_kg=degraded_initial_mass,
        full_pressure_pa=prop_config.fuel_tank.full_pressure_pa * (1.0 - pressure_loss_factor),
        dry_pressure_pa=prop_config.fuel_tank.dry_pressure_pa * (1.0 - pressure_loss_factor),
    )
    return replace(prop_config, thruster_physical=degraded_thruster, fuel_tank=degraded_fuel_tank)


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "PropulsionDegradation",
    "build_propulsion_degradation",
    "default_propulsion_degradation",
    "apply_propulsion_degradation",
    "default_degradation_scenarios",
    "build_degradation_update_specs",
    "register_degradation_events",
]
