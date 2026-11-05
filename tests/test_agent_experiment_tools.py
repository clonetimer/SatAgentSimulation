from __future__ import annotations

import pytest

from sat_sim.agent_tools import (
    AgentExperimentTools,
    AgentOperationStore,
    AgentToolError,
    agent_experiment_tool_surface,
)
from sat_sim.experiment_manager import ExperimentStore
from sat_sim.scenario_templates import instantiate_scenario_template


def _tools(tmp_path, *, max_variants=3):
    return AgentExperimentTools(
        experiment_store=ExperimentStore(tmp_path / "experiments.sqlite"),
        operation_store=AgentOperationStore(tmp_path / "agent_operations.sqlite"),
        max_create_variants=max_variants,
    )


def _spec():
    return instantiate_scenario_template("adcs_pointing")


def test_surface_marks_write_boundary():
    surface = agent_experiment_tool_surface(max_create_variants=3)
    create = next(item for item in surface["tools"] if item["name"] == "create_experiment")
    assert create["effect"] == "write"
    assert create["requires_operation_id"] is True
    assert create["launches_runs"] is False
    assert create["max_variants"] == 3
    assert "retry_unknown_write_without_reconciliation" in surface["forbidden"]


def test_preview_reports_agent_variant_budget(tmp_path):
    tools = _tools(tmp_path, max_variants=2)
    preview = tools.preview(
        base_task_spec=_spec(),
        sweep={"simulation.duration_s": [10, 20]},
    )
    assert preview["variant_count"] == 2
    assert preview["write_allowed"] is True
    assert preview["write_budget"]["max_create_variants"] == 2

    too_many = tools.preview(
        base_task_spec=_spec(),
        sweep={"simulation.duration_s": [10, 20, 30]},
    )
    assert too_many["variant_count"] == 3
    assert too_many["write_allowed"] is False


def test_create_experiment_is_idempotent_for_same_operation(tmp_path):
    tools = _tools(tmp_path)
    kwargs = {
        "operation_id": "goal-1:create-exp-1",
        "name": "agent sweep",
        "base_task_spec": _spec(),
        "sweep": {"simulation.duration_s": [10, 20]},
    }
    first = tools.create_experiment(**kwargs)
    second = tools.create_experiment(**kwargs)
    operation = tools.operation_store.get(kwargs["operation_id"])

    assert operation is not None
    assert operation["operation_state"] == "SUCCEEDED"
    assert operation["result"]["experiment_id"] == first["experiment_id"]

    assert first["ok"] is True
    assert first["replayed"] is False
    assert first["launch_permitted"] is False
    assert second["ok"] is True
    assert second["replayed"] is True
    assert second["experiment_id"] == first["experiment_id"]
    assert len(tools.experiment_store.list(limit=10)) == 1


def test_operation_id_cannot_be_reused_for_different_input(tmp_path):
    tools = _tools(tmp_path)
    tools.create_experiment(
        operation_id="goal-2:create-exp",
        name="first",
        base_task_spec=_spec(),
        sweep={"simulation.duration_s": [10, 20]},
    )
    with pytest.raises(AgentToolError) as captured:
        tools.create_experiment(
            operation_id="goal-2:create-exp",
            name="changed",
            base_task_spec=_spec(),
            sweep={"simulation.duration_s": [10, 30]},
        )
    assert captured.value.reason_code == "OPERATION_ID_CONFLICT"
    assert len(tools.experiment_store.list(limit=10)) == 1


def test_create_rejects_variant_budget_before_persisting(tmp_path):
    tools = _tools(tmp_path, max_variants=2)
    with pytest.raises(AgentToolError) as captured:
        tools.create_experiment(
            operation_id="goal-3:create-exp",
            name="too many",
            base_task_spec=_spec(),
            sweep={"simulation.duration_s": [10, 20, 30]},
        )
    assert captured.value.reason_code == "AGENT_EXPERIMENT_VARIANT_BUDGET_EXCEEDED"
    assert tools.experiment_store.list(limit=10) == []


def test_invalid_operation_id_is_rejected(tmp_path):
    tools = _tools(tmp_path)
    with pytest.raises(AgentToolError) as captured:
        tools.create_experiment(
            operation_id="bad operation id with spaces",
            name="invalid id",
            base_task_spec=_spec(),
            sweep={"simulation.duration_s": [10]},
        )
    assert captured.value.reason_code == "INVALID_OPERATION_ID"


def test_agent_experiment_api_is_idempotent_and_budgeted(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app

    monkeypatch.setenv("SAT_SIM_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_CREATE_VARIANTS", "2")
    client = TestClient(create_app())
    spec = _spec()

    tools = client.get("/agent/tools")
    assert tools.status_code == 200
    experiment_tools = tools.json()["experiment_tools"]
    create_contract = next(item for item in experiment_tools["tools"] if item["name"] == "create_experiment")
    assert create_contract["max_variants"] == 2
    assert create_contract["launches_runs"] is False

    plan = client.post(
        "/agent/experiments/plan",
        json={
            "base_task_spec": spec,
            "experiment_type": "sweep",
            "sweep": {"simulation.duration_s": [10, 20]},
        },
    )
    assert plan.status_code == 200
    assert plan.json()["variant_count"] == 2
    assert plan.json()["write_allowed"] is True

    body = {
        "operation_id": "api:create:1",
        "name": "api agent sweep",
        "base_task_spec": spec,
        "experiment_type": "sweep",
        "sweep": {"simulation.duration_s": [10, 20]},
    }
    first = client.post("/agent/experiments", json=body)
    status = client.get("/agent/operations/api:create:1")
    second = client.post("/agent/experiments", json=body)
    assert status.status_code == 200
    assert status.json()["operation_state"] == "SUCCEEDED"
    assert status.json()["result"]["experiment_id"] == first.json()["experiment_id"]
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["experiment_id"] == second.json()["experiment_id"]
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True

    conflict = dict(body)
    conflict["name"] = "changed"
    rejected = client.post("/agent/experiments", json=conflict)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason_code"] == "OPERATION_ID_CONFLICT"

    too_many = dict(body)
    too_many["operation_id"] = "api:create:2"
    too_many["sweep"] = {"simulation.duration_s": [10, 20, 30]}
    budget = client.post("/agent/experiments", json=too_many)
    assert budget.status_code == 422
    assert budget.json()["detail"]["reason_code"] == "AGENT_EXPERIMENT_VARIANT_BUDGET_EXCEEDED"

    missing = client.get("/agent/operations/api:missing")
    assert missing.status_code == 404
    assert missing.json()["detail"]["reason_code"] == "AGENT_OPERATION_NOT_FOUND"


def test_agent_launch_is_budgeted_idempotent_and_queryable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app

    monkeypatch.setenv("SAT_SIM_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_CREATE_VARIANTS", "3")
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_LAUNCH_RUNS", "2")
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_EXECUTION_ATTEMPTS", "2")
    app = create_app(embedded_worker=False)
    client = TestClient(app)
    spec = _spec()

    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "launch-test:create",
            "name": "launch test",
            "base_task_spec": spec,
            "experiment_type": "sweep",
            "sweep": {"simulation.duration_s": [10, 20]},
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_id"]

    body = {
        "operation_id": "launch-test:run",
        "max_attempts": 1,
        "hard_timeout": True,
        "max_run_count": 2,
        "max_execution_attempts": 2,
    }
    launched = client.post(f"/agent/experiments/{experiment_id}/launch", json=body)
    assert launched.status_code == 200
    payload = launched.json()
    assert payload["operation_state"] == "SUCCEEDED"
    assert payload["reserved_run_capacity"] == 2
    assert payload["reserved_attempt_capacity"] == 2
    assert payload["launched_count"] == 2
    assert payload["reconciliation"]["all_assigned"] is True

    jobs = app.state.execution_queue.list_jobs(limit=10)["jobs"]
    assert len(jobs) == 2
    assert all(job["metadata"]["submitted_by"] == "agent" for job in jobs)
    assert all(job["metadata"]["agent_operation_id"] == "launch-test:run" for job in jobs)

    replay = client.post(f"/agent/experiments/{experiment_id}/launch", json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(app.state.execution_queue.list_jobs(limit=10)["jobs"]) == 2

    operation = client.get("/agent/operations/launch-test:run")
    assert operation.status_code == 200
    assert operation.json()["operation_state"] == "SUCCEEDED"
    assert operation.json()["reconciliation"]["all_assigned"] is True

    result = client.get(f"/agent/experiments/{experiment_id}/result")
    assert result.status_code == 200
    assert result.json()["complete"] is False
    assert result.json()["execution"]["assigned_count"] == 2
    assert result.json()["comparison"] is None


def test_agent_launch_budget_rejects_before_queue_side_effect(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app

    monkeypatch.setenv("SAT_SIM_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_CREATE_VARIANTS", "3")
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_LAUNCH_RUNS", "2")
    monkeypatch.setenv("SAT_SIM_AGENT_MAX_EXECUTION_ATTEMPTS", "2")
    app = create_app(embedded_worker=False)
    client = TestClient(app)

    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "budget-test:create",
            "name": "three variants",
            "base_task_spec": _spec(),
            "experiment_type": "sweep",
            "sweep": {"simulation.duration_s": [10, 20, 30]},
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_id"]

    rejected = client.post(
        f"/agent/experiments/{experiment_id}/launch",
        json={
            "operation_id": "budget-test:run",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 3,
            "max_execution_attempts": 3,
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["reason_code"] == "AGENT_GLOBAL_RUN_BUDGET_EXCEEDED"
    assert app.state.execution_queue.list_jobs(limit=10)["jobs"] == []
    members = app.state.experiment_store.members(experiment_id)
    assert all(member["run_id"] is None for member in members)


def test_agent_launch_operation_id_conflict_is_rejected(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app

    monkeypatch.setenv("SAT_SIM_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    app = create_app(embedded_worker=False)
    client = TestClient(app)

    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "conflict:create",
            "name": "conflict",
            "base_task_spec": _spec(),
            "sweep": {"simulation.duration_s": [10]},
        },
    )
    experiment_id = created.json()["experiment_id"]
    body = {
        "operation_id": "conflict:launch",
        "max_attempts": 1,
        "hard_timeout": True,
        "max_run_count": 1,
        "max_execution_attempts": 1,
    }
    assert client.post(f"/agent/experiments/{experiment_id}/launch", json=body).status_code == 200

    changed = dict(body)
    changed["max_attempts"] = 2
    changed["max_execution_attempts"] = 2
    rejected = client.post(f"/agent/experiments/{experiment_id}/launch", json=changed)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["reason_code"] == "OPERATION_ID_CONFLICT"
    assert len(app.state.execution_queue.list_jobs(limit=10)["jobs"]) == 1
