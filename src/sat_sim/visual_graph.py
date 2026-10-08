"""Typed visual-composer graph validation and normalization.

The browser graph is deliberately small: it orchestrates one registered
Capability into the existing TaskSpec/compiler/runtime path.  This module is
the server-side trust boundary for the visual topology so clients cannot skip
port typing, acyclicity, or required data-flow checks.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Any, Mapping

VISUAL_GRAPH_SCHEMA_VERSION = "sat-sim.visual-graph.v1"
MAX_VISUAL_GRAPH_NODES = 32
MAX_VISUAL_GRAPH_EDGES = 96
MAX_VISUAL_GRAPH_ID_LENGTH = 128

# One graph still targets one registered executable Capability.  These ports
# model orchestration artifacts, not arbitrary Basilisk message connections.
PORT_CONTRACT: dict[str, dict[str, Any]] = {
    "model": {
        "inputs": {},
        "outputs": {"capability": "capability_ref"},
        "required_inputs": [],
    },
    "config": {
        "inputs": {"capability": "capability_ref"},
        "outputs": {"task": "task_draft"},
        "required_inputs": ["capability"],
    },
    "effects": {
        "inputs": {"task": "task_draft"},
        "outputs": {"task": "task_draft"},
        "required_inputs": ["task"],
    },
    "outputs": {
        "inputs": {"task": "task_draft"},
        "outputs": {"spec": "task_spec"},
        "required_inputs": ["task"],
    },
    "code": {
        "inputs": {"spec": "task_spec"},
        "outputs": {"script": "python_script"},
        "required_inputs": ["spec"],
    },
    "run": {
        "inputs": {"spec": "task_spec", "script": "python_script"},
        "outputs": {},
        "required_inputs": ["spec"],
    },
}


@dataclass(frozen=True)
class GraphIssue:
    code: str
    message: str
    path: str = "$.graph"
    severity: str = "error"
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.details is None:
            result.pop("details")
        return result


@dataclass(frozen=True)
class VisualGraphValidation:
    ok: bool
    schema_version: str
    topological_order: list[str]
    issues: list[GraphIssue]
    node_count: int
    edge_count: int

    @property
    def errors(self) -> list[GraphIssue]:
        return [item for item in self.issues if item.severity == "error"]

    @property
    def warnings(self) -> list[GraphIssue]:
        return [item for item in self.issues if item.severity != "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": self.schema_version,
            "topological_order": list(self.topological_order),
            "issues": [item.to_dict() for item in self.issues],
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }


def _issue(
    issues: list[GraphIssue],
    code: str,
    message: str,
    *,
    path: str = "$.graph",
    severity: str = "error",
    details: dict[str, Any] | None = None,
) -> None:
    issues.append(GraphIssue(code=code, message=message, path=path, severity=severity, details=details))


def _reachable(adjacency: Mapping[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(adjacency.get(node_id, []))
    return seen


def validate_visual_graph(
    graph: Mapping[str, Any],
    *,
    expected_capability_id: str | None = None,
    require_code: bool = True,
    require_run: bool = True,
) -> VisualGraphValidation:
    """Validate a visual graph against the orchestration port contract.

    Validation is intentionally deterministic and registry-agnostic.  The
    normal TaskSpec/compiler layer still validates whether the selected
    Capability and its parameters/effects/outputs are executable.
    """

    issues: list[GraphIssue] = []
    if not isinstance(graph, Mapping):
        _issue(issues, "GRAPH_NOT_OBJECT", "Visual graph must be an object.")
        return VisualGraphValidation(False, VISUAL_GRAPH_SCHEMA_VERSION, [], issues, 0, 0)

    schema_version = str(graph.get("schema_version") or VISUAL_GRAPH_SCHEMA_VERSION)
    if schema_version != VISUAL_GRAPH_SCHEMA_VERSION:
        _issue(
            issues,
            "GRAPH_SCHEMA_VERSION_UNSUPPORTED",
            f"Unsupported visual graph schema version {schema_version!r}.",
            path="$.graph.schema_version",
        )

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    if not isinstance(raw_nodes, list):
        _issue(issues, "GRAPH_NODES_INVALID", "graph.nodes must be a list.", path="$.graph.nodes")
        raw_nodes = []
    if not isinstance(raw_edges, list):
        _issue(issues, "GRAPH_EDGES_INVALID", "graph.edges must be a list.", path="$.graph.edges")
        raw_edges = []

    if len(raw_nodes) > MAX_VISUAL_GRAPH_NODES:
        _issue(
            issues,
            "GRAPH_NODE_LIMIT_EXCEEDED",
            f"Visual graph supports at most {MAX_VISUAL_GRAPH_NODES} nodes.",
            path="$.graph.nodes",
            details={"count": len(raw_nodes), "limit": MAX_VISUAL_GRAPH_NODES},
        )
        raw_nodes = raw_nodes[:MAX_VISUAL_GRAPH_NODES]
    if len(raw_edges) > MAX_VISUAL_GRAPH_EDGES:
        _issue(
            issues,
            "GRAPH_EDGE_LIMIT_EXCEEDED",
            f"Visual graph supports at most {MAX_VISUAL_GRAPH_EDGES} edges.",
            path="$.graph.edges",
            details={"count": len(raw_edges), "limit": MAX_VISUAL_GRAPH_EDGES},
        )
        raw_edges = raw_edges[:MAX_VISUAL_GRAPH_EDGES]

    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    node_id_by_type: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        path = f"$.graph.nodes[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "GRAPH_NODE_INVALID", "Graph node must be an object.", path=path)
            continue
        node_id = str(raw.get("id") or "").strip()
        node_type = str(raw.get("type") or "").strip()
        if not node_id:
            _issue(issues, "GRAPH_NODE_ID_MISSING", "Graph node id is required.", path=f"{path}.id")
            continue
        if len(node_id) > MAX_VISUAL_GRAPH_ID_LENGTH:
            _issue(issues, "GRAPH_NODE_ID_TOO_LONG", "Graph node id is too long.", path=f"{path}.id", details={"limit": MAX_VISUAL_GRAPH_ID_LENGTH})
            continue
        if node_id in nodes_by_id:
            _issue(issues, "GRAPH_NODE_ID_DUPLICATE", f"Duplicate graph node id {node_id!r}.", path=f"{path}.id")
            continue
        if node_type not in PORT_CONTRACT:
            _issue(issues, "GRAPH_NODE_TYPE_UNKNOWN", f"Unknown graph node type {node_type!r}.", path=f"{path}.type")
            continue
        if node_type in node_id_by_type:
            _issue(
                issues,
                "GRAPH_NODE_TYPE_DUPLICATE",
                f"Only one {node_type!r} node is allowed in the current composer contract.",
                path=f"{path}.type",
            )
            continue
        nodes_by_id[node_id] = raw
        node_id_by_type[node_type] = node_id

    for required_type in ("model", "config", "outputs"):
        if required_type not in node_id_by_type:
            _issue(issues, "GRAPH_REQUIRED_NODE_MISSING", f"Missing required {required_type!r} node.", details={"node_type": required_type})
    if require_code and "code" not in node_id_by_type:
        _issue(issues, "GRAPH_REQUIRED_NODE_MISSING", "Missing required 'code' node.", details={"node_type": "code"})
    if require_run and "run" not in node_id_by_type:
        _issue(issues, "GRAPH_REQUIRED_NODE_MISSING", "Missing required 'run' node.", details={"node_type": "run"})

    model_id = node_id_by_type.get("model")
    if model_id and expected_capability_id:
        graph_capability = str(nodes_by_id[model_id].get("capabilityId") or nodes_by_id[model_id].get("capability_id") or "").strip()
        if graph_capability and graph_capability != expected_capability_id:
            _issue(
                issues,
                "GRAPH_CAPABILITY_MISMATCH",
                "Visual graph model Capability does not match the submitted form Capability.",
                path=f"$.graph.nodes[{list(nodes_by_id).index(model_id)}].capabilityId",
                details={"graph_capability_id": graph_capability, "form_capability_id": expected_capability_id},
            )
        elif not graph_capability:
            _issue(issues, "GRAPH_CAPABILITY_MISSING", "Model node must identify its Capability.", details={"form_capability_id": expected_capability_id})

    adjacency: dict[str, list[str]] = defaultdict(list)
    reverse_adjacency: dict[str, list[str]] = defaultdict(list)
    indegree: dict[str, int] = {node_id: 0 for node_id in nodes_by_id}
    incoming_ports: dict[tuple[str, str], int] = defaultdict(int)
    seen_edge_ids: set[str] = set()
    valid_edges: list[tuple[str, str, str, str, str]] = []

    for index, raw in enumerate(raw_edges):
        path = f"$.graph.edges[{index}]"
        if not isinstance(raw, Mapping):
            _issue(issues, "GRAPH_EDGE_INVALID", "Graph edge must be an object.", path=path)
            continue
        edge_id = str(raw.get("id") or f"edge-{index}").strip()
        source_id = str(raw.get("source") or "").strip()
        target_id = str(raw.get("target") or "").strip()
        source_port = str(raw.get("sourcePort") or raw.get("source_port") or "").strip()
        target_port = str(raw.get("targetPort") or raw.get("target_port") or "").strip()
        if any(len(value) > MAX_VISUAL_GRAPH_ID_LENGTH for value in (edge_id, source_id, target_id, source_port, target_port)):
            _issue(issues, "GRAPH_EDGE_FIELD_TOO_LONG", "Graph edge identifier or port name is too long.", path=path, details={"limit": MAX_VISUAL_GRAPH_ID_LENGTH})
            continue
        if edge_id in seen_edge_ids:
            _issue(issues, "GRAPH_EDGE_ID_DUPLICATE", f"Duplicate graph edge id {edge_id!r}.", path=f"{path}.id")
            continue
        seen_edge_ids.add(edge_id)
        if source_id not in nodes_by_id or target_id not in nodes_by_id:
            _issue(
                issues,
                "GRAPH_EDGE_NODE_UNKNOWN",
                "Graph edge references an unknown source or target node.",
                path=path,
                details={"source": source_id, "target": target_id},
            )
            continue
        if source_id == target_id:
            _issue(issues, "GRAPH_EDGE_SELF_LOOP", "A node cannot connect to itself.", path=path)
            continue
        source_type = str(nodes_by_id[source_id].get("type"))
        target_type = str(nodes_by_id[target_id].get("type"))
        source_outputs = PORT_CONTRACT[source_type]["outputs"]
        target_inputs = PORT_CONTRACT[target_type]["inputs"]
        if source_port not in source_outputs:
            _issue(
                issues,
                "GRAPH_SOURCE_PORT_UNKNOWN",
                f"Node type {source_type!r} has no output port {source_port!r}.",
                path=f"{path}.sourcePort",
            )
            continue
        if target_port not in target_inputs:
            _issue(
                issues,
                "GRAPH_TARGET_PORT_UNKNOWN",
                f"Node type {target_type!r} has no input port {target_port!r}.",
                path=f"{path}.targetPort",
            )
            continue
        source_data_type = source_outputs[source_port]
        target_data_type = target_inputs[target_port]
        if source_data_type != target_data_type:
            _issue(
                issues,
                "GRAPH_PORT_TYPE_MISMATCH",
                f"Cannot connect {source_data_type!r} to {target_data_type!r}.",
                path=path,
                details={
                    "source_type": source_data_type,
                    "target_type": target_data_type,
                    "source": source_id,
                    "source_port": source_port,
                    "target": target_id,
                    "target_port": target_port,
                },
            )
            continue
        incoming_ports[(target_id, target_port)] += 1
        if incoming_ports[(target_id, target_port)] > 1:
            _issue(
                issues,
                "GRAPH_INPUT_PORT_MULTIPLE_DRIVERS",
                f"Input {target_id}.{target_port} has more than one driver.",
                path=path,
            )
            continue
        adjacency[source_id].append(target_id)
        reverse_adjacency[target_id].append(source_id)
        indegree[target_id] += 1
        valid_edges.append((source_id, source_port, target_id, target_port, source_data_type))

    for node_id, node in nodes_by_id.items():
        node_type = str(node.get("type"))
        for port_name in PORT_CONTRACT[node_type]["required_inputs"]:
            if incoming_ports.get((node_id, port_name), 0) != 1:
                _issue(
                    issues,
                    "GRAPH_REQUIRED_INPUT_UNCONNECTED",
                    f"Required input {node_type}.{port_name} is not connected.",
                    details={"node_id": node_id, "node_type": node_type, "port": port_name},
                )

    # Stable Kahn order follows node declaration order when multiple nodes are ready.
    node_order_index = {node_id: index for index, node_id in enumerate(nodes_by_id)}
    ready = [node_id for node_id, count in indegree.items() if count == 0]
    ready.sort(key=lambda item: node_order_index[item])
    queue: deque[str] = deque(ready)
    topo: list[str] = []
    indegree_work = dict(indegree)
    while queue:
        node_id = queue.popleft()
        topo.append(node_id)
        for target in adjacency.get(node_id, []):
            indegree_work[target] -= 1
            if indegree_work[target] == 0:
                queue.append(target)
    if len(topo) != len(nodes_by_id):
        cycle_nodes = [node_id for node_id in nodes_by_id if node_id not in topo]
        _issue(
            issues,
            "GRAPH_CYCLE_DETECTED",
            "Visual graph contains a dependency cycle.",
            details={"node_ids": cycle_nodes},
        )

    # Topology contract: configuration must feed outputs; effects, when present,
    # must participate in that path instead of becoming a decorative orphan.
    if model_id and "outputs" in node_id_by_type:
        reachable_from_model = _reachable(adjacency, model_id)
        outputs_id = node_id_by_type["outputs"]
        if outputs_id not in reachable_from_model:
            _issue(issues, "GRAPH_OUTPUTS_NOT_REACHABLE", "Outputs node must be reachable from the model node.")
        for node_type in ("config", "effects"):
            node_id = node_id_by_type.get(node_type)
            if node_id and node_id not in reachable_from_model:
                _issue(issues, "GRAPH_NODE_NOT_REACHABLE", f"{node_type!r} node is not reachable from the model node.", details={"node_id": node_id})
        effects_id = node_id_by_type.get("effects")
        if effects_id:
            reaches_outputs = outputs_id in _reachable(adjacency, effects_id)
            if not reaches_outputs:
                _issue(issues, "GRAPH_EFFECTS_BYPASSED", "Effects node is present but does not feed the outputs path.", details={"node_id": effects_id})

        if require_code and "code" in node_id_by_type and node_id_by_type["code"] not in _reachable(adjacency, outputs_id):
            _issue(issues, "GRAPH_CODE_NOT_CONNECTED", "Code node must consume the TaskSpec produced by outputs.")
        if require_run and "run" in node_id_by_type and node_id_by_type["run"] not in _reachable(adjacency, outputs_id):
            _issue(issues, "GRAPH_RUN_NOT_CONNECTED", "Run node must consume the TaskSpec produced by outputs.")

    ok = not any(item.severity == "error" for item in issues)
    return VisualGraphValidation(
        ok=ok,
        schema_version=VISUAL_GRAPH_SCHEMA_VERSION,
        topological_order=topo if ok else topo,
        issues=issues,
        node_count=len(nodes_by_id),
        edge_count=len(valid_edges),
    )


__all__ = [
    "VISUAL_GRAPH_SCHEMA_VERSION",
    "MAX_VISUAL_GRAPH_NODES",
    "MAX_VISUAL_GRAPH_EDGES",
    "MAX_VISUAL_GRAPH_ID_LENGTH",
    "PORT_CONTRACT",
    "GraphIssue",
    "VisualGraphValidation",
    "validate_visual_graph",
]
