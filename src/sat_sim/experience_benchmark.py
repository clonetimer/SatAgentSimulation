"""Scale benchmark for the indexed approved-experience retrieval path."""
from __future__ import annotations

import sqlite3
import statistics
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

from .experience import ExperienceStore
from .experience_lessons import ExperienceLessonService
from .task_spec import write_json

DEFAULT_SCALES = (1_000, 10_000, 100_000)
PERFORMANCE_BUDGETS = {
    1_000: {"p95_ms": 20.0},
    10_000: {"p95_ms": 50.0},
    100_000: {"p95_ms": 150.0},
    "max_bytes_per_record": 2_048.0,
    "max_total_insert_seconds": 120.0,
}


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return ordered[index]


def _query(db: sqlite3.Connection, *, limit: int = 10) -> list[str]:
    return [
        str(row["lesson_id"])
        for row in db.execute(
            """SELECT lesson_id FROM lesson_retrieval_index
               WHERE tenant_id=? AND project_id=? AND state='approved'
               AND facet_kind='capability' AND facet_value=?
               ORDER BY created_at DESC,lesson_id LIMIT ?""",
            ("benchmark", "scale", "component.battery.v1", limit),
        ).fetchall()
    ]


def run_experience_retrieval_benchmark(
    *,
    output_path: str | Path,
    scales: Sequence[int] = DEFAULT_SCALES,
    repetitions: int = 100,
) -> dict[str, Any]:
    normalized_scales = tuple(sorted(set(int(item) for item in scales)))
    if not normalized_scales or normalized_scales[0] < 1:
        raise ValueError("scales must contain positive record counts")
    if repetitions < 10:
        raise ValueError("repetitions must be at least 10")

    rows: list[dict[str, Any]] = []
    total_insert_seconds = 0.0
    inserted = 0
    with tempfile.TemporaryDirectory(prefix="sat-experience-benchmark-") as temporary:
        store = ExperienceStore(Path(temporary) / "store")
        ExperienceLessonService(store)
        for target in normalized_scales:
            start = perf_counter()
            lesson_rows = []
            facet_rows = []
            retrieval_rows = []
            for index in range(inserted, target):
                lesson_id = f"bench_{index:012d}"
                digest = f"{index:064x}"[-64:]
                lesson_rows.append(
                    (
                        lesson_id,
                        "benchmark",
                        "scale",
                        "taskspec_dag_example",
                        "approved",
                        digest,
                        digest,
                        "benchmark-loader",
                        f"2026-01-01T00:00:{index % 60:02d}Z",
                        f"2026-01-01T00:00:{index % 60:02d}Z",
                    )
                )
                capability = (
                    "component.battery.v1"
                    if index % 100 == 0
                    else f"benchmark.capability.{index % 997}"
                )
                facet_rows.append((lesson_id, "capability", capability))
                retrieval_rows.append(
                    (
                        lesson_id,
                        "benchmark",
                        "scale",
                        "approved",
                        "capability",
                        capability,
                        f"2026-01-01T00:00:{index % 60:02d}Z",
                    )
                )
            with store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.executemany(
                    """INSERT INTO experience_lessons(
                        lesson_id,tenant_id,project_id,kind,state,lesson_sha256,
                        object_sha256,compiled_by,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    lesson_rows,
                )
                db.executemany(
                    "INSERT INTO lesson_facets(lesson_id,kind,value) VALUES(?,?,?)",
                    facet_rows,
                )
                db.executemany(
                    """INSERT INTO lesson_retrieval_index(
                        lesson_id,tenant_id,project_id,state,facet_kind,
                        facet_value,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    retrieval_rows,
                )
                db.execute("COMMIT")
            elapsed_insert = perf_counter() - start
            total_insert_seconds += elapsed_insert
            inserted = target

            with store._connect() as db:
                for _ in range(10):
                    _query(db)
                timings_ms = []
                result_count = 0
                for _ in range(repetitions):
                    query_start = perf_counter()
                    result_count = len(_query(db))
                    timings_ms.append((perf_counter() - query_start) * 1_000.0)
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            database_bytes = store.database_path.stat().st_size
            p95 = _percentile(timings_ms, 0.95)
            budget = PERFORMANCE_BUDGETS.get(target, {"p95_ms": 150.0})
            rows.append(
                {
                    "record_count": target,
                    "matched_result_count": result_count,
                    "insert_seconds_increment": round(elapsed_insert, 6),
                    "database_bytes": database_bytes,
                    "bytes_per_record": round(database_bytes / target, 3),
                    "query_repetitions": repetitions,
                    "p50_ms": round(statistics.median(timings_ms), 6),
                    "p95_ms": round(p95, 6),
                    "max_ms": round(max(timings_ms), 6),
                    "p95_budget_ms": budget["p95_ms"],
                    "passed": bool(
                        p95 <= budget["p95_ms"]
                        and database_bytes / target
                        <= PERFORMANCE_BUDGETS["max_bytes_per_record"]
                    ),
                }
            )

    report = {
        "schema_version": "sat-sim.experience-retrieval-benchmark.v1",
        "status": "PASS"
        if all(row["passed"] for row in rows)
        and total_insert_seconds <= PERFORMANCE_BUDGETS["max_total_insert_seconds"]
        else "FAIL",
        "synthetic_metadata_only": True,
        "query_path": "scope + approved state + capability facet + limit",
        "budgets": PERFORMANCE_BUDGETS,
        "total_insert_seconds": round(total_insert_seconds, 6),
        "rows": rows,
    }
    write_json(output_path, report)
    return report


__all__ = [
    "DEFAULT_SCALES",
    "PERFORMANCE_BUDGETS",
    "run_experience_retrieval_benchmark",
]
