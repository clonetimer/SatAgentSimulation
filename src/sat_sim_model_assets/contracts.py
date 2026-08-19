"""Versioned parameter sets and bound-model execution payloads for A3R."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from sat_sim_kernel import (
    ImplementationId,
    KernelValidationError,
    LocalId,
    ModelDefinition,
    ModelGraphDefinition,
    ModelRef,
    canonical_json,
    content_sha256,
    to_canonical_dict,
)


def _frozen_mapping(value: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise KernelValidationError(f"{label} must be a mapping with string keys")
    copied = {str(key): item for key, item in value.items()}
    canonical_json(copied)
    return MappingProxyType(copied)


@dataclass(frozen=True)
class ParameterSet:
    """Concrete values separated from the physical semantics in ModelDefinition."""

    parameter_set_id: LocalId
    model_ref: ModelRef
    values: Mapping[str, Any]
    source_refs: tuple[str, ...] = ()
    applicability: str = "engineering_demo"
    confidence: str = "demo"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _frozen_mapping(self.values, "parameter values"))
        for label, items in (("source_refs", self.source_refs), ("notes", self.notes)):
            if any(not isinstance(item, str) or not item.strip() for item in items):
                raise KernelValidationError(f"{label} must contain non-empty strings")
            if len(set(items)) != len(items):
                raise KernelValidationError(f"{label} must not contain duplicates")
        if not self.applicability.strip() or not self.confidence.strip():
            raise KernelValidationError("applicability and confidence must be non-empty")

    def validate_against(self, definition: ModelDefinition, *, allow_runtime_extensions: bool = False) -> None:
        if definition.model_ref != self.model_ref:
            raise KernelValidationError(
                f"parameter set {self.parameter_set_id} targets {self.model_ref}, not {definition.model_ref}"
            )
        declared = {str(item.name): item for item in definition.parameters}
        for name, value in self.values.items():
            spec = declared.get(name)
            if spec is None:
                if allow_runtime_extensions:
                    continue
                raise KernelValidationError(f"parameter {name!r} is not declared by {definition.model_ref}")
            spec.validate(value)
        missing = [str(item.name) for item in definition.parameters if item.required and str(item.name) not in self.values]
        if missing:
            raise KernelValidationError(f"required parameters are missing: {missing}")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)


@dataclass(frozen=True)
class BoundModelGraph:
    """Immutable, auditable result of binding O-side calls to an M-side graph."""

    bound_graph_id: LocalId
    graph_ref: ModelRef
    graph_sha256: str
    root_model_ref: ModelRef
    implementation_id: ImplementationId
    implementation_profile_sha256: str
    parameter_set_id: LocalId
    parameter_set_sha256: str
    binding_set_id: LocalId
    binding_set_sha256: str
    capability_projection_id: LocalId
    capability_projection_sha256: str
    resolved_parameters: Mapping[str, Any] = field(default_factory=dict)
    resolved_actions: tuple[Mapping[str, Any], ...] = ()
    resolved_effects: tuple[Mapping[str, Any], ...] = ()
    requested_outputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "resolved_parameters", _frozen_mapping(self.resolved_parameters, "resolved_parameters"))
        for name in (
            "graph_sha256",
            "implementation_profile_sha256",
            "parameter_set_sha256",
            "binding_set_sha256",
            "capability_projection_sha256",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise KernelValidationError(f"{name} must be a lowercase SHA-256 value")
        actions = tuple(_frozen_mapping(item, "resolved action") for item in self.resolved_actions)
        effects = tuple(_frozen_mapping(item, "resolved effect") for item in self.resolved_effects)
        object.__setattr__(self, "resolved_actions", actions)
        object.__setattr__(self, "resolved_effects", effects)
        if any(not isinstance(item, str) or not item.strip() for item in self.requested_outputs):
            raise KernelValidationError("requested_outputs must contain non-empty strings")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)

    def to_dict(self) -> dict[str, Any]:
        return to_canonical_dict(self)


class ModelAssetRegistry:
    """Small explicit registry for A3R model definitions and graphs."""

    def __init__(self) -> None:
        self._definitions: dict[str, ModelDefinition] = {}
        self._graphs: dict[str, ModelGraphDefinition] = {}
        self._parameter_sets: dict[str, ParameterSet] = {}

    def register_definition(self, definition: ModelDefinition) -> None:
        key = str(definition.model_ref)
        if key in self._definitions:
            raise KernelValidationError(f"duplicate model definition: {key}")
        self._definitions[key] = definition

    def register_graph(self, graph: ModelGraphDefinition) -> None:
        key = str(graph.graph_ref)
        if key in self._graphs:
            raise KernelValidationError(f"duplicate model graph: {key}")
        self._graphs[key] = graph

    def register_parameter_set(self, parameter_set: ParameterSet) -> None:
        key = str(parameter_set.parameter_set_id)
        if key in self._parameter_sets:
            raise KernelValidationError(f"duplicate parameter set: {key}")
        self._parameter_sets[key] = parameter_set

    def definition(self, model_ref: ModelRef | str) -> ModelDefinition:
        key = str(ModelRef.parse(model_ref) if isinstance(model_ref, str) else model_ref)
        try:
            return self._definitions[key]
        except KeyError as exc:
            raise KernelValidationError(f"unknown model definition: {key}") from exc

    def graph(self, graph_ref: ModelRef | str) -> ModelGraphDefinition:
        key = str(ModelRef.parse(graph_ref) if isinstance(graph_ref, str) else graph_ref)
        try:
            return self._graphs[key]
        except KeyError as exc:
            raise KernelValidationError(f"unknown model graph: {key}") from exc

    def parameter_set(self, parameter_set_id: LocalId | str) -> ParameterSet:
        key = str(LocalId(parameter_set_id) if isinstance(parameter_set_id, str) else parameter_set_id)
        try:
            return self._parameter_sets[key]
        except KeyError as exc:
            raise KernelValidationError(f"unknown parameter set: {key}") from exc

    def validate(self) -> None:
        for graph in self._graphs.values():
            graph.validate_with_resolver(self.definition)
        for parameter_set in self._parameter_sets.values():
            parameter_set.validate_against(self.definition(parameter_set.model_ref))

    def inventory(self) -> dict[str, Any]:
        return {
            "definitions": {key: value.content_sha256 for key, value in sorted(self._definitions.items())},
            "graphs": {key: value.content_sha256 for key, value in sorted(self._graphs.items())},
            "parameter_sets": {key: value.content_sha256 for key, value in sorted(self._parameter_sets.items())},
        }


__all__ = ["BoundModelGraph", "ModelAssetRegistry", "ParameterSet"]
