"""Explicit model graph contracts with feedback-aware validation."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Sequence
from typing import Any

from .errors import DuplicateIdError, KernelValidationError, ReferenceResolutionError
from .identifiers import LocalId, ModelRef
from .model_definition import ModelDefinition
from .serialization import canonical_json, content_sha256
from .value_types import ConnectionKind, ModelKind


def _unique(items: Sequence[str], label: str) -> None:
    if len(set(items)) != len(items):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        raise DuplicateIdError(f"duplicate {label}: {', '.join(duplicates)}")


@dataclass(frozen=True)
class ParameterOverride:
    parameter_name: LocalId
    value: Any

    def __post_init__(self) -> None:
        canonical_json(self.value)


@dataclass(frozen=True)
class ModelNode:
    node_id: LocalId
    model_ref: ModelRef
    parameter_overrides: tuple[ParameterOverride, ...] = ()

    def __post_init__(self) -> None:
        _unique([str(item.parameter_name) for item in self.parameter_overrides], f"parameter override in {self.node_id}")


@dataclass(frozen=True, order=True)
class PortEndpoint:
    node_id: LocalId
    port_name: LocalId

    def __str__(self) -> str:
        return f"{self.node_id}.{self.port_name}"


@dataclass(frozen=True)
class ModelConnection:
    source: PortEndpoint
    target: PortEndpoint
    kind: ConnectionKind = ConnectionKind.SIGNAL
    feedback: bool = False
    transform: str | None = None

    def __post_init__(self) -> None:
        if self.source == self.target:
            raise KernelValidationError("a connection cannot connect a port to itself")
        if self.transform is not None and not self.transform.strip():
            raise KernelValidationError("connection transform must be non-empty when provided")


@dataclass(frozen=True)
class ModelGraphDefinition:
    graph_ref: ModelRef
    kind: ModelKind
    title: str
    nodes: tuple[ModelNode, ...]
    connections: tuple[ModelConnection, ...]
    exposed_inputs: tuple[PortEndpoint, ...] = ()
    exposed_outputs: tuple[PortEndpoint, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in {ModelKind.SUBSYSTEM, ModelKind.SPACECRAFT, ModelKind.SCENARIO}:
            raise KernelValidationError("model graphs must be subsystem, spacecraft, or scenario kind")
        if not self.title.strip():
            raise KernelValidationError("graph title must be non-empty")
        if not self.nodes:
            raise KernelValidationError("model graph must contain at least one node")
        node_ids = [str(item.node_id) for item in self.nodes]
        _unique(node_ids, "graph nodes")
        known = set(node_ids)
        for endpoint in [
            *(connection.source for connection in self.connections),
            *(connection.target for connection in self.connections),
            *self.exposed_inputs,
            *self.exposed_outputs,
        ]:
            if str(endpoint.node_id) not in known:
                raise ReferenceResolutionError(f"unknown graph node in endpoint {endpoint}")
        connection_keys = [
            (str(item.source), str(item.target), item.kind.value, item.feedback, item.transform)
            for item in self.connections
        ]
        if len(set(connection_keys)) != len(connection_keys):
            raise DuplicateIdError("duplicate model graph connection")
        _unique([str(item) for item in self.exposed_inputs], "exposed inputs")
        _unique([str(item) for item in self.exposed_outputs], "exposed outputs")
        self._validate_non_feedback_acyclic()

    def _validate_non_feedback_acyclic(self) -> None:
        """Require cycles to contain at least one explicitly marked feedback edge."""

        graph: dict[str, set[str]] = {str(node.node_id): set() for node in self.nodes}
        indegree = {node_id: 0 for node_id in graph}
        for connection in self.connections:
            if connection.feedback:
                continue
            source = str(connection.source.node_id)
            target = str(connection.target.node_id)
            if target not in graph[source]:
                graph[source].add(target)
                indegree[target] += 1
        queue = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            node_id = queue.pop()
            visited += 1
            for target in graph[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(graph):
            raise KernelValidationError(
                "model graph contains an unmarked cycle; control/resource feedback edges must set feedback=True"
            )

    def validate_with_resolver(self, resolver: Callable[[ModelRef], ModelDefinition]) -> None:
        definitions: dict[str, ModelDefinition] = {}
        for node in self.nodes:
            try:
                definition = resolver(node.model_ref)
            except Exception as exc:
                raise ReferenceResolutionError(f"cannot resolve model {node.model_ref}: {exc}") from exc
            if definition.model_ref != node.model_ref:
                raise ReferenceResolutionError(
                    f"resolver returned {definition.model_ref} for requested {node.model_ref}"
                )
            definitions[str(node.node_id)] = definition
            for override in node.parameter_overrides:
                definition.parameter(str(override.parameter_name)).validate(override.value)

        for connection in self.connections:
            source_def = definitions[str(connection.source.node_id)]
            target_def = definitions[str(connection.target.node_id)]
            source = source_def.output_port(str(connection.source.port_name))
            target = target_def.input_port(str(connection.target.port_name))
            if source.data_type != target.data_type:
                raise KernelValidationError(
                    f"connection {connection.source}->{connection.target} data type mismatch: "
                    f"{source.data_type.value} != {target.data_type.value}"
                )
            if source.shape != target.shape:
                raise KernelValidationError(
                    f"connection {connection.source}->{connection.target} shape mismatch: "
                    f"{source.shape} != {target.shape}"
                )
            if source.unit and target.unit and source.unit != target.unit:
                raise KernelValidationError(
                    f"connection {connection.source}->{connection.target} unit mismatch: "
                    f"{source.unit} != {target.unit}"
                )
            if source.frame and target.frame and source.frame != target.frame:
                raise KernelValidationError(
                    f"connection {connection.source}->{connection.target} frame mismatch: "
                    f"{source.frame} != {target.frame}"
                )

        for endpoint in self.exposed_inputs:
            definitions[str(endpoint.node_id)].input_port(str(endpoint.port_name))
        for endpoint in self.exposed_outputs:
            definitions[str(endpoint.node_id)].output_port(str(endpoint.port_name))

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)


__all__ = [
    "ModelConnection",
    "ModelGraphDefinition",
    "ModelNode",
    "ParameterOverride",
    "PortEndpoint",
]
