"""Persistent GoalRun coordination ledger for bounded Agent experimentation.

GoalRun records why experiments are being performed and enforces aggregate
budgets across multiple experiment operations. ExperimentStore, execution queue
and Run Bundles remain authoritative for experiment/run state and evidence.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from .sqlite_connections import connect
from .task_models import canonicalize_task_spec

GOAL_RUN_SCHEMA_VERSION = "agent-goal-run.v1"
TERMINAL_GOAL_STATES = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
GOAL_STATES = {
    "DRAFT",
    "READY",
    "RUNNING",
    "ANALYZING",
    "NEEDS_REVISION",
    *TERMINAL_GOAL_STATES,
}
_ALLOWED_TRANSITIONS = {
    "DRAFT": {"READY", "CANCELLED", "FAILED"},
    "READY": {"RUNNING", "CANCELLED", "FAILED"},
    "RUNNING": {"ANALYZING", "NEEDS_REVISION", "PARTIAL", "FAILED", "CANCELLED", "COMPLETED"},
    "ANALYZING": {"RUNNING", "NEEDS_REVISION", "PARTIAL", "FAILED", "CANCELLED", "COMPLETED"},
    "NEEDS_REVISION": {"READY", "RUNNING", "PARTIAL", "FAILED", "CANCELLED"},
    "COMPLETED": set(),
    "PARTIAL": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}
_BUDGET_KEYS = ("experiments", "runs", "execution_attempts", "model_calls")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


class GoalRunError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "message": self.message}


@dataclass(frozen=True)
class GoalRunRecord:
    goal_id: str
    objective: str
    status: str
    base_task_spec: dict[str, Any]
    allowed_parameter_paths: list[str]
    success_criteria: list[dict[str, Any]]
    budget: dict[str, int]
    stop_reason: str | None
    created_at: str
    updated_at: str

    def to_dict(self, *, include_task_spec: bool = True) -> dict[str, Any]:
        value = asdict(self)
        if not include_task_spec:
            value.pop("base_task_spec", None)
        return value


class GoalRunStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS goal_runs (
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
                );
                CREATE TABLE IF NOT EXISTS goal_budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL,
                    operation_id TEXT,
                    amounts_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(goal_id, operation_id),
                    FOREIGN KEY(goal_id) REFERENCES goal_runs(goal_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS goal_experiments (
                    goal_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    plan_revision INTEGER NOT NULL,
                    purpose TEXT NOT NULL,
                    create_operation_id TEXT,
                    launch_operation_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(goal_id, experiment_id),
                    UNIQUE(goal_id, sequence),
                    FOREIGN KEY(goal_id) REFERENCES goal_runs(goal_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS goal_decisions (
                    goal_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    decision_kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(goal_id, sequence),
                    FOREIGN KEY(goal_id) REFERENCES goal_runs(goal_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_goal_runs_updated ON goal_runs(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_goal_reservations_goal ON goal_budget_reservations(goal_id, state);
                CREATE INDEX IF NOT EXISTS idx_goal_experiments_goal ON goal_experiments(goal_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_goal_decisions_goal ON goal_decisions(goal_id, sequence);
                """
            )

    @staticmethod
    def _normalize_budget(budget: Mapping[str, Any]) -> dict[str, int]:
        normalized: dict[str, int] = {}
        defaults = {
            "experiments": 4,
            "runs": 12,
            "execution_attempts": 24,
            "model_calls": 8,
        }
        for key in _BUDGET_KEYS:
            raw = budget.get(key, defaults[key])
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
                raise GoalRunError("INVALID_GOAL_BUDGET", f"{key} must be a non-negative integer")
            normalized[key] = int(raw)
        return normalized

    @staticmethod
    def _row(row: sqlite3.Row | None) -> GoalRunRecord | None:
        if row is None:
            return None
        return GoalRunRecord(
            goal_id=str(row["goal_id"]),
            objective=str(row["objective"]),
            status=str(row["status"]),
            base_task_spec=_json_load(row["base_task_spec_json"], {}),
            allowed_parameter_paths=list(_json_load(row["allowed_parameter_paths_json"], [])),
            success_criteria=list(_json_load(row["success_criteria_json"], [])),
            budget=dict(_json_load(row["budget_json"], {})),
            stop_reason=str(row["stop_reason"]) if row["stop_reason"] is not None else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def create(
        self,
        *,
        objective: str,
        base_task_spec: Mapping[str, Any],
        allowed_parameter_paths: Sequence[str],
        success_criteria: Sequence[Mapping[str, Any]] = (),
        budget: Mapping[str, Any] | None = None,
        goal_id: str | None = None,
    ) -> GoalRunRecord:
        objective = str(objective or "").strip()
        if not objective:
            raise GoalRunError("GOAL_OBJECTIVE_REQUIRED", "objective is required")
        canonical = canonicalize_task_spec(base_task_spec)
        normalized_budget = self._normalize_budget(dict(budget or {}))
        allowed = list(dict.fromkeys(str(path) for path in allowed_parameter_paths if str(path).strip()))
        resolved_id = str(goal_id or f"goal_{uuid4().hex[:16]}")
        now = _now()
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO goal_runs(
                        goal_id, objective, status, base_task_spec_json,
                        allowed_parameter_paths_json, success_criteria_json,
                        budget_json, stop_reason, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        resolved_id,
                        objective,
                        "DRAFT",
                        _json_dump(canonical),
                        _json_dump(allowed),
                        _json_dump([dict(item) for item in success_criteria]),
                        _json_dump(normalized_budget),
                        None,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise GoalRunError("GOAL_ID_CONFLICT", f"goal_id already exists: {resolved_id}") from exc
            row = connection.execute("SELECT * FROM goal_runs WHERE goal_id=?", (resolved_id,)).fetchone()
        record = self._row(row)
        assert record is not None
        return record

    def get(self, goal_id: str) -> GoalRunRecord | None:
        with self._connect() as connection:
            return self._row(connection.execute("SELECT * FROM goal_runs WHERE goal_id=?", (str(goal_id),)).fetchone())

    def list(self, *, limit: int = 100) -> list[GoalRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM goal_runs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [record for row in rows if (record := self._row(row)) is not None]

    def transition(self, goal_id: str, status: str, *, stop_reason: str | None = None) -> GoalRunRecord:
        target = str(status or "").upper()
        if target not in GOAL_STATES:
            raise GoalRunError("INVALID_GOAL_STATE", f"unsupported goal state: {target}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM goal_runs WHERE goal_id=?", (goal_id,)).fetchone()
            current = self._row(row)
            if current is None:
                raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
            if target != current.status and target not in _ALLOWED_TRANSITIONS.get(current.status, set()):
                raise GoalRunError(
                    "INVALID_GOAL_TRANSITION",
                    f"cannot transition goal from {current.status} to {target}",
                )
            reason = stop_reason if target in TERMINAL_GOAL_STATES else None
            connection.execute(
                "UPDATE goal_runs SET status=?, stop_reason=?, updated_at=? WHERE goal_id=?",
                (target, reason, _now(), goal_id),
            )
            updated = self._row(connection.execute("SELECT * FROM goal_runs WHERE goal_id=?", (goal_id,)).fetchone())
        assert updated is not None
        return updated

    def budget_usage(self, goal_id: str) -> dict[str, Any]:
        goal = self.get(goal_id)
        if goal is None:
            raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
        reserved = {key: 0 for key in _BUDGET_KEYS}
        committed = {key: 0 for key in _BUDGET_KEYS}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT amounts_json, state FROM goal_budget_reservations WHERE goal_id=?",
                (goal_id,),
            ).fetchall()
        for row in rows:
            amounts = _json_load(row["amounts_json"], {})
            if row["state"] in {"RESERVED", "COMMITTED"}:
                for key in _BUDGET_KEYS:
                    reserved[key] += int(amounts.get(key, 0) or 0)
            if row["state"] == "COMMITTED":
                for key in _BUDGET_KEYS:
                    committed[key] += int(amounts.get(key, 0) or 0)
        remaining = {key: max(0, int(goal.budget[key]) - reserved[key]) for key in _BUDGET_KEYS}
        return {
            "limit": dict(goal.budget),
            "reserved_or_committed": reserved,
            "committed": committed,
            "remaining": remaining,
        }

    def reserve_budget(
        self,
        goal_id: str,
        *,
        operation_id: str,
        amounts: Mapping[str, int],
    ) -> dict[str, Any]:
        normalized = {key: int(amounts.get(key, 0) or 0) for key in _BUDGET_KEYS}
        if any(value < 0 for value in normalized.values()):
            raise GoalRunError("INVALID_GOAL_BUDGET_RESERVATION", "budget reservation cannot be negative")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            goal_row = connection.execute("SELECT * FROM goal_runs WHERE goal_id=?", (goal_id,)).fetchone()
            goal = self._row(goal_row)
            if goal is None:
                raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
            if goal.status in TERMINAL_GOAL_STATES:
                raise GoalRunError("GOAL_TERMINAL", f"goal is terminal: {goal.status}")
            existing = connection.execute(
                "SELECT * FROM goal_budget_reservations WHERE goal_id=? AND operation_id=?",
                (goal_id, operation_id),
            ).fetchone()
            if existing is not None:
                stored = dict(_json_load(existing["amounts_json"], {}))
                if stored != normalized:
                    raise GoalRunError(
                        "GOAL_RESERVATION_CONFLICT",
                        "operation_id is already bound to a different budget reservation",
                    )
                return {
                    "reservation_id": str(existing["reservation_id"]),
                    "state": str(existing["state"]),
                    "amounts": stored,
                    "replayed": True,
                }

            active = {key: 0 for key in _BUDGET_KEYS}
            rows = connection.execute(
                "SELECT amounts_json FROM goal_budget_reservations WHERE goal_id=? AND state IN ('RESERVED','COMMITTED')",
                (goal_id,),
            ).fetchall()
            for row in rows:
                item = _json_load(row["amounts_json"], {})
                for key in _BUDGET_KEYS:
                    active[key] += int(item.get(key, 0) or 0)
            for key in _BUDGET_KEYS:
                if active[key] + normalized[key] > int(goal.budget[key]):
                    raise GoalRunError(
                        "GOAL_BUDGET_EXCEEDED",
                        f"{key} budget exceeded: {active[key]} + {normalized[key]} > {goal.budget[key]}",
                    )
            reservation_id = f"gres_{uuid4().hex[:16]}"
            connection.execute(
                """
                INSERT INTO goal_budget_reservations(
                    reservation_id, goal_id, operation_id, amounts_json,
                    state, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (reservation_id, goal_id, operation_id, _json_dump(normalized), "RESERVED", now, now),
            )
        return {
            "reservation_id": reservation_id,
            "state": "RESERVED",
            "amounts": normalized,
            "replayed": False,
        }

    def _set_reservation_state(self, goal_id: str, reservation_id: str, state: Literal["COMMITTED", "RELEASED"]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM goal_budget_reservations WHERE goal_id=? AND reservation_id=?",
                (goal_id, reservation_id),
            ).fetchone()
            if row is None:
                raise GoalRunError("GOAL_RESERVATION_NOT_FOUND", "budget reservation not found")
            current = str(row["state"])
            if current == state:
                return {"reservation_id": reservation_id, "state": state, "replayed": True}
            if current != "RESERVED":
                raise GoalRunError(
                    "GOAL_RESERVATION_STATE_CONFLICT",
                    f"cannot change reservation from {current} to {state}",
                )
            connection.execute(
                "UPDATE goal_budget_reservations SET state=?, updated_at=? WHERE reservation_id=?",
                (state, _now(), reservation_id),
            )
        return {"reservation_id": reservation_id, "state": state, "replayed": False}

    def commit_reservation(self, goal_id: str, reservation_id: str) -> dict[str, Any]:
        return self._set_reservation_state(goal_id, reservation_id, "COMMITTED")

    def release_reservation(self, goal_id: str, reservation_id: str) -> dict[str, Any]:
        return self._set_reservation_state(goal_id, reservation_id, "RELEASED")

    def link_experiment(
        self,
        goal_id: str,
        experiment_id: str,
        *,
        plan_revision: int,
        purpose: str,
        create_operation_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM goal_runs WHERE goal_id=?", (goal_id,)).fetchone() is None:
                raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
            existing = connection.execute(
                "SELECT * FROM goal_experiments WHERE goal_id=? AND experiment_id=?",
                (goal_id, experiment_id),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            sequence = int(connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM goal_experiments WHERE goal_id=?",
                (goal_id,),
            ).fetchone()[0])
            connection.execute(
                """
                INSERT INTO goal_experiments(
                    goal_id, experiment_id, sequence, plan_revision, purpose,
                    create_operation_id, launch_operation_id, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    goal_id,
                    experiment_id,
                    sequence,
                    int(plan_revision),
                    str(purpose),
                    create_operation_id,
                    None,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM goal_experiments WHERE goal_id=? AND experiment_id=?",
                (goal_id, experiment_id),
            ).fetchone()
        return dict(row)

    def bind_launch_operation(self, goal_id: str, experiment_id: str, operation_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE goal_experiments
                SET launch_operation_id=?, updated_at=?
                WHERE goal_id=? AND experiment_id=?
                """,
                (operation_id, _now(), goal_id, experiment_id),
            )
        if cursor.rowcount != 1:
            raise GoalRunError("GOAL_EXPERIMENT_NOT_FOUND", "experiment is not linked to goal")

    def experiments(self, goal_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM goal_experiments WHERE goal_id=? ORDER BY sequence",
                (goal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_decision(
        self,
        goal_id: str,
        *,
        decision_kind: str,
        summary: str,
        evidence: Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM goal_runs WHERE goal_id=?", (goal_id,)).fetchone() is None:
                raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
            sequence = int(connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM goal_decisions WHERE goal_id=?",
                (goal_id,),
            ).fetchone()[0])
            connection.execute(
                """
                INSERT INTO goal_decisions(
                    goal_id, sequence, decision_kind, summary,
                    evidence_json, action_json, created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    goal_id,
                    sequence,
                    str(decision_kind),
                    str(summary),
                    _json_dump(dict(evidence or {})),
                    _json_dump(dict(action or {})),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM goal_decisions WHERE goal_id=? AND sequence=?",
                (goal_id, sequence),
            ).fetchone()
        result = dict(row)
        result["evidence"] = _json_load(result.pop("evidence_json"), {})
        result["action"] = _json_load(result.pop("action_json"), {})
        return result

    def decisions(self, goal_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM goal_decisions WHERE goal_id=? ORDER BY sequence",
                (goal_id,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["evidence"] = _json_load(value.pop("evidence_json"), {})
            value["action"] = _json_load(value.pop("action_json"), {})
            results.append(value)
        return results

    def snapshot(self, goal_id: str) -> dict[str, Any]:
        goal = self.get(goal_id)
        if goal is None:
            raise GoalRunError("GOAL_NOT_FOUND", f"goal not found: {goal_id}")
        return {
            "schema_version": GOAL_RUN_SCHEMA_VERSION,
            "goal": goal.to_dict(),
            "budget": self.budget_usage(goal_id),
            "experiments": self.experiments(goal_id),
            "decisions": self.decisions(goal_id),
        }


__all__ = [
    "GOAL_RUN_SCHEMA_VERSION",
    "GOAL_STATES",
    "TERMINAL_GOAL_STATES",
    "GoalRunError",
    "GoalRunRecord",
    "GoalRunStore",
]
