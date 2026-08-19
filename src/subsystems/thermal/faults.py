"""Thermal subsystem fault aggregation."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from components.heater import faults as _heater_faults
from components.radiator import faults as _radiator_faults
from components.thermal_node import faults as _thermal_node_faults
from components.heater.faults import HeaterFaultType
from components.fault_spec import FaultSpec

from ..fault_base import (
    ComponentFaultBinding,
    SubsystemFaultScenario,
    bind_fault,
    build_fault_event_specs,
    pick_default,
    register_fault_events,
)


COMPONENT_COVERAGE: tuple[str, ...] = (
    "heater",
    "radiator",
    "thermal_node",
)


class ThermalFaultType(Enum):
    ThermalControlFailure = "thermal_control_failure"
    HeaterFailure = "heater_failure"
    RadiatorBlockage = "radiator_blockage"
    ThermalNodeFault = "thermal_node_fault"

    @property
    def severity_level(self) -> int:
        severity_mapping = {
            ThermalFaultType.ThermalControlFailure: 3,
            ThermalFaultType.HeaterFailure: 2,
            ThermalFaultType.RadiatorBlockage: 2,
            ThermalFaultType.ThermalNodeFault: 2,
        }
        return severity_mapping[self]


_COMPONENT_FAULT_MODULES = {
    "heater": _heater_faults,
    "radiator": _radiator_faults,
    "thermal_node": _thermal_node_faults,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_FAULT_MODULES[component], "default_faults", component, index)


def default_fault_scenarios() -> dict[str, SubsystemFaultScenario]:
    """Return representative thermal scenarios traceable to component faults."""

    heater_off = bind_fault(
        "heater",
        _pick("heater", 0),
        target_id="heater.battery",
        mapping={"module": "thermalScheduledSysModel", "parameter": "heater_power_w/commanded_on", "injection": "scheduled_sysmodel_parameter"},
    )
    radiator_contamination = bind_fault(
        "radiator",
        _pick("radiator", 0),
        target_id="radiator.primary",
        mapping={"module": "thermalScheduledSysModel", "parameter": "cooling_power_w/rejection_factor", "injection": "scheduled_sysmodel_parameter"},
    )
    thermal_runaway = bind_fault(
        "thermal_node",
        _pick("thermal_node", 0),
        target_id="thermal_node.electronics",
        mapping={"module": "thermalScheduledSysModel", "parameter": "heat_input_w/temperature_k", "injection": "scheduled_sysmodel_parameter"},
    )
    sensor_bias = bind_fault(
        "thermal_node",
        _pick("thermal_node", 1),
        target_id="thermal_node.electronics_sensor",
        mapping={"module": "thermalScheduledSysModel", "parameter": "sensor_bias_k", "injection": "message_adapter"},
    )

    return {
        "heater_stuck_off": SubsystemFaultScenario(
            name="heater_stuck_off",
            component_faults=(heater_off,),
            description="Heater relay/open fault prevents heater power delivery.",
        ),
        "radiator_rejection_loss": SubsystemFaultScenario(
            name="radiator_rejection_loss",
            component_faults=(radiator_contamination,),
            description="Radiator contamination lowers emissivity and rejection factor.",
        ),
        "thermal_node_fault": SubsystemFaultScenario(
            name="thermal_node_fault",
            component_faults=(thermal_runaway, sensor_bias),
            description="Thermal node runaway heat input and sensor bias stress thermal control.",
        ),
        "combined_thermal_fault": SubsystemFaultScenario(
            name="combined_thermal_fault",
            component_faults=(heater_off, radiator_contamination, thermal_runaway, sensor_bias),
            description="Coverage scenario exercising every Thermal component fault binding.",
        ),
    }


def aggregate_thermal_faults(fault_specs: List[FaultSpec]) -> Optional[ThermalFaultType]:
    """Legacy aggregator retained for existing fault_campaign callers."""

    if not fault_specs:
        return None

    has_heater_fault = False
    has_radiator_fault = False
    has_thermal_node_fault = False

    for fault in fault_specs:
        if isinstance(fault.fault_type, HeaterFaultType):
            has_heater_fault = True
        name = getattr(fault.fault_type, "value", str(fault.fault_type))
        if "radiator" in name or "surface" in name or "deploy" in name or "blockage" in name:
            has_radiator_fault = True
        if "thermal_node" in name or "runaway" in name or "sensor_bias" in name:
            has_thermal_node_fault = True

    if has_heater_fault and (has_radiator_fault or has_thermal_node_fault):
        return ThermalFaultType.ThermalControlFailure
    if has_heater_fault:
        return ThermalFaultType.HeaterFailure
    if has_radiator_fault:
        return ThermalFaultType.RadiatorBlockage
    if has_thermal_node_fault:
        return ThermalFaultType.ThermalNodeFault
    return None



def build_thermal_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build Thermal runtime FaultSpec values for whole-spacecraft injection.

    These specs target the thermal graph objects registered by
    ``whole_spacecraft.builder``.  The subsystem owns the names and target IDs;
    the whole-spacecraft layer only schedules and applies them.
    """

    if name == "heater_stuck_off":
        return (
            FaultSpec(
                fault_type="thermal_heater_stuck_off",
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="heater.battery",
            ),
        )
    if name == "heater_stuck_on":
        return (
            FaultSpec(
                fault_type="thermal_heater_stuck_on",
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="heater.battery",
            ),
        )
    if name == "radiator_rejection_loss":
        return (
            FaultSpec(
                fault_type="thermal_radiator_rejection_loss",
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.60,
                target_id="radiator.electronics",
            ),
        )
    if name == "thermal_node_heat_bias":
        return (
            FaultSpec(
                fault_type="thermal_node_heat_bias",
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=15.0,
                target_id="thermal_node.electronics",
            ),
        )
    return ()



def apply_runtime_thermal_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Route runtime thermal faults to heater/radiator/node components."""

    from components.heater.faults import apply_runtime_heater_fault
    from components.radiator.faults import apply_runtime_radiator_fault
    from components.thermal_node.faults import apply_runtime_thermal_node_fault

    target_id = str(getattr(spec, "target_id", "") or "")
    target_lower = target_id.lower()
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if "radiator" in target_lower or fault_value == "thermal_radiator_rejection_loss":
        target = component_registry.get(target_id) or component_registry.get("radiator.electronics") or component_registry.get("thermal_radiator_electronics")
        if target is None and resolve_component is not None:
            target = resolve_component(target_id or "radiator.electronics", "radiator")
        changed = bool(target is not None and apply_runtime_radiator_fault(target, spec, set_attr=set_attr))
        return ({"component": "radiator", "target_id": target_id, "changed": changed},) if changed else ()
    if "thermal_node" in target_lower or fault_value == "thermal_node_heat_bias":
        target = component_registry.get(target_id) or component_registry.get("thermal_node.electronics") or component_registry.get("thermal_node.battery")
        if target is None and resolve_component is not None:
            target = resolve_component(target_id or "thermal_node.electronics", "thermal_node")
        changed = bool(target is not None and apply_runtime_thermal_node_fault(target, spec, set_attr=set_attr))
        return ({"component": "thermal_node", "target_id": target_id, "changed": changed},) if changed else ()
    target = component_registry.get(target_id) or component_registry.get("heater.battery") or component_registry.get("thermal_heater_battery") or component_registry.get("battery_heater")
    if target is None and resolve_component is not None:
        target = resolve_component(target_id or "heater.battery", "heater")
    changed = bool(target is not None and apply_runtime_heater_fault(target, spec, set_attr=set_attr))
    return ({"component": "heater", "target_id": target_id, "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "ThermalFaultType",
    "aggregate_thermal_faults",
    "default_fault_scenarios",
    "build_fault_event_specs",
    "build_thermal_direct_fault_specs",
    "register_fault_events",
]
