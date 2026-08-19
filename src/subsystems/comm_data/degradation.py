"""Comm/Data subsystem degradation aggregation.

The subsystem composes component-local time-function degradations and exposes
Basilisk-facing update metadata for later runtime event registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from components.antenna import degradation as _antenna_degradation
from components.transmitter import degradation as _transmitter_degradation
from components.link_budget import degradation as _link_budget_degradation
from components.ground_station import degradation as _ground_station_degradation
from components.data_queue import degradation as _data_queue_degradation
from components.onboard_storage import degradation as _onboard_storage_degradation


COMPONENT_COVERAGE: tuple[str, ...] = (
    "antenna",
    "transmitter",
    "link_budget",
    "ground_station",
    "data_queue",
    "onboard_storage",
)


@dataclass(frozen=True)
class ComponentDegradationBinding:
    """A component-local degradation bound to a Comm/Data subsystem target."""

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
    "antenna": _antenna_degradation,
    "transmitter": _transmitter_degradation,
    "link_budget": _link_budget_degradation,
    "ground_station": _ground_station_degradation,
    "data_queue": _data_queue_degradation,
    "onboard_storage": _onboard_storage_degradation,
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
    """Return representative Comm/Data degradation scenarios."""

    antenna_surface = _binding(
        "antenna",
        _pick("antenna", 0),
        target_id="antenna.primary",
        mapping={"module": "antenna", "parameter": "gain_or_path_factor", "injection": "periodic_message_adapter"},
    )
    transmitter_power = _binding(
        "transmitter",
        _pick("transmitter", 0),
        target_id="transmitter.primary",
        mapping={"module": "simpleTransmitter", "parameter": "baudRate/outputPower", "injection": "periodic_module_parameter"},
    )
    link_margin = _binding(
        "link_budget",
        _pick("link_budget", 0),
        target_id="link_budget.downlink",
        mapping={"module": "link_budget", "parameter": "link_margin_db", "injection": "periodic_message_adapter"},
    )
    ground_tracking = _binding(
        "ground_station",
        _pick("ground_station", 1),
        target_id="ground_station.primary",
        mapping={"module": "ground_station", "parameter": "tracking_accuracy", "injection": "periodic_command_gate"},
    )
    queue_throughput = _binding(
        "data_queue",
        _pick("data_queue", 0),
        target_id="data_queue.science",
        mapping={"module": "simpleStorageUnit", "parameter": "write_read_throughput", "injection": "periodic_message_adapter"},
    )
    storage_capacity = _binding(
        "onboard_storage",
        _pick("onboard_storage", 0),
        target_id="onboard_storage.science",
        mapping={"module": "simpleStorageUnit", "parameter": "storageCapacity", "injection": "periodic_module_parameter"},
    )

    return {
        "rf_path_aging": SubsystemDegradationScenario(
            name="rf_path_aging",
            component_degradations=(antenna_surface, transmitter_power, link_margin, ground_tracking),
            description="Aging in antenna, transmitter, link budget, and ground tracking reduces downlink capability.",
        ),
        "storage_path_aging": SubsystemDegradationScenario(
            name="storage_path_aging",
            component_degradations=(queue_throughput, storage_capacity),
            description="Queue throughput decay and storage capacity loss reduce data retention.",
        ),
        "combined_comm_data_aging": SubsystemDegradationScenario(
            name="combined_comm_data_aging",
            component_degradations=(antenna_surface, transmitter_power, link_margin, ground_tracking, queue_throughput, storage_capacity),
            description="Coverage scenario exercising every Comm/Data component degradation binding.",
        ),
    }



@dataclass(frozen=True)
class CommDataRuntimeDegradation:
    """Build/runtime parameters exposed by Comm/Data for whole-spacecraft assembly."""

    transmitter_baud_factor: float = 1.0
    storage_capacity_factor: float = 1.0
    source_scenario: str = "manual"


def build_comm_data_degradation(
    *,
    transmitter_baud_factor: float = 1.0,
    storage_capacity_factor: float = 1.0,
    source_scenario: str = "manual",
) -> CommDataRuntimeDegradation:
    """Return compact Comm/Data degradation effects for integrated builders."""

    return CommDataRuntimeDegradation(
        transmitter_baud_factor=max(0.0, float(transmitter_baud_factor)),
        storage_capacity_factor=max(0.0, float(storage_capacity_factor)),
        source_scenario=str(source_scenario),
    )


def build_comm_data_degradation_from_scenario(scenario: SubsystemDegradationScenario) -> CommDataRuntimeDegradation:
    """Map Comm/Data degradation metadata to supported build-time knobs.

    RF/link/ground tracking degradation is represented by transmitter baud
    reduction.  Storage/queue degradation is represented by storage capacity
    reduction.  BER and RF-margin internals remain metadata until the graph has
    native consumers for those quantities.
    """

    tx_factor = 1.0
    storage_factor = 1.0
    for binding in scenario.component_degradations:
        component = str(binding.component)
        dtype = _degradation_type(binding.degradation)
        if component in {"antenna", "transmitter", "link_budget", "ground_station"} or any(token in dtype for token in ("power", "margin", "tracking", "gain")):
            tx_factor = min(tx_factor, 0.65)
        if component in {"onboard_storage", "data_queue"} or "capacity" in dtype or "throughput" in dtype:
            storage_factor = min(storage_factor, 0.80)
    return build_comm_data_degradation(
        transmitter_baud_factor=tx_factor,
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
    "CommDataRuntimeDegradation",
    "build_comm_data_degradation",
    "build_comm_data_degradation_from_scenario",
    "build_degradation_update_specs",
    "register_degradation_events",
]
