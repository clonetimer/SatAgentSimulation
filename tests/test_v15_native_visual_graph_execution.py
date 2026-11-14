"""Execute a V15 visual-graph compiled TaskSpec with genuine Basilisk.

The graph compiler and Run API are the same contracts used by the V15 editor.
The embedded execution worker launches the real sat_sim CLI; no run-state
fixture or telemetry injection is used in this test.
"""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _original_task_spec() -> dict:
    path = Path(__file__).parent / "fixtures" / "form_projection" / "recorded_create17.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec = copy.deepcopy(spec)
    spec["simulation"]["duration_s"] = 2.0
    spec["simulation"]["step_s"] = 0.2
    spec["simulation"]["sample_s"] = 0.2
    if isinstance(spec["simulation"].get("solver"), dict):
        spec["simulation"]["solver"]["step_s"] = 0.2
    spec["events"]["faults"] = []
    spec["model"]["target"]["mode"] = "nominal"
    spec["parameters"]["values"]["environment_profile"] = "simplified"
    spec["model"]["config"]["environment_profile"] = "simplified"
    spec["outputs"]["output_root"] = "runs/v15-native-visual-graph"
    agent = spec.get("metadata", {}).get("agent")
    if isinstance(agent, dict):
        agent["requires_confirmation"] = False
        agent["confirmation_reasons"] = []
    return spec


def _visual_graph(capability: str) -> dict:
    kinds = ("model", "config", "outputs", "code", "run")
    nodes = [
        {"id": kind, "type": kind, **({"capabilityId": capability} if kind == "model" else {})}
        for kind in kinds
    ]
    connections = (
        ("model", "capability", "config", "capability"),
        ("config", "task", "outputs", "task"),
        ("outputs", "spec", "code", "spec"),
        ("outputs", "spec", "run", "spec"),
    )
    return {
        "schema_version": "sat-sim.visual-graph.v1",
        "nodes": nodes,
        "edges": [
            {"id": str(index), "source": source, "sourcePort": source_port,
             "target": target, "targetPort": target_port}
            for index, (source, source_port, target, target_port) in enumerate(connections)
        ],
    }


def test_v15_visual_graph_compiled_task_executes_with_real_basilisk(tmp_path, monkeypatch):
    import Basilisk

    capability = "whole_spacecraft.unified_native.v1"
    for key, filename in (
        ("SAT_SIM_QUEUE_DATABASE", "queue.sqlite"),
        ("SAT_SIM_TASK_CENTER_DATABASE", "tasks.sqlite"),
        ("SAT_SIM_EXPERIMENT_DATABASE", "experiments.sqlite"),
        ("SAT_SIM_AUDIT_LOG", "audit.jsonl"),
        ("SAT_SIM_WORKBENCH_LOG", "workbench.jsonl"),
    ):
        monkeypatch.setenv(key, str(tmp_path / filename))
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "api",
        embedded_worker=True,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        original = _original_task_spec()
        pair = {
            "task_spec": original,
            "task_spec_json": json.dumps(original, ensure_ascii=False, indent=2),
        }
        projection = client.post(
            f"/forms/capabilities/{capability}/project", json=pair
        )
        assert projection.status_code == 200, projection.text
        form_data = projection.json()["projection"]["form_data"]

        compiled = client.post("/tasks/compile-graph", json={
            "graph": _visual_graph(capability),
            "form_data": form_data,
        })
        assert compiled.status_code == 200, compiled.text
        result = compiled.json()["result"]
        task_spec = result["task_spec"]
        assert task_spec["model"]["capability_id"] == capability
        assert task_spec["simulation"]["backend"] == "basilisk"
        assert task_spec["simulation"]["duration_s"] == 2.0

        created = client.post("/runs", json={
            "task_spec": task_spec,
            "task_spec_json": result["task_spec_json"],
            "execute": True,
            "execution_mode": "async",
        })
        assert created.status_code in (200, 202), created.text
        prepared = created.json().get("prepared_run") or {}
        run_id = prepared.get("run_id") or created.json().get("run_id")
        assert run_id, created.text

        deadline = time.monotonic() + 240
        latest = {}
        while time.monotonic() < deadline:
            response = client.get(f"/runs/{run_id}/execution")
            assert response.status_code == 200, response.text
            latest = response.json().get("execution_state") or response.json()
            state = str(latest.get("state") or "").upper()
            if state in {"SUCCEEDED", "FAILED", "ABORTED", "INTERRUPTED", "CANCELLED"}:
                break
            time.sleep(0.25)
        else:
            raise AssertionError(f"V15 native Run did not reach terminal state: {latest!r}")

        assert state == "SUCCEEDED", latest
        detail_response = client.get(f"/runs/{run_id}")
        assert detail_response.status_code == 200, detail_response.text
        run = detail_response.json()["run"]
        assert run["sealed"] is True
        assert run["integrity"]["ok"] is True
        assert run["run_record"]["status"] == "SUCCEEDED"
        assert run["task_spec"]["model"]["capability_id"] == capability
        assert run["task_spec"]["simulation"]["backend"] == "basilisk"
        assert Basilisk.__version__ == "2.11.0"
        metrics = client.get(f"/runs/{run_id}/metrics")
        assert metrics.status_code == 200, metrics.text
