"""EPS subsystem degradation aggregation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from components.battery import degradation as _battery_degradation
from components.solar_panel import degradation as _solar_panel_degradation
from components.pdu import degradation as _pdu_degradation
from components.power_sink import degradation as _power_sink_degradation
from components.battery.degradation import BatteryDegradation, apply_battery_degradation
from components.solar_panel.degradation import SolarPanelDegradation, apply_solar_panel_degradation

from ..degradation_base import (
    ComponentDegradationBinding,
    SubsystemDegradationScenario,
    bind_degradation,
    build_degradation_update_specs,
    pick_default,
    register_degradation_events,
)


COMPONENT_COVERAGE: tuple[str, ...] = (
    "battery",
    "solar_panel",
    "pdu",
    "power_sink",
)


@dataclass(frozen=True)
class EPSDegradation:
    battery_degradation: BatteryDegradation
    solar_panel_degradation: SolarPanelDegradation
    pdu_efficiency_loss_pct: float = 0.0

    def total_power_budget_loss_pct(self) -> float:
        solar_loss = self.solar_panel_degradation.efficiency_loss_pct
        battery_loss = self.battery_degradation.capacity_loss_pct
        total_loss = solar_loss * 0.5 + battery_loss * 0.3 + self.pdu_efficiency_loss_pct * 0.2
        return min(total_loss, 100.0)


_COMPONENT_DEGRADATION_MODULES = {
    "battery": _battery_degradation,
    "solar_panel": _solar_panel_degradation,
    "pdu": _pdu_degradation,
    "power_sink": _power_sink_degradation,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_DEGRADATION_MODULES[component], "default_degradations", component, index)


def default_degradation_scenarios() -> dict[str, SubsystemDegradationScenario]:
    """Return representative EPS degradation scenarios."""

    battery_capacity = bind_degradation(
        "battery",
        _pick("battery", 0),
        target_id="battery.main",
        mapping={"module": "simpleBattery", "parameter": "storageCapacity/internal_resistance", "injection": "periodic_module_parameter"},
    )
    solar_efficiency = bind_degradation(
        "solar_panel",
        _pick("solar_panel", 0),
        target_id="solar_panel.array",
        mapping={"module": "simpleSolarPanel_or_fallback_source", "parameter": "efficiency/netPower", "injection": "periodic_module_parameter"},
    )
    pdu_resistance = bind_degradation(
        "pdu",
        _pick("pdu", 0),
        target_id="pdu.bus",
        mapping={"module": "pdu", "parameter": "contact_resistance/voltage_regulation", "injection": "periodic_command_gate"},
    )
    load_drift = bind_degradation(
        "power_sink",
        _pick("power_sink", 0),
        target_id="power_sink.payload",
        mapping={"module": "simplePowerSink", "parameter": "load_demand/efficiency", "injection": "periodic_module_parameter"},
    )

    return {
        "source_storage_aging": SubsystemDegradationScenario(
            name="source_storage_aging",
            component_degradations=(battery_capacity, solar_efficiency),
            description="Battery capacity/resistance and solar array output degrade EPS energy balance.",
        ),
        "distribution_load_aging": SubsystemDegradationScenario(
            name="distribution_load_aging",
            component_degradations=(pdu_resistance, load_drift),
            description="PDU distribution losses and load drift stress EPS channel control.",
        ),
        "combined_eps_aging": SubsystemDegradationScenario(
            name="combined_eps_aging",
            component_degradations=(battery_capacity, solar_efficiency, pdu_resistance, load_drift),
            description="Coverage scenario exercising every EPS component degradation binding.",
        ),
    }


def build_eps_degradation(
    *,
    battery_capacity_loss_pct: float = 0.0,
    solar_efficiency_loss_pct: float = 0.0,
    pdu_efficiency_loss_pct: float = 0.0,
) -> EPSDegradation:
    """Build the compact EPS degradation object used by subsystem and whole-spacecraft callers.

    Whole-spacecraft code should request EPS degradation through this subsystem
    factory rather than importing battery/solar-panel component degradation classes.
    """

    return EPSDegradation(
        battery_degradation=BatteryDegradation(capacity_loss_pct=float(battery_capacity_loss_pct)),
        solar_panel_degradation=SolarPanelDegradation(efficiency_loss_pct=float(solar_efficiency_loss_pct)),
        pdu_efficiency_loss_pct=float(pdu_efficiency_loss_pct),
    )


def default_eps_degradation() -> EPSDegradation:
    return build_eps_degradation()


def apply_eps_degradation(eps_config, eps_degradation: EPSDegradation):
    degraded_battery = apply_battery_degradation(eps_config.battery, eps_degradation.battery_degradation)
    degraded_solar_panel = apply_solar_panel_degradation(eps_config.solar_panel, eps_degradation.solar_panel_degradation)
    original_bus_max_w = eps_config.pdu.bus_max_w
    degraded_bus_max_w = original_bus_max_w * (1.0 - eps_degradation.pdu_efficiency_loss_pct / 100.0)
    degraded_pdu = replace(eps_config.pdu, bus_max_w=degraded_bus_max_w)
    return replace(eps_config, battery=degraded_battery, solar_panel=degraded_solar_panel, pdu=degraded_pdu)


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "EPSDegradation",
    "build_eps_degradation",
    "default_eps_degradation",
    "apply_eps_degradation",
    "default_degradation_scenarios",
    "build_degradation_update_specs",
    "register_degradation_events",
]
