"""Structured local observability for the Web workbench.

The store is intentionally small and file based.  It records control-plane
metadata only; request bodies, Authorization headers, model API keys and other
secret values are never persisted.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LOG_SCHEMA_VERSION = "workbench-log.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class WorkbenchLogStore:
    """Append-only JSONL event store with bounded query helpers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(
        self,
        *,
        category: str,
        event: str,
        level: str = "INFO",
        message: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": LOG_SCHEMA_VERSION,
            "timestamp": _now(),
            "level": str(level).upper(),
            "category": str(category),
            "event": str(event),
            "message": str(message),
            "details": dict(details or {}),
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return payload

    def query(
        self,
        *,
        offset: int = 0,
        limit: int = 200,
        level: str | None = None,
        category: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        if self.path.exists():
            with self._lock:
                raw_lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(raw_lines):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if level and str(row.get("level", "")).upper() != level.upper():
                    continue
                if category and str(row.get("category", "")) != category:
                    continue
                if search:
                    needle = search.casefold()
                    haystack = json.dumps(row, ensure_ascii=False).casefold()
                    if needle not in haystack:
                        continue
                rows.append(row)
        total = len(rows)
        selected = rows[max(0, offset): max(0, offset) + max(1, min(limit, 2000))]
        return {
            "schema_version": "sat-sim.workbench-log-query.v1",
            "path_exposed": False,
            "total": total,
            "offset": max(0, offset),
            "limit": max(1, min(limit, 2000)),
            "rows": selected,
        }

    def tail_text(self, *, max_lines: int = 500) -> str:
        if not self.path.exists():
            return ""
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max(1, min(max_lines, 5000)):]) + ("\n" if lines else "")


__all__ = ["LOG_SCHEMA_VERSION", "WorkbenchLogStore"]
