"""Subsystem-level fault base contracts and event helpers.

This module is the canonical root contract for subsystem fault scenarios.
Subsystem packages compose component-local fault mechanisms into these records;
runtime application remains in ``runtime_injection.py`` and
``runtime_fault_router.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from components.fault_spec import FaultSpec


@dataclass(frozen=True)
class ComponentFaultBinding:
    """A component-local fault bound to a subsystem target and Basilisk path."""

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


def default_items(module: Any, function_name: str) -> tuple[Any, ...]:
    """Return component module defaults as a tuple."""

    default_fn = getattr(module, function_name, None)
    if default_fn is None:
        return ()
    return tuple(default_fn())


def pick_default(module: Any, function_name: str, component: str, index: int = 0) -> Any:
    """Pick a component-local default mechanism with a clear error on absence."""

    items = default_items(module, function_name)
    if not items:
        raise RuntimeError(f"No {function_name} available for component {component!r}")
    return items[min(index, len(items) - 1)]


def bind_fault(component: str, fault: Any, *, target_id: str, mapping: Mapping[str, Any]) -> ComponentFaultBinding:
    """Create a fault binding while preserving component-provided target IDs."""

    return ComponentFaultBinding(
        component=component,
        fault=fault,
        target_id=str(getattr(fault, "target_id", "") or target_id),
        basilisk_mapping=dict(mapping),
    )


def mechanism_name(mechanism: Any) -> str:
    return str(getattr(mechanism, "name", type(mechanism).__name__))


def mechanism_type(mechanism: Any) -> str:
    return str(getattr(mechanism, "fault_type", getattr(mechanism, "degradation_type", mechanism_name(mechanism))))


def mechanism_start_s(mechanism: Any) -> float:
    return float(getattr(mechanism, "start_s", getattr(mechanism, "onset_time_s", 0.0)))


def fault_end_s(fault: Any) -> float | None:
    if hasattr(fault, "end_s"):
        return float(getattr(fault, "end_s"))
    onset = float(getattr(fault, "onset_time_s", 0.0))
    duration = float(getattr(fault, "duration_s", -1.0))
    return onset + duration if duration > 0.0 else None


def build_fault_event_specs(scenario: SubsystemFaultScenario) -> tuple[dict[str, Any], ...]:
    """Convert a subsystem fault scenario to Basilisk-facing event metadata."""

    specs: list[dict[str, Any]] = []
    for binding in scenario.component_faults:
        fault = binding.fault
        start_s = mechanism_start_s(fault)
        end_s = fault_end_s(fault)
        name = mechanism_name(fault)
        base = {
            "scenario": scenario.name,
            "component": binding.component,
            "target_id": binding.target_id,
            "fault_name": name,
            "fault_type": mechanism_type(fault),
            "severity": float(getattr(fault, "severity", getattr(fault, "magnitude", 1.0))),
            "basilisk_mapping": dict(binding.basilisk_mapping),
            "effects": tuple(getattr(fault, "effects", ())),
            "fault": fault,
        }
        specs.append({**base, "event_name": f"{scenario.name}.{binding.component}.{name}.start", "phase": "start", "time_s": start_s})
        if end_s is not None:
            specs.append({**base, "event_name": f"{scenario.name}.{binding.component}.{name}.end", "phase": "end", "time_s": end_s})
    return tuple(specs)


def register_fault_events(sim_context: Any, scenario: SubsystemFaultScenario) -> tuple[dict[str, Any], ...]:
    """Attach event metadata to a Basilisk context base-parameter bag."""

    specs = build_fault_event_specs(scenario)
    params = getattr(sim_context, "base_parameters", None)
    if isinstance(params, dict):
        params["fault_event_specs"] = tuple(params.get("fault_event_specs", ())) + specs
    return specs


def covered_fault_components(scenarios: Mapping[str, SubsystemFaultScenario]) -> tuple[str, ...]:
    return tuple(sorted({binding.component for scenario in scenarios.values() for binding in scenario.component_faults}))


__all__ = [
    "FaultSpec",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "default_items",
    "pick_default",
    "bind_fault",
    "mechanism_name",
    "mechanism_type",
    "mechanism_start_s",
    "fault_end_s",
    "build_fault_event_specs",
    "register_fault_events",
    "covered_fault_components",
]
