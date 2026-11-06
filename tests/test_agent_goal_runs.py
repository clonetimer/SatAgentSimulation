from __future__ import annotations

import pytest

from sat_sim.goal_runs import GoalRunError, GoalRunStore
from sat_sim.scenario_templates import instantiate_scenario_template


def _store(tmp_path):
    return GoalRunStore(tmp_path / "goals.sqlite")


def _goal(store):
    return store.create(
        goal_id="goal_test",
        objective="比较候选并保留可追溯证据",
        base_task_spec=instantiate_scenario_template("adcs_pointing"),
        allowed_parameter_paths=["simulation.duration_s"],
        success_criteria=[{"metric": "status", "operator": "==", "value": "PASS"}],
        budget={
            "experiments": 2,
            "runs": 4,
            "execution_attempts": 6,
            "model_calls": 3,
        },
    )


def test_goal_create_snapshot_and_transition(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    assert goal.status == "DRAFT"
    ready = store.transition(goal.goal_id, "READY")
    assert ready.status == "READY"
    running = store.transition(goal.goal_id, "RUNNING")
    assert running.status == "RUNNING"

    snapshot = store.snapshot(goal.goal_id)
    assert snapshot["budget"]["remaining"]["experiments"] == 2
    assert snapshot["experiments"] == []
    assert snapshot["decisions"] == []


def test_invalid_goal_transition_is_rejected(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    with pytest.raises(GoalRunError) as captured:
        store.transition(goal.goal_id, "COMPLETED")
    assert captured.value.reason_code == "INVALID_GOAL_TRANSITION"


def test_budget_reservation_is_idempotent_and_aggregate(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    store.transition(goal.goal_id, "READY")

    first = store.reserve_budget(
        goal.goal_id,
        operation_id="op-create-1",
        amounts={"experiments": 1},
    )
    replay = store.reserve_budget(
        goal.goal_id,
        operation_id="op-create-1",
        amounts={"experiments": 1},
    )
    assert replay["reservation_id"] == first["reservation_id"]
    assert replay["replayed"] is True

    second = store.reserve_budget(
        goal.goal_id,
        operation_id="op-create-2",
        amounts={"experiments": 1},
    )
    assert second["state"] == "RESERVED"

    with pytest.raises(GoalRunError) as captured:
        store.reserve_budget(
            goal.goal_id,
            operation_id="op-create-3",
            amounts={"experiments": 1},
        )
    assert captured.value.reason_code == "GOAL_BUDGET_EXCEEDED"

    usage = store.budget_usage(goal.goal_id)
    assert usage["reserved_or_committed"]["experiments"] == 2
    assert usage["remaining"]["experiments"] == 0


def test_same_goal_operation_cannot_change_reservation_amounts(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    store.reserve_budget(goal.goal_id, operation_id="op-1", amounts={"runs": 1})
    with pytest.raises(GoalRunError) as captured:
        store.reserve_budget(goal.goal_id, operation_id="op-1", amounts={"runs": 2})
    assert captured.value.reason_code == "GOAL_RESERVATION_CONFLICT"


def test_commit_and_release_budget_reservations(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    committed = store.reserve_budget(goal.goal_id, operation_id="launch-1", amounts={"runs": 2, "execution_attempts": 2})
    released = store.reserve_budget(goal.goal_id, operation_id="launch-2", amounts={"runs": 1, "execution_attempts": 1})

    store.commit_reservation(goal.goal_id, committed["reservation_id"])
    store.release_reservation(goal.goal_id, released["reservation_id"])

    usage = store.budget_usage(goal.goal_id)
    assert usage["committed"]["runs"] == 2
    assert usage["reserved_or_committed"]["runs"] == 2
    assert usage["remaining"]["runs"] == 2


def test_link_experiment_and_record_decision_are_ordered(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    first = store.link_experiment(
        goal.goal_id,
        "exp-1",
        plan_revision=1,
        purpose="建立基线",
        create_operation_id="create-1",
    )
    replay = store.link_experiment(
        goal.goal_id,
        "exp-1",
        plan_revision=1,
        purpose="建立基线",
        create_operation_id="create-1",
    )
    second = store.link_experiment(
        goal.goal_id,
        "exp-2",
        plan_revision=2,
        purpose="复核候选",
        create_operation_id="create-2",
    )
    assert first["sequence"] == 1
    assert replay["sequence"] == 1
    assert second["sequence"] == 2

    decision = store.record_decision(
        goal.goal_id,
        decision_kind="PLAN",
        summary="先运行基线。",
        evidence={"source": "user_goal"},
        action={"experiment_id": "exp-1"},
    )
    assert decision["sequence"] == 1
    snapshot = store.snapshot(goal.goal_id)
    assert len(snapshot["experiments"]) == 2
    assert snapshot["decisions"][0]["action"]["experiment_id"] == "exp-1"


def test_unbind_launch_operation_requires_matching_binding(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    store.link_experiment(
        goal.goal_id,
        "exp-unbind",
        plan_revision=1,
        purpose="验证启动失败恢复",
    )
    store.bind_launch_operation(goal.goal_id, "exp-unbind", "launch-op-1")

    with pytest.raises(GoalRunError) as captured:
        store.unbind_launch_operation(goal.goal_id, "exp-unbind", "different-op")
    assert captured.value.reason_code == "GOAL_LAUNCH_OPERATION_CONFLICT"

    cleared = store.unbind_launch_operation(goal.goal_id, "exp-unbind", "launch-op-1")
    assert cleared["replayed"] is False
    assert store.experiments(goal.goal_id)[0]["launch_operation_id"] is None
    replay = store.unbind_launch_operation(goal.goal_id, "exp-unbind", "launch-op-1")
    assert replay["replayed"] is True


def test_terminal_goal_rejects_new_budget(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    store.transition(goal.goal_id, "READY")
    store.transition(goal.goal_id, "RUNNING")
    store.transition(goal.goal_id, "CANCELLED", stop_reason="用户取消")
    with pytest.raises(GoalRunError) as captured:
        store.reserve_budget(goal.goal_id, operation_id="after-stop", amounts={"runs": 1})
    assert captured.value.reason_code == "GOAL_TERMINAL"


def test_needs_revision_can_be_completed_after_review(tmp_path):
    store = _store(tmp_path)
    goal = _goal(store)
    store.transition(goal.goal_id, "READY")
    store.transition(goal.goal_id, "RUNNING")
    store.transition(goal.goal_id, "ANALYZING")
    revision = store.transition(goal.goal_id, "NEEDS_REVISION")
    assert revision.status == "NEEDS_REVISION"

    completed = store.transition(
        goal.goal_id,
        "COMPLETED",
        stop_reason="复核后确认已有证据满足目标。",
    )
    assert completed.status == "COMPLETED"
    assert completed.stop_reason == "复核后确认已有证据满足目标。"
