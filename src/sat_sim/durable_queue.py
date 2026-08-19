"""SQLite-backed durable execution queue for local and remote workers.

The queue stores only execution metadata and immutable Run Bundle references.
Simulation truth remains in the Run Bundle; the queue is scheduling evidence,
not a replacement for validation outcomes or claim reports.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from .run_bundle import RunStatus, finalize_interrupted_run, recover_interrupted_run

QUEUE_SCHEMA_VERSION = "execution-queue.v1"
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}
ACTIVE_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plus_seconds(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=float(seconds))).isoformat()


def _decode_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@dataclass(frozen=True)
class QueueJob:
    job_id: str
    run_id: str
    state: str
    bundle_root: str
    execution_plan_sha256: str
    max_attempts: int
    hard_timeout: bool
    priority: int
    submitted_at: str
    available_at: str
    claimed_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    lease_expires_at: str | None = None
    worker_id: str | None = None
    worker_host: str | None = None
    pid: int | None = None
    attempts: int = 0
    return_code: int | None = None
    validation_result: str | None = None
    error: str | None = None
    cancel_requested_at: str | None = None
    cancel_reason: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueJob":
        return cls(
            job_id=str(row["job_id"]),
            run_id=str(row["run_id"]),
            state=str(row["state"]),
            bundle_root=str(row["bundle_root"]),
            execution_plan_sha256=str(row["execution_plan_sha256"]),
            max_attempts=int(row["max_attempts"]),
            hard_timeout=bool(row["hard_timeout"]),
            priority=int(row["priority"]),
            submitted_at=str(row["submitted_at"]),
            available_at=str(row["available_at"]),
            claimed_at=row["claimed_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            heartbeat_at=row["heartbeat_at"],
            lease_expires_at=row["lease_expires_at"],
            worker_id=row["worker_id"],
            worker_host=row["worker_host"],
            pid=row["pid"],
            attempts=int(row["attempts"]),
            return_code=row["return_code"],
            validation_result=row["validation_result"],
            error=row["error"],
            cancel_requested_at=row["cancel_requested_at"],
            cancel_reason=row["cancel_reason"],
            metadata=_decode_json(row["metadata_json"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "state": self.state,
            "bundle_root": self.bundle_root,
            "execution_plan_sha256": self.execution_plan_sha256,
            "max_attempts": self.max_attempts,
            "hard_timeout": self.hard_timeout,
            "priority": self.priority,
            "submitted_at": self.submitted_at,
            "available_at": self.available_at,
            "claimed_at": self.claimed_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "heartbeat_at": self.heartbeat_at,
            "lease_expires_at": self.lease_expires_at,
            "worker_id": self.worker_id,
            "worker_host": self.worker_host,
            "pid": self.pid,
            "attempts": self.attempts,
            "return_code": self.return_code,
            "validation_result": self.validation_result,
            "error": self.error,
            "cancel_requested_at": self.cancel_requested_at,
            "cancel_reason": self.cancel_reason,
            "metadata": dict(self.metadata or {}),
            "persistent": True,
        }


class DurableExecutionQueue:
    """Small transactional queue suitable for one control plane and many workers."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    bundle_root TEXT NOT NULL,
                    execution_plan_sha256 TEXT NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    hard_timeout INTEGER NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 100,
                    submitted_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    claimed_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    heartbeat_at TEXT,
                    lease_expires_at TEXT,
                    worker_id TEXT,
                    worker_host TEXT,
                    pid INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    return_code INTEGER,
                    validation_result TEXT,
                    error TEXT,
                    cancel_requested_at TEXT,
                    cancel_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS jobs_claim_idx
                    ON jobs(state, available_at, priority, submitted_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS job_events_job_idx ON job_events(job_id, event_id);
                CREATE TABLE IF NOT EXISTS workers (
                    worker_id TEXT PRIMARY KEY,
                    worker_host TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            db.execute(
                "INSERT INTO queue_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (QUEUE_SCHEMA_VERSION,),
            )

    def _event(self, db: sqlite3.Connection, job_id: str, state: str, details: dict[str, Any] | None = None) -> None:
        db.execute(
            "INSERT INTO job_events(job_id, occurred_at, state, details_json) VALUES(?,?,?,?)",
            (job_id, utc_now(), state, json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
        )

    def enqueue(
        self,
        *,
        run_id: str,
        bundle_root: str | Path,
        execution_plan_sha256: str,
        max_attempts: int = 2,
        hard_timeout: bool = True,
        priority: int = 100,
        metadata: dict[str, Any] | None = None,
    ) -> QueueJob:
        existing = self.get_by_run_id(run_id)
        if existing and existing.state in ACTIVE_STATES | TERMINAL_STATES:
            return existing
        job_id = f"job_{uuid4().hex}"
        now = utc_now()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT job_id FROM jobs WHERE run_id=?", (run_id,)).fetchone()
                if row:
                    db.commit()
                    persisted = self.get(str(row["job_id"]))
                    if persisted is None:  # pragma: no cover - transaction invariant
                        raise RuntimeError("existing queue row disappeared")
                    return persisted
                db.execute(
                    """
                    INSERT INTO jobs(
                        job_id, run_id, state, bundle_root, execution_plan_sha256,
                        max_attempts, hard_timeout, priority, submitted_at, available_at,
                        metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        job_id,
                        run_id,
                        "QUEUED",
                        str(Path(bundle_root).resolve()),
                        execution_plan_sha256,
                        int(max_attempts),
                        int(bool(hard_timeout)),
                        int(priority),
                        now,
                        now,
                        json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self._event(db, job_id, "QUEUED", {"run_id": run_id})
                db.commit()
        except sqlite3.IntegrityError:
            persisted = self.get_by_run_id(run_id)
            if persisted is None:
                raise
            return persisted
        job = self.get(job_id)
        if job is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("queue insert did not persist")
        return job

    def get(self, job_id: str) -> QueueJob | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return QueueJob.from_row(row) if row else None

    def get_by_run_id(self, run_id: str) -> QueueJob | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
        return QueueJob.from_row(row) if row else None

    def list_jobs(self, *, limit: int = 100, offset: int = 0, states: Iterable[str] | None = None) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        state_list = [str(item).upper() for item in (states or []) if str(item).strip()]
        if state_list:
            where = " WHERE state IN (" + ",".join("?" for _ in state_list) + ")"
            params.extend(state_list)
        with self._connect() as db:
            total = int(db.execute(f"SELECT COUNT(*) FROM jobs{where}", params).fetchone()[0])
            rows = db.execute(
                f"SELECT * FROM jobs{where} ORDER BY priority ASC, submitted_at DESC LIMIT ? OFFSET ?",
                [*params, int(limit), int(offset)],
            ).fetchall()
        jobs = [QueueJob.from_row(row).to_dict() for row in rows]
        return {"schema_version": QUEUE_SCHEMA_VERSION, "count": len(jobs), "total": total, "offset": offset, "limit": limit, "jobs": jobs}

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id, occurred_at, state, details_json FROM job_events WHERE job_id=? ORDER BY event_id",
                (job_id,),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "occurred_at": str(row["occurred_at"]),
                "state": str(row["state"]),
                "details": _decode_json(row["details_json"]),
            }
            for row in rows
        ]

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 30.0,
        worker_host: str | None = None,
    ) -> QueueJob | None:
        now = utc_now()
        lease = _plus_seconds(lease_seconds)
        host = worker_host or socket.gethostname()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT job_id FROM jobs
                WHERE state='QUEUED' AND available_at<=?
                ORDER BY priority ASC, submitted_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if not row:
                db.commit()
                return None
            job_id = str(row["job_id"])
            changed = db.execute(
                """
                UPDATE jobs SET state='CLAIMED', claimed_at=?, heartbeat_at=?, lease_expires_at=?,
                    worker_id=?, worker_host=?, attempts=attempts+1
                WHERE job_id=? AND state='QUEUED'
                """,
                (now, now, lease, worker_id, host, job_id),
            ).rowcount
            if changed != 1:
                db.rollback()
                return None
            self._event(db, job_id, "CLAIMED", {"worker_id": worker_id, "worker_host": host})
            db.commit()
        return self.get(job_id)

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str,
        pid: int | None,
        lease_seconds: float = 30.0,
        worker_host: str | None = None,
    ) -> QueueJob:
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                """
                UPDATE jobs SET state='RUNNING', started_at=COALESCE(started_at, ?), heartbeat_at=?,
                    lease_expires_at=?, worker_id=?, worker_host=?, pid=?
                WHERE job_id=? AND state='CLAIMED' AND worker_id=?
                """,
                (now, now, _plus_seconds(lease_seconds), worker_id, worker_host or socket.gethostname(), pid, job_id, worker_id),
            ).rowcount
            if changed == 1:
                self._event(db, job_id, "RUNNING", {"worker_id": worker_id, "pid": pid})
            db.commit()
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if changed != 1 and job.state not in TERMINAL_STATES | {"CANCEL_REQUESTED"}:
            raise RuntimeError(f"worker {worker_id} no longer owns queue job {job_id}")
        return job

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: float = 30.0, pid: int | None = None) -> bool:
        with self._connect() as db:
            changed = db.execute(
                """
                UPDATE jobs SET heartbeat_at=?, lease_expires_at=?, pid=COALESCE(?, pid)
                WHERE job_id=? AND worker_id=? AND state IN ('CLAIMED','RUNNING','CANCEL_REQUESTED')
                """,
                (utc_now(), _plus_seconds(lease_seconds), pid, job_id, worker_id),
            ).rowcount
        return changed == 1

    def finish(
        self,
        job_id: str,
        *,
        state: str,
        return_code: int | None = None,
        validation_result: str | None = None,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> QueueJob:
        terminal = str(state).upper()
        if terminal not in TERMINAL_STATES:
            raise ValueError(f"not a terminal queue state: {state}")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state, worker_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if current is None:
                db.rollback()
                raise KeyError(job_id)
            current_state = str(current["state"])
            current_worker = current["worker_id"]
            if current_state in TERMINAL_STATES:
                db.commit()
                persisted = self.get(job_id)
                if persisted is None:  # pragma: no cover - transaction invariant
                    raise KeyError(job_id)
                return persisted
            if worker_id is not None and current_worker != worker_id:
                db.rollback()
                raise RuntimeError(f"worker {worker_id} no longer owns queue job {job_id}")
            changed = db.execute(
                """
                UPDATE jobs SET state=?, finished_at=?, heartbeat_at=?, lease_expires_at=NULL,
                    return_code=?, validation_result=?, error=?, pid=NULL
                WHERE job_id=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')
                """,
                (terminal, utc_now(), utc_now(), return_code, validation_result, error, job_id),
            ).rowcount
            if changed == 1:
                self._event(
                    db,
                    job_id,
                    terminal,
                    {"return_code": return_code, "validation_result": validation_result, "error": error},
                )
            db.commit()
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if changed != 1:
            raise RuntimeError(f"queue job {job_id} could not transition to {terminal}")
        return job

    def request_cancel(self, *, run_id: str, reason: str) -> tuple[QueueJob | None, bool]:
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id, state FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                db.commit()
                return None, False
            job_id = str(row["job_id"])
            current_state = str(row["state"])
            if current_state in TERMINAL_STATES:
                db.commit()
                return self.get(job_id), False
            cancelled_before_start = current_state in {"QUEUED", "CLAIMED"}
            new_state = "CANCELLED" if cancelled_before_start else "CANCEL_REQUESTED"
            db.execute(
                """
                UPDATE jobs SET state=?, cancel_requested_at=?, cancel_reason=?,
                    finished_at=CASE WHEN ?='CANCELLED' THEN ? ELSE finished_at END,
                    lease_expires_at=CASE WHEN ?='CANCELLED' THEN NULL ELSE lease_expires_at END
                WHERE job_id=?
                """,
                (new_state, now, reason, new_state, now, new_state, job_id),
            )
            self._event(db, job_id, new_state, {"reason": reason, "cancelled_before_start": cancelled_before_start})
            db.commit()
        return self.get(job_id), cancelled_before_start

    def requeue_stale(self, *, stale_before: str | None = None) -> int:
        cutoff = stale_before or utc_now()
        count = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT job_id, run_id, bundle_root, pid, worker_host, state, cancel_requested_at,
                       attempts, max_attempts FROM jobs
                WHERE state IN ('CLAIMED','RUNNING','CANCEL_REQUESTED')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at<?
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                pid = row["pid"]
                same_host = row["worker_host"] == socket.gethostname()
                if pid and same_host:
                    try:
                        os.kill(int(pid), 0)
                    except OSError:
                        pass
                    else:
                        continue
                root = Path(str(row["bundle_root"]))
                record: dict[str, Any] = {}
                try:
                    record = json.loads((root / "run_record.json").read_text(encoding="utf-8"))
                except Exception:
                    record = {}
                terminal = str(record.get("status") or "").upper()
                sealed = (root / "SEALED.json").is_file()
                job_id = str(row["job_id"])
                state = str(row["state"])

                if state == "CANCEL_REQUESTED":
                    try:
                        finalize_interrupted_run(
                            root,
                            status=RunStatus.CANCELLED,
                            reason_code="EXECUTION_CANCELLED_STALE_WORKER",
                            reason="cancel request reconciled after worker lease expired",
                        )
                    except Exception as exc:
                        db.execute(
                            "UPDATE jobs SET state='FAILED', finished_at=?, lease_expires_at=NULL, pid=NULL, error=? WHERE job_id=?",
                            (utc_now(), f"stale cancellation recovery failed: {exc}", job_id),
                        )
                        self._event(db, job_id, "FAILED", {"stale_cancel_recovery_failed": True, "error": str(exc)})
                    else:
                        db.execute(
                            "UPDATE jobs SET state='CANCELLED', finished_at=COALESCE(finished_at, ?), lease_expires_at=NULL, pid=NULL WHERE job_id=?",
                            (utc_now(), job_id),
                        )
                        self._event(db, job_id, "CANCELLED", {"stale_cancel_reconciled": True})
                    count += 1
                    continue

                if terminal in TERMINAL_STATES and sealed:
                    db.execute(
                        "UPDATE jobs SET state=?, finished_at=COALESCE(finished_at, ?), lease_expires_at=NULL, pid=NULL WHERE job_id=?",
                        (terminal, utc_now(), job_id),
                    )
                    self._event(db, job_id, terminal, {"reconciled_from_bundle": True, "sealed": True})
                elif int(row["attempts"]) >= int(row["max_attempts"]):
                    try:
                        finalize_interrupted_run(
                            root,
                            status=RunStatus.FAILED,
                            reason_code="STALE_WORKER_MAX_ATTEMPTS_EXCEEDED",
                            reason="stale worker lease exceeded queue retry limit",
                        )
                    except Exception:
                        pass
                    db.execute(
                        """
                        UPDATE jobs SET state='FAILED', finished_at=?, heartbeat_at=?, lease_expires_at=NULL,
                            pid=NULL, error='stale worker lease exceeded max attempts'
                        WHERE job_id=?
                        """,
                        (utc_now(), utc_now(), job_id),
                    )
                    self._event(db, job_id, "FAILED", {"stale_lease_recovered": True, "max_attempts_exhausted": True, "sealed": sealed})
                else:
                    try:
                        if terminal == "RUNNING":
                            recover_interrupted_run(root, reason="stale worker lease expired")
                        elif terminal not in {"PREPARED", ""}:
                            raise RuntimeError(f"bundle is not recoverable from status {terminal}")
                    except Exception as exc:
                        db.execute(
                            "UPDATE jobs SET state='FAILED', finished_at=?, lease_expires_at=NULL, pid=NULL, error=? WHERE job_id=?",
                            (utc_now(), f"stale run recovery failed: {exc}", job_id),
                        )
                        self._event(db, job_id, "FAILED", {"stale_run_recovery_failed": True, "error": str(exc)})
                    else:
                        db.execute(
                            """
                            UPDATE jobs SET state='QUEUED', available_at=?, claimed_at=NULL, started_at=NULL,
                                heartbeat_at=NULL, lease_expires_at=NULL, worker_id=NULL, worker_host=NULL,
                                pid=NULL, error='stale worker lease recovered'
                            WHERE job_id=?
                            """,
                            (utc_now(), job_id),
                        )
                        self._event(db, job_id, "QUEUED", {"stale_lease_recovered": True, "bundle_reset_to_prepared": terminal == "RUNNING"})
                count += 1
            db.commit()
        return count

    def register_worker(
        self,
        worker_id: str,
        *,
        status: str = "ONLINE",
        capabilities: dict[str, Any] | None = None,
        worker_host: str | None = None,
    ) -> None:
        now = utc_now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO workers(worker_id, worker_host, started_at, heartbeat_at, status, capabilities_json)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET worker_host=excluded.worker_host,
                    heartbeat_at=excluded.heartbeat_at, status=excluded.status,
                    capabilities_json=excluded.capabilities_json
                """,
                (worker_id, worker_host or socket.gethostname(), now, now, status, json.dumps(capabilities or {}, ensure_ascii=False, sort_keys=True)),
            )

    def heartbeat_worker(self, worker_id: str, *, status: str = "ONLINE") -> None:
        with self._connect() as db:
            db.execute("UPDATE workers SET heartbeat_at=?, status=? WHERE worker_id=?", (utc_now(), status, worker_id))

    def list_workers(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM workers ORDER BY heartbeat_at DESC").fetchall()
        return [
            {
                "worker_id": row["worker_id"],
                "worker_host": row["worker_host"],
                "started_at": row["started_at"],
                "heartbeat_at": row["heartbeat_at"],
                "status": row["status"],
                "capabilities": _decode_json(row["capabilities_json"]),
            }
            for row in rows
        ]

    def health(self) -> dict[str, Any]:
        states: dict[str, int] = {}
        with self._connect() as db:
            for row in db.execute("SELECT state, COUNT(*) AS count FROM jobs GROUP BY state").fetchall():
                states[str(row["state"])] = int(row["count"])
            schema = db.execute("SELECT value FROM queue_meta WHERE key='schema_version'").fetchone()
        return {
            "ok": True,
            "schema_version": str(schema[0]) if schema else None,
            "database_path": str(self.database_path),
            "states": states,
            "worker_count": len(self.list_workers()),
        }


__all__ = [
    "ACTIVE_STATES",
    "DurableExecutionQueue",
    "QUEUE_SCHEMA_VERSION",
    "QueueJob",
    "TERMINAL_STATES",
    "utc_now",
]
