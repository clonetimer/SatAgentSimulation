"""Persistent parameter-sweep experiments and multi-run comparison."""
from __future__ import annotations

import copy
import itertools
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .assertions import normalize_assertions
from .task_models import canonicalize_task_spec
from .form_schema import capability_form_schema

EXPERIMENT_SCHEMA_VERSION = "experiment-center.v2"
TERMINAL_RUN_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = payload
    for part in parts[:-1]:
        current = cursor.get(part)
        if not isinstance(current, dict):
            current = {}
            cursor[part] = current
        cursor = current
    cursor[parts[-1]] = copy.deepcopy(value)



def sweep_parameter_options(base_task_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    canonical = canonicalize_task_spec(base_task_spec)
    capability_id = str(canonical.get("model", {}).get("capability_id") or "")
    if not capability_id:
        return []
    schema = capability_form_schema(capability_id)
    options: list[dict[str, Any]] = []
    for field in schema.get("fields", []):
        if not isinstance(field, Mapping):
            continue
        path = str(field.get("path") or "")
        field_type = str(field.get("type") or "")
        if not (path.startswith("simulation.") or path.startswith("parameters.values.")):
            continue
        if field_type not in {"number", "integer", "boolean", "string"}:
            continue
        if str(field.get("widget") or "") == "object_editor":
            continue
        options.append({
            "path": path, "label": field.get("label") or path, "description": field.get("description") or "",
            "unit": field.get("unit"), "type": field_type, "minimum": field.get("minimum"),
            "maximum": field.get("maximum"), "enum": field.get("enum") or [],
        })
    return options

def validate_sweep_paths(base_task_spec: Mapping[str, Any], sweep: Mapping[str, Sequence[Any]]) -> None:
    allowed = {item["path"] for item in sweep_parameter_options(base_task_spec)}
    unknown = [str(path) for path in sweep if str(path) not in allowed]
    if unknown:
        raise ValueError(f"unsupported sweep parameter path(s): {', '.join(unknown)}")

def expand_sweep(base_task_spec: Mapping[str, Any], sweep: Mapping[str, Sequence[Any]], *, max_variants: int = 64) -> list[dict[str, Any]]:
    canonical = canonicalize_task_spec(base_task_spec)
    validate_sweep_paths(canonical, sweep)
    paths = list(sweep)
    values: list[list[Any]] = []
    for path in paths:
        raw = sweep[path]
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
            raise ValueError(f"sweep value for {path} must be a non-empty list")
        values.append(list(raw))
    count = 1
    for item in values:
        count *= len(item)
    if count > max_variants:
        raise ValueError(f"experiment expands to {count} variants; limit is {max_variants}")
    variants: list[dict[str, Any]] = []
    for index, combination in enumerate(itertools.product(*values) if values else [()]):
        spec = copy.deepcopy(canonical)
        parameters = dict(zip(paths, combination))
        for path, value in parameters.items():
            _set_path(spec, path, value)
            if path.startswith("parameters.values."):
                _set_path(spec, "model.config." + path.removeprefix("parameters.values."), value)
            elif path.startswith("model.config."):
                _set_path(spec, "parameters.values." + path.removeprefix("model.config."), value)
        spec["task"]["id"] = f"{canonical['task']['id']}_v{index + 1:03d}"
        spec["task"]["name"] = f"{canonical['task']['name']} · 变体 {index + 1}"
        spec.setdefault("metadata", {})["experiment_variant"] = {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "variant_index": index,
            "parameters": copy.deepcopy(parameters),
        }
        variants.append({"variant_index": index, "parameters": parameters, "task_spec": canonicalize_task_spec(spec)})
    return variants


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    name: str
    status: str
    base_task_spec: dict[str, Any]
    sweep: dict[str, list[Any]]
    assertions: list[dict[str, Any]]
    created_at: str
    updated_at: str
    variant_count: int
    experiment_type: str = "sweep"
    sampling_plan: dict[str, Any] | None = None

    def to_dict(self, *, include_task_spec: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_task_spec:
            payload.pop("base_task_spec", None)
        return payload


class ExperimentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    base_task_spec_json TEXT NOT NULL,
                    sweep_json TEXT NOT NULL,
                    assertions_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    variant_count INTEGER NOT NULL,
                    experiment_type TEXT NOT NULL DEFAULT 'sweep',
                    sampling_plan_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS experiment_runs (
                    experiment_id TEXT NOT NULL,
                    variant_index INTEGER NOT NULL,
                    run_id TEXT,
                    parameters_json TEXT NOT NULL,
                    task_spec_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    validation_result TEXT,
                    assertion_status TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, variant_index),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_experiments_updated ON experiments(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_experiment_runs_state ON experiment_runs(experiment_id, state);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(experiments)").fetchall()}
            if "experiment_type" not in columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN experiment_type TEXT NOT NULL DEFAULT 'sweep'")
            if "sampling_plan_json" not in columns:
                connection.execute("ALTER TABLE experiments ADD COLUMN sampling_plan_json TEXT NOT NULL DEFAULT '{}'")

    @staticmethod
    def _record(row: sqlite3.Row | None) -> ExperimentRecord | None:
        if row is None:
            return None
        return ExperimentRecord(
            experiment_id=row["experiment_id"], name=row["name"], status=row["status"],
            base_task_spec=json.loads(row["base_task_spec_json"]), sweep=json.loads(row["sweep_json"]),
            assertions=json.loads(row["assertions_json"]), created_at=row["created_at"], updated_at=row["updated_at"],
            variant_count=int(row["variant_count"]),
            experiment_type=str(row["experiment_type"] if "experiment_type" in row.keys() else "sweep"),
            sampling_plan=json.loads(row["sampling_plan_json"] if "sampling_plan_json" in row.keys() else "{}"),
        )

    def create(
        self,
        *,
        name: str,
        base_task_spec: Mapping[str, Any],
        sweep: Mapping[str, Sequence[Any]] | None = None,
        assertions: Any = None,
        experiment_id: str | None = None,
        experiment_type: str = "sweep",
        sampling_plan: Mapping[str, Any] | None = None,
    ) -> ExperimentRecord:
        canonical = canonicalize_task_spec(base_task_spec)
        normalized_assertions = normalize_assertions(assertions)
        if normalized_assertions:
            canonical.setdefault("model", {}).setdefault("validation", {})["acceptance_assertions"] = normalized_assertions
        normalized_type = str(experiment_type or "sweep").lower()
        if normalized_type in {"montecarlo", "monte_carlo", "mc"}:
            normalized_type = "monte_carlo"
        plan = dict(sampling_plan or {})
        if normalized_type == "monte_carlo":
            from sat_sim.experiments.monte_carlo_engine import expand_monte_carlo
            dispersions = plan.get("dispersions") or {}
            variants = expand_monte_carlo(
                canonical,
                dispersions,
                sample_count=int(plan.get("sample_count") or 16),
                seed=int(plan.get("seed") or 1),
            )
            normalized_sweep = {}
            from sat_sim.experiments.basilisk_mc_adapter import build_basilisk_mc_plan
            native_controller = build_basilisk_mc_plan(
                canonical,
                dispersions,
                sample_count=int(plan.get("sample_count") or 16),
                seed=int(plan.get("seed") or 1),
            )
            stored_plan = {
                "schema_version": EXPERIMENT_SCHEMA_VERSION,
                "experiment_type": "monte_carlo",
                "seed": int(plan.get("seed") or 1),
                "sample_count": int(plan.get("sample_count") or 16),
                "dispersions": copy.deepcopy(dispersions),
                "execution_engine_default": "taskspec_run_bundle",
                "engine_preference": str(plan.get("engine_preference") or "auto"),
                "native_controller": native_controller.to_dict(),
                "boundary": "The standard launch endpoint preserves independent queue jobs and Run Bundles. Eligible native scenarios may explicitly use the official Controller endpoint.",
            }
            for variant in variants:
                variant_spec = variant.get("task_spec") if isinstance(variant.get("task_spec"), dict) else {}
                variant_spec.setdefault("metadata", {}).setdefault("experiment_variant", {})["native_controller_eligible"] = native_controller.eligible
                variant_spec["metadata"]["experiment_variant"]["standard_execution_engine"] = "taskspec_run_bundle"
                variant["task_spec"] = canonicalize_task_spec(variant_spec)
        elif normalized_type == "sweep":
            normalized_sweep = {str(path): list(values) for path, values in (sweep or {}).items()}
            variants = expand_sweep(canonical, normalized_sweep)
            stored_plan = {
                "schema_version": EXPERIMENT_SCHEMA_VERSION,
                "experiment_type": "sweep",
                "sweep": copy.deepcopy(normalized_sweep),
            }
        else:
            raise ValueError(f"unsupported experiment_type: {experiment_type}")
        eid = experiment_id or f"exp_{uuid4().hex[:12]}"
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO experiments (experiment_id,name,status,base_task_spec_json,sweep_json,assertions_json,created_at,updated_at,variant_count,experiment_type,sampling_plan_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (eid, name, "DRAFT", json.dumps(canonical, ensure_ascii=False), json.dumps(normalized_sweep, ensure_ascii=False),
                 json.dumps(normalized_assertions, ensure_ascii=False), now, now, len(variants), normalized_type, json.dumps(stored_plan, ensure_ascii=False)),
            )
            for variant in variants:
                connection.execute(
                    "INSERT INTO experiment_runs VALUES(?,?,?,?,?,?,?,?,?)",
                    (eid, variant["variant_index"], None, json.dumps(variant["parameters"], ensure_ascii=False),
                     json.dumps(variant["task_spec"], ensure_ascii=False), "DRAFT", None, None, now),
                )
        record = self.get(eid)
        assert record is not None
        return record

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        with self._connect() as connection:
            return self._record(connection.execute("SELECT * FROM experiments WHERE experiment_id=?", (experiment_id,)).fetchone())

    def list(self, *, limit: int = 100) -> list[ExperimentRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM experiments ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [item for row in rows if (item := self._record(row))]

    def members(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM experiment_runs WHERE experiment_id=? ORDER BY variant_index", (experiment_id,)).fetchall()
        return [{
            "experiment_id": row["experiment_id"], "variant_index": int(row["variant_index"]), "run_id": row["run_id"],
            "parameters": json.loads(row["parameters_json"]), "task_spec": json.loads(row["task_spec_json"]),
            "state": row["state"], "validation_result": row["validation_result"],
            "assertion_status": row["assertion_status"], "updated_at": row["updated_at"],
        } for row in rows]

    def assign_run(self, experiment_id: str, variant_index: int, run_id: str, *, state: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE experiment_runs SET run_id=?, state=?, updated_at=? WHERE experiment_id=? AND variant_index=?",
                (run_id, state, now, experiment_id, int(variant_index)),
            )
            connection.execute("UPDATE experiments SET status='RUNNING', updated_at=? WHERE experiment_id=?", (now, experiment_id))

    def update_member(self, experiment_id: str, variant_index: int, *, state: str, validation_result: str | None = None, assertion_status: str | None = None) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE experiment_runs SET state=?, validation_result=?, assertion_status=?, updated_at=? WHERE experiment_id=? AND variant_index=?",
                (state, validation_result, assertion_status, now, experiment_id, int(variant_index)),
            )
            states = [str(row[0]) for row in connection.execute("SELECT state FROM experiment_runs WHERE experiment_id=?", (experiment_id,)).fetchall()]
            if states and all(item in TERMINAL_RUN_STATES for item in states):
                status = "COMPLETED" if all(item == "SUCCEEDED" for item in states) else "COMPLETED_WITH_ERRORS"
            elif any(item in {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"} for item in states):
                status = "RUNNING"
            else:
                status = "DRAFT"
            connection.execute("UPDATE experiments SET status=?, updated_at=? WHERE experiment_id=?", (status, now, experiment_id))

    def delete(self, experiment_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM experiments WHERE experiment_id=?", (experiment_id,))
        return cursor.rowcount > 0

    def health(self) -> dict[str, Any]:
        with self._connect() as connection:
            experiment_count = int(connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0])
            run_count = int(connection.execute("SELECT COUNT(*) FROM experiment_runs").fetchone()[0])
            type_rows = connection.execute("SELECT experiment_type, COUNT(*) FROM experiments GROUP BY experiment_type").fetchall()
        return {"schema_version": EXPERIMENT_SCHEMA_VERSION, "database": str(self.path), "experiment_count": experiment_count, "run_count": run_count, "by_type": {str(row[0]): int(row[1]) for row in type_rows}}


__all__ = ["EXPERIMENT_SCHEMA_VERSION", "TERMINAL_RUN_STATES", "ExperimentRecord", "ExperimentStore", "expand_sweep", "sweep_parameter_options", "validate_sweep_paths"]
