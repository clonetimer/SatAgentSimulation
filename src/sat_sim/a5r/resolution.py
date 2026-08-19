"""Resolve focused subsystem TaskSpecs into governed A5R bound model graphs."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, Callable

from sat_sim_kernel import KernelValidationError, LocalId
from sat_sim_model_assets import BoundModelGraph
from sat_sim_model_assets.subsystem_verticals import (
    SubsystemAssetBundle,
    bundle_for_adapter_key,
)

from ..task_models import to_runtime_task_spec


@dataclass(frozen=True)
class A5Resolution:
    bound_graph: BoundModelGraph
    runtime_task_spec: dict[str, Any]
    diagnostics: tuple[dict[str, Any], ...] = ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _has_events(runtime: Mapping[str, Any]) -> bool:
    if runtime.get("faults"):
        return True
    if _mapping(runtime.get("degradations")):
        return True
    modifiers = _mapping(runtime.get("modifiers"))
    return any(bool(modifiers.get(key)) for key in ("faults", "degradations", "constraints"))


def _resolved_registry_effects(
    bundle: SubsystemAssetBundle,
    runtime: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ..capability_registry import get_capability

    contract = get_capability(bundle.capability_id)
    modes = _mapping(contract.data.get("modes"))
    modifiers = _mapping(runtime.get("modifiers"))
    effects: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    categories = (
        ("fault", "faults", "fault_type"),
        ("degradation", "degradations", "degradation_type"),
        ("constraint", "constraints", "constraint_type"),
    )
    for kind, plural, type_key in categories:
        mode = _mapping(modes.get(kind))
        allowed = {
            str(value)
            for value in (mode.get("effects") or mode.get(f"{kind}_types") or ())
        }
        rows = modifiers.get(plural)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            continue
        for index, item in enumerate(rows):
            if not isinstance(item, Mapping):
                continue
            effect_id = str(item.get(type_key) or item.get("effect") or "").strip()
            if not effect_id or effect_id not in allowed:
                diagnostics.append({
                    "severity": "error",
                    "code": "A5R_EFFECT_NOT_SUPPORTED",
                    "capability_id": bundle.capability_id,
                    "kind": kind,
                    "effect_id": effect_id or "<unknown>",
                    "message": "effect is not declared by the authoritative capability contract",
                })
                continue
            effects.append({
                "effect_id": effect_id,
                "binding_id": f"registry-{kind}-{index}",
                "kind": kind,
                "target": str(item.get("target") or contract.target_name),
                "onset_time_s": float(item.get("onset_time_s", 0.0) or 0.0),
                "duration_s": float(item.get("duration_s", -1.0) or -1.0),
                "severity": float(item.get("severity", item.get("magnitude", 1.0)) or 1.0),
                "parameters": dict(_mapping(item.get("parameters"))),
            })
    return effects, diagnostics


def _parameter_diagnostics(bundle: SubsystemAssetBundle, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    declared = {str(item.name): item for item in bundle.root_definition.parameters}
    for name, value in parameters.items():
        spec = declared.get(str(name))
        if spec is None:
            diagnostics.append({
                "severity": "error",
                "code": "A5R_PARAMETER_NOT_DECLARED",
                "capability_id": bundle.capability_id,
                "parameter": str(name),
            })
            continue
        try:
            spec.validate(value)
        except KernelValidationError as exc:
            diagnostics.append({
                "severity": "error",
                "code": "A5R_PARAMETER_INVALID",
                "capability_id": bundle.capability_id,
                "parameter": str(name),
                "message": str(exc),
            })
    return diagnostics


def resolve_subsystem_execution(
    adapter_key: str,
    task_spec: Mapping[str, Any],
    *,
    requested_outputs: Sequence[str] = (),
) -> A5Resolution:
    """Resolve one of the registered A5R focused subsystem capabilities."""

    bundle = bundle_for_adapter_key(adapter_key)
    runtime = deepcopy(to_runtime_task_spec(task_spec))
    diagnostics: list[dict[str, Any]] = []

    capability_id = str(runtime.get("capability_id") or _mapping(runtime.get("model")).get("capability_id") or "")
    if capability_id and capability_id != bundle.capability_id:
        diagnostics.append({
            "severity": "error",
            "code": "A5R_CAPABILITY_MISMATCH",
            "expected": bundle.capability_id,
            "actual": capability_id,
        })

    runtime_parameters = _mapping(runtime.get("parameters"))
    merged_parameters = dict(bundle.parameter_set.values)
    merged_parameters.update(dict(runtime_parameters))
    diagnostics.extend(_parameter_diagnostics(bundle, merged_parameters))
    runtime["parameters"] = merged_parameters

    resolved_effects: list[dict[str, Any]] = []
    if _has_events(runtime):
        resolved_effects, effect_diagnostics = _resolved_registry_effects(bundle, runtime)
        diagnostics.extend(effect_diagnostics)
    else:
        resolved_effects.append({
            "effect_id": "nominal",
            "binding_id": "nominal-effect",
            "target_node": bundle.model_ref.model_id.value.rsplit(".", 1)[-1],
        })

    outputs = tuple(dict.fromkeys([*requested_outputs, *bundle.required_outputs]))
    bound = BoundModelGraph(
        bound_graph_id=LocalId(f"{bundle.graph.graph_ref.model_id.value.rsplit('.', 1)[-1]}-run"),
        graph_ref=bundle.graph.graph_ref,
        graph_sha256=bundle.graph.content_sha256,
        root_model_ref=bundle.model_ref,
        implementation_id=bundle.implementation_id,
        implementation_profile_sha256=bundle.implementation_profile.content_sha256,
        parameter_set_id=bundle.parameter_set.parameter_set_id,
        parameter_set_sha256=bundle.parameter_set.content_sha256,
        binding_set_id=bundle.binding_set.binding_set_id,
        binding_set_sha256=bundle.binding_set.content_sha256,
        capability_projection_id=bundle.projection.projection_id,
        capability_projection_sha256=bundle.projection.content_sha256,
        resolved_parameters=merged_parameters,
        resolved_effects=tuple(resolved_effects),
        requested_outputs=outputs,
    )
    return A5Resolution(bound, runtime, tuple(diagnostics))


def make_subsystem_resolver(adapter_key: str) -> Callable[..., A5Resolution]:
    """Create a registry-compatible resolver with a fixed logical adapter key."""

    bundle = bundle_for_adapter_key(adapter_key)

    def resolver(task_spec: Mapping[str, Any], *, requested_outputs: Sequence[str] = ()) -> A5Resolution:
        return resolve_subsystem_execution(adapter_key, task_spec, requested_outputs=requested_outputs)

    resolver.__name__ = f"resolve_{bundle.model_ref.model_id.value.replace('.', '_')}_execution"
    return resolver


__all__ = ["A5Resolution", "make_subsystem_resolver", "resolve_subsystem_execution"]
