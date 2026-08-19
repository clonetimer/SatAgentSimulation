from __future__ import annotations

import json
from pathlib import Path

from sat_sim.dag_harness import SimulationDAGHarness, dag_schema, mutation_schema
from sat_sim.dag_mutations import DAGMutation, SimulationDAG
from sat_sim.dag_validation import dag_state_hash, validate_dag
from sat_sim.release_closure import representative_release_forms


def _subsystem_form() -> dict:
    return next(item["form"] for item in representative_release_forms() if item["case_id"] == "subsystem_nominal")


def test_g2_dag_and_mutation_schemas_are_versioned() -> None:
    d_schema = dag_schema()
    m_schema = mutation_schema()
    assert d_schema["title"] == "Satellite Simulation DAG v1"
    assert m_schema["title"] == "Satellite Simulation DAG Mutation v1"
    assert "nodes" in d_schema["properties"]
    assert "operation" in m_schema["properties"]


def test_g2_state_hash_is_stable_for_same_dag_content() -> None:
    payload = {
        "dag_id": "stable",
        "version": 1,
        "nodes": [],
        "edges": [],
        "state_hash": "",
        "created_at": "2026-07-29T00:00:00Z",
        "updated_at": "2026-07-29T00:00:01Z",
        "source_request": "demo",
        "provenance": {},
    }
    first = SimulationDAG.model_validate(payload)
    second_payload = dict(payload)
    second_payload["updated_at"] = "2026-07-29T00:00:02Z"
    second_payload["state_hash"] = "ignored"
    second = SimulationDAG.model_validate(second_payload)
    assert dag_state_hash(first) == dag_state_hash(second)


def test_g2_store_rejects_stale_base_hash_and_preserves_current_dag(tmp_path: Path) -> None:
    harness = SimulationDAGHarness.from_root(str(tmp_path))
    dag, _ = harness.create_dag(dag_id="stale", actor="test", mutation_id="m0")
    current_hash = dag.state_hash
    next_dag, first = harness.commit(DAGMutation(
        mutation_id="m1",
        actor="test",
        operation="add_node",
        base_dag_hash=current_hash,
        payload={"node": {"node_id": "n1", "node_type": "intent_parse", "payload": {"request": "demo"}}},
    ))
    assert first.committed is True
    preserved, stale = harness.commit(DAGMutation(
        mutation_id="m2",
        actor="test",
        operation="add_node",
        base_dag_hash=current_hash,
        payload={"node": {"node_id": "n2", "node_type": "intent_parse"}},
    ))
    assert stale.committed is False
    assert stale.validation_result and stale.validation_result.errors[0]["code"] == "DAG_STALE_BASE_HASH"
    assert preserved.state_hash == next_dag.state_hash
    assert len(harness.current().nodes) == 1


def test_g2_validation_failure_is_atomic(tmp_path: Path) -> None:
    harness = SimulationDAGHarness.from_root(str(tmp_path))
    dag, _ = harness.create_dag(dag_id="atomic", actor="test", mutation_id="m0")
    before_hash = dag.state_hash
    before_json = (tmp_path / "dag.json").read_text(encoding="utf-8")
    _, rejected = harness.commit(DAGMutation(
        mutation_id="m1",
        actor="test",
        operation="connect_edge",
        base_dag_hash=before_hash,
        payload={
            "edge": {
                "edge_id": "e1",
                "source_node_id": "missing-a",
                "target_node_id": "missing-b",
                "edge_type": "depends_on",
            }
        },
    ))
    assert rejected.committed is False
    assert rejected.validation_result and rejected.validation_result.errors
    assert harness.current().state_hash == before_hash
    assert (tmp_path / "dag.json").read_text(encoding="utf-8") == before_json


def test_g2_graph_validation_rejects_cycles(tmp_path: Path) -> None:
    harness = SimulationDAGHarness.from_root(str(tmp_path))
    dag, _ = harness.create_dag(dag_id="cycle", actor="test", mutation_id="m0")
    dag, _ = harness.commit(DAGMutation(
        mutation_id="m1",
        actor="test",
        operation="add_node",
        base_dag_hash=dag.state_hash,
        payload={"node": {"node_id": "a", "node_type": "intent_parse"}},
    ))
    dag, _ = harness.commit(DAGMutation(
        mutation_id="m2",
        actor="test",
        operation="add_node",
        base_dag_hash=dag.state_hash,
        payload={"node": {"node_id": "b", "node_type": "taskspec_draft", "payload": {"form": _subsystem_form()}}},
    ))
    dag_payload = harness.current().model_dump(mode="json")
    dag_payload["edges"] = [
        {"edge_id": "e1", "source_node_id": "a", "target_node_id": "b", "edge_type": "produces", "payload": {}, "provenance": {}},
        {"edge_id": "e2", "source_node_id": "b", "target_node_id": "a", "edge_type": "depends_on", "payload": {}, "provenance": {}},
    ]
    result = validate_dag(SimulationDAG.model_validate(dag_payload))
    assert result.ok is False
    assert any(error["code"] == "DAG_CYCLE" for error in result.errors)


def test_g2_exports_taskspec_and_execution_plan_from_dag(tmp_path: Path) -> None:
    harness = SimulationDAGHarness.from_root(str(tmp_path))
    dag, _ = harness.create_dag(dag_id="export", actor="test", mutation_id="m0", source_request="EPS nominal")
    dag, mutation = harness.commit(DAGMutation(
        mutation_id="m1",
        actor="test",
        operation="add_node",
        base_dag_hash=dag.state_hash,
        payload={
            "node": {
                "node_id": "draft",
                "node_type": "taskspec_draft",
                "payload": {"form": _subsystem_form()},
            }
        },
    ))
    assert mutation.committed is True
    exported = harness.export_execution_plan()
    assert exported.ok is True
    assert exported.task_spec is not None
    assert exported.task_spec["schema_version"] == "1.0.0"
    assert exported.task_spec["model"]["capability_id"] == "subsystem.eps.basic.v1"
    assert exported.planning is not None
    assert exported.planning.execution_plan is not None
    assert exported.planning.execution_plan.plan_sha256

    report = {
        "ok": True,
        "dag_hash": dag.state_hash,
        "mutation_committed": mutation.committed,
        "task_spec_id": exported.task_spec["task"]["id"],
        "execution_plan_sha256": exported.planning.execution_plan.plan_sha256,
    }
    (tmp_path / "g2_dag_harness_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
