"""Thermal subsystem degradation aggregation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from components.heater import degradation as _heater_degradation
from components.radiator import degradation as _radiator_degradation
from components.thermal_node import degradation as _thermal_node_degradation
from components.heater.degradation import HeaterDegradation, apply_heater_degradation
from components.radiator.degradation import RadiatorDegradation

from ..degradation_base import (
    ComponentDegradationBinding,
    SubsystemDegradationScenario,
    bind_degradation,
    build_degradation_update_specs,
    pick_default,
    register_degradation_events,
)


COMPONENT_COVERAGE: tuple[str, ...] = (
    "heater",
    "radiator",
    "thermal_node",
)


@dataclass(frozen=True)
class ThermalDegradation:
    heater_degradation: HeaterDegradation
    radiator_degradation: RadiatorDegradation

    def thermal_control_capacity_loss_pct(self) -> float:
        heater_loss = self.heater_degradation.efficiency_loss_pct
        radiator_loss = self.radiator_degradation.efficiency_loss_pct
        radiator_emissivity_loss = self.radiator_degradation.emissivity_degradation_pct
        total_loss = heater_loss * 0.4 + radiator_loss * 0.4 + radiator_emissivity_loss * 0.2
        return min(total_loss, 100.0)


_COMPONENT_DEGRADATION_MODULES = {
    "heater": _heater_degradation,
    "radiator": _radiator_degradation,
    "thermal_node": _thermal_node_degradation,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_DEGRADATION_MODULES[component], "default_degradations", component, index)


def default_degradation_scenarios() -> dict[str, SubsystemDegradationScenario]:
    """Return representative thermal degradation scenarios."""

    heater_efficiency = bind_degradation(
        "heater",
        _pick("heater", 0),
        target_id="heater.battery",
        mapping={"module": "thermalScheduledSysModel", "parameter": "heater_power_w/efficiency", "injection": "periodic_scheduled_sysmodel_parameter"},
    )
    heater_resistance = bind_degradation(
        "heater",
        _pick("heater", 1),
        target_id="heater.battery",
        mapping={"module": "thermalScheduledSysModel", "parameter": "heater_power_w", "injection": "periodic_scheduled_sysmodel_parameter"},
    )
    radiator_emissivity = bind_degradation(
        "radiator",
        _pick("radiator", 0),
        target_id="radiator.primary",
        mapping={"module": "thermalScheduledSysModel", "parameter": "cooling_power_w/rejection_factor/emissivity", "injection": "periodic_scheduled_sysmodel_parameter"},
    )
    thermal_resistance = bind_degradation(
        "thermal_node",
        _pick("thermal_node", 0),
        target_id="thermal_node.electronics",
        mapping={"module": "thermalScheduledSysModel", "parameter": "thermal_resistance/conductance", "injection": "periodic_scheduled_sysmodel_parameter"},
    )
    heat_capacity = bind_degradation(
        "thermal_node",
        _pick("thermal_node", 1),
        target_id="thermal_node.electronics",
        mapping={"module": "thermalScheduledSysModel", "parameter": "heat_capacity", "injection": "periodic_scheduled_sysmodel_parameter"},
    )

    return {
        "heater_radiator_aging": SubsystemDegradationScenario(
            name="heater_radiator_aging",
            component_degradations=(heater_efficiency, heater_resistance, radiator_emissivity),
            description="Heater and radiator degradation reduces thermal actuation and heat rejection.",
        ),
        "thermal_node_aging": SubsystemDegradationScenario(
            name="thermal_node_aging",
            component_degradations=(thermal_resistance, heat_capacity),
            description="Thermal node resistance and heat capacity drift alter node dynamics.",
        ),
        "combined_thermal_aging": SubsystemDegradationScenario(
            name="combined_thermal_aging",
            component_degradations=(heater_efficiency, heater_resistance, radiator_emissivity, thermal_resistance, heat_capacity),
            description="Coverage scenario exercising every Thermal component degradation binding.",
        ),
    }


def build_thermal_degradation(
    *,
    heater_efficiency_loss_pct: float = 0.0,
    radiator_efficiency_loss_pct: float = 0.0,
    radiator_emissivity_degradation_pct: float = 0.0,
) -> ThermalDegradation:
    """Build the compact Thermal degradation object for higher-level callers."""

    return ThermalDegradation(
        heater_degradation=HeaterDegradation(efficiency_loss_pct=float(heater_efficiency_loss_pct)),
        radiator_degradation=RadiatorDegradation(
            efficiency_loss_pct=float(radiator_efficiency_loss_pct),
            emissivity_degradation_pct=float(radiator_emissivity_degradation_pct),
        ),
    )


def default_thermal_degradation() -> ThermalDegradation:
    return build_thermal_degradation()


def apply_thermal_degradation(thermal_config, thermal_degradation: ThermalDegradation):
    degraded_heaters = {}
    for name, heater in thermal_config.heaters.items():
        degraded_heaters[name] = apply_heater_degradation(heater, thermal_degradation.heater_degradation)

    degraded_heat_efficiency = {}
    for comp, eff in thermal_config.heat_efficiency_by_component.items():
        degraded_heat_efficiency[comp] = eff * (1.0 - thermal_degradation.radiator_degradation.efficiency_loss_pct / 100.0)

    return replace(thermal_config, heaters=degraded_heaters, heat_efficiency_by_component=degraded_heat_efficiency)


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "ThermalDegradation",
    "build_thermal_degradation",
    "default_thermal_degradation",
    "apply_thermal_degradation",
    "default_degradation_scenarios",
    "build_degradation_update_specs",
    "register_degradation_events",
]
