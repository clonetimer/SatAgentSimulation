from __future__ import annotations

import json

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.llm_backends import LLMCallResult


def _client(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    return app, TestClient(app)


def _create_goal(client, *, budget=None, activate=True):
    response = client.post(
        "/agent/goals",
        json={
            "objective": "比较候选参数并保留可追溯证据",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "allowed_parameter_paths": ["simulation.duration_s"],
            "success_criteria": [{"metric": "status", "operator": "==", "value": "PASS"}],
            "budget": budget or {
                "experiments": 2,
                "runs": 4,
                "execution_attempts": 4,
                "model_calls": 2,
            },
            "activate": activate,
        },
    )
    assert response.status_code == 200
    return response.json()["goal"]["goal_id"]


def test_goal_api_create_experiment_launch_and_replay_are_budget_idempotent(tmp_path):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 2, "execution_attempts": 2, "model_calls": 1},
    )

    create_body = {
        "operation_id": "goal-api:create-1",
        "name": "goal sweep",
        "sweep": {"simulation.duration_s": [10, 20]},
        "plan_revision": 1,
        "purpose": "比较两个已登记时长候选",
    }
    created = client.post(f"/agent/goals/{goal_id}/experiments", json=create_body)
    assert created.status_code == 200
    payload = created.json()
    experiment_id = payload["experiment_operation"]["experiment_id"]
    assert payload["budget"]["committed"]["experiments"] == 1
    assert len(payload["experiments"]) == 1
    decision_count = len(payload["decisions"])

    replay = client.post(f"/agent/goals/{goal_id}/experiments", json=create_body)
    assert replay.status_code == 200
    assert replay.json()["experiment_operation"]["replayed"] is True
    assert len(replay.json()["experiments"]) == 1
    assert len(replay.json()["decisions"]) == decision_count
    assert replay.json()["budget"]["committed"]["experiments"] == 1

    launch_body = {
        "operation_id": "goal-api:launch-1",
        "max_attempts": 1,
        "hard_timeout": True,
        "max_run_count": 2,
        "max_execution_attempts": 2,
    }
    launched = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json=launch_body,
    )
    assert launched.status_code == 200
    launched_payload = launched.json()
    assert launched_payload["goal"]["status"] == "RUNNING"
    assert launched_payload["budget"]["committed"]["runs"] == 2
    assert launched_payload["budget"]["committed"]["execution_attempts"] == 2
    jobs = app.state.execution_queue.list_jobs(limit=10)["jobs"]
    assert len(jobs) == 2
    assert all(job["metadata"]["agent_operation_id"] == "goal-api:launch-1" for job in jobs)

    launch_decisions = len(launched_payload["decisions"])
    replay_launch = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json=launch_body,
    )
    assert replay_launch.status_code == 200
    assert replay_launch.json()["launch_operation"]["replayed"] is True
    assert len(app.state.execution_queue.list_jobs(limit=10)["jobs"]) == 2
    assert len(replay_launch.json()["decisions"]) == launch_decisions
    assert replay_launch.json()["budget"]["committed"]["runs"] == 2


def test_goal_experiment_creation_rejects_out_of_scope_parameter_without_budget_use(tmp_path):
    _, client = _client(tmp_path)
    goal_id = _create_goal(client)

    response = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-scope:create",
            "name": "out of scope",
            "sweep": {"parameters.values.invented": [1]},
            "purpose": "不应允许",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "GOAL_PARAMETER_OUT_OF_SCOPE"

    snapshot = client.get(f"/agent/goals/{goal_id}")
    assert snapshot.status_code == 200
    assert snapshot.json()["budget"]["reserved_or_committed"]["experiments"] == 0
    assert snapshot.json()["experiments"] == []


def test_goal_aggregate_experiment_budget_blocks_second_experiment(tmp_path):
    _, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 4, "execution_attempts": 4, "model_calls": 1},
    )

    first = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-budget:create-1",
            "name": "first",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "第一轮",
        },
    )
    assert first.status_code == 200

    second = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-budget:create-2",
            "name": "second",
            "sweep": {"simulation.duration_s": [20]},
            "purpose": "第二轮",
        },
    )
    assert second.status_code == 422
    assert second.json()["detail"]["reason_code"] == "GOAL_BUDGET_EXCEEDED"
    snapshot = client.get(f"/agent/goals/{goal_id}").json()
    assert len(snapshot["experiments"]) == 1


def test_goal_run_budget_blocks_launch_before_queue_side_effect(tmp_path):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 1},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-run-budget:create",
            "name": "two variants",
            "sweep": {"simulation.duration_s": [10, 20]},
            "purpose": "测试总运行预算",
        },
    )
    experiment_id = created.json()["experiment_operation"]["experiment_id"]

    rejected = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json={
            "operation_id": "goal-run-budget:launch",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 2,
            "max_execution_attempts": 2,
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["reason_code"] == "GOAL_BUDGET_EXCEEDED"
    assert app.state.execution_queue.list_jobs(limit=10)["jobs"] == []


def test_draft_goal_cannot_create_experiment(tmp_path):
    _, client = _client(tmp_path)
    goal_id = _create_goal(client, activate=False)
    response = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "draft:create",
            "name": "draft",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "草稿不应执行",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "GOAL_NOT_READY"


def test_goal_parameter_allowlist_must_be_registered(tmp_path):
    _, client = _client(tmp_path)
    response = client.post(
        "/agent/goals",
        json={
            "objective": "非法参数范围",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "allowed_parameter_paths": ["parameters.values.not_registered"],
            "budget": {"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 1},
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "GOAL_PARAMETER_NOT_REGISTERED"


class _GoalFakeBackend:
    name = "goal-fake"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate_text(self, prompt):
        self.calls += 1
        return LLMCallResult(
            ok=True,
            backend=self.name,
            raw_text=json.dumps(self.payload, ensure_ascii=False),
            prompt_chars=len(prompt),
            metadata={"model": "goal-fake-model"},
        )


def _goal_ready_evidence():
    return {
        "schema_version": "agent-experiment-evidence.v1",
        "experiment_id": "goal-exp",
        "evidence_ready": True,
        "runs": [
            {
                "run_id": "goal-exp_v001",
                "metrics": {"final_pointing_error_deg": 0.12},
            }
        ],
        "comparisons": [],
        "warnings": [],
        "claim_policy": {
            "causal_explanation": "Not verified by this evidence package alone.",
        },
    }


def _goal_interpretation_payload():
    return {
        "schema_version": "agent-evidence-interpretation.v1",
        "analysis_status": "EVIDENCE_READY",
        "observations": [
            {
                "statement": "该运行提供了可引用的姿态结果。",
                "evidence": [
                    {
                        "run_id": "goal-exp_v001",
                        "metric": "final_pointing_error_deg",
                    }
                ],
            }
        ],
        "comparisons": [],
        "hypotheses": [],
        "next_experiments": [],
        "unresolved": [],
    }


def test_goal_model_interpretation_consumes_one_call_and_replay_is_free(tmp_path, monkeypatch):
    import sat_sim.api as api_module
    import sat_sim.evidence_interpreter as interpreter_module

    provider_path = tmp_path / "providers.json"
    provider_path.write_text(
        json.dumps({
            "providers": [{
                "provider_id": "goal-provider",
                "label": "goal-provider",
                "backend": "openai_compatible",
                "location": "local",
                "tier": "L2",
                "model": "goal-fake-model",
                "base_url": "http://127.0.0.1:9999/v1",
                "enabled": True,
            }]
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("SAT_SIM_MODEL_PROVIDERS_FILE", str(provider_path))
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: _goal_ready_evidence())
    backend = _GoalFakeBackend(_goal_interpretation_payload())
    monkeypatch.setattr(interpreter_module, "make_text_llm_backend", lambda _config: backend)

    _, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 1},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-model:create",
            "name": "model evidence",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "为受约束解释建立实验引用",
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_operation"]["experiment_id"]

    body = {
        "operation_id": "goal-model:interpret-1",
        "question": "总结现有证据",
        "mode": "model",
        "provider_id": "goal-provider",
        "metrics": ["final_pointing_error_deg"],
    }
    first = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/interpret",
        json=body,
    )
    assert first.status_code == 200
    assert backend.calls == 1
    assert first.json()["budget"]["committed"]["model_calls"] == 1
    decision_count = len(first.json()["decisions"])

    replay = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/interpret",
        json=body,
    )
    assert replay.status_code == 200
    assert replay.json()["analysis_operation"]["replayed"] is True
    assert backend.calls == 1
    assert replay.json()["budget"]["committed"]["model_calls"] == 1
    assert len(replay.json()["decisions"]) == decision_count

    second = dict(body)
    second["operation_id"] = "goal-model:interpret-2"
    rejected = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/interpret",
        json=second,
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["reason_code"] == "GOAL_BUDGET_EXCEEDED"
    assert backend.calls == 1


def test_goal_deterministic_interpretation_does_not_consume_model_budget(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: _goal_ready_evidence())
    _, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 0},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-det:create",
            "name": "det evidence",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "确定性证据解释",
        },
    )
    experiment_id = created.json()["experiment_operation"]["experiment_id"]
    response = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/interpret",
        json={
            "operation_id": "goal-det:interpret",
            "question": "整理现有证据",
            "mode": "deterministic",
            "metrics": ["final_pointing_error_deg"],
        },
    )
    assert response.status_code == 200
    assert response.json()["analysis_operation"]["result"]["analysis"]["mode"] == "deterministic"
    assert response.json()["budget"]["committed"]["model_calls"] == 0
    assert response.json()["budget"]["remaining"]["model_calls"] == 0
