"""EPS subsystem fault aggregation.

EPS fault scenarios aggregate component-local faults from battery, solar panel,
PDU, and power sink modules.  Component mechanisms remain the canonical source;
this module binds them to EPS-level Basilisk targets and event metadata.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from components.battery import faults as _battery_faults
from components.solar_panel import faults as _solar_panel_faults
from components.pdu import faults as _pdu_faults
from components.power_sink import faults as _power_sink_faults
from components.battery.faults import BatteryFaultType
from components.solar_panel.faults import SolarPanelFaultType
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
    "battery",
    "solar_panel",
    "pdu",
    "power_sink",
)


class EPSFaultType(Enum):
    PowerBusFailure = "power_bus_failure"
    BatteryFailure = "battery_failure"
    SolarPanelFailure = "solar_panel_failure"
    PDUFailure = "pdu_failure"
    PowerSinkOverload = "power_sink_overload"

    @property
    def severity_level(self) -> int:
        severity_mapping = {
            EPSFaultType.PowerBusFailure: 3,
            EPSFaultType.BatteryFailure: 2,
            EPSFaultType.SolarPanelFailure: 2,
            EPSFaultType.PDUFailure: 2,
            EPSFaultType.PowerSinkOverload: 2,
        }
        return severity_mapping[self]


_COMPONENT_FAULT_MODULES = {
    "battery": _battery_faults,
    "solar_panel": _solar_panel_faults,
    "pdu": _pdu_faults,
    "power_sink": _power_sink_faults,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_FAULT_MODULES[component], "default_faults", component, index)


def default_fault_scenarios() -> dict[str, SubsystemFaultScenario]:
    """Return representative EPS scenarios traceable to component faults."""

    battery_open = bind_fault(
        "battery",
        _pick("battery", 0),
        target_id="battery.main",
        mapping={"module": "simpleBattery", "parameter": "storageCapacity/storageLevel", "injection": "module_parameter"},
    )
    solar_string = bind_fault(
        "solar_panel",
        _pick("solar_panel", 0),
        target_id="solar_panel.array",
        mapping={"module": "simpleSolarPanel_or_fallback_source", "parameter": "netPower/efficiency", "injection": "module_parameter"},
    )
    pdu_trip = bind_fault(
        "pdu",
        _pick("pdu", 0),
        target_id="pdu.payload_channel",
        mapping={"module": "pdu", "parameter": "channel_enabled/current_limit", "injection": "command_gate"},
    )
    load_overload = bind_fault(
        "power_sink",
        _pick("power_sink", 0),
        target_id="power_sink.payload",
        mapping={"module": "simplePowerSink", "parameter": "nodePowerOut/load_demand", "injection": "module_parameter"},
    )

    return {
        "battery_capacity_loss": SubsystemFaultScenario(
            name="battery_capacity_loss",
            component_faults=(battery_open,),
            description="Battery fault reduces usable stored energy or disconnects the battery path.",
        ),
        "solar_string_loss": SubsystemFaultScenario(
            name="solar_string_loss",
            component_faults=(solar_string,),
            description="Solar panel string/open fault reduces generated power.",
        ),
        "pdu_channel_trip": SubsystemFaultScenario(
            name="pdu_channel_trip",
            component_faults=(pdu_trip,),
            description="PDU channel trip gates a downstream load.",
        ),
        "power_sink_overload": SubsystemFaultScenario(
            name="power_sink_overload",
            component_faults=(load_overload,),
            description="Downstream power sink overload stresses EPS load shedding.",
        ),
        "combined_eps_fault": SubsystemFaultScenario(
            name="combined_eps_fault",
            component_faults=(battery_open, solar_string, pdu_trip, load_overload),
            description="Coverage scenario exercising every EPS component fault binding.",
        ),
    }


def build_eps_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build EPS runtime FaultSpec values for whole-spacecraft direct injection.

    The enum classes remain component-local, but higher layers obtain them via
    this EPS subsystem API rather than importing battery component modules.
    """

    if name == "battery_capacity_loss":
        return (
            FaultSpec(
                fault_type=BatteryFaultType.SuddenCapacityLoss,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.30,
                target_id="battery",
            ),
        )
    if name == "battery_open_circuit":
        return (
            FaultSpec(
                fault_type=BatteryFaultType.OpenCircuit,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="battery",
            ),
        )
    return ()


def aggregate_eps_faults(fault_specs: List[FaultSpec]) -> Optional[EPSFaultType]:
    """Legacy aggregator retained for existing fault_campaign callers."""

    if not fault_specs:
        return None

    has_battery_open_circuit = False
    has_solar_panel_failure = False
    has_battery_failure = False
    has_solar_panel_fault = False
    has_pdu_fault = False
    has_power_sink_fault = False

    for fault in fault_specs:
        if isinstance(fault.fault_type, BatteryFaultType):
            has_battery_failure = True
            if fault.fault_type == BatteryFaultType.OpenCircuit:
                has_battery_open_circuit = True
        if isinstance(fault.fault_type, SolarPanelFaultType):
            has_solar_panel_fault = True
            if fault.fault_type == SolarPanelFaultType.Failure:
                has_solar_panel_failure = True
        name = getattr(fault.fault_type, "value", str(fault.fault_type))
        if "pdu" in name or "channel" in name or "over_current" in name:
            has_pdu_fault = True
        if "power_sink" in name or "overload" in name or "load" in name:
            has_power_sink_fault = True

    if has_battery_open_circuit and has_solar_panel_failure:
        return EPSFaultType.PowerBusFailure
    if has_battery_failure:
        return EPSFaultType.BatteryFailure
    if has_solar_panel_fault:
        return EPSFaultType.SolarPanelFailure
    if has_pdu_fault:
        return EPSFaultType.PDUFailure
    if has_power_sink_fault:
        return EPSFaultType.PowerSinkOverload
    return None



def apply_runtime_eps_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Route an EPS runtime fault to the owning component implementation."""

    from components.battery.faults import apply_runtime_battery_fault

    target_id = str(getattr(spec, "target_id", "") or "")
    battery = component_registry.get(target_id) or component_registry.get("battery")
    if battery is None and resolve_component is not None:
        battery = resolve_component(target_id or "battery", "battery")
    if battery is None:
        return ()
    changed = apply_runtime_battery_fault(battery, spec, set_attr=set_attr)
    return ({"component": "battery", "target_id": target_id or "battery", "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "EPSFaultType",
    "aggregate_eps_faults",
    "build_eps_direct_fault_specs",
    "default_fault_scenarios",
    "build_fault_event_specs",
    "register_fault_events",
]
