"""Immutable experience records and a scoped, audited SQLite reservoir."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXPERIENCE_SCHEMA_VERSION = "sat-sim.experience-record.v1"
STORE_SCHEMA_VERSION = 2
TrustLevel = Literal["raw", "verified", "approved", "revoked"]

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: item.model_dump(mode="json")
        if isinstance(item, BaseModel)
        else str(item),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def redact_sensitive_text(value: str) -> tuple[str, str | None]:
    redacted = value
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, _sha256(value.encode("utf-8")) if redacted != value else None


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        redacted, digest = redact_sensitive_text(value)
        return redacted, digest is not None
    if isinstance(value, Mapping):
        changed = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            result[str(key)], item_changed = _redact_value(item)
            changed = changed or item_changed
        return result, changed
    if isinstance(value, (list, tuple)):
        values = []
        changed = False
        for item in value:
            redacted, item_changed = _redact_value(item)
            values.append(redacted)
            changed = changed or item_changed
        return values, changed
    return value, False


class ExperienceScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)


class ExperienceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal["sat-sim.experience-record.v1"] = EXPERIENCE_SCHEMA_VERSION
    experience_id: str
    created_at: str
    observed_at_start: str | None = None
    observed_at_end: str | None = None
    scope: ExperienceScope
    request_sha256: str
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    task_spec_sha256: str | None = None
    dag_sha256: str | None = None
    execution_plan_sha256: str | None = None
    script_sha256: str | None = None
    run_bundle_sha256: str | None = None
    model_identity: dict[str, Any] = Field(default_factory=dict)
    tool_calls: tuple[dict[str, Any], ...] = ()
    retry_count: int = Field(default=0, ge=0)
    errors: tuple[dict[str, Any], ...] = ()
    capability_ids: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()
    parameter_ranges: dict[str, Any] = Field(default_factory=dict)
    environment_tags: tuple[str, ...] = ()
    validation_result: str
    feedback: dict[str, Any] = Field(default_factory=dict)
    attribution: dict[str, Any] = Field(default_factory=dict)
    source_experience_ids: tuple[str, ...] = ()
    security_labels: tuple[str, ...] = ()
    sensitive_digest: str | None = None
    retention_until: str | None = None
    initial_trust_level: Literal["raw"] = "raw"
    record_sha256: str

    @field_validator(
        "request_sha256", "task_spec_sha256", "dag_sha256",
        "execution_plan_sha256", "script_sha256", "run_bundle_sha256",
        "sensitive_digest", "record_sha256",
    )
    @classmethod
    def _valid_hash(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise ValueError("must be a lowercase SHA-256 digest")
        return value

    def integrity_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"experience_id", "record_sha256"},
        )

    def verify_integrity(self) -> bool:
        expected = _sha256(_canonical(self.integrity_payload()))
        return self.record_sha256 == expected and self.experience_id == f"exp_{expected[:24]}"


def build_experience_record(**payload: Any) -> ExperienceRecord:
    base = {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "created_at": payload.pop("created_at", _now()),
        "initial_trust_level": "raw",
        **payload,
    }
    candidate = ExperienceRecord(
        **base,
        experience_id="exp_" + ("0" * 24),
        record_sha256="0" * 64,
    )
    digest = _sha256(_canonical(candidate.integrity_payload()))
    return candidate.model_copy(
        update={"experience_id": f"exp_{digest[:24]}", "record_sha256": digest}
    )


def _dedup_digest(payload: Mapping[str, Any]) -> str:
    stable = dict(payload)
    stable.pop("created_at", None)
    return _sha256(_canonical(stable))


class ExperienceStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.database_path = self.root / "experience.sqlite3"
        self.objects_root = self.root / "objects" / "sha256"
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=30.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiences (
                    experience_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    dedup_sha256 TEXT,
                    record_sha256 TEXT NOT NULL UNIQUE,
                    record_object_sha256 TEXT NOT NULL,
                    validation_result TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_experience_scope
                    ON experiences(tenant_id, project_id, created_at);
                CREATE TABLE IF NOT EXISTS artifacts (
                    sha256 TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experience_artifacts (
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    role TEXT NOT NULL,
                    sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
                    PRIMARY KEY (experience_id, role)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    previous_sha256 TEXT,
                    event_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS conflicts (
                    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experience_id TEXT NOT NULL,
                    conflicting_experience_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(experience_id, conflicting_experience_id, reason)
                );
                CREATE TABLE IF NOT EXISTS experience_facets (
                    experience_id TEXT NOT NULL REFERENCES experiences(experience_id),
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (experience_id, kind, value)
                );
                CREATE INDEX IF NOT EXISTS idx_experience_facets
                    ON experience_facets(kind, value, experience_id);
                """
            )
            version = db.execute(
                "SELECT value FROM store_meta WHERE key='schema_version'"
            ).fetchone()
            if version is None:
                db.execute(
                    "INSERT INTO store_meta(key, value) VALUES('schema_version', ?)",
                    (str(STORE_SCHEMA_VERSION),),
                )
            else:
                current = int(version["value"])
                if current > STORE_SCHEMA_VERSION:
                    raise RuntimeError(f"unsupported experience store schema {current}")
                if current < 2:
                    columns = {
                        str(row["name"])
                        for row in db.execute("PRAGMA table_info(experiences)").fetchall()
                    }
                    if "dedup_sha256" not in columns:
                        db.execute("ALTER TABLE experiences ADD COLUMN dedup_sha256 TEXT")
                    db.execute(
                        "UPDATE store_meta SET value='2' WHERE key='schema_version'"
                    )
            pending_dedup = db.execute(
                """SELECT experience_id,record_object_sha256 FROM experiences
                   WHERE dedup_sha256 IS NULL"""
            ).fetchall()
            for row in pending_dedup:
                try:
                    raw = self._object_path(str(row["record_object_sha256"])).read_bytes()
                    record = ExperienceRecord.model_validate_json(raw)
                    digest = _dedup_digest(record.integrity_payload())
                except Exception as exc:
                    raise RuntimeError(
                        f"cannot migrate experience {row['experience_id']}"
                    ) from exc
                duplicate = db.execute(
                    """SELECT 1 FROM experiences
                       WHERE tenant_id=? AND project_id=? AND dedup_sha256=?""",
                    (record.scope.tenant_id, record.scope.project_id, digest),
                ).fetchone()
                if duplicate is None:
                    db.execute(
                        "UPDATE experiences SET dedup_sha256=? WHERE experience_id=?",
                        (digest, row["experience_id"]),
                    )
            db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_experience_dedup
                   ON experiences(tenant_id, project_id, dedup_sha256)
                   WHERE dedup_sha256 IS NOT NULL"""
            )

    def _object_path(self, digest: str) -> Path:
        return self.objects_root / digest[:2] / digest[2:]

    def _put_object(self, value: bytes, media_type: str) -> str:
        digest = _sha256(value)
        target = self._object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                handle.write(value)
                temporary = Path(handle.name)
            os.replace(temporary, target)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO artifacts(sha256,size_bytes,media_type,created_at) VALUES(?,?,?,?)",
                (digest, len(value), media_type, _now()),
            )
        return digest

    @staticmethod
    def _artifact_bytes(value: Any) -> tuple[bytes, str]:
        if isinstance(value, bytes):
            return value, "application/octet-stream"
        if isinstance(value, Path):
            return value.read_bytes(), "application/octet-stream"
        if isinstance(value, str):
            return value.encode("utf-8"), "text/plain; charset=utf-8"
        return _canonical(value), "application/json"

    def _audit(
        self,
        db: sqlite3.Connection,
        *,
        experience_id: str | None,
        action: str,
        actor: str,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        owns_transaction = not db.in_transaction
        if owns_transaction:
            db.execute("BEGIN IMMEDIATE")
        try:
            previous = db.execute(
                "SELECT event_sha256 FROM audit_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
            payload = {
                "experience_id": experience_id,
                "action": action,
                "actor": actor,
                "created_at": _now(),
                "details": dict(details or {}),
                "previous_sha256": previous["event_sha256"] if previous else None,
            }
            digest = _sha256(_canonical(payload))
            db.execute(
                """INSERT INTO audit_events(
                    experience_id,action,actor,created_at,details_json,previous_sha256,event_sha256
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    experience_id, action, actor, payload["created_at"],
                    json.dumps(payload["details"], ensure_ascii=False, sort_keys=True),
                    payload["previous_sha256"], digest,
                ),
            )
            if owns_transaction:
                db.execute("COMMIT")
            return digest
        except Exception:
            if owns_transaction and db.in_transaction:
                db.execute("ROLLBACK")
            raise

    def capture(
        self,
        *,
        request_text: str,
        scope: ExperienceScope,
        validation_result: str,
        artifacts: Mapping[str, Any] | None = None,
        actor: str = "system",
        **metadata: Any,
    ) -> tuple[ExperienceRecord, bool]:
        redacted_request, sensitive_digest = redact_sensitive_text(request_text)
        artifact_values: dict[str, Any] = {"request": redacted_request}
        sensitive_inputs = [sensitive_digest] if sensitive_digest else []
        for role, value in dict(artifacts or {}).items():
            redacted_value, changed = _redact_value(value)
            artifact_values[str(role)] = redacted_value
            if changed:
                raw, _ = self._artifact_bytes(value)
                sensitive_inputs.append(_sha256(raw))
        artifact_hashes: dict[str, str] = {}
        for role, value in artifact_values.items():
            raw, media_type = self._artifact_bytes(value)
            artifact_hashes[str(role)] = self._put_object(raw, media_type)
        if sensitive_inputs:
            sensitive_digest = _sha256(_canonical(sorted(sensitive_inputs)))
        artifact_fields = {
            "task_spec": "task_spec_sha256",
            "dag": "dag_sha256",
            "execution_plan": "execution_plan_sha256",
            "script": "script_sha256",
            "run_bundle": "run_bundle_sha256",
        }
        record_payload = {
            "scope": scope,
            "request_sha256": artifact_hashes["request"],
            "artifact_hashes": artifact_hashes,
            "validation_result": validation_result,
            "sensitive_digest": sensitive_digest,
            **metadata,
        }
        for role, field in artifact_fields.items():
            if role in artifact_hashes and not record_payload.get(field):
                record_payload[field] = artifact_hashes[role]
        record = build_experience_record(
            **record_payload,
        )
        dedup_sha256 = _dedup_digest(record.integrity_payload())
        record_bytes = _canonical(record.model_dump(mode="json"))
        record_object_sha256 = self._put_object(record_bytes, "application/json")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT experience_id FROM experiences
                   WHERE tenant_id=? AND project_id=? AND dedup_sha256=?""",
                (scope.tenant_id, scope.project_id, dedup_sha256),
            ).fetchone()
            if existing:
                loaded = self.get(
                    str(existing["experience_id"]),
                    scope=scope,
                    connection=db,
                )
                db.execute("COMMIT")
                return loaded, False
            db.execute(
                """INSERT INTO experiences(
                    experience_id,tenant_id,project_id,request_sha256,dedup_sha256,record_sha256,
                    record_object_sha256,validation_result,trust_level,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.experience_id, scope.tenant_id, scope.project_id,
                    record.request_sha256, dedup_sha256, record.record_sha256,
                    record_object_sha256, validation_result, "raw", record.created_at,
                ),
            )
            for role, digest in artifact_hashes.items():
                db.execute(
                    "INSERT INTO experience_artifacts(experience_id,role,sha256) VALUES(?,?,?)",
                    (record.experience_id, role, digest),
                )
            facets = {
                "capability": record.capability_ids,
                "mode": record.modes,
                "effect": record.effects,
                "environment": record.environment_tags,
                "error": tuple(
                    str(error.get("code"))
                    for error in record.errors
                    if error.get("code")
                ),
            }
            for kind, values in facets.items():
                for value in values:
                    db.execute(
                        """INSERT INTO experience_facets(experience_id,kind,value)
                           VALUES(?,?,?)""",
                        (record.experience_id, kind, value),
                    )
            conflicts = db.execute(
                """SELECT experience_id,validation_result FROM experiences
                   WHERE tenant_id=? AND project_id=? AND request_sha256=?
                   AND experience_id<>? AND validation_result<>?""",
                (
                    scope.tenant_id, scope.project_id, record.request_sha256,
                    record.experience_id, validation_result,
                ),
            ).fetchall()
            for conflict in conflicts:
                db.execute(
                    """INSERT OR IGNORE INTO conflicts(
                        experience_id,conflicting_experience_id,reason,created_at
                    ) VALUES(?,?,?,?)""",
                    (
                        record.experience_id, conflict["experience_id"],
                        "validation_result_conflict", _now(),
                    ),
                )
            self._audit(
                db,
                experience_id=record.experience_id,
                action="capture",
                actor=actor,
                details={"record_sha256": record.record_sha256},
            )
            db.execute("COMMIT")
        return record, True

    def capture_run_bundle(
        self,
        bundle_root: str | Path,
        *,
        request_text: str,
        scope: ExperienceScope,
        actor: str = "system",
        model_identity: Mapping[str, Any] | None = None,
        feedback: Mapping[str, Any] | None = None,
        environment_tags: Sequence[str] = (),
    ) -> tuple[ExperienceRecord, bool]:
        """Capture one sealed execution trajectory after its native integrity gate."""
        from .run_bundle import verify_run_bundle

        root = Path(bundle_root)
        integrity = verify_run_bundle(root)
        if not integrity.get("ok"):
            raise ValueError("RUN_BUNDLE_INTEGRITY_FAILED")

        def read(relative: str) -> dict[str, Any]:
            value = json.loads((root / relative).read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError(f"run bundle artifact is not an object: {relative}")
            return value

        task_spec = read("input/task_spec.json")
        execution_plan = read("input/execution_plan.json")
        run_record = read("run_record.json")
        validation = read("validation/validation_outcome.json")
        claim_report = read("validation/claim_report.json")
        bundle_manifest = read("bundle_manifest.json")
        target = task_spec.get("model", {}).get("target", {})
        events = task_spec.get("events", {})
        event_items = [
            item
            for category in ("faults", "degradations")
            for item in events.get(category, [])
            if isinstance(item, Mapping)
        ]
        failures = [
            attempt.get("failure")
            for attempt in run_record.get("attempts", [])
            if isinstance(attempt, Mapping) and isinstance(attempt.get("failure"), Mapping)
        ]
        capability_id = task_spec.get("model", {}).get("capability_id")
        mode = target.get("mode") if isinstance(target, Mapping) else None
        validation_result = validation.get("result")
        if not isinstance(validation_result, str):
            raise ValueError("RUN_BUNDLE_VALIDATION_RESULT_MISSING")
        artifacts = {
            "task_spec": task_spec,
            "execution_plan": execution_plan,
            "run_bundle": bundle_manifest,
            "run_record": run_record,
            "validation": validation,
            "claim_report": claim_report,
        }
        return self.capture(
            request_text=request_text,
            scope=scope,
            validation_result=validation_result,
            artifacts=artifacts,
            actor=actor,
            observed_at_start=run_record.get("created_at"),
            observed_at_end=run_record.get("updated_at"),
            model_identity=dict(model_identity or {}),
            capability_ids=(str(capability_id),) if capability_id else (),
            modes=(str(mode),) if mode else (),
            effects=tuple(
                sorted({str(item["effect"]) for item in event_items if item.get("effect")})
            ),
            environment_tags=tuple(environment_tags),
            retry_count=max(0, len(run_record.get("attempts", [])) - 1),
            errors=tuple(dict(item) for item in failures if item),
            feedback=dict(feedback or {}),
            attribution={
                "run_id": run_record.get("run_id"),
                "run_bundle_integrity": True,
                "claim_level": claim_report.get("claim_level"),
            },
            security_labels=("sealed_run_bundle", "hash_verified"),
        )

    def get(
        self,
        experience_id: str,
        *,
        scope: ExperienceScope,
        connection: sqlite3.Connection | None = None,
        actor: str = "system",
    ) -> ExperienceRecord:
        owned = connection is None
        db = connection or self._connect()
        try:
            row = db.execute(
                """SELECT * FROM experiences
                   WHERE experience_id=? AND tenant_id=? AND project_id=?""",
                (experience_id, scope.tenant_id, scope.project_id),
            ).fetchone()
            if row is None:
                if owned:
                    self._audit(
                        db,
                        experience_id=experience_id,
                        action="inspect_denied",
                        actor=actor,
                        details=scope.model_dump(mode="json"),
                    )
                raise KeyError(experience_id)
            raw = self._object_path(str(row["record_object_sha256"])).read_bytes()
            record = ExperienceRecord.model_validate_json(raw)
            if owned:
                self._audit(
                    db,
                    experience_id=experience_id,
                    action="inspect",
                    actor=actor,
                    details=scope.model_dump(mode="json"),
                )
            return record
        finally:
            if owned:
                db.close()

    def list(
        self,
        *,
        scope: ExperienceScope,
        include_revoked: bool = False,
        actor: str = "system",
    ) -> list[dict[str, Any]]:
        query = """SELECT experience_id,validation_result,trust_level,revoked,created_at
                   FROM experiences WHERE tenant_id=? AND project_id=?"""
        if not include_revoked:
            query += " AND revoked=0"
        query += " ORDER BY created_at, experience_id"
        with self._connect() as db:
            result = [dict(row) for row in db.execute(
                query, (scope.tenant_id, scope.project_id)
            ).fetchall()]
            self._audit(
                db,
                experience_id=None,
                action="list",
                actor=actor,
                details={**scope.model_dump(mode="json"), "count": len(result)},
            )
            return result

    def search(
        self,
        *,
        scope: ExperienceScope,
        capability_id: str | None = None,
        mode: str | None = None,
        effect: str | None = None,
        error_code: str | None = None,
        validation_result: str | None = None,
        trust_level: TrustLevel | None = None,
        include_revoked: bool = False,
        limit: int = 100,
        actor: str = "system",
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        clauses = ["e.tenant_id=?", "e.project_id=?"]
        parameters: list[Any] = [scope.tenant_id, scope.project_id]
        if not include_revoked:
            clauses.append("e.revoked=0")
        for kind, value in (
            ("capability", capability_id),
            ("mode", mode),
            ("effect", effect),
            ("error", error_code),
        ):
            if value is not None:
                clauses.append(
                    """EXISTS (
                        SELECT 1 FROM experience_facets f
                        WHERE f.experience_id=e.experience_id AND f.kind=? AND f.value=?
                    )"""
                )
                parameters.extend((kind, value))
        if validation_result is not None:
            clauses.append("e.validation_result=?")
            parameters.append(validation_result)
        if trust_level is not None:
            clauses.append("e.trust_level=?")
            parameters.append(trust_level)
        parameters.append(limit)
        query = f"""SELECT e.experience_id,e.validation_result,e.trust_level,
                           e.revoked,e.created_at
                    FROM experiences e
                    WHERE {' AND '.join(clauses)}
                    ORDER BY e.created_at DESC,e.experience_id
                    LIMIT ?"""
        with self._connect() as db:
            result = [dict(row) for row in db.execute(query, parameters).fetchall()]
            self._audit(
                db,
                experience_id=None,
                action="search",
                actor=actor,
                details={
                    **scope.model_dump(mode="json"),
                    "count": len(result),
                    "filters": {
                        "capability_id": capability_id,
                        "mode": mode,
                        "effect": effect,
                        "error_code": error_code,
                        "validation_result": validation_result,
                        "trust_level": trust_level,
                    },
                },
            )
            return result

    def revoke(self, experience_id: str, *, scope: ExperienceScope, actor: str, reason: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT experience_id FROM experiences
                   WHERE experience_id=? AND tenant_id=? AND project_id=?""",
                (experience_id, scope.tenant_id, scope.project_id),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise KeyError(experience_id)
            db.execute(
                "UPDATE experiences SET revoked=1,trust_level='revoked' WHERE experience_id=?",
                (experience_id,),
            )
            self._audit(
                db, experience_id=experience_id, action="revoke",
                actor=actor, details={"reason": reason},
            )
            db.execute("COMMIT")

    def verify(
        self,
        experience_id: str,
        *,
        scope: ExperienceScope,
        actor: str = "system",
    ) -> dict[str, Any]:
        record = self.get(experience_id, scope=scope, actor=actor)
        missing: list[str] = []
        mismatched: list[str] = []
        for role, digest in record.artifact_hashes.items():
            path = self._object_path(digest)
            if not path.is_file():
                missing.append(role)
            elif _sha256(path.read_bytes()) != digest:
                mismatched.append(role)
        with self._connect() as db:
            index = db.execute(
                """SELECT record_sha256,record_object_sha256 FROM experiences
                   WHERE experience_id=? AND tenant_id=? AND project_id=?""",
                (experience_id, scope.tenant_id, scope.project_id),
            ).fetchone()
            self._audit(
                db,
                experience_id=experience_id,
                action="verify",
                actor=actor,
                details=scope.model_dump(mode="json"),
            )
            events = db.execute(
                "SELECT * FROM audit_events ORDER BY event_id"
            ).fetchall()
        record_bytes = _canonical(record.model_dump(mode="json"))
        index_ok = bool(
            index
            and index["record_sha256"] == record.record_sha256
            and index["record_object_sha256"] == _sha256(record_bytes)
        )
        previous = None
        audit_ok = True
        for event in events:
            payload = {
                "experience_id": event["experience_id"],
                "action": event["action"],
                "actor": event["actor"],
                "created_at": event["created_at"],
                "details": json.loads(event["details_json"]),
                "previous_sha256": event["previous_sha256"],
            }
            if event["previous_sha256"] != previous or _sha256(_canonical(payload)) != event["event_sha256"]:
                audit_ok = False
                break
            previous = event["event_sha256"]
        ok = (
            record.verify_integrity()
            and index_ok
            and not missing
            and not mismatched
            and audit_ok
        )
        return {
            "ok": ok,
            "experience_id": experience_id,
            "record_integrity": record.verify_integrity(),
            "record_index_integrity": index_ok,
            "missing_artifacts": missing,
            "mismatched_artifacts": mismatched,
            "audit_chain_ok": audit_ok,
        }

    def purge_expired_revoked(
        self,
        *,
        scope: ExperienceScope,
        actor: str,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        cutoff = datetime.fromisoformat((as_of or _now()).replace("Z", "+00:00"))
        purged: list[str] = []
        object_candidates: set[str] = set()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT experience_id,record_object_sha256 FROM experiences
                   WHERE tenant_id=? AND project_id=? AND revoked=1""",
                (scope.tenant_id, scope.project_id),
            ).fetchall()
            for row in rows:
                record = self.get(
                    str(row["experience_id"]),
                    scope=scope,
                    connection=db,
                )
                if not record.retention_until:
                    continue
                expires = datetime.fromisoformat(record.retention_until.replace("Z", "+00:00"))
                if expires > cutoff:
                    continue
                links = db.execute(
                    "SELECT sha256 FROM experience_artifacts WHERE experience_id=?",
                    (record.experience_id,),
                ).fetchall()
                object_candidates.update(str(item["sha256"]) for item in links)
                object_candidates.add(str(row["record_object_sha256"]))
                db.execute(
                    "DELETE FROM experience_facets WHERE experience_id=?",
                    (record.experience_id,),
                )
                db.execute(
                    "DELETE FROM experience_artifacts WHERE experience_id=?",
                    (record.experience_id,),
                )
                db.execute(
                    "DELETE FROM conflicts WHERE experience_id=? OR conflicting_experience_id=?",
                    (record.experience_id, record.experience_id),
                )
                db.execute(
                    "DELETE FROM experiences WHERE experience_id=?",
                    (record.experience_id,),
                )
                self._audit(
                    db,
                    experience_id=record.experience_id,
                    action="retention_purge",
                    actor=actor,
                    details={
                        **scope.model_dump(mode="json"),
                        "retention_until": record.retention_until,
                    },
                )
                purged.append(record.experience_id)
            db.execute("COMMIT")

        removed_objects = 0
        with self._connect() as db:
            for digest in object_candidates:
                referenced = db.execute(
                    """SELECT 1 FROM experience_artifacts WHERE sha256=?
                       UNION SELECT 1 FROM experiences WHERE record_object_sha256=?
                       LIMIT 1""",
                    (digest, digest),
                ).fetchone()
                if referenced:
                    continue
                path = self._object_path(digest)
                if path.exists():
                    path.unlink()
                    removed_objects += 1
                db.execute("DELETE FROM artifacts WHERE sha256=?", (digest,))
        return {
            "purged_count": len(purged),
            "purged_experience_ids": purged,
            "removed_object_count": removed_objects,
        }

    def backup(self, archive_path: str | Path) -> Path:
        archive = Path(archive_path)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "experience.sqlite3"
            with self._connect() as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(snapshot, "experience.sqlite3")
                for path in sorted(self.objects_root.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(self.root).as_posix())
        return archive

    @classmethod
    def restore(cls, archive_path: str | Path, root: str | Path) -> "ExperienceStore":
        target = Path(root)
        if target.exists() and any(target.iterdir()):
            raise FileExistsError(f"restore target is not empty: {target}")
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as bundle:
            for member in bundle.infolist():
                destination = (target / member.filename).resolve()
                if target.resolve() not in destination.parents and destination != target.resolve():
                    raise ValueError("backup contains an unsafe path")
            bundle.extractall(target)
        return cls(target)


__all__ = [
    "EXPERIENCE_SCHEMA_VERSION",
    "ExperienceRecord",
    "ExperienceScope",
    "ExperienceStore",
    "TrustLevel",
    "build_experience_record",
    "redact_sensitive_text",
]
