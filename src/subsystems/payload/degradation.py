"""Payload subsystem degradation aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from components.payload import degradation as _payload_degradation
from components.payload_sensor import degradation as _payload_sensor_degradation
from components.onboard_storage import degradation as _onboard_storage_degradation
from components.data_queue import degradation as _data_queue_degradation


COMPONENT_COVERAGE: tuple[str, ...] = (
    "payload",
    "payload_sensor",
    "onboard_storage",
    "data_queue",
)


@dataclass(frozen=True)
class ComponentDegradationBinding:
    """A component-local degradation bound to a Payload subsystem target."""

    component: str
    degradation: Any
    target_id: str
    basilisk_mapping: Mapping[str, Any]


@dataclass(frozen=True)
class SubsystemDegradationScenario:
    """Degradation scenario assembled from component-local mechanisms."""

    name: str
    component_degradations: tuple[ComponentDegradationBinding, ...]
    description: str


_COMPONENT_DEGRADATION_MODULES = {
    "payload": _payload_degradation,
    "payload_sensor": _payload_sensor_degradation,
    "onboard_storage": _onboard_storage_degradation,
    "data_queue": _data_queue_degradation,
}


def _defaults(component: str) -> tuple[Any, ...]:
    module = _COMPONENT_DEGRADATION_MODULES[component]
    default_fn = getattr(module, "default_degradations", None)
    if default_fn is None:
        return ()
    return tuple(default_fn())


def _pick(component: str, index: int = 0) -> Any:
    degradations = _defaults(component)
    if not degradations:
        raise RuntimeError(f"No default degradations available for component {component!r}")
    return degradations[min(index, len(degradations) - 1)]


def _degradation_name(degradation: Any) -> str:
    return str(getattr(degradation, "name", type(degradation).__name__))


def _degradation_type(degradation: Any) -> str:
    return str(getattr(degradation, "degradation_type", _degradation_name(degradation)))


def _start_s(degradation: Any) -> float:
    return float(getattr(degradation, "start_s", 0.0))


def _binding(component: str, degradation: Any, *, target_id: str, mapping: Mapping[str, Any]) -> ComponentDegradationBinding:
    return ComponentDegradationBinding(
        component=component,
        degradation=degradation,
        target_id=target_id,
        basilisk_mapping=dict(mapping),
    )


def default_degradation_scenarios() -> dict[str, SubsystemDegradationScenario]:
    """Return representative Payload degradation scenarios."""

    payload_sensitivity = _binding(
        "payload",
        _pick("payload", 0),
        target_id="payload.instrument",
        mapping={"module": "instrument", "parameter": "sensitivity/data_rate", "injection": "periodic_command_gate"},
    )
    sensor_responsivity = _binding(
        "payload_sensor",
        _pick("payload_sensor", 0),
        target_id="payload_sensor.detector",
        mapping={"module": "payload_sensor", "parameter": "responsivity/noise", "injection": "periodic_message_adapter"},
    )
    storage_capacity = _binding(
        "onboard_storage",
        _pick("onboard_storage", 0),
        target_id="onboard_storage.payload",
        mapping={"module": "simpleStorageUnit", "parameter": "storageCapacity", "injection": "periodic_module_parameter"},
    )
    queue_throughput = _binding(
        "data_queue",
        _pick("data_queue", 0),
        target_id="data_queue.payload",
        mapping={"module": "data_queue", "parameter": "throughput/error_rate", "injection": "periodic_message_adapter"},
    )

    return {
        "instrument_sensor_aging": SubsystemDegradationScenario(
            name="instrument_sensor_aging",
            component_degradations=(payload_sensitivity, sensor_responsivity),
            description="Payload instrument sensitivity and detector responsivity degrade science quality.",
        ),
        "payload_storage_aging": SubsystemDegradationScenario(
            name="payload_storage_aging",
            component_degradations=(storage_capacity, queue_throughput),
            description="Payload data queue and onboard storage degrade buffering capability.",
        ),
        "combined_payload_aging": SubsystemDegradationScenario(
            name="combined_payload_aging",
            component_degradations=(payload_sensitivity, sensor_responsivity, storage_capacity, queue_throughput),
            description="Coverage scenario exercising every Payload component degradation binding.",
        ),
    }



@dataclass(frozen=True)
class PayloadRuntimeDegradation:
    """Build/runtime parameters exposed by Payload for whole-spacecraft assembly.

    These are subsystem-level effects.  The whole-spacecraft builder consumes
    them without importing payload component degradation classes directly.
    """

    instrument_baud_factor: float = 1.0
    storage_capacity_factor: float = 1.0
    source_scenario: str = "manual"


def build_payload_degradation(
    *,
    instrument_baud_factor: float = 1.0,
    storage_capacity_factor: float = 1.0,
    source_scenario: str = "manual",
) -> PayloadRuntimeDegradation:
    """Return compact Payload degradation effects for integrated builders."""

    return PayloadRuntimeDegradation(
        instrument_baud_factor=max(0.0, float(instrument_baud_factor)),
        storage_capacity_factor=max(0.0, float(storage_capacity_factor)),
        source_scenario=str(source_scenario),
    )


def build_payload_degradation_from_scenario(scenario: SubsystemDegradationScenario) -> PayloadRuntimeDegradation:
    """Map Payload subsystem degradation metadata to supported build-time knobs.

    Only effects backed by current whole-spacecraft builder parameters are
    claimed: payload instrument baud and shared science-storage capacity.
    Sensor/data-quality effects remain metadata until a real sensor-quality
    consumer exists in the Basilisk graph.
    """

    instrument_factor = 1.0
    storage_factor = 1.0
    for binding in scenario.component_degradations:
        component = str(binding.component)
        dtype = _degradation_type(binding.degradation)
        if component in {"payload", "payload_sensor"} or "sensitivity" in dtype or "responsivity" in dtype:
            instrument_factor = min(instrument_factor, 0.75)
        if component in {"onboard_storage", "data_queue"} or "capacity" in dtype:
            storage_factor = min(storage_factor, 0.80)
    return build_payload_degradation(
        instrument_baud_factor=instrument_factor,
        storage_capacity_factor=storage_factor,
        source_scenario=scenario.name,
    )

def build_degradation_update_specs(
    scenario: SubsystemDegradationScenario,
    *,
    update_period_s: float = 10.0,
) -> tuple[dict[str, Any], ...]:
    """Convert a scenario into Basilisk-facing periodic update metadata."""

    specs: list[dict[str, Any]] = []
    for binding in scenario.component_degradations:
        degradation = binding.degradation
        specs.append(
            {
                "scenario": scenario.name,
                "event_name": f"{scenario.name}.{binding.component}.{_degradation_name(degradation)}.update",
                "component": binding.component,
                "target_id": binding.target_id,
                "degradation_name": _degradation_name(degradation),
                "degradation_type": _degradation_type(degradation),
                "start_s": _start_s(degradation),
                "update_period_s": float(update_period_s),
                "coefficient_method": "coefficient(t_s)",
                "basilisk_mapping": dict(binding.basilisk_mapping),
                "effects": tuple(getattr(degradation, "effects", ())),
                "degradation": degradation,
            }
        )
    return tuple(specs)


def register_degradation_events(
    sim_context: Any,
    scenario: SubsystemDegradationScenario,
    *,
    update_period_s: float = 10.0,
) -> tuple[dict[str, Any], ...]:
    """Attach periodic degradation update metadata to a Basilisk context."""

    specs = build_degradation_update_specs(scenario, update_period_s=update_period_s)
    params = getattr(sim_context, "base_parameters", None)
    if isinstance(params, dict):
        params["degradation_update_specs"] = tuple(params.get("degradation_update_specs", ())) + specs
    return specs


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "default_degradation_scenarios",
    "PayloadRuntimeDegradation",
    "build_payload_degradation",
    "build_payload_degradation_from_scenario",
    "build_degradation_update_specs",
    "register_degradation_events",
]
