"""Payload subsystem fault aggregation.

Payload subsystem scenarios are composed from component-local mechanisms in
``payload``, ``payload_sensor``, ``onboard_storage``, and ``data_queue``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from components.payload import faults as _payload_faults
from components.payload_sensor import faults as _payload_sensor_faults
from components.onboard_storage import faults as _onboard_storage_faults
from components.data_queue import faults as _data_queue_faults
from components.fault_spec import FaultSpec
from components.payload.faults import PayloadFaultType


COMPONENT_COVERAGE: tuple[str, ...] = (
    "payload",
    "payload_sensor",
    "onboard_storage",
    "data_queue",
)


@dataclass(frozen=True)
class ComponentFaultBinding:
    """A component-local fault bound to a Payload subsystem target."""

    component: str
    fault: Any
    target_id: str
    basilisk_mapping: Mapping[str, Any]


@dataclass(frozen=True)
class SubsystemFaultScenario:
    """Fault scenario assembled from component-local fault mechanisms."""

    name: str
    component_faults: tuple[ComponentFaultBinding, ...]
    description: str


_COMPONENT_FAULT_MODULES = {
    "payload": _payload_faults,
    "payload_sensor": _payload_sensor_faults,
    "onboard_storage": _onboard_storage_faults,
    "data_queue": _data_queue_faults,
}


def _defaults(component: str) -> tuple[Any, ...]:
    module = _COMPONENT_FAULT_MODULES[component]
    default_fn = getattr(module, "default_faults", None)
    if default_fn is None:
        return ()
    return tuple(default_fn())


def _pick(component: str, index: int = 0) -> Any:
    faults = _defaults(component)
    if not faults:
        raise RuntimeError(f"No default faults available for component {component!r}")
    return faults[min(index, len(faults) - 1)]


def _fault_name(fault: Any) -> str:
    return str(getattr(fault, "name", type(fault).__name__))


def _fault_type(fault: Any) -> str:
    return str(getattr(fault, "fault_type", _fault_name(fault)))


def _start_s(fault: Any) -> float:
    return float(getattr(fault, "start_s", getattr(fault, "onset_time_s", 0.0)))


def _end_s(fault: Any) -> float | None:
    if hasattr(fault, "end_s"):
        return float(getattr(fault, "end_s"))
    onset = float(getattr(fault, "onset_time_s", 0.0))
    duration = float(getattr(fault, "duration_s", -1.0))
    return onset + duration if duration > 0.0 else None


def _binding(component: str, fault: Any, *, target_id: str, mapping: Mapping[str, Any]) -> ComponentFaultBinding:
    return ComponentFaultBinding(
        component=component,
        fault=fault,
        target_id=str(getattr(fault, "target_id", "") or target_id),
        basilisk_mapping=dict(mapping),
    )


def default_fault_scenarios() -> dict[str, SubsystemFaultScenario]:
    """Return representative Payload scenarios traceable to component faults."""

    payload_off = _binding(
        "payload",
        _pick("payload", 0),
        target_id="payload.instrument",
        mapping={"module": "instrument", "parameter": "instrument_enabled", "injection": "command_gate"},
    )
    sensor_dropout = _binding(
        "payload_sensor",
        _pick("payload_sensor", 0),
        target_id="payload_sensor.detector",
        mapping={"module": "payload_sensor", "parameter": "data_quality/responsivity", "injection": "message_adapter"},
    )
    storage_bad_block = _binding(
        "onboard_storage",
        _pick("onboard_storage", 0),
        target_id="onboard_storage.payload",
        mapping={"module": "simpleStorageUnit", "parameter": "storageCapacity", "injection": "module_parameter"},
    )
    queue_overflow = _binding(
        "data_queue",
        _pick("data_queue", 0),
        target_id="data_queue.payload",
        mapping={"module": "data_queue", "parameter": "write_acceptance", "injection": "message_adapter"},
    )

    return {
        "instrument_off": SubsystemFaultScenario(
            name="instrument_off",
            component_faults=(payload_off,),
            description="Payload instrument is inhibited or unavailable.",
        ),
        "sensor_dropout": SubsystemFaultScenario(
            name="sensor_dropout",
            component_faults=(sensor_dropout,),
            description="Payload sensor pixel/dropout fault reduces generated science quality.",
        ),
        "payload_storage_queue_fault": SubsystemFaultScenario(
            name="payload_storage_queue_fault",
            component_faults=(storage_bad_block, queue_overflow),
            description="Payload data queue and storage faults reduce buffered science data.",
        ),
        "combined_payload_fault": SubsystemFaultScenario(
            name="combined_payload_fault",
            component_faults=(payload_off, sensor_dropout, storage_bad_block, queue_overflow),
            description="Coverage scenario exercising every Payload component fault binding.",
        ),
    }


def build_fault_event_specs(scenario: SubsystemFaultScenario) -> tuple[dict[str, Any], ...]:
    """Convert a scenario into Basilisk-facing start/end event metadata."""

    specs: list[dict[str, Any]] = []
    for binding in scenario.component_faults:
        fault = binding.fault
        start_s = _start_s(fault)
        end_s = _end_s(fault)
        base = {
            "scenario": scenario.name,
            "component": binding.component,
            "target_id": binding.target_id,
            "fault_name": _fault_name(fault),
            "fault_type": _fault_type(fault),
            "severity": float(getattr(fault, "severity", getattr(fault, "magnitude", 1.0))),
            "basilisk_mapping": dict(binding.basilisk_mapping),
            "effects": tuple(getattr(fault, "effects", ())),
        }
        specs.append({**base, "event_name": f"{scenario.name}.{binding.component}.{_fault_name(fault)}.start", "phase": "start", "time_s": start_s})
        if end_s is not None:
            specs.append({**base, "event_name": f"{scenario.name}.{binding.component}.{_fault_name(fault)}.end", "phase": "end", "time_s": end_s})
    return tuple(specs)



def build_payload_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build Payload runtime FaultSpec values for whole-spacecraft injection.

    The returned specs target the whole-spacecraft MissionGate/instrument bridge,
    so the fault persists even though MissionGate updates nodeBaudRate each step.
    """

    if name == "instrument_off":
        return (
            FaultSpec(
                fault_type=PayloadFaultType.StuckOff,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="mission_gate",
            ),
        )
    if name == "instrument_degraded_rate":
        return (
            FaultSpec(
                fault_type=PayloadFaultType.ImageDegradation,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.50,
                target_id="mission_gate",
            ),
        )
    return ()

def register_fault_events(sim_context: Any, scenario: SubsystemFaultScenario) -> tuple[dict[str, Any], ...]:
    """Attach event metadata to a Basilisk simulation context."""

    specs = build_fault_event_specs(scenario)
    params = getattr(sim_context, "base_parameters", None)
    if isinstance(params, dict):
        params["fault_event_specs"] = tuple(params.get("fault_event_specs", ())) + specs
    return specs



def apply_runtime_payload_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Apply a payload fault through the payload subsystem boundary."""

    from components.payload.faults import apply_runtime_payload_instrument_fault

    target_id = str(getattr(spec, "target_id", "") or "")
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    changed = False
    gate = component_registry.get(target_id) or component_registry.get("mission_gate")
    if gate is not None and hasattr(gate, "payload_nominal_baud_bps"):
        if fault_value in {"instrument_off", "stuck_off", "power_loss"}:
            set_attr(gate, "payload_nominal_baud_bps", 0.0)
            changed = True
        elif fault_value in {"image_degradation", "saturation", "calibration_drift"}:
            factor = max(0.0, 1.0 - min(1.0, magnitude))
            set_attr(gate, "payload_nominal_baud_bps", float(getattr(gate, "payload_nominal_baud_bps")) * factor)
            changed = True
    instrument = component_registry.get("instrument") or component_registry.get("payload_instrument")
    if instrument is None and resolve_component is not None:
        instrument = resolve_component("instrument", "instrument")
    if instrument is not None:
        changed = apply_runtime_payload_instrument_fault(instrument, spec, set_attr=set_attr) or changed
    return ({"component": "payload", "target_id": target_id or "instrument", "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "default_fault_scenarios",
    "build_payload_direct_fault_specs",
    "build_fault_event_specs",
    "register_fault_events",
]
