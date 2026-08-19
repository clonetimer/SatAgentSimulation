"""Typed mutation models for the persistent Simulation DAG harness."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DAG_SCHEMA_VERSION = "sat-sim.simulation-dag.v1"
MUTATION_SCHEMA_VERSION = "sat-sim.dag-mutation.v1"

NodeType = Literal[
    "intent_parse",
    "capability_select",
    "taskspec_draft",
    "validate_taskspec",
    "agent_guard",
    "resolve_spec",
    "execution_plan",
    "run_capability",
    "dataset_package",
    "evidence_review",
]

EdgeType = Literal[
    "depends_on",
    "produces",
    "consumes",
    "validates",
    "guards",
    "executes",
    "packages",
    "reviews",
]

MutationOperation = Literal[
    "create_dag",
    "add_node",
    "update_node",
    "remove_node",
    "connect_edge",
    "disconnect_edge",
    "export_taskspec",
    "export_campaign_plan",
    "export_execution_plan",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DAGNode(StrictModel):
    node_id: str = Field(min_length=1)
    node_type: NodeType
    payload: dict[str, Any] = Field(default_factory=dict)
    label: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class DAGEdge(StrictModel):
    edge_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    edge_type: EdgeType
    payload: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)


class SimulationDAG(StrictModel):
    schema_version: Literal[DAG_SCHEMA_VERSION] = DAG_SCHEMA_VERSION
    dag_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    nodes: list[DAGNode] = Field(default_factory=list)
    edges: list[DAGEdge] = Field(default_factory=list)
    state_hash: str = ""
    created_at: str
    updated_at: str
    source_request: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class MutationValidation(StrictModel):
    ok: bool
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class DAGMutation(StrictModel):
    schema_version: Literal[MUTATION_SCHEMA_VERSION] = MUTATION_SCHEMA_VERSION
    mutation_id: str = Field(min_length=1)
    base_dag_hash: str | None = None
    actor: str = Field(min_length=1)
    operation: MutationOperation
    payload: dict[str, Any] = Field(default_factory=dict)
    validation_result: MutationValidation | None = None
    committed: bool = False
    new_dag_hash: str | None = None


def dag_schema() -> dict[str, Any]:
    schema = SimulationDAG.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/simulation-dag-v1.schema.json"
    schema["title"] = "Satellite Simulation DAG v1"
    return schema


def mutation_schema() -> dict[str, Any]:
    schema = DAGMutation.model_json_schema(mode="validation")
    schema["$id"] = "https://example.local/sat-sim/dag-mutation-v1.schema.json"
    schema["title"] = "Satellite Simulation DAG Mutation v1"
    return schema


__all__ = [
    "DAG_SCHEMA_VERSION",
    "MUTATION_SCHEMA_VERSION",
    "DAGNode",
    "DAGEdge",
    "SimulationDAG",
    "MutationValidation",
    "DAGMutation",
    "dag_schema",
    "mutation_schema",
]
