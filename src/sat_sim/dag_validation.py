"""Validation and hashing helpers for Simulation DAG documents."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping

from .dag_mutations import DAGEdge, DAGNode, SimulationDAG, MutationValidation


def canonical_dag_payload(dag: SimulationDAG | Mapping[str, Any]) -> dict[str, Any]:
    payload = dag.model_dump(mode="json") if isinstance(dag, SimulationDAG) else copy.deepcopy(dict(dag))
    payload.pop("state_hash", None)
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    payload["nodes"] = sorted(payload.get("nodes") or [], key=lambda item: str(item.get("node_id")))
    payload["edges"] = sorted(payload.get("edges") or [], key=lambda item: str(item.get("edge_id")))
    return payload


def dag_state_hash(dag: SimulationDAG | Mapping[str, Any]) -> str:
    encoded = json.dumps(canonical_dag_payload(dag), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def with_state_hash(dag: SimulationDAG) -> SimulationDAG:
    payload = dag.model_dump(mode="json")
    payload["state_hash"] = dag_state_hash(payload)
    return SimulationDAG.model_validate(payload)


def validate_dag(dag: SimulationDAG | Mapping[str, Any]) -> MutationValidation:
    try:
        model = dag if isinstance(dag, SimulationDAG) else SimulationDAG.model_validate(dict(dag))
    except Exception as exc:
        return MutationValidation(ok=False, errors=[{"code": "DAG_SCHEMA_INVALID", "path": "$", "message": str(exc)}])

    errors: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    edge_ids: set[str] = set()
    adjacency: dict[str, list[str]] = {}

    for index, node in enumerate(model.nodes):
        if node.node_id in node_ids:
            errors.append({"code": "DAG_DUPLICATE_NODE", "path": f"$.nodes[{index}].node_id", "message": f"duplicate node_id {node.node_id!r}"})
        node_ids.add(node.node_id)
        adjacency.setdefault(node.node_id, [])

    for index, edge in enumerate(model.edges):
        if edge.edge_id in edge_ids:
            errors.append({"code": "DAG_DUPLICATE_EDGE", "path": f"$.edges[{index}].edge_id", "message": f"duplicate edge_id {edge.edge_id!r}"})
        edge_ids.add(edge.edge_id)
        if edge.source_node_id not in node_ids:
            errors.append({"code": "DAG_EDGE_SOURCE_MISSING", "path": f"$.edges[{index}].source_node_id", "message": f"source node {edge.source_node_id!r} does not exist"})
        if edge.target_node_id not in node_ids:
            errors.append({"code": "DAG_EDGE_TARGET_MISSING", "path": f"$.edges[{index}].target_node_id", "message": f"target node {edge.target_node_id!r} does not exist"})
        if edge.source_node_id == edge.target_node_id:
            errors.append({"code": "DAG_SELF_EDGE", "path": f"$.edges[{index}]", "message": "self edges are not allowed"})
        adjacency.setdefault(edge.source_node_id, []).append(edge.target_node_id)

    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append({"code": "DAG_CYCLE", "path": "$.edges", "message": f"cycle detected at {node_id!r}"})
            return
        if node_id in seen:
            return
        visiting.add(node_id)
        for target in adjacency.get(node_id, []):
            if target in node_ids:
                visit(target)
        visiting.remove(node_id)
        seen.add(node_id)

    for node_id in sorted(node_ids):
        visit(node_id)

    return MutationValidation(ok=not errors, errors=errors)


def apply_mutation_to_dag(dag: SimulationDAG, operation: str, payload: Mapping[str, Any]) -> SimulationDAG:
    next_payload = dag.model_dump(mode="json")
    if operation == "add_node":
        node = DAGNode.model_validate(payload.get("node") or payload)
        next_payload["nodes"].append(node.model_dump(mode="json"))
    elif operation == "update_node":
        node_id = str(payload.get("node_id") or "")
        patch = payload.get("patch")
        if not isinstance(patch, Mapping):
            raise ValueError("update_node requires payload.patch")
        for index, node in enumerate(next_payload["nodes"]):
            if node["node_id"] == node_id:
                updated = copy.deepcopy(node)
                updated.update(dict(patch))
                next_payload["nodes"][index] = DAGNode.model_validate(updated).model_dump(mode="json")
                break
        else:
            raise ValueError(f"node {node_id!r} does not exist")
    elif operation == "remove_node":
        node_id = str(payload.get("node_id") or "")
        if not any(node["node_id"] == node_id for node in next_payload["nodes"]):
            raise ValueError(f"node {node_id!r} does not exist")
        next_payload["nodes"] = [node for node in next_payload["nodes"] if node["node_id"] != node_id]
        next_payload["edges"] = [edge for edge in next_payload["edges"] if edge["source_node_id"] != node_id and edge["target_node_id"] != node_id]
    elif operation == "connect_edge":
        edge = DAGEdge.model_validate(payload.get("edge") or payload)
        next_payload["edges"].append(edge.model_dump(mode="json"))
    elif operation == "disconnect_edge":
        edge_id = str(payload.get("edge_id") or "")
        if not any(edge["edge_id"] == edge_id for edge in next_payload["edges"]):
            raise ValueError(f"edge {edge_id!r} does not exist")
        next_payload["edges"] = [edge for edge in next_payload["edges"] if edge["edge_id"] != edge_id]
    else:
        raise ValueError(f"operation {operation!r} does not mutate a DAG")

    next_payload["version"] = int(next_payload["version"]) + 1
    return with_state_hash(SimulationDAG.model_validate(next_payload))


__all__ = ["canonical_dag_payload", "dag_state_hash", "with_state_hash", "validate_dag", "apply_mutation_to_dag"]
