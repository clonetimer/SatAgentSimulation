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
    second = client.post("/agent/experiments", json=body)
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
