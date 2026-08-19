"""Capability projection derived from governed model and operations assets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sat_sim_kernel import ImplementationId, KernelValidationError, LocalId, ModelRef, ObjectRef, content_sha256


@dataclass(frozen=True)
class ExposedParameter:
    source_name: str
    alias: str
    editable: bool = True

    def __post_init__(self) -> None:
        if not self.source_name.strip() or not self.alias.strip():
            raise KernelValidationError("exposed parameter names must be non-empty")


@dataclass(frozen=True)
class CapabilityProjection:
    projection_id: LocalId
    capability_id: str
    object_ref: ObjectRef
    model_ref: ModelRef
    graph_ref: ModelRef
    implementation_id: ImplementationId
    parameter_set_id: LocalId
    adapter_key: str
    exposed_parameters: tuple[ExposedParameter, ...]
    exposed_properties: tuple[ObjectRef, ...]
    exposed_actions: tuple[ObjectRef, ...]
    exposed_effects: tuple[ObjectRef, ...]
    evidence_refs: tuple[ObjectRef, ...]
    description: str = "Execute the governed attitude-control object through the bound spacecraft model graph."

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.adapter_key.strip():
            raise KernelValidationError("capability_id and adapter_key must be non-empty")
        aliases = [item.alias for item in self.exposed_parameters]
        sources = [item.source_name for item in self.exposed_parameters]
        if len(aliases) != len(set(aliases)) or len(sources) != len(set(sources)):
            raise KernelValidationError("capability projection parameter aliases and sources must be unique")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)

    def parameter_alias_map(self) -> dict[str, str]:
        return {item.alias: item.source_name for item in self.exposed_parameters}

    def form_schema(self, parameter_contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for item in self.exposed_parameters:
            source = dict(parameter_contracts.get(item.source_name) or {})
            schema: dict[str, Any] = {
                "title": item.alias,
                "type": source.get("type", "number"),
                "readOnly": not item.editable,
                "x-model-source": item.source_name,
            }
            for source_key, target_key in (("default", "default"), ("min", "minimum"), ("max", "maximum"), ("unit", "x-unit")):
                if source_key in source:
                    schema[target_key] = source[source_key]
            properties[item.alias] = schema
            if source.get("required"):
                required.append(item.alias)
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": self.capability_id,
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
            "x-projection-id": str(self.projection_id),
            "x-projection-sha256": self.content_sha256,
        }

    def agent_tool_schema(self, parameter_contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "name": self.capability_id.replace(".", "_"),
            "description": self.description,
            "input_schema": self.form_schema(parameter_contracts),
            "properties": [str(item) for item in self.exposed_properties],
            "actions": [str(item) for item in self.exposed_actions],
            "effects": [str(item) for item in self.exposed_effects],
            "evidence": [str(item) for item in self.evidence_refs],
        }

    def api_schema(self, parameter_contracts: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "model_ref": str(self.model_ref),
            "graph_ref": str(self.graph_ref),
            "implementation_id": str(self.implementation_id),
            "parameter_set_id": str(self.parameter_set_id),
            "adapter_key": self.adapter_key,
            "request_body": self.form_schema(parameter_contracts),
        }


__all__ = ["CapabilityProjection", "ExposedParameter"]
