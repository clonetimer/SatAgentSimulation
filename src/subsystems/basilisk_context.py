"""Common Basilisk simulation context protocol for subsystem builders.

The concrete subsystem builders remain responsible for constructing subsystem-
specific module graphs, but they should return objects that expose this shared
set of attributes.  This keeps runners, fault/degradation injectors, and smoke
checks from depending on ad-hoc per-subsystem containers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class BasiliskSimContextProtocol(Protocol):
    """Structural protocol implemented by all subsystem Basilisk contexts."""

    subsystem: str
    config: Any
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: Mapping[str, Any]
    recorders: Mapping[str, Any]
    message_handles: Mapping[str, Any]
    base_parameters: Mapping[str, Any]
    component_sources: Mapping[str, str]


@dataclass(frozen=True)
class BasiliskContextSnapshot:
    """Serializable summary of a built Basilisk context."""

    subsystem: str
    task_name: str
    module_names: tuple[str, ...] = ()
    recorder_names: tuple[str, ...] = ()
    message_names: tuple[str, ...] = ()
    component_sources: Mapping[str, str] = field(default_factory=dict)
    base_parameter_names: tuple[str, ...] = ()


def validate_basilisk_sim_context(
    context: Any,
    *,
    required_components: tuple[str, ...] = (),
) -> BasiliskContextSnapshot:
    """Validate and summarize the common subsystem Basilisk builder protocol.

    This check is intentionally structural: concrete subsystem context classes may
    be dataclasses with additional subsystem-specific fields, but they must expose
    the shared attributes used by runners and event injection code.
    """

    required_attrs = (
        "subsystem",
        "config",
        "simulation",
        "process",
        "task",
        "task_name",
        "modules",
        "recorders",
        "message_handles",
        "base_parameters",
        "component_sources",
    )
    missing = [name for name in required_attrs if not hasattr(context, name)]
    if missing:
        raise AssertionError(f"Basilisk context is missing required attributes: {missing}")

    for map_name in ("modules", "recorders", "message_handles", "base_parameters", "component_sources"):
        value = getattr(context, map_name)
        if not isinstance(value, Mapping):
            raise AssertionError(f"Basilisk context field {map_name!r} must be a Mapping, got {type(value)!r}")

    component_sources = dict(getattr(context, "component_sources"))
    missing_components = [name for name in required_components if name not in component_sources]
    if missing_components:
        raise AssertionError(
            f"Basilisk context for {getattr(context, 'subsystem', '<unknown>')} does not source required components: "
            f"{missing_components}"
        )

    return BasiliskContextSnapshot(
        subsystem=str(getattr(context, "subsystem")),
        task_name=str(getattr(context, "task_name")),
        module_names=tuple(str(k) for k in getattr(context, "modules").keys()),
        recorder_names=tuple(str(k) for k in getattr(context, "recorders").keys()),
        message_names=tuple(str(k) for k in getattr(context, "message_handles").keys()),
        component_sources=component_sources,
        base_parameter_names=tuple(str(k) for k in getattr(context, "base_parameters").keys()),
    )
