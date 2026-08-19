"""Controlled compilation, evaluation, and approval of advisory experiences."""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, field_validator

from .capability_registry import get_capability
from .experience import ExperienceScope, ExperienceStore, _canonical, _now, _sha256

LESSON_SCHEMA_VERSION = "sat-sim.experience-lesson.v1"
LessonKind = Literal[
    "parameter_output_explanation",
    "failure_recovery_advice",
    "taskspec_dag_example",
]
LessonState = Literal[
    "candidate",
    "evaluated",
    "approved",
    "rejected",
    "revoked",
]


class ExperienceLesson(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["sat-sim.experience-lesson.v1"] = LESSON_SCHEMA_VERSION
    lesson_id: str
    created_at: str
    scope: ExperienceScope
    kind: LessonKind
    state: LessonState = "candidate"
    title: str
    advisory: dict[str, Any]
    source_experience_ids: tuple[str, ...]
    evidence_hashes: dict[str, str]
    capability_ids: tuple[str, ...]
    modes: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    compatible_task_spec_versions: tuple[str, ...] = ("1.0.0",)
    safety_labels: tuple[str, ...] = (
        "advisory_only",
        "no_executable_code",
        "registry_validator_authoritative",
    )
    lesson_sha256: str

    @field_validator("lesson_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("must be a lowercase SHA-256 digest")
        return value

    def integrity_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"lesson_id", "lesson_sha256", "state"},
        )

    def verify_integrity(self) -> bool:
        expected = _sha256(_canonical(self.integrity_payload()))
        return self.lesson_sha256 == expected and self.lesson_id == f"lesson_{expected[:24]}"


def _build_lesson(**payload: Any) -> ExperienceLesson:
    candidate = ExperienceLesson(
        schema_version=LESSON_SCHEMA_VERSION,
        created_at=payload.pop("created_at", _now()),
        state="candidate",
        **payload,
        lesson_id="lesson_" + ("0" * 24),
        lesson_sha256="0" * 64,
    )
    digest = _sha256(_canonical(candidate.integrity_payload()))
    return candidate.model_copy(
        update={"lesson_id": f"lesson_{digest[:24]}", "lesson_sha256": digest}
    )


def _verified_task_outline(store: ExperienceStore, record: Any) -> dict[str, Any] | None:
    digest = record.task_spec_sha256
    if not digest:
        return None
    try:
        task_spec = json.loads(store._object_path(digest).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(task_spec, Mapping):
        return None
    model = task_spec.get("model") if isinstance(task_spec.get("model"), Mapping) else {}
    target = model.get("target") if isinstance(model.get("target"), Mapping) else {}
    simulation = (
        task_spec.get("simulation")
        if isinstance(task_spec.get("simulation"), Mapping)
        else {}
    )
    parameters = (
        task_spec.get("parameters")
        if isinstance(task_spec.get("parameters"), Mapping)
        else {}
    )
    values = (
        parameters.get("values")
        if isinstance(parameters.get("values"), Mapping)
        else {}
    )
    outputs = (
        task_spec.get("outputs") if isinstance(task_spec.get("outputs"), Mapping) else {}
    )
    events = task_spec.get("events") if isinstance(task_spec.get("events"), Mapping) else {}
    event_outline: dict[str, list[dict[str, Any]]] = {}
    for category in ("faults", "degradations", "constraints"):
        rows = []
        for item in events.get(category, []):
            if not isinstance(item, Mapping):
                continue
            rows.append(
                {
                    key: item[key]
                    for key in (
                        "effect",
                        "target",
                        "target_type",
                        "start_s",
                        "end_s",
                        "magnitude",
                        "scale",
                        "parameters",
                    )
                    if key in item
                }
            )
        if rows:
            event_outline[category] = rows
    return {
        "schema_version": task_spec.get("schema_version"),
        "model": {
            "capability_id": model.get("capability_id"),
            "target": {
                key: target[key] for key in ("level", "name", "mode") if key in target
            },
        },
        "simulation": {
            key: simulation[key]
            for key in ("level", "subsystem", "duration_s", "step_s", "sample_s")
            if key in simulation
        },
        "parameters": {"values": dict(values)},
        "events": event_outline,
        "outputs": {
            key: list(outputs[key])[:4]
            for key in ("qoi", "plots")
            if isinstance(outputs.get(key), list) and outputs[key]
        },
    }


class ExperienceLessonService:
    def __init__(self, store: ExperienceStore):
        self.store = store
        self._initialize()

    def _initialize(self) -> None:
        with self.store._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS experience_lessons (
                    lesson_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    lesson_sha256 TEXT NOT NULL UNIQUE,
                    object_sha256 TEXT NOT NULL,
                    compiled_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lesson_scope_state
                    ON experience_lessons(tenant_id,project_id,state,created_at);
                CREATE TABLE IF NOT EXISTS lesson_sources (
                    lesson_id TEXT NOT NULL REFERENCES experience_lessons(lesson_id),
                    experience_id TEXT NOT NULL,
                    PRIMARY KEY(lesson_id,experience_id)
                );
                CREATE TABLE IF NOT EXISTS lesson_facets (
                    lesson_id TEXT NOT NULL REFERENCES experience_lessons(lesson_id),
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY(lesson_id,kind,value)
                );
                CREATE INDEX IF NOT EXISTS idx_lesson_facets
                    ON lesson_facets(kind,value,lesson_id);
                CREATE TABLE IF NOT EXISTS lesson_retrieval_index (
                    lesson_id TEXT NOT NULL REFERENCES experience_lessons(lesson_id),
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    facet_kind TEXT NOT NULL,
                    facet_value TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(lesson_id,facet_kind,facet_value)
                );
                CREATE INDEX IF NOT EXISTS idx_lesson_retrieval
                    ON lesson_retrieval_index(
                        tenant_id,project_id,state,facet_kind,facet_value,
                        created_at DESC,lesson_id
                    );
                CREATE TABLE IF NOT EXISTS lesson_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    lesson_id TEXT NOT NULL REFERENCES experience_lessons(lesson_id),
                    evaluator TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    held_out_json TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    evaluation_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS lesson_reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id TEXT NOT NULL REFERENCES experience_lessons(lesson_id),
                    reviewer TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(lesson_id,reviewer,decision)
                );
                CREATE TABLE IF NOT EXISTS lesson_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    lesson_ids_json TEXT NOT NULL,
                    snapshot_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS lesson_reuse_config (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    active_snapshot_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,project_id)
                );
                """
            )
            evaluation_columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(lesson_evaluations)").fetchall()
            }
            if "evidence_json" not in evaluation_columns:
                db.execute(
                    """ALTER TABLE lesson_evaluations
                       ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'"""
                )
            db.execute(
                """INSERT OR IGNORE INTO lesson_retrieval_index(
                    lesson_id,tenant_id,project_id,state,facet_kind,facet_value,created_at
                )
                SELECT l.lesson_id,l.tenant_id,l.project_id,l.state,
                       f.kind,f.value,l.created_at
                FROM experience_lessons l
                JOIN lesson_facets f ON f.lesson_id=l.lesson_id"""
            )

    def compile(
        self,
        experience_ids: Sequence[str],
        *,
        scope: ExperienceScope,
        actor: str,
    ) -> tuple[ExperienceLesson, bool]:
        source_ids = tuple(sorted(set(str(item) for item in experience_ids)))
        if not source_ids:
            raise ValueError("at least one source experience is required")
        records = []
        for experience_id in source_ids:
            verification = self.store.verify(experience_id, scope=scope, actor=actor)
            if not verification["ok"]:
                raise ValueError(f"SOURCE_EXPERIENCE_INTEGRITY_FAILED:{experience_id}")
            record = self.store.get(experience_id, scope=scope, actor=actor)
            with self.store._connect() as db:
                current = db.execute(
                    """SELECT trust_level,revoked FROM experiences
                       WHERE experience_id=? AND tenant_id=? AND project_id=?""",
                    (experience_id, scope.tenant_id, scope.project_id),
                ).fetchone()
            if (
                current is None
                or current["revoked"]
                or current["trust_level"] == "revoked"
            ):
                raise ValueError(f"SOURCE_EXPERIENCE_REVOKED:{experience_id}")
            required_labels = {"sealed_run_bundle", "hash_verified"}
            if (
                not required_labels.issubset(record.security_labels)
                or not record.run_bundle_sha256
                or not record.task_spec_sha256
                or not record.execution_plan_sha256
            ):
                raise ValueError(f"SOURCE_EXPERIENCE_NOT_SEALED:{experience_id}")
            records.append(record)

        capabilities = tuple(sorted({item for record in records for item in record.capability_ids}))
        if not capabilities:
            raise ValueError("source experience has no capability")
        for capability_id in capabilities:
            get_capability(capability_id)
        modes = tuple(sorted({item for record in records for item in record.modes}))
        effects = tuple(sorted({item for record in records for item in record.effects}))
        evidence_hashes = {
            f"{record.experience_id}:record": record.record_sha256 for record in records
        }
        for record in records:
            if record.run_bundle_sha256:
                evidence_hashes[f"{record.experience_id}:run_bundle"] = record.run_bundle_sha256

        error_codes = tuple(
            sorted(
                {
                    str(error.get("code") or error.get("reason_code"))
                    for record in records
                    for error in record.errors
                    if error.get("code") or error.get("reason_code")
                }
            )
        )
        failed = [record for record in records if record.validation_result != "PASS"]
        if failed or error_codes:
            kind: LessonKind = "failure_recovery_advice"
            advisory = {
                "error_codes": list(error_codes),
                "action": "Re-run the deterministic planner and validators using the same "
                "Capability Registry scope; do not bypass failed physical or security gates.",
                "may_change_capability": False,
                "may_change_claim_level": False,
            }
            title = f"Validated recovery boundary for {', '.join(capabilities)}"
        else:
            kind = "taskspec_dag_example"
            outlines = [
                outline
                for record in records
                if (outline := _verified_task_outline(self.store, record)) is not None
            ]
            advisory = {
                "task_spec_hashes": [
                    record.task_spec_sha256 for record in records if record.task_spec_sha256
                ],
                "execution_plan_hashes": [
                    record.execution_plan_sha256
                    for record in records
                    if record.execution_plan_sha256
                ],
                "validation_results": [record.validation_result for record in records],
                "verified_taskspec_outlines": outlines,
                "reuse_mode": "reference_only",
                "may_emit_code": False,
            }
            title = f"Verified TaskSpec and DAG references for {', '.join(capabilities)}"

        lesson = _build_lesson(
            scope=scope,
            kind=kind,
            title=title,
            advisory=advisory,
            source_experience_ids=source_ids,
            evidence_hashes=evidence_hashes,
            capability_ids=capabilities,
            modes=modes,
            effects=effects,
        )
        raw = _canonical(lesson.model_dump(mode="json"))
        object_sha256 = self.store._put_object(raw, "application/json")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT lesson_id FROM experience_lessons
                   WHERE tenant_id=? AND project_id=? AND lesson_sha256=?""",
                (scope.tenant_id, scope.project_id, lesson.lesson_sha256),
            ).fetchone()
            if existing:
                loaded = self.get(str(existing["lesson_id"]), scope=scope, connection=db)
                db.execute("COMMIT")
                return loaded, False
            db.execute(
                """INSERT INTO experience_lessons(
                    lesson_id,tenant_id,project_id,kind,state,lesson_sha256,
                    object_sha256,compiled_by,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    lesson.lesson_id,
                    scope.tenant_id,
                    scope.project_id,
                    lesson.kind,
                    "candidate",
                    lesson.lesson_sha256,
                    object_sha256,
                    actor,
                    lesson.created_at,
                    lesson.created_at,
                ),
            )
            for source_id in source_ids:
                db.execute(
                    "INSERT INTO lesson_sources(lesson_id,experience_id) VALUES(?,?)",
                    (lesson.lesson_id, source_id),
                )
            for kind_name, values in (
                ("capability", capabilities),
                ("mode", modes),
                ("effect", effects),
            ):
                for value in values:
                    db.execute(
                        "INSERT INTO lesson_facets(lesson_id,kind,value) VALUES(?,?,?)",
                        (lesson.lesson_id, kind_name, value),
                    )
                    db.execute(
                        """INSERT INTO lesson_retrieval_index(
                            lesson_id,tenant_id,project_id,state,facet_kind,
                            facet_value,created_at
                        ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            lesson.lesson_id,
                            scope.tenant_id,
                            scope.project_id,
                            "candidate",
                            kind_name,
                            value,
                            lesson.created_at,
                        ),
                    )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_compile",
                actor=actor,
                details={"lesson_id": lesson.lesson_id, **scope.model_dump(mode="json")},
            )
            db.execute("COMMIT")
        return lesson, True

    def get(
        self,
        lesson_id: str,
        *,
        scope: ExperienceScope,
        connection: sqlite3.Connection | None = None,
    ) -> ExperienceLesson:
        owned = connection is None
        db = connection or self.store._connect()
        try:
            row = db.execute(
                """SELECT * FROM experience_lessons
                   WHERE lesson_id=? AND tenant_id=? AND project_id=?""",
                (lesson_id, scope.tenant_id, scope.project_id),
            ).fetchone()
            if row is None:
                raise KeyError(lesson_id)
            raw = self.store._object_path(str(row["object_sha256"])).read_bytes()
            lesson = ExperienceLesson.model_validate_json(raw)
            if (
                _sha256(raw) != row["object_sha256"]
                or lesson.lesson_sha256 != row["lesson_sha256"]
                or not lesson.verify_integrity()
            ):
                raise ValueError("LESSON_INTEGRITY_FAILED")
            return lesson.model_copy(update={"state": str(row["state"])})
        finally:
            if owned:
                db.close()

    @staticmethod
    def _metrics(values: Mapping[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, value in values.items():
            number = float(value)
            if not math.isfinite(number):
                raise ValueError("evaluation metrics must be finite")
            result[str(key)] = number
        return result

    def evaluate(
        self,
        lesson_id: str,
        *,
        scope: ExperienceScope,
        evaluator: str,
        held_out_case_ids: Sequence[str],
        baseline_metrics: Mapping[str, Any],
        candidate_metrics: Mapping[str, Any],
        evaluation_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not held_out_case_ids:
            raise ValueError("held-out evaluation requires cases")
        lesson = self.get(lesson_id, scope=scope)
        if lesson.state in {"approved", "rejected", "revoked"}:
            raise ValueError(f"lesson cannot be evaluated from state {lesson.state}")
        baseline = self._metrics(baseline_metrics)
        candidate = self._metrics(candidate_metrics)
        required = {
            "primary_score",
            "safety_regressions",
            "physical_regressions",
            "backward_regressions",
        }
        if not required.issubset(baseline) or not required.issubset(candidate):
            raise ValueError(f"evaluation requires metrics: {sorted(required)}")
        evidence = dict(evaluation_evidence)
        evidence_required = {"runner", "case_result_sha256", "metrics_source_sha256"}
        if not evidence_required.issubset(evidence):
            raise ValueError(f"evaluation evidence requires: {sorted(evidence_required)}")
        for field in ("case_result_sha256", "metrics_source_sha256"):
            digest = str(evidence[field])
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError(f"{field} must be a lowercase SHA-256 digest")
        if not str(evidence["runner"]).strip():
            raise ValueError("evaluation evidence runner is required")
        passed = (
            candidate["primary_score"] >= baseline["primary_score"]
            and candidate["safety_regressions"] == 0
            and candidate["physical_regressions"] == 0
            and candidate["backward_regressions"] == 0
        )
        payload = {
            "lesson_id": lesson_id,
            "evaluator": evaluator,
            "created_at": _now(),
            "held_out_case_ids": sorted(set(str(item) for item in held_out_case_ids)),
            "baseline_metrics": baseline,
            "candidate_metrics": candidate,
            "evaluation_evidence": evidence,
            "passed": passed,
        }
        digest = _sha256(_canonical(payload))
        evaluation_id = f"eval_{digest[:24]}"
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT OR IGNORE INTO lesson_evaluations(
                    evaluation_id,lesson_id,evaluator,created_at,held_out_json,
                    baseline_json,candidate_json,evidence_json,passed,evaluation_sha256
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    evaluation_id,
                    lesson_id,
                    evaluator,
                    payload["created_at"],
                    json.dumps(payload["held_out_case_ids"]),
                    json.dumps(baseline, sort_keys=True),
                    json.dumps(candidate, sort_keys=True),
                    json.dumps(evidence, sort_keys=True),
                    int(passed),
                    digest,
                ),
            )
            db.execute(
                """UPDATE experience_lessons SET state='evaluated',updated_at=?
                   WHERE lesson_id=? AND state='candidate'""",
                (_now(), lesson_id),
            )
            db.execute(
                """UPDATE lesson_retrieval_index SET state='evaluated'
                   WHERE lesson_id=? AND state='candidate'""",
                (lesson_id,),
            )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_evaluate",
                actor=evaluator,
                details={"lesson_id": lesson_id, "passed": passed},
            )
            db.execute("COMMIT")
        return {"evaluation_id": evaluation_id, **payload}

    def review(
        self,
        lesson_id: str,
        *,
        scope: ExperienceScope,
        reviewer: str,
        decision: Literal["APPROVE", "REJECT"],
        reason: str,
    ) -> ExperienceLesson:
        lesson = self.get(lesson_id, scope=scope)
        if lesson.state in {"approved", "rejected", "revoked"}:
            raise ValueError(f"lesson cannot be reviewed from state {lesson.state}")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            evaluation = db.execute(
                """SELECT passed FROM lesson_evaluations
                   WHERE lesson_id=? ORDER BY created_at DESC LIMIT 1""",
                (lesson_id,),
            ).fetchone()
            if decision == "APPROVE" and (evaluation is None or not evaluation["passed"]):
                db.execute("ROLLBACK")
                raise ValueError("LESSON_HELD_OUT_EVALUATION_REQUIRED")
            identities = {
                str(row["identity"])
                for row in db.execute(
                    """SELECT compiled_by AS identity FROM experience_lessons WHERE lesson_id=?
                       UNION SELECT evaluator AS identity FROM lesson_evaluations WHERE lesson_id=?""",
                    (lesson_id, lesson_id),
                ).fetchall()
            }
            if reviewer in identities:
                db.execute("ROLLBACK")
                raise ValueError("LESSON_REVIEW_ROLE_SEPARATION_REQUIRED")
            db.execute(
                """INSERT INTO lesson_reviews(lesson_id,reviewer,decision,reason,created_at)
                   VALUES(?,?,?,?,?)""",
                (lesson_id, reviewer, decision, reason, _now()),
            )
            if decision == "REJECT":
                state = "rejected"
            else:
                count = db.execute(
                    """SELECT COUNT(DISTINCT reviewer) AS count FROM lesson_reviews
                       WHERE lesson_id=? AND decision='APPROVE'""",
                    (lesson_id,),
                ).fetchone()["count"]
                state = "approved" if int(count) >= 2 else "evaluated"
            db.execute(
                "UPDATE experience_lessons SET state=?,updated_at=? WHERE lesson_id=?",
                (state, _now(), lesson_id),
            )
            db.execute(
                "UPDATE lesson_retrieval_index SET state=? WHERE lesson_id=?",
                (state, lesson_id),
            )
            self.store._audit(
                db,
                experience_id=None,
                action=f"lesson_{decision.lower()}",
                actor=reviewer,
                details={"lesson_id": lesson_id, "state": state, "reason": reason},
            )
            db.execute("COMMIT")
        return self.get(lesson_id, scope=scope)

    def revoke(
        self,
        lesson_id: str,
        *,
        scope: ExperienceScope,
        actor: str,
        reason: str,
    ) -> ExperienceLesson:
        lesson = self.get(lesson_id, scope=scope)
        if lesson.state != "approved":
            raise ValueError("only approved lessons may be revoked")
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE experience_lessons SET state='revoked',updated_at=? WHERE lesson_id=?",
                (_now(), lesson_id),
            )
            db.execute(
                """UPDATE lesson_retrieval_index SET state='revoked'
                   WHERE lesson_id=?""",
                (lesson_id,),
            )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_revoke",
                actor=actor,
                details={"lesson_id": lesson_id, "reason": reason},
            )
            db.execute("COMMIT")
        return self.get(lesson_id, scope=scope)

    def retrieve_approved(
        self,
        *,
        scope: ExperienceScope,
        capability_id: str,
        mode: str | None = None,
        task_spec_version: str = "1.0.0",
        limit: int = 10,
    ) -> list[ExperienceLesson]:
        with self.store._connect() as db:
            config = db.execute(
                """SELECT enabled,active_snapshot_id FROM lesson_reuse_config
                   WHERE tenant_id=? AND project_id=?""",
                (scope.tenant_id, scope.project_id),
            ).fetchone()
            if config is None or not config["enabled"] or not config["active_snapshot_id"]:
                return []
            snapshot = db.execute(
                "SELECT lesson_ids_json FROM lesson_snapshots WHERE snapshot_id=?",
                (config["active_snapshot_id"],),
            ).fetchone()
            if snapshot is None:
                return []
            allowed = set(json.loads(snapshot["lesson_ids_json"]))
            rows = db.execute(
                """SELECT lesson_id FROM lesson_retrieval_index
                   WHERE tenant_id=? AND project_id=? AND state='approved'
                   AND facet_kind='capability' AND facet_value=?
                   ORDER BY created_at DESC,lesson_id LIMIT ?""",
                (scope.tenant_id, scope.project_id, capability_id, limit),
            ).fetchall()
        lessons = [
            self.get(str(row["lesson_id"]), scope=scope)
            for row in rows
            if str(row["lesson_id"]) in allowed
        ]
        return [
            lesson
            for lesson in lessons
            if (mode is None or mode in lesson.modes)
            and task_spec_version in lesson.compatible_task_spec_versions
        ]

    def create_snapshot(self, *, scope: ExperienceScope, actor: str) -> dict[str, Any]:
        with self.store._connect() as db:
            lesson_ids = [
                str(row["lesson_id"])
                for row in db.execute(
                    """SELECT lesson_id FROM experience_lessons
                       WHERE tenant_id=? AND project_id=? AND state='approved'
                       ORDER BY lesson_id""",
                    (scope.tenant_id, scope.project_id),
                ).fetchall()
            ]
            payload = {
                "scope": scope.model_dump(mode="json"),
                "lesson_ids": lesson_ids,
                "created_at": _now(),
                "actor": actor,
            }
            digest = _sha256(_canonical(payload))
            snapshot_id = f"snapshot_{digest[:24]}"
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO lesson_snapshots(
                    snapshot_id,tenant_id,project_id,created_at,actor,
                    lesson_ids_json,snapshot_sha256
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    scope.tenant_id,
                    scope.project_id,
                    payload["created_at"],
                    actor,
                    json.dumps(lesson_ids),
                    digest,
                ),
            )
            db.execute(
                """INSERT INTO lesson_reuse_config(
                    tenant_id,project_id,enabled,active_snapshot_id,updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(tenant_id,project_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    active_snapshot_id=excluded.active_snapshot_id,
                    updated_at=excluded.updated_at""",
                (scope.tenant_id, scope.project_id, 1, snapshot_id, _now()),
            )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_snapshot_activate",
                actor=actor,
                details={"snapshot_id": snapshot_id, "lesson_count": len(lesson_ids)},
            )
            db.execute("COMMIT")
        return {"snapshot_id": snapshot_id, **payload}

    def set_reuse_enabled(
        self,
        *,
        scope: ExperienceScope,
        enabled: bool,
        actor: str,
    ) -> None:
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO lesson_reuse_config(
                    tenant_id,project_id,enabled,active_snapshot_id,updated_at
                ) VALUES(?,?,?,NULL,?)
                ON CONFLICT(tenant_id,project_id) DO UPDATE SET
                    enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (scope.tenant_id, scope.project_id, int(enabled), _now()),
            )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_reuse_enable" if enabled else "lesson_reuse_disable",
                actor=actor,
                details=scope.model_dump(mode="json"),
            )
            db.execute("COMMIT")

    def rollback_snapshot(
        self,
        snapshot_id: str,
        *,
        scope: ExperienceScope,
        actor: str,
    ) -> None:
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = db.execute(
                """SELECT snapshot_id FROM lesson_snapshots
                   WHERE snapshot_id=? AND tenant_id=? AND project_id=?""",
                (snapshot_id, scope.tenant_id, scope.project_id),
            ).fetchone()
            if snapshot is None:
                db.execute("ROLLBACK")
                raise KeyError(snapshot_id)
            db.execute(
                """INSERT INTO lesson_reuse_config(
                    tenant_id,project_id,enabled,active_snapshot_id,updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(tenant_id,project_id) DO UPDATE SET
                    enabled=1,active_snapshot_id=excluded.active_snapshot_id,
                    updated_at=excluded.updated_at""",
                (scope.tenant_id, scope.project_id, 1, snapshot_id, _now()),
            )
            self.store._audit(
                db,
                experience_id=None,
                action="lesson_snapshot_rollback",
                actor=actor,
                details={"snapshot_id": snapshot_id, **scope.model_dump(mode="json")},
            )
            db.execute("COMMIT")


__all__ = [
    "ExperienceLesson",
    "ExperienceLessonService",
    "LESSON_SCHEMA_VERSION",
    "LessonKind",
    "LessonState",
]
