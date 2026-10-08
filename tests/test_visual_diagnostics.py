from __future__ import annotations

import copy
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.assembly_graph import assembly_contract
from sat_sim.visual_diagnostics import diagnose_visual_failure


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_flow_runtime_failure_maps_back_to_run_and_capability_nodes() -> None:
    graph = {
        "schema_version": "sat-sim.visual-graph.v1",
        "nodes": [
            {"id": "model-1", "type": "model", "capabilityId": "component.reaction_wheel.v1"},
            {"id": "config-1", "type": "config"},
            {"id": "run-1", "type": "run"},
        ],
        "edges": [],
    }
    diagnostics = diagnose_visual_failure(
        error_text="Basilisk runtime failed while executing component.reaction_wheel.v1",
        reason_code="RUN_EXECUTION_FAILED",
        task_spec={"model": {"capability_id": "component.reaction_wheel.v1"}},
        visual_graph=graph,
    )
    assert diagnostics["ok"] is True
    targets = diagnostics["targets"]
    assert any(item.get("node_id") == "model-1" for item in targets)
    assert any(item.get("node_id") == "run-1" for item in targets)


def test_assembly_runtime_failure_maps_reaction_wheel_and_parent_owner() -> None:
    capability_id = "subsystem.adcs_fidelity.v1"
    contract = assembly_contract(capability_id)
    graph = copy.deepcopy(contract["default_graph"])
    wheel = next(node for node in graph["nodes"] if node["moduleAlias"] == "reaction_wheel")
    wheel["capabilityId"] = "component.reaction_wheel.v1"
    diagnostics = diagnose_visual_failure(
        error_text="runtime error in component.reaction_wheel.v1 while executing wheel state",
        reason_code="RUN_EXECUTION_FAILED",
        task_spec={"model": {"capability_id": capability_id}},
        assembly_graph=graph,
    )
    ids = {item.get("node_id") for item in diagnostics["targets"] if item.get("node_id")}
    assert wheel["id"] in ids
    assert next(node["id"] for node in graph["nodes"] if node["moduleAlias"] == "assembly") in ids


def test_visual_diagnose_api_returns_structured_targets(tmp_path: Path) -> None:
    graph = {
        "schema_version": "sat-sim.visual-graph.v1",
        "nodes": [{"id": "run-node", "type": "run"}],
        "edges": [],
    }
    with _client(tmp_path) as client:
        response = client.post(
            "/visual-composer/diagnose",
            json={"error_text": "worker timeout during execution", "reason_code": "RUN_EXECUTION_FAILED", "visual_graph": graph},
        )
    assert response.status_code == 200
    payload = response.json()["diagnostics"]
    assert payload["schema_version"] == "sat-sim.visual-diagnostics.v1"
    assert payload["mode"] == "flow"
    assert payload["targets"][0]["node_id"] == "run-node"
    assert payload["arbitrary_code_inspected"] is False


def test_visual_diagnose_api_rejects_multiple_graph_sources(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/visual-composer/diagnose",
            json={"error_text": "failed", "visual_graph": {"nodes": [], "edges": []}, "assembly_graph": {"nodes": [], "edges": []}},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "VISUAL_DIAGNOSE_MULTIPLE_GRAPH_SOURCES"
