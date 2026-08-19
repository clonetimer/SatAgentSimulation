"""Subsystem-level degradation base contracts and update helpers.

This module is the canonical root contract for subsystem degradation scenarios.
Subsystem packages compose component-local degradation mechanisms into these
records; runtime application remains in ``runtime_injection.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ComponentDegradationBinding:
    """A component-local degradation bound to a subsystem target and Basilisk path."""

    component: str
    degradation: Any
    target_id: str
    basilisk_mapping: Mapping[str, Any]


@dataclass(frozen=True)
class SubsystemDegradationScenario:
    """Degradation scenario assembled from component-local time functions."""

    name: str
    component_degradations: tuple[ComponentDegradationBinding, ...]
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


def bind_degradation(component: str, degradation: Any, *, target_id: str, mapping: Mapping[str, Any]) -> ComponentDegradationBinding:
    """Create a degradation binding."""

    return ComponentDegradationBinding(
        component=component,
        degradation=degradation,
        target_id=target_id,
        basilisk_mapping=dict(mapping),
    )


def mechanism_name(mechanism: Any) -> str:
    return str(getattr(mechanism, "name", type(mechanism).__name__))


def mechanism_type(mechanism: Any) -> str:
    return str(getattr(mechanism, "fault_type", getattr(mechanism, "degradation_type", mechanism_name(mechanism))))


def mechanism_start_s(mechanism: Any) -> float:
    return float(getattr(mechanism, "start_s", getattr(mechanism, "onset_time_s", 0.0)))


def build_degradation_update_specs(
    scenario: SubsystemDegradationScenario,
    *,
    update_period_s: float = 10.0,
) -> tuple[dict[str, Any], ...]:
    """Convert a subsystem degradation scenario to periodic update metadata."""

    specs: list[dict[str, Any]] = []
    for binding in scenario.component_degradations:
        degradation = binding.degradation
        name = mechanism_name(degradation)
        specs.append(
            {
                "scenario": scenario.name,
                "event_name": f"{scenario.name}.{binding.component}.{name}.update",
                "component": binding.component,
                "target_id": binding.target_id,
                "degradation_name": name,
                "degradation_type": mechanism_type(degradation),
                "start_s": mechanism_start_s(degradation),
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
    """Attach degradation update metadata to a Basilisk context base-parameter bag."""

    specs = build_degradation_update_specs(scenario, update_period_s=update_period_s)
    params = getattr(sim_context, "base_parameters", None)
    if isinstance(params, dict):
        params["degradation_update_specs"] = tuple(params.get("degradation_update_specs", ())) + specs
    return specs


def covered_degradation_components(scenarios: Mapping[str, SubsystemDegradationScenario]) -> tuple[str, ...]:
    return tuple(sorted({binding.component for scenario in scenarios.values() for binding in scenario.component_degradations}))


__all__ = [
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "default_items",
    "pick_default",
    "bind_degradation",
    "mechanism_name",
    "mechanism_type",
    "mechanism_start_s",
    "build_degradation_update_specs",
    "register_degradation_events",
    "covered_degradation_components",
]
