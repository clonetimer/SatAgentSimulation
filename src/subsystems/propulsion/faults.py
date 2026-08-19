"""Propulsion subsystem fault aggregation."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from components.thruster import faults as _thruster_faults
from components.fuel_tank import faults as _fuel_tank_faults
from components.thruster.faults import ThrusterFaultType
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
    "thruster",
    "fuel_tank",
)


class PropulsionFaultType(Enum):
    PropulsionSystemFailure = "propulsion_system_failure"
    FuelTankLeak = "fuel_tank_leak"
    ThrusterClusterFailure = "thruster_cluster_failure"

    @property
    def severity_level(self) -> int:
        severity_mapping = {
            PropulsionFaultType.PropulsionSystemFailure: 3,
            PropulsionFaultType.FuelTankLeak: 3,
            PropulsionFaultType.ThrusterClusterFailure: 2,
        }
        return severity_mapping[self]


_COMPONENT_FAULT_MODULES = {
    "thruster": _thruster_faults,
    "fuel_tank": _fuel_tank_faults,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_FAULT_MODULES[component], "default_faults", component, index)


def default_fault_scenarios() -> dict[str, SubsystemFaultScenario]:
    """Return representative propulsion scenarios traceable to component faults."""

    thruster_valve_closed = bind_fault(
        "thruster",
        _pick("thruster", 0),
        target_id="thruster.primary",
        mapping={"module": "thruster_effector", "parameter": "valve_opening/thrust_n/mass_flow", "injection": "command_or_module_parameter"},
    )
    thruster_valve_open = bind_fault(
        "thruster",
        _pick("thruster", 1),
        target_id="thruster.primary",
        mapping={"module": "thruster_effector", "parameter": "valve_opening/leak_rate", "injection": "command_or_module_parameter"},
    )
    tank_leak = bind_fault(
        "fuel_tank",
        _pick("fuel_tank", 0),
        target_id="fuel_tank.main",
        mapping={"module": "fuel_tank", "parameter": "leak_rate/tank_pressure", "injection": "module_parameter"},
    )
    outlet_blockage = bind_fault(
        "fuel_tank",
        _pick("fuel_tank", 1),
        target_id="fuel_tank.outlet",
        mapping={"module": "fuel_tank", "parameter": "outlet_flow_factor", "injection": "module_parameter"},
    )

    return {
        "thruster_valve_closed": SubsystemFaultScenario(
            name="thruster_valve_closed",
            component_faults=(thruster_valve_closed,),
            description="Thruster valve closed fault blocks commanded propellant flow.",
        ),
        "fuel_tank_leak": SubsystemFaultScenario(
            name="fuel_tank_leak",
            component_faults=(tank_leak,),
            description="Fuel tank leak produces parasitic mass loss and pressure decay.",
        ),
        "feed_system_restriction": SubsystemFaultScenario(
            name="feed_system_restriction",
            component_faults=(outlet_blockage, thruster_valve_closed),
            description="Fuel-tank outlet blockage and thruster valve fault reduce delivered impulse.",
        ),
        "combined_propulsion_fault": SubsystemFaultScenario(
            name="combined_propulsion_fault",
            component_faults=(thruster_valve_closed, thruster_valve_open, tank_leak, outlet_blockage),
            description="Coverage scenario exercising every Propulsion component fault binding.",
        ),
    }


def build_propulsion_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build Propulsion runtime FaultSpec values for whole-spacecraft direct injection."""

    if name == "thruster_ignition_failure":
        return (
            FaultSpec(
                fault_type=ThrusterFaultType.IgnitionFailure,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="thruster",
            ),
        )
    if name == "thruster_nozzle_blockage":
        return (
            FaultSpec(
                fault_type=ThrusterFaultType.NozzleBlockage,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.75,
                target_id="thruster",
            ),
        )
    return ()


def aggregate_propulsion_faults(fault_specs: List[FaultSpec]) -> Optional[PropulsionFaultType]:
    """Legacy aggregator retained for existing fault_campaign callers."""

    if not fault_specs:
        return None

    thruster_ignition_failure_count = 0
    has_fuel_leak = False
    thruster_fault_count = 0
    tank_fault_count = 0

    for fault in fault_specs:
        if isinstance(fault.fault_type, ThrusterFaultType):
            thruster_fault_count += 1
            if fault.fault_type == ThrusterFaultType.FuelLeak:
                has_fuel_leak = True
            elif fault.fault_type == ThrusterFaultType.IgnitionFailure:
                thruster_ignition_failure_count += 1
        name = getattr(fault.fault_type, "value", str(fault.fault_type))
        if "leak" in name:
            has_fuel_leak = True
        if "tank" in name or "outlet" in name:
            tank_fault_count += 1

    if has_fuel_leak:
        return PropulsionFaultType.FuelTankLeak
    if thruster_ignition_failure_count >= 2:
        return PropulsionFaultType.ThrusterClusterFailure
    if thruster_fault_count > 0:
        return PropulsionFaultType.ThrusterClusterFailure
    if tank_fault_count > 0:
        return PropulsionFaultType.PropulsionSystemFailure
    return None



def apply_runtime_propulsion_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Route propulsion faults to thruster or fuel-tank component modules."""

    from components.thruster.faults import apply_runtime_thruster_fault
    from components.fuel_tank.faults import apply_runtime_fuel_tank_fault

    target_id = str(getattr(spec, "target_id", "") or "")
    target_lower = target_id.lower()
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if "fuel_tank" in target_lower or fault_value in {"rapid_leak", "pressure_loss", "overpressure", "valve_stuck"}:
        target = component_registry.get(target_id) or component_registry.get("fuel_tank")
        if target is None and resolve_component is not None:
            target = resolve_component(target_id or "fuel_tank", "fuel_tank")
        if target is None:
            return ()
        changed = apply_runtime_fuel_tank_fault(
            target,
            spec,
            set_attr=set_attr,
            record_mutation=record_mutation,
            register_post_restore=register_post_restore,
        )
        return ({"component": "fuel_tank", "target_id": target_id or "fuel_tank", "changed": changed},) if changed else ()

    target = component_registry.get(target_id) or component_registry.get("thruster")
    if target is None and resolve_component is not None:
        target = resolve_component(target_id or "thruster", "thruster")
    if target is None:
        return ()
    changed = apply_runtime_thruster_fault(
        target,
        spec,
        set_attr=set_attr,
        record_mutation=record_mutation,
        register_post_restore=register_post_restore,
    )
    return ({"component": "thruster", "target_id": target_id or "thruster", "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "PropulsionFaultType",
    "aggregate_propulsion_faults",
    "build_propulsion_direct_fault_specs",
    "default_fault_scenarios",
    "build_fault_event_specs",
    "register_fault_events",
]
