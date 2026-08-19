"""Persistent simulation task center with version and run history.

A task is a mutable user-facing simulation definition.  Every material edit
creates an immutable task version.  Every execution creates or updates a run
history row while the Run Bundle remains the authoritative immutable evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

TASK_CENTER_SCHEMA_VERSION = "task-center.v2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_identity(spec: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    task = spec.get("task") if isinstance(spec.get("task"), Mapping) else {}
    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    return (
        str(task.get("id") or "").strip() or None,
        str(task.get("name") or "").strip() or None,
        str(model.get("capability_id") or "").strip() or None,
    )


def _encoded(spec: Mapping[str, Any]) -> str:
    return json.dumps(dict(spec), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskCenterRecord:
    task_id: str
    name: str
    capability_id: str | None
    status: str
    source: str
    task_spec: dict[str, Any]
    created_at: str
    updated_at: str
    last_run_id: str | None = None
    last_run_status: str | None = None
    validation_result: str | None = None
    notes: str = ""
    current_version: int = 1
    baseline_version: int | None = None
    version_count: int = 1
    run_count: int = 0

    def to_dict(self, *, include_task_spec: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_task_spec:
            payload.pop("task_spec", None)
        return payload


@dataclass(frozen=True)
class TaskVersionRecord:
    task_id: str
    version: int
    name: str
    status: str
    source: str
    task_spec: dict[str, Any]
    spec_sha256: str
    change_summary: str
    created_at: str
    is_baseline: bool = False

    def to_dict(self, *, include_task_spec: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_task_spec:
            payload.pop("task_spec", None)
        return payload


@dataclass(frozen=True)
class TaskRunRecord:
    task_id: str
    run_id: str
    task_version: int
    status: str
    validation_result: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskCenterStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS simulation_tasks (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    capability_id TEXT,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    task_spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_id TEXT,
                    last_run_status TEXT,
                    validation_result TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    current_version INTEGER NOT NULL DEFAULT 1,
                    baseline_version INTEGER,
                    run_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS simulation_task_versions (
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    task_spec_json TEXT NOT NULL,
                    spec_sha256 TEXT NOT NULL,
                    change_summary TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, version),
                    FOREIGN KEY(task_id) REFERENCES simulation_tasks(task_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS simulation_task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    task_version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    validation_result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES simulation_tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_simulation_tasks_updated
                    ON simulation_tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_simulation_tasks_status
                    ON simulation_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_simulation_tasks_last_run
                    ON simulation_tasks(last_run_id);
                CREATE INDEX IF NOT EXISTS idx_simulation_task_versions_task
                    ON simulation_task_versions(task_id, version DESC);
                CREATE INDEX IF NOT EXISTS idx_simulation_task_runs_task
                    ON simulation_task_runs(task_id, created_at DESC);
                """
            )
            # Migrate legacy task-center databases in place.
            columns = self._column_names(connection, "simulation_tasks")
            if "current_version" not in columns:
                connection.execute("ALTER TABLE simulation_tasks ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1")
            if "baseline_version" not in columns:
                connection.execute("ALTER TABLE simulation_tasks ADD COLUMN baseline_version INTEGER")
            if "run_count" not in columns:
                connection.execute("ALTER TABLE simulation_tasks ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0")
            rows = connection.execute("SELECT * FROM simulation_tasks").fetchall()
            for row in rows:
                exists = connection.execute(
                    "SELECT 1 FROM simulation_task_versions WHERE task_id=? LIMIT 1", (row["task_id"],)
                ).fetchone()
                if not exists:
                    encoded = str(row["task_spec_json"])
                    connection.execute(
                        """
                        INSERT INTO simulation_task_versions(
                            task_id, version, name, status, source, task_spec_json,
                            spec_sha256, change_summary, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            row["task_id"], 1, row["name"], row["status"], row["source"], encoded,
                            _sha256_text(encoded), "从旧任务中心结构迁移", row["created_at"],
                        ),
                    )
                if row["last_run_id"]:
                    run_exists = connection.execute(
                        "SELECT 1 FROM simulation_task_runs WHERE run_id=?", (row["last_run_id"],)
                    ).fetchone()
                    if not run_exists:
                        connection.execute(
                            """
                            INSERT INTO simulation_task_runs(
                                run_id, task_id, task_version, status, validation_result, created_at, updated_at
                            ) VALUES(?,?,?,?,?,?,?)
                            """,
                            (
                                row["last_run_id"], row["task_id"], int(row["current_version"] or 1),
                                row["last_run_status"] or "UNKNOWN", row["validation_result"],
                                row["updated_at"], row["updated_at"],
                            ),
                        )
                count = int(connection.execute(
                    "SELECT COUNT(*) FROM simulation_task_runs WHERE task_id=?", (row["task_id"],)
                ).fetchone()[0])
                connection.execute("UPDATE simulation_tasks SET run_count=? WHERE task_id=?", (count, row["task_id"]))

    @staticmethod
    def _row(row: sqlite3.Row | None, *, version_count: int = 1) -> TaskCenterRecord | None:
        if row is None:
            return None
        spec = json.loads(row["task_spec_json"])
        keys = set(row.keys())
        return TaskCenterRecord(
            task_id=row["task_id"],
            name=row["name"],
            capability_id=row["capability_id"],
            status=row["status"],
            source=row["source"],
            task_spec=spec if isinstance(spec, dict) else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_run_id=row["last_run_id"],
            last_run_status=row["last_run_status"],
            validation_result=row["validation_result"],
            notes=row["notes"] or "",
            current_version=int(row["current_version"] if "current_version" in keys else 1),
            baseline_version=int(row["baseline_version"]) if "baseline_version" in keys and row["baseline_version"] is not None else None,
            version_count=int(version_count),
            run_count=int(row["run_count"] if "run_count" in keys else 0),
        )

    def _record(self, connection: sqlite3.Connection, task_id: str) -> TaskCenterRecord | None:
        row = connection.execute("SELECT * FROM simulation_tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            return None
        count = int(connection.execute(
            "SELECT COUNT(*) FROM simulation_task_versions WHERE task_id=?", (task_id,)
        ).fetchone()[0])
        return self._row(row, version_count=count)

    def save(
        self,
        task_spec: Mapping[str, Any],
        *,
        task_id: str | None = None,
        name: str | None = None,
        status: str = "DRAFT",
        source: str = "workbench",
        notes: str = "",
        change_summary: str = "",
        force_new_version: bool = False,
    ) -> TaskCenterRecord:
        declared_id, declared_name, capability_id = _task_identity(task_spec)
        resolved_id = str(task_id or declared_id or f"task_{uuid4().hex[:12]}")
        resolved_name = str(name or declared_name or resolved_id)
        now = _now()
        encoded = _encoded(task_spec)
        digest = _sha256_text(encoded)
        normalized_status = str(status).upper()
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM simulation_tasks WHERE task_id=?", (resolved_id,)).fetchone()
            if existing is None:
                current_version = 1
                connection.execute(
                    """
                    INSERT INTO simulation_tasks(
                        task_id, name, capability_id, status, source, task_spec_json,
                        created_at, updated_at, last_run_id, last_run_status,
                        validation_result, notes, current_version, baseline_version, run_count
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        resolved_id, resolved_name, capability_id, normalized_status, source, encoded,
                        now, now, None, None, None, notes, current_version, None, 0,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO simulation_task_versions(
                        task_id, version, name, status, source, task_spec_json,
                        spec_sha256, change_summary, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        resolved_id, current_version, resolved_name, normalized_status, source, encoded,
                        digest, change_summary or "创建任务", now,
                    ),
                )
            else:
                latest = connection.execute(
                    "SELECT * FROM simulation_task_versions WHERE task_id=? ORDER BY version DESC LIMIT 1",
                    (resolved_id,),
                ).fetchone()
                changed = force_new_version or latest is None or str(latest["spec_sha256"]) != digest
                current_version = int(existing["current_version"] or 1)
                if changed:
                    current_version = int(latest["version"] if latest else current_version) + 1
                    connection.execute(
                        """
                        INSERT INTO simulation_task_versions(
                            task_id, version, name, status, source, task_spec_json,
                            spec_sha256, change_summary, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            resolved_id, current_version, resolved_name, normalized_status, source, encoded,
                            digest, change_summary or f"保存版本 V{current_version}", now,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE simulation_tasks SET
                        name=?, capability_id=?, status=?, source=?, task_spec_json=?,
                        updated_at=?, notes=?, current_version=?
                    WHERE task_id=?
                    """,
                    (
                        resolved_name, capability_id, normalized_status, source, encoded,
                        now, notes, current_version, resolved_id,
                    ),
                )
            record = self._record(connection, resolved_id)
        assert record is not None
        return record

    def get(self, task_id: str) -> TaskCenterRecord | None:
        with self._connect() as connection:
            return self._record(connection, task_id)

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status.upper())
        if query:
            clauses.append("(LOWER(task_id) LIKE ? OR LOWER(name) LIKE ? OR LOWER(COALESCE(capability_id,'')) LIKE ?)")
            needle = f"%{query.lower()}%"
            values.extend([needle, needle, needle])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM simulation_tasks{where}", values).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM simulation_tasks{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*values, int(limit), int(offset)],
            ).fetchall()
            records = []
            for row in rows:
                count = int(connection.execute(
                    "SELECT COUNT(*) FROM simulation_task_versions WHERE task_id=?", (row["task_id"],)
                ).fetchone()[0])
                records.append(self._row(row, version_count=count))
        return {
            "schema_version": TASK_CENTER_SCHEMA_VERSION,
            "count": len(records),
            "total": int(total),
            "limit": int(limit),
            "offset": int(offset),
            "tasks": [record.to_dict(include_task_spec=False) for record in records if record],
        }

    def list_versions(self, task_id: str, *, include_task_spec: bool = False) -> list[TaskVersionRecord]:
        with self._connect() as connection:
            task = connection.execute("SELECT baseline_version FROM simulation_tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                return []
            rows = connection.execute(
                "SELECT * FROM simulation_task_versions WHERE task_id=? ORDER BY version DESC", (task_id,)
            ).fetchall()
            baseline = task["baseline_version"]
        records = []
        for row in rows:
            spec = json.loads(row["task_spec_json"])
            records.append(TaskVersionRecord(
                task_id=row["task_id"], version=int(row["version"]), name=row["name"],
                status=row["status"], source=row["source"],
                task_spec=spec if isinstance(spec, dict) else {}, spec_sha256=row["spec_sha256"],
                change_summary=row["change_summary"] or "", created_at=row["created_at"],
                is_baseline=baseline is not None and int(baseline) == int(row["version"]),
            ))
        return records

    def get_version(self, task_id: str, version: int) -> TaskVersionRecord | None:
        with self._connect() as connection:
            task = connection.execute("SELECT baseline_version FROM simulation_tasks WHERE task_id=?", (task_id,)).fetchone()
            row = connection.execute(
                "SELECT * FROM simulation_task_versions WHERE task_id=? AND version=?", (task_id, int(version))
            ).fetchone()
        if row is None:
            return None
        spec = json.loads(row["task_spec_json"])
        return TaskVersionRecord(
            task_id=row["task_id"], version=int(row["version"]), name=row["name"],
            status=row["status"], source=row["source"], task_spec=spec if isinstance(spec, dict) else {},
            spec_sha256=row["spec_sha256"], change_summary=row["change_summary"] or "",
            created_at=row["created_at"],
            is_baseline=bool(task and task["baseline_version"] is not None and int(task["baseline_version"]) == int(version)),
        )

    def restore_version(self, task_id: str, version: int, *, change_summary: str = "") -> TaskCenterRecord | None:
        item = self.get_version(task_id, version)
        current = self.get(task_id)
        if item is None or current is None:
            return None
        return self.save(
            item.task_spec, task_id=task_id, name=current.name, status="READY", source="version_restore",
            notes=current.notes, change_summary=change_summary or f"从 V{version} 恢复", force_new_version=True,
        )

    def set_baseline(self, task_id: str, version: int) -> TaskCenterRecord | None:
        if self.get_version(task_id, version) is None:
            return None
        with self._connect() as connection:
            connection.execute(
                "UPDATE simulation_tasks SET baseline_version=?, updated_at=? WHERE task_id=?",
                (int(version), _now(), task_id),
            )
        return self.get(task_id)

    def clone(self, task_id: str, *, name: str | None = None) -> TaskCenterRecord | None:
        source = self.get(task_id)
        if source is None:
            return None
        cloned_spec = json.loads(json.dumps(source.task_spec, ensure_ascii=False))
        task = cloned_spec.get("task") if isinstance(cloned_spec.get("task"), dict) else {}
        new_id = f"task_{uuid4().hex[:12]}"
        task["id"] = new_id
        task["name"] = name or f"{source.name}（副本）"
        cloned_spec["task"] = task
        return self.save(
            cloned_spec, task_id=new_id, name=task["name"], status="DRAFT", source="task_clone",
            notes=f"克隆自 {task_id}", change_summary=f"从任务 {task_id} 克隆",
        )

    def list_runs(self, task_id: str, *, limit: int = 100) -> list[TaskRunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM simulation_task_runs WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                (task_id, int(limit)),
            ).fetchall()
        return [TaskRunRecord(
            task_id=row["task_id"], run_id=row["run_id"], task_version=int(row["task_version"]),
            status=row["status"], validation_result=row["validation_result"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        ) for row in rows]

    def delete(self, task_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM simulation_tasks WHERE task_id=?", (task_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _task_status(run_status: str) -> str:
        return {
            "QUEUED": "QUEUED", "CLAIMED": "RUNNING", "RUNNING": "RUNNING",
            "CANCEL_REQUESTED": "RUNNING", "SUCCEEDED": "COMPLETED", "FAILED": "FAILED",
            "CANCELLED": "CANCELLED", "TIMED_OUT": "FAILED",
        }.get(run_status.upper(), run_status.upper())

    def mark_run(
        self,
        task_id: str,
        *,
        run_id: str,
        run_status: str,
        validation_result: str | None = None,
    ) -> TaskCenterRecord | None:
        now = _now()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT current_version FROM simulation_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if task is None:
                return None
            connection.execute(
                """
                INSERT INTO simulation_task_runs(
                    run_id, task_id, task_version, status, validation_result, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    validation_result=excluded.validation_result,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id, task_id, int(task["current_version"] or 1), run_status.upper(),
                    validation_result, now, now,
                ),
            )
            count = int(connection.execute(
                "SELECT COUNT(*) FROM simulation_task_runs WHERE task_id=?", (task_id,)
            ).fetchone()[0])
            connection.execute(
                """
                UPDATE simulation_tasks
                SET status=?, last_run_id=?, last_run_status=?, validation_result=?, updated_at=?, run_count=?
                WHERE task_id=?
                """,
                (self._task_status(run_status), run_id, run_status.upper(), validation_result, now, count, task_id),
            )
        return self.get(task_id)

    def mark_run_by_run_id(self, run_id: str, *, run_status: str, validation_result: str | None = None) -> int:
        now = _now()
        with self._connect() as connection:
            run = connection.execute("SELECT task_id FROM simulation_task_runs WHERE run_id=?", (run_id,)).fetchone()
            connection.execute(
                "UPDATE simulation_task_runs SET status=?, validation_result=?, updated_at=? WHERE run_id=?",
                (run_status.upper(), validation_result, now, run_id),
            )
            if run is not None:
                cursor = connection.execute(
                    """
                    UPDATE simulation_tasks
                    SET status=?, last_run_status=?, validation_result=?, updated_at=?
                    WHERE task_id=?
                    """,
                    (self._task_status(run_status), run_status.upper(), validation_result, now, run["task_id"]),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE simulation_tasks
                    SET status=?, last_run_status=?, validation_result=?, updated_at=?
                    WHERE last_run_id=?
                    """,
                    (self._task_status(run_status), run_status.upper(), validation_result, now, run_id),
                )
            return cursor.rowcount

    def health(self) -> dict[str, Any]:
        with self._connect() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM simulation_tasks").fetchone()[0])
            versions = int(connection.execute("SELECT COUNT(*) FROM simulation_task_versions").fetchone()[0])
            runs = int(connection.execute("SELECT COUNT(*) FROM simulation_task_runs").fetchone()[0])
        return {
            "schema_version": TASK_CENTER_SCHEMA_VERSION,
            "database": str(self.path),
            "task_count": count,
            "version_count": versions,
            "run_count": runs,
            "ok": True,
        }


__all__ = [
    "TASK_CENTER_SCHEMA_VERSION", "TaskCenterRecord", "TaskVersionRecord", "TaskRunRecord", "TaskCenterStore",
]
