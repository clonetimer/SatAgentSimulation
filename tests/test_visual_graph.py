from __future__ import annotations

from sat_sim.visual_graph import VISUAL_GRAPH_SCHEMA_VERSION, validate_visual_graph


def _graph(capability_id: str = "component.battery.v1") -> dict:
    nodes = [
        {"id": "graph-model", "type": "model", "capabilityId": capability_id},
        {"id": "graph-config", "type": "config"},
        {"id": "graph-effects", "type": "effects"},
        {"id": "graph-outputs", "type": "outputs"},
        {"id": "graph-code", "type": "code"},
        {"id": "graph-run", "type": "run"},
    ]

    def edge(source: str, source_port: str, target: str, target_port: str, suffix: str = "") -> dict:
        return {
            "id": f"{source}.{source_port}__{target}.{target_port}{suffix}",
            "source": source,
            "sourcePort": source_port,
            "target": target,
            "targetPort": target_port,
        }

    return {
        "schema_version": VISUAL_GRAPH_SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [
            edge("graph-model", "capability", "graph-config", "capability"),
            edge("graph-config", "task", "graph-effects", "task"),
            edge("graph-effects", "task", "graph-outputs", "task"),
            edge("graph-outputs", "spec", "graph-code", "spec"),
            edge("graph-outputs", "spec", "graph-run", "spec"),
            edge("graph-code", "script", "graph-run", "script"),
        ],
    }


def test_typed_visual_graph_accepts_default_dag() -> None:
    result = validate_visual_graph(_graph(), expected_capability_id="component.battery.v1")
    assert result.ok is True
    assert result.topological_order == [
        "graph-model",
        "graph-config",
        "graph-effects",
        "graph-outputs",
        "graph-code",
        "graph-run",
    ]
    assert result.edge_count == 6


def test_visual_graph_rejects_capability_mismatch_and_wrong_port_type() -> None:
    graph = _graph("component.reaction_wheel.v1")
    graph["edges"][-2] = {
        "id": "bad-type",
        "source": "graph-code",
        "sourcePort": "script",
        "target": "graph-run",
        "targetPort": "spec",
    }
    result = validate_visual_graph(graph, expected_capability_id="component.battery.v1")
    codes = {item.code for item in result.errors}
    assert result.ok is False
    assert "GRAPH_CAPABILITY_MISMATCH" in codes
    assert "GRAPH_PORT_TYPE_MISMATCH" in codes
    assert "GRAPH_REQUIRED_INPUT_UNCONNECTED" in codes


def test_visual_graph_rejects_multiple_drivers_for_one_input() -> None:
    graph = _graph()
    graph["edges"].append(
        {
            "id": "duplicate-driver",
            "source": "graph-outputs",
            "sourcePort": "spec",
            "target": "graph-run",
            "targetPort": "spec",
        }
    )
    result = validate_visual_graph(graph, expected_capability_id="component.battery.v1")
    codes = {item.code for item in result.errors}
    assert result.ok is False
    assert "GRAPH_INPUT_PORT_MULTIPLE_DRIVERS" in codes


def test_visual_graph_rejects_oversized_payloads() -> None:
    graph = _graph()
    graph["nodes"] = graph["nodes"] + [
        {"id": f"extra-{index}", "type": "config"}
        for index in range(40)
    ]
    graph["edges"] = graph["edges"] + [
        {
            "id": f"extra-edge-{index}",
            "source": "graph-model",
            "sourcePort": "capability",
            "target": "graph-config",
            "targetPort": "capability",
        }
        for index in range(120)
    ]
    result = validate_visual_graph(graph, expected_capability_id="component.battery.v1")
    codes = {item.code for item in result.errors}
    assert result.ok is False
    assert "GRAPH_NODE_LIMIT_EXCEEDED" in codes
    assert "GRAPH_EDGE_LIMIT_EXCEEDED" in codes


def test_visual_graph_rejects_excessively_long_identifiers() -> None:
    graph = _graph()
    graph["nodes"][0]["id"] = "n" * 129
    result = validate_visual_graph(graph, expected_capability_id="component.battery.v1")
    codes = {item.code for item in result.errors}
    assert result.ok is False
    assert "GRAPH_NODE_ID_TOO_LONG" in codes
