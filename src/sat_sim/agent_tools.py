"""Structured Agent tools for bounded experiment design and creation.

P1 intentionally starts with deterministic read/preview operations and an
idempotent experiment-create write.  It does not launch runs.  Execution is
added only after operation reconciliation and run-budget accounting are
available.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .experiment_manager import ExperimentStore
from .sqlite_connections import connect
from .task_models import canonicalize_task_spec
from .workbench_integration import (
    experiment_design_options,
    list_workbench_fault_models,
    preview_experiment_plan,
)

_AGENT_TOOL_SCHEMA_VERSION = "agent-experiment-tools.v1"
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


class AgentToolError(ValueError):
    """Deterministic tool-contract rejection with a stable reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"reason_code": self.reason_code, "message": self.message}


@dataclass(frozen=True)
class OperationReservation:
    state: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AgentOperationStore:
    """Small durable idempotency journal for Agent write operations.

    The journal is deliberately separate from the execution queue.  It records
    the Agent's intent to perform a side effect and the resulting persistent
    object.  A future launch tool can use the same operation identifier while
    queue/run state remains authoritative for execution.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_operations (
                    operation_id TEXT PRIMARY KEY,
                    input_sha256 TEXT NOT NULL,
                    operation_kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    request_json TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_operations_updated "
                "ON agent_operations(updated_at DESC)"
            )
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(agent_operations)").fetchall()
            }
            if "request_json" not in columns:
                connection.execute("ALTER TABLE agent_operations ADD COLUMN request_json TEXT")
            if "lease_owner" not in columns:
                connection.execute("ALTER TABLE agent_operations ADD COLUMN lease_owner TEXT")
            if "lease_expires_at" not in columns:
                connection.execute("ALTER TABLE agent_operations ADD COLUMN lease_expires_at TEXT")

    @staticmethod
    def _validate_operation_id(operation_id: str) -> str:
        value = str(operation_id or "").strip()
        if not _OPERATION_ID_RE.fullmatch(value):
            raise AgentToolError(
                "INVALID_OPERATION_ID",
                "operation_id must be 1-128 characters using letters, numbers, '.', '_', ':', or '-'",
            )
        return value

    def reserve(
        self,
        *,
        operation_id: str,
        input_sha256: str,
        operation_kind: str,
        request_payload: Mapping[str, Any] | None = None,
        lease_seconds: float = 120.0,
        lease_owner: str | None = None,
    ) -> OperationReservation:
        operation_id = self._validate_operation_id(operation_id)
        now = _now()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(float(lease_seconds), 1.0))
        ).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO agent_operations
                    (
                        operation_id, input_sha256, operation_kind, state,
                        result_json, error_json, request_json, lease_owner, lease_expires_at, created_at, updated_at
                    )
                VALUES (?, ?, ?, 'PENDING', NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    input_sha256,
                    operation_kind,
                    _stable_json(dict(request_payload)) if request_payload is not None else None,
                    str(lease_owner) if lease_owner is not None else None,
                    lease_expires_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM agent_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("operation reservation disappeared")
        if str(row["input_sha256"]) != input_sha256 or str(row["operation_kind"]) != operation_kind:
            raise AgentToolError(
                "OPERATION_ID_CONFLICT",
                "operation_id is already bound to different input or operation kind",
            )
        if cursor.rowcount == 1:
            return OperationReservation(state="NEW")
        state = str(row["state"])
        if state == "SUCCEEDED":
            result = json.loads(row["result_json"]) if row["result_json"] else {}
            return OperationReservation(state="REPLAY", result=result)
        if state == "FAILED":
            error = json.loads(row["error_json"]) if row["error_json"] else {}
            return OperationReservation(state="FAILED", error=error)
        if state == "RECONCILE":
            error = json.loads(row["error_json"]) if row["error_json"] else {}
            return OperationReservation(state="RECONCILE", error=error)
        return OperationReservation(state="IN_PROGRESS")

    def reserve_payload(
        self,
        *,
        operation_id: str,
        operation_kind: str,
        request_payload: Mapping[str, Any],
        lease_seconds: float = 120.0,
        lease_owner: str | None = None,
    ) -> tuple[OperationReservation, str]:
        input_sha256 = _payload_hash(request_payload)
        reservation = self.reserve(
            operation_id=operation_id,
            input_sha256=input_sha256,
            operation_kind=operation_kind,
            request_payload=request_payload,
            lease_seconds=lease_seconds,
            lease_owner=lease_owner,
        )
        return reservation, input_sha256

    def get(self, operation_id: str) -> dict[str, Any] | None:
        operation_id = self._validate_operation_id(operation_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"]) if row["result_json"] else None
        error = json.loads(row["error_json"]) if row["error_json"] else None
        request = json.loads(row["request_json"]) if "request_json" in row.keys() and row["request_json"] else None
        return {
            "operation_id": operation_id,
            "operation_kind": str(row["operation_kind"]),
            "operation_state": str(row["state"]),
            "input_sha256": str(row["input_sha256"]),
            "result": result,
            "error": error,
            "request": request,
            "lease_expires_at": str(row["lease_expires_at"]) if "lease_expires_at" in row.keys() and row["lease_expires_at"] else None,
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def heartbeat(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_seconds: float = 120.0,
    ) -> bool:
        operation_id = self._validate_operation_id(operation_id)
        now = _now()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(float(lease_seconds), 1.0))
        ).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_operations
                SET lease_expires_at = ?, updated_at = ?
                WHERE operation_id = ? AND state = 'PENDING' AND lease_owner = ?
                """,
                (lease_expires_at, now, operation_id, str(lease_owner)),
            )
        return cursor.rowcount == 1

    def claim_reconciliation(self, operation_id: str) -> bool:
        operation_id = self._validate_operation_id(operation_id)
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_operations
                SET state = 'RECONCILE', lease_owner = NULL, updated_at = ?
                WHERE operation_id = ?
                  AND state = 'PENDING'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (now, operation_id, now),
            )
            if cursor.rowcount == 1:
                return True
            row = connection.execute(
                "SELECT state FROM agent_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return bool(row is not None and str(row["state"]) == "RECONCILE")

    def resume_reconciliation(
        self,
        operation_id: str,
        *,
        lease_owner: str,
        lease_seconds: float = 120.0,
    ) -> bool:
        operation_id = self._validate_operation_id(operation_id)
        now = _now()
        lease_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=max(float(lease_seconds), 1.0))
        ).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_operations
                SET state = 'PENDING', lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE operation_id = ? AND state = 'RECONCILE'
                """,
                (str(lease_owner), lease_expires_at, now, operation_id),
            )
        return cursor.rowcount == 1

    def complete(
        self,
        operation_id: str,
        result: Mapping[str, Any],
        *,
        lease_owner: str | None = None,
    ) -> bool:
        operation_id = self._validate_operation_id(operation_id)
        with self._connect() as connection:
            if lease_owner is None:
                cursor = connection.execute(
                    """
                    UPDATE agent_operations
                    SET state = 'SUCCEEDED', result_json = ?, error_json = NULL, updated_at = ?
                    WHERE operation_id = ? AND state IN ('PENDING', 'RECONCILE') AND lease_owner IS NULL
                    """,
                    (_stable_json(dict(result)), _now(), operation_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE agent_operations
                    SET state = 'SUCCEEDED', result_json = ?, error_json = NULL, updated_at = ?
                    WHERE operation_id = ? AND state = 'PENDING' AND lease_owner = ?
                    """,
                    (_stable_json(dict(result)), _now(), operation_id, str(lease_owner)),
                )
        return cursor.rowcount == 1

    def require_reconciliation(
        self,
        operation_id: str,
        error: Mapping[str, Any],
        *,
        lease_owner: str | None = None,
    ) -> bool:
        operation_id = self._validate_operation_id(operation_id)
        with self._connect() as connection:
            if lease_owner is None:
                cursor = connection.execute(
                    """
                    UPDATE agent_operations
                    SET state = 'RECONCILE', lease_owner = NULL, error_json = ?, updated_at = ?
                    WHERE operation_id = ? AND state IN ('PENDING', 'RECONCILE') AND lease_owner IS NULL
                    """,
                    (_stable_json(dict(error)), _now(), operation_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE agent_operations
                    SET state = 'RECONCILE', lease_owner = NULL, error_json = ?, updated_at = ?
                    WHERE operation_id = ? AND state = 'PENDING' AND lease_owner = ?
                    """,
                    (_stable_json(dict(error)), _now(), operation_id, str(lease_owner)),
                )
        return cursor.rowcount == 1

    def fail(self, operation_id: str, error: Mapping[str, Any]) -> None:
        operation_id = self._validate_operation_id(operation_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE agent_operations
                SET state = 'FAILED', error_json = ?, updated_at = ?
                WHERE operation_id = ? AND state = 'PENDING'
                """,
                (_stable_json(dict(error)), _now(), operation_id),
            )


class AgentExperimentTools:
    """Bounded deterministic tool layer for experiment authoring."""

    def __init__(
        self,
        *,
        experiment_store: ExperimentStore,
        operation_store: AgentOperationStore,
        max_create_variants: int = 12,
    ) -> None:
        if int(max_create_variants) < 1:
            raise ValueError("max_create_variants must be >= 1")
        self.experiment_store = experiment_store
        self.operation_store = operation_store
        self.max_create_variants = int(max_create_variants)

    def list_fault_models(
        self,
        *,
        capability_id: str | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        return list_workbench_fault_models(capability_id=capability_id, category=category)

    def experiment_options(self, base_task_spec: Mapping[str, Any]) -> dict[str, Any]:
        return experiment_design_options(base_task_spec)

    def preview(
        self,
        *,
        base_task_spec: Mapping[str, Any],
        experiment_type: str = "sweep",
        sweep: Mapping[str, list[Any]] | None = None,
        sampling_plan: Mapping[str, Any] | None = None,
        assertions: list[dict[str, Any]] | None = None,
        preview_limit: int = 8,
    ) -> dict[str, Any]:
        payload = preview_experiment_plan(
            base_task_spec=base_task_spec,
            experiment_type=experiment_type,
            sweep=sweep,
            sampling_plan=sampling_plan,
            assertions=assertions,
            preview_limit=preview_limit,
        )
        payload["write_allowed"] = payload["variant_count"] <= self.max_create_variants
        payload["write_budget"] = {
            "max_create_variants": self.max_create_variants,
            "requested_variants": payload["variant_count"],
        }
        return payload

    def create_experiment(
        self,
        *,
        operation_id: str,
        name: str,
        base_task_spec: Mapping[str, Any],
        experiment_type: str = "sweep",
        sweep: Mapping[str, Sequence[Any]] | None = None,
        sampling_plan: Mapping[str, Any] | None = None,
        assertions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        canonical = canonicalize_task_spec(base_task_spec)
        normalized_sweep = {str(path): list(values) for path, values in (sweep or {}).items()}
        normalized_plan = dict(sampling_plan or {})
        normalized_assertions = list(assertions or [])
        preview = self.preview(
            base_task_spec=canonical,
            experiment_type=experiment_type,
            sweep=normalized_sweep,
            sampling_plan=normalized_plan,
            assertions=normalized_assertions,
            preview_limit=0,
        )
        requested = int(preview["variant_count"])
        if requested > self.max_create_variants:
            raise AgentToolError(
                "AGENT_EXPERIMENT_VARIANT_BUDGET_EXCEEDED",
                f"experiment requests {requested} variants; Agent create limit is {self.max_create_variants}",
            )
        input_payload = {
            "schema_version": _AGENT_TOOL_SCHEMA_VERSION,
            "operation_kind": "create_experiment",
            "name": str(name),
            "base_task_spec": canonical,
            "experiment_type": str(preview["experiment_type"]),
            "sweep": normalized_sweep,
            "sampling_plan": normalized_plan,
            "assertions": normalized_assertions,
            "max_create_variants": self.max_create_variants,
        }
        reservation, input_sha256 = self.operation_store.reserve_payload(
            operation_id=operation_id,
            operation_kind="create_experiment",
            request_payload=input_payload,
        )
        if reservation.state == "REPLAY":
            replay = dict(reservation.result or {})
            replay["replayed"] = True
            return replay
        if reservation.state == "IN_PROGRESS":
            return {
                "ok": False,
                "schema_version": _AGENT_TOOL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "operation_state": "IN_PROGRESS",
                "replayed": True,
            }
        if reservation.state == "FAILED":
            return {
                "ok": False,
                "schema_version": _AGENT_TOOL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "operation_state": "FAILED",
                "error": dict(reservation.error or {}),
                "replayed": True,
            }

        try:
            record = self.experiment_store.create(
                name=str(name),
                base_task_spec=canonical,
                sweep=normalized_sweep,
                assertions=normalized_assertions,
                experiment_type=str(preview["experiment_type"]),
                sampling_plan=normalized_plan,
            )
            result = {
                "ok": True,
                "schema_version": _AGENT_TOOL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "operation_state": "SUCCEEDED",
                "experiment_id": record.experiment_id,
                "experiment_type": record.experiment_type,
                "variant_count": record.variant_count,
                "status": record.status,
                "input_sha256": input_sha256,
                "replayed": False,
                "launch_permitted": False,
                "boundary": "P1 create tool persists experiment variants but does not launch runs.",
            }
            self.operation_store.complete(operation_id, result)
            return result
        except Exception as exc:
            error = {
                "reason_code": getattr(exc, "reason_code", "EXPERIMENT_CREATE_FAILED"),
                "message": str(exc),
            }
            self.operation_store.fail(operation_id, error)
            raise


def agent_experiment_tool_surface(
    *,
    max_create_variants: int = 12,
    max_launch_runs: int = 12,
    max_execution_attempts: int = 24,
) -> dict[str, Any]:
    """Machine-readable P1 tool contract for planners and UI adapters."""
    return {
        "ok": True,
        "schema_version": _AGENT_TOOL_SCHEMA_VERSION,
        "tools": [
            {"name": "list_fault_models", "effect": "read", "deterministic": True},
            {"name": "experiment_options", "effect": "read", "deterministic": True},
            {"name": "preview_experiment", "effect": "read", "deterministic": True},
            {"name": "get_operation", "effect": "read", "deterministic": True},
            {
                "name": "reconcile_operation",
                "effect": "write",
                "deterministic": True,
                "requires_expired_lease": True,
            },
            {
                "name": "create_experiment",
                "effect": "write",
                "deterministic": True,
                "requires_operation_id": True,
                "max_variants": int(max_create_variants),
                "launches_runs": False,
            },
            {
                "name": "launch_experiment",
                "effect": "write",
                "deterministic": True,
                "requires_operation_id": True,
                "max_runs": int(max_launch_runs),
                "max_execution_attempts": int(max_execution_attempts),
                "requires_explicit_request_budget": True,
                "launches_runs": True,
            },
            {
                "name": "get_experiment_result",
                "effect": "read",
                "deterministic": True,
            },
            {
                "name": "get_experiment_evidence",
                "effect": "read",
                "deterministic": True,
                "causal_claims_verified": False,
            },
        ],
        "forbidden": [
            "launch_without_budget_reservation",
            "launch_above_global_budget",
            "retry_unknown_write_without_reconciliation",
            "invent_parameter_path",
            "execute_arbitrary_python",
        ],
    }


__all__ = [
    "AgentExperimentTools",
    "AgentOperationStore",
    "AgentToolError",
    "OperationReservation",
    "agent_experiment_tool_surface",
]
