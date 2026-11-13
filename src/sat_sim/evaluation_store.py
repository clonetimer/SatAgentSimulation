"""Owner-scoped, append-only persistence for independent evaluation reports."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class EvaluationStoreConflict(RuntimeError):
    """A deterministic report identity was reused with different content."""


class EvaluationStore:
    def __init__(self, database: str | Path) -> None:
        self.path = Path(database).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_reports (
                    principal_id TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    plan_version TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (principal_id, report_id)
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_evaluation_reports_owner_run "
                "ON evaluation_reports (principal_id, run_id, created_at DESC)"
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> str:
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "report": json.loads(row["report_json"]),
            "plan": json.loads(row["plan_json"]),
            "created_at": row["created_at"],
        }

    def save(self, *, principal_id: str, report: Mapping[str, Any],
             plan: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("EVALUATION_PRINCIPAL_REQUIRED")
        report_json, plan_json = self._canonical(report), self._canonical(plan)
        report_id = str(report.get("report_id") or "")
        run_id = str(report.get("source_run_id") or "")
        plan_id = str(report.get("plan_id") or "")
        plan_version = str(report.get("plan_version") or "")
        plan_digest = str(report.get("plan_digest") or "")
        source_digest = str(report.get("source_digest") or "")
        if not all((report_id, run_id, plan_id, plan_version, plan_digest, source_digest)):
            raise ValueError("EVALUATION_REPORT_IDENTITY_INCOMPLETE")
        now = datetime.now(timezone.utc).isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM evaluation_reports WHERE principal_id=? AND report_id=?",
                (principal_id, report_id),
            ).fetchone()
            if existing is not None:
                if existing["report_json"] != report_json or existing["plan_json"] != plan_json:
                    raise EvaluationStoreConflict("EVALUATION_REPORT_IDENTITY_CONFLICT")
                return self._row_payload(existing), False
            connection.execute("""
                INSERT INTO evaluation_reports
                (principal_id, report_id, run_id, plan_id, plan_version,
                 plan_digest, source_digest, report_json, plan_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (principal_id, report_id, run_id, plan_id, plan_version,
                  plan_digest, source_digest, report_json, plan_json, now))
            row = connection.execute(
                "SELECT * FROM evaluation_reports WHERE principal_id=? AND report_id=?",
                (principal_id, report_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("EVALUATION_REPORT_PERSIST_FAILED")
            connection.commit()
            return self._row_payload(row), True

    def get(self, *, principal_id: str, report_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_reports WHERE principal_id=? AND report_id=?",
                (principal_id, report_id),
            ).fetchone()
        return self._row_payload(row) if row else None

    def list_for_run(self, *, principal_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_reports WHERE principal_id=? AND run_id=? "
                "ORDER BY created_at DESC, report_id",
                (principal_id, run_id),
            ).fetchall()
        return [self._row_payload(row) for row in rows]

    def health(self) -> dict[str, Any]:
        with self._connection() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM evaluation_reports").fetchone()[0])
        return {"available": True, "report_count": count}
