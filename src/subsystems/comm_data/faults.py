"""Comm/Data subsystem fault aggregation.

The subsystem owns scenario composition and Basilisk-facing event metadata.
Component-level mechanisms remain in ``src/components/<component>/faults.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from components.antenna import faults as _antenna_faults
from components.transmitter import faults as _transmitter_faults
from components.link_budget import faults as _link_budget_faults
from components.ground_station import faults as _ground_station_faults
from components.data_queue import faults as _data_queue_faults
from components.onboard_storage import faults as _onboard_storage_faults
from components.fault_spec import FaultSpec
from components.transmitter.faults import TransmitterFaultType
from components.onboard_storage.faults import OnboardStorageFaultType


COMPONENT_COVERAGE: tuple[str, ...] = (
    "antenna",
    "transmitter",
    "link_budget",
    "ground_station",
    "data_queue",
    "onboard_storage",
)


@dataclass(frozen=True)
class ComponentFaultBinding:
    """A component-local fault bound to a Comm/Data subsystem target."""

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
    "antenna": _antenna_faults,
    "transmitter": _transmitter_faults,
    "link_budget": _link_budget_faults,
    "ground_station": _ground_station_faults,
    "data_queue": _data_queue_faults,
    "onboard_storage": _onboard_storage_faults,
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
    """Return representative Comm/Data scenarios traceable to component faults."""

    antenna_pointing = _binding(
        "antenna",
        _pick("antenna", 0),
        target_id="antenna.primary",
        mapping={"module": "antenna", "parameter": "gain_or_path_factor", "injection": "message_adapter"},
    )
    transmitter_loss = _binding(
        "transmitter",
        _pick("transmitter", 0),
        target_id="transmitter.primary",
        mapping={"module": "simpleTransmitter", "parameter": "baudRate/outputPower", "injection": "module_parameter"},
    )
    link_fade = _binding(
        "link_budget",
        _pick("link_budget", 1),
        target_id="link_budget.downlink",
        mapping={"module": "link_budget", "parameter": "link_margin_db", "injection": "message_adapter"},
    )
    ground_loss = _binding(
        "ground_station",
        _pick("ground_station", 0),
        target_id="ground_station.primary",
        mapping={"module": "ground_station", "parameter": "contact_available", "injection": "command_gate"},
    )
    queue_overflow = _binding(
        "data_queue",
        _pick("data_queue", 0),
        target_id="data_queue.science",
        mapping={"module": "simpleStorageUnit", "parameter": "write_acceptance", "injection": "message_adapter"},
    )
    storage_bad_block = _binding(
        "onboard_storage",
        _pick("onboard_storage", 0),
        target_id="onboard_storage.science",
        mapping={"module": "simpleStorageUnit", "parameter": "storageCapacity", "injection": "module_parameter"},
    )

    return {
        "antenna_pointing_loss": SubsystemFaultScenario(
            name="antenna_pointing_loss",
            component_faults=(antenna_pointing,),
            description="Antenna boresight loss reduces downlink path quality.",
        ),
        "transmitter_power_loss": SubsystemFaultScenario(
            name="transmitter_power_loss",
            component_faults=(transmitter_loss,),
            description="Transmitter power-amplifier fault reduces available downlink rate.",
        ),
        "link_fade_ground_loss": SubsystemFaultScenario(
            name="link_fade_ground_loss",
            component_faults=(link_fade, ground_loss),
            description="RF link fade and ground tracking loss gate downlink contact.",
        ),
        "storage_queue_fault": SubsystemFaultScenario(
            name="storage_queue_fault",
            component_faults=(queue_overflow, storage_bad_block),
            description="Queue overflow plus storage bad blocks reduce data retention capability.",
        ),
        "combined_downlink_storage_fault": SubsystemFaultScenario(
            name="combined_downlink_storage_fault",
            component_faults=(antenna_pointing, transmitter_loss, link_fade, ground_loss, queue_overflow, storage_bad_block),
            description="Coverage scenario exercising every Comm/Data component fault binding.",
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



def build_comm_data_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build Comm/Data runtime FaultSpec values for whole-spacecraft injection.

    Transmitter rate faults target MissionGate so scheduled gate updates do not
    immediately overwrite the injected effect.  Storage capacity faults target
    the Basilisk storage unit directly.
    """

    if name == "downlink_link_loss":
        return (
            FaultSpec(
                fault_type=TransmitterFaultType.SignalLoss,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=1.0,
                target_id="mission_gate",
            ),
        )
    if name == "transmitter_rate_loss":
        return (
            FaultSpec(
                fault_type=TransmitterFaultType.PowerAmplifierFailure,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.50,
                target_id="mission_gate",
            ),
        )
    if name == "storage_capacity_loss":
        return (
            FaultSpec(
                fault_type=OnboardStorageFaultType.CapacityLoss,
                onset_time_s=0.0,
                duration_s=-1.0,
                magnitude=0.40,
                target_id="storage",
            ),
        )
    return ()

def register_fault_events(sim_context: Any, scenario: SubsystemFaultScenario) -> tuple[dict[str, Any], ...]:
    """Attach event metadata to a Basilisk simulation context.

    Batch STRUCT-1 records the mapping without assuming a project-wide event API.
    The later runtime batch can consume ``base_parameters['fault_event_specs']``
    to call ``CreateNewEvent`` or message/module adapters in one place.
    """

    specs = build_fault_event_specs(scenario)
    params = getattr(sim_context, "base_parameters", None)
    if isinstance(params, dict):
        params["fault_event_specs"] = tuple(params.get("fault_event_specs", ())) + specs
    return specs



def apply_runtime_comm_data_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Apply Comm/Data faults through transmitter/storage component APIs."""

    from components.transmitter.faults import apply_runtime_transmitter_fault
    from components.data_queue.faults import apply_runtime_storage_fault

    target_id = str(getattr(spec, "target_id", "") or "")
    target_lower = target_id.lower()
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    if "storage" in target_lower or fault_value in {"capacity_loss", "bad_block", "queue_overflow", "read_only"}:
        storage = component_registry.get(target_id) or component_registry.get("storage") or component_registry.get("comm_storage")
        if storage is None and resolve_component is not None:
            storage = resolve_component(target_id or "storage", "storage")
        if storage is None:
            return ()
        changed = apply_runtime_storage_fault(storage, spec, set_attr=set_attr)
        return ({"component": "storage", "target_id": target_id or "storage", "changed": changed},) if changed else ()

    changed = False
    gate = component_registry.get(target_id) or component_registry.get("mission_gate")
    magnitude = max(0.0, float(getattr(spec, "magnitude", 0.0)))
    original_native_rate = None
    if gate is not None and hasattr(gate, "transmitter_nominal_baud_bps"):
        if fault_value in {"link_loss", "signal_loss", "power_loss"}:
            # The native DownlinkHandling path owns the actual storage drain.
            # Mutate the gate command, its CNR floor, and the native module's
            # requested bit rate so no parallel path can silently bypass the
            # injected outage.
            set_attr(gate, "transmitter_nominal_baud_bps", 0.0)
            if hasattr(gate, "downlink_bit_rate_request_bps"):
                original_native_rate = float(getattr(gate, "downlink_bit_rate_request_bps"))
                set_attr(gate, "downlink_bit_rate_request_bps", 0.0)
            if hasattr(gate, "downlink_cnr_linear"):
                set_attr(gate, "downlink_cnr_linear", 0.0)
            if hasattr(gate, "native_link_budget_cnr_floor_linear"):
                set_attr(gate, "native_link_budget_cnr_floor_linear", 0.0)
            changed = True
        elif fault_value == "power_amplifier_failure":
            scale = max(0.0, 1.0 - magnitude)
            set_attr(gate, "transmitter_nominal_baud_bps", float(getattr(gate, "transmitter_nominal_baud_bps")) * scale)
            if hasattr(gate, "downlink_bit_rate_request_bps"):
                original_native_rate = float(getattr(gate, "downlink_bit_rate_request_bps"))
                set_attr(gate, "downlink_bit_rate_request_bps", original_native_rate * scale)
            changed = True

    native_downlink = component_registry.get("native_downlink")
    if native_downlink is not None and hasattr(native_downlink, "setBitRateRequest") and original_native_rate is not None:
        requested_rate = 0.0 if fault_value in {"link_loss", "signal_loss", "power_loss"} else original_native_rate * max(0.0, 1.0 - magnitude)
        native_downlink.setBitRateRequest(float(requested_rate))
        if record_mutation is not None:
            record_mutation(native_downlink, "bit_rate_request_bps", original_native_rate, requested_rate)
        if register_post_restore is not None:
            register_post_restore(lambda module=native_downlink, rate=original_native_rate: module.setBitRateRequest(float(rate)))
        changed = True
    transmitter = component_registry.get("transmitter")
    if transmitter is None and resolve_component is not None:
        transmitter = resolve_component(target_id or "transmitter", "transmitter")
    if transmitter is not None:
        changed = apply_runtime_transmitter_fault(transmitter, spec, set_attr=set_attr) or changed
    return ({"component": "comm_data", "target_id": target_id or "transmitter", "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "default_fault_scenarios",
    "build_comm_data_direct_fault_specs",
    "build_fault_event_specs",
    "register_fault_events",
]
