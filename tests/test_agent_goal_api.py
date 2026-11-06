from __future__ import annotations

import pytest

import json
import sqlite3

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
            "operation_id": "goal-api:create-root",
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
            "operation_id": "goal-invalid-allowlist",
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
        "complete": True,
        "evidence_ready": True,
        "runs": [
            {
                "run_id": "goal-exp_v001",
                "execution_state": "SUCCEEDED",
                "validation_result": "PASS",
                "assertion_status": "PASS",
                "terminal": True,
                "sealed": True,
                "integrity": {"ok": True},
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


def test_partial_evidence_interpretation_does_not_advance_running_goal(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    partial = {
        "schema_version": "agent-experiment-evidence.v1",
        "experiment_id": "partial-exp",
        "evidence_ready": False,
        "complete": False,
        "runs": [],
        "comparisons": [],
        "warnings": [{"code": "RUN_NOT_ASSIGNED"}],
        "claim_policy": {
            "causal_explanation": "Not verified by this evidence package alone.",
        },
    }
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: partial)

    _, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 0},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "partial:create",
            "name": "partial",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "阶段性分析",
        },
    )
    experiment_id = created.json()["experiment_operation"]["experiment_id"]
    launched = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json={
            "operation_id": "partial:launch",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 1,
            "max_execution_attempts": 1,
        },
    )
    assert launched.status_code == 200
    assert launched.json()["goal"]["status"] == "RUNNING"

    interpreted = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/interpret",
        json={
            "operation_id": "partial:interpret",
            "question": "当前有什么证据",
            "mode": "deterministic",
        },
    )
    assert interpreted.status_code == 200
    assert interpreted.json()["goal"]["status"] == "RUNNING"
    assert interpreted.json()["analysis_operation"]["result"]["evidence"]["complete"] is False


def test_goal_experiment_cannot_be_relaunched_with_new_operation_or_double_charge_budget(tmp_path):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 2, "execution_attempts": 2, "model_calls": 0},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "single-launch:create",
            "name": "single launch",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "单次 launch 约束",
        },
    )
    experiment_id = created.json()["experiment_operation"]["experiment_id"]
    first = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json={
            "operation_id": "single-launch:run-1",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 1,
            "max_execution_attempts": 1,
        },
    )
    assert first.status_code == 200
    assert first.json()["budget"]["committed"]["runs"] == 1

    second = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json={
            "operation_id": "single-launch:run-2",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 1,
            "max_execution_attempts": 1,
        },
    )
    assert second.status_code == 422
    assert second.json()["detail"]["reason_code"] == "GOAL_EXPERIMENT_ALREADY_LAUNCHED"
    snapshot = client.get(f"/agent/goals/{goal_id}").json()
    assert snapshot["budget"]["reserved_or_committed"]["runs"] == 1
    assert len(app.state.execution_queue.list_jobs(limit=10)["jobs"]) == 1


def test_goal_create_is_idempotent_and_rejects_operation_id_reuse(tmp_path):
    _, client = _client(tmp_path)
    body = {
        "operation_id": "goal-create:stable-op",
        "objective": "比较电池容量并保留证据",
        "base_task_spec": instantiate_scenario_template("adcs_pointing"),
        "allowed_parameter_paths": ["simulation.duration_s"],
        "success_criteria": [{"metric": "status", "operator": "==", "value": "PASS"}],
        "budget": {
            "experiments": 2,
            "runs": 4,
            "execution_attempts": 4,
            "model_calls": 1,
        },
        "activate": True,
    }
    first = client.post("/agent/goals", json=body)
    assert first.status_code == 200
    goal_id = first.json()["goal"]["goal_id"]
    assert first.json()["replayed"] is False
    assert first.json()["goal"]["status"] == "READY"
    decision_count = len(first.json()["decisions"])

    replay = client.post("/agent/goals", json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["goal"]["goal_id"] == goal_id
    assert len(replay.json()["decisions"]) == decision_count

    changed = dict(body)
    changed["objective"] = "改成另一个目标"
    conflict = client.post("/agent/goals", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason_code"] == "GOAL_CREATE_OPERATION_CONFLICT"

    listed = client.get("/agent/goals")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


def test_goal_create_requires_operation_id(tmp_path):
    _, client = _client(tmp_path)
    response = client.post(
        "/agent/goals",
        json={
            "objective": "缺少操作 ID",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "allowed_parameter_paths": ["simulation.duration_s"],
        },
    )
    assert response.status_code == 422


def test_goal_run_store_migrates_legacy_database_for_idempotent_create(tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    database = artifact_root / "goal_runs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE goal_runs (
                goal_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                base_task_spec_json TEXT NOT NULL,
                allowed_parameter_paths_json TEXT NOT NULL,
                success_criteria_json TEXT NOT NULL,
                budget_json TEXT NOT NULL,
                stop_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=artifact_root,
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(goal_runs)")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(goal_runs)")}
    assert "create_operation_id" in columns
    assert "create_input_sha256" in columns
    assert "idx_goal_runs_create_operation_id" in indexes
    assert app.state.goal_run_store is not None


def test_failed_goal_experiment_create_replay_releases_reserved_budget(tmp_path, monkeypatch):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 2, "runs": 4, "execution_attempts": 4, "model_calls": 0},
    )
    goal = app.state.goal_run_store.get(goal_id)
    tool = app.state.agent_experiment_tools
    operation_id = "goal-failed-create:replay"
    sweep = {"simulation.duration_s": [10]}
    original_create = tool.experiment_store.create

    def fail_create(**_kwargs):
        raise RuntimeError("injected create failure")

    monkeypatch.setattr(tool.experiment_store, "create", fail_create)
    try:
        tool.create_experiment(
            operation_id=operation_id,
            name="failed create replay",
            base_task_spec=goal.base_task_spec,
            experiment_type="sweep",
            sweep=sweep,
            sampling_plan={},
            assertions=goal.success_criteria,
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected create failure did not propagate")
    monkeypatch.setattr(tool.experiment_store, "create", original_create)

    app.state.goal_run_store.reserve_budget(
        goal_id,
        operation_id=operation_id,
        amounts={"experiments": 1},
    )
    response = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": operation_id,
            "name": "failed create replay",
            "sweep": sweep,
            "purpose": "验证失败操作恢复时释放预算",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["experiment_operation"]["operation_state"] == "FAILED"
    assert payload["goal"]["budget"]["reserved_or_committed"]["experiments"] == 0
    assert payload["goal"]["budget"]["remaining"]["experiments"] == 2


def test_goal_interpretation_waits_for_every_linked_experiment_to_finish(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: _goal_ready_evidence())
    _, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 2, "runs": 4, "execution_attempts": 4, "model_calls": 0},
    )
    first = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-all-terminal:create-1",
            "name": "first round",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "建立第一轮证据",
        },
    )
    assert first.status_code == 200
    first_experiment_id = first.json()["experiment_operation"]["experiment_id"]
    launched = client.post(
        f"/agent/goals/{goal_id}/experiments/{first_experiment_id}/launch",
        json={
            "operation_id": "goal-all-terminal:launch-1",
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 1,
            "max_execution_attempts": 1,
        },
    )
    assert launched.status_code == 200
    assert launched.json()["goal"]["status"] == "RUNNING"

    second = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-all-terminal:create-2",
            "name": "second round",
            "sweep": {"simulation.duration_s": [20]},
            "purpose": "保留尚待执行的第二轮",
        },
    )
    assert second.status_code == 200

    interpreted = client.post(
        f"/agent/goals/{goal_id}/experiments/{first_experiment_id}/interpret",
        json={
            "operation_id": "goal-all-terminal:interpret",
            "question": "总结第一轮现有证据",
            "mode": "deterministic",
        },
    )
    assert interpreted.status_code == 200
    assert interpreted.json()["goal"]["status"] == "RUNNING"


@pytest.mark.parametrize("release_before_sync", [False, True])
def test_goal_sync_releases_failed_launch_when_no_run_side_effects(tmp_path, release_before_sync):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 0},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-sync-failed:create",
            "name": "failed launch recovery",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "验证同步时回收无副作用的失败启动预算",
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_operation"]["experiment_id"]
    operation_id = "goal-sync-failed:launch"
    request_payload = {
        "schema_version": "agent-experiment-launch.v1",
        "operation_kind": "launch_experiment",
        "experiment_id": experiment_id,
        "max_attempts": 1,
        "hard_timeout": True,
        "max_run_count": 1,
        "max_execution_attempts": 1,
        "reserved_run_capacity": 1,
        "reserved_attempt_capacity": 1,
    }
    app.state.agent_operation_store.reserve_payload(
        operation_id=operation_id,
        operation_kind="launch_experiment",
        request_payload=request_payload,
    )
    app.state.agent_operation_store.fail(
        operation_id,
        {"reason_code": "SYNTHETIC_LAUNCH_FAILURE", "message": "failed before queue side effects"},
    )
    # Reproduce a crash after the GoalRun reservation/binding but before API cleanup.
    reservation = app.state.goal_run_store.reserve_budget(
        goal_id,
        operation_id=operation_id,
        amounts={"runs": 1, "execution_attempts": 1},
    )
    app.state.goal_run_store.bind_launch_operation(goal_id, experiment_id, operation_id)
    assert reservation["state"] == "RESERVED"
    if release_before_sync:
        app.state.goal_run_store.release_reservation(goal_id, reservation["reservation_id"])

    synced = client.post(f"/agent/goals/{goal_id}/sync")
    assert synced.status_code == 200
    assert synced.json()["budget"]["reserved_or_committed"]["runs"] == 0
    assert synced.json()["budget"]["remaining"]["runs"] == 1
    assert synced.json()["experiments"][0]["launch_operation_id"] is None
    assert any(
        item["decision_kind"] == "EXPERIMENT_LAUNCH_FAILED"
        for item in synced.json()["decisions"]
    )


def test_goal_launch_failed_without_side_effects_releases_budget_immediately(tmp_path):
    app, client = _client(tmp_path)
    goal_id = _create_goal(
        client,
        budget={"experiments": 1, "runs": 1, "execution_attempts": 1, "model_calls": 0},
    )
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-launch-immediate:create",
            "name": "immediate launch failure",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "验证明确失败的启动立即回收预算",
        },
    )
    assert created.status_code == 200
    experiment_id = created.json()["experiment_operation"]["experiment_id"]
    operation_id = "goal-launch-immediate:launch"
    request_payload = {
        "schema_version": "agent-experiment-launch.v1",
        "operation_kind": "launch_experiment",
        "experiment_id": experiment_id,
        "max_attempts": 1,
        "hard_timeout": True,
        "max_run_count": 1,
        "max_execution_attempts": 1,
        "reserved_run_capacity": 1,
        "reserved_attempt_capacity": 1,
    }
    app.state.agent_operation_store.reserve_payload(
        operation_id=operation_id,
        operation_kind="launch_experiment",
        request_payload=request_payload,
    )
    app.state.agent_operation_store.fail(
        operation_id,
        {"reason_code": "SYNTHETIC_LAUNCH_FAILURE", "message": "failed before queue side effects"},
    )

    response = client.post(
        f"/agent/goals/{goal_id}/experiments/{experiment_id}/launch",
        json={
            "operation_id": operation_id,
            "max_attempts": 1,
            "hard_timeout": True,
            "max_run_count": 1,
            "max_execution_attempts": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["budget_released"] is True
    assert payload["reconciliation_required"] is False
    assert payload["budget"]["reserved_or_committed"]["runs"] == 0
    assert payload["budget"]["remaining"]["runs"] == 1
    assert payload["experiments"][0]["launch_operation_id"] is None

def test_goal_resolution_requires_complete_evidence_and_is_idempotent(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    evidence_result = _goal_ready_evidence()
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: evidence_result)
    app, client = _client(tmp_path)
    goal_id = _create_goal(client)
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-resolution:create",
            "name": "resolution evidence",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "建立目标结论证据",
        },
    )
    assert created.status_code == 200
    app.state.goal_run_store.transition(goal_id, "RUNNING")
    app.state.goal_run_store.transition(goal_id, "ANALYZING")

    incomplete = dict(_goal_ready_evidence())
    incomplete["complete"] = False
    incomplete["evidence_ready"] = False
    evidence_result = incomplete
    body = {
        "operation_id": "goal-resolution:complete",
        "status": "COMPLETED",
        "reason": "当前证据尚未完成，先验证接口门禁。",
    }
    blocked = client.post(f"/agent/goals/{goal_id}/resolve", json=body)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["reason_code"] == "GOAL_EVIDENCE_INCOMPLETE"
    assert client.get(f"/agent/goals/{goal_id}").json()["goal"]["status"] == "ANALYZING"

    evidence_result = _goal_ready_evidence()
    body["reason"] = "所有运行均已封存，证据完整，至少一个候选满足目标成功条件。"
    first = client.post(f"/agent/goals/{goal_id}/resolve", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False
    assert first.json()["goal"]["status"] == "COMPLETED"
    decision_count = len(first.json()["decisions"])

    replay = client.post(f"/agent/goals/{goal_id}/resolve", json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert len(replay.json()["decisions"]) == decision_count

    changed = dict(body)
    changed["reason"] = "试图复用操作 ID 修改结论。"
    conflict = client.post(f"/agent/goals/{goal_id}/resolve", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason_code"] == "GOAL_RESOLUTION_OPERATION_CONFLICT"


def test_goal_completion_rejects_pass_assertion_without_successful_validated_execution(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    app, client = _client(tmp_path)
    goal_id = _create_goal(client)
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-execution-gate:create",
            "name": "execution gate",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "验证执行和验证状态也是目标完成门槛",
        },
    )
    assert created.status_code == 200
    app.state.goal_run_store.transition(goal_id, "RUNNING")
    app.state.goal_run_store.transition(goal_id, "ANALYZING")

    invalid_success = _goal_ready_evidence()
    invalid_success["runs"][0]["execution_state"] = "FAILED"
    invalid_success["runs"][0]["validation_result"] = "FAIL"
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: invalid_success)

    response = client.post(
        f"/agent/goals/{goal_id}/resolve",
        json={
            "operation_id": "goal-execution-gate:complete",
            "status": "COMPLETED",
            "reason": "断言虽为 PASS，但运行执行和验证均失败，不应完成。",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "GOAL_SUCCESS_CRITERIA_NOT_MET"
    assert client.get(f"/agent/goals/{goal_id}").json()["goal"]["status"] == "ANALYZING"


def test_goal_completion_rejects_unmet_success_criteria_but_allows_partial(tmp_path, monkeypatch):
    import sat_sim.api as api_module

    evidence_result = dict(_goal_ready_evidence())
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: evidence_result)
    app, client = _client(tmp_path)
    goal_id = _create_goal(client)
    created = client.post(
        f"/agent/goals/{goal_id}/experiments",
        json={
            "operation_id": "goal-partial:create",
            "name": "partial evidence",
            "sweep": {"simulation.duration_s": [10]},
            "purpose": "验证成功条件未满足时的部分结论",
        },
    )
    assert created.status_code == 200
    app.state.goal_run_store.transition(goal_id, "RUNNING")
    app.state.goal_run_store.transition(goal_id, "ANALYZING")

    failed_criteria = dict(_goal_ready_evidence())
    failed_criteria["runs"] = [
        {
            "run_id": "goal-exp_v001",
            "execution_state": "SUCCEEDED",
            "validation_result": "PASS",
            "assertion_status": "FAIL",
            "terminal": True,
            "sealed": True,
            "integrity": {"ok": True},
            "metrics": {"final_pointing_error_deg": 0.8},
        }
    ]
    evidence_result = failed_criteria
    completed = client.post(
        f"/agent/goals/{goal_id}/resolve",
        json={
            "operation_id": "goal-partial:complete",
            "status": "COMPLETED",
            "reason": "尝试在成功条件未满足时标记完成。",
        },
    )
    assert completed.status_code == 409
    assert completed.json()["detail"]["reason_code"] == "GOAL_SUCCESS_CRITERIA_NOT_MET"

    partial = client.post(
        f"/agent/goals/{goal_id}/resolve",
        json={
            "operation_id": "goal-partial:partial",
            "status": "PARTIAL",
            "reason": "运行已结束，但没有候选满足全部成功条件。",
        },
    )
    assert partial.status_code == 200
    assert partial.json()["goal"]["status"] == "PARTIAL"
