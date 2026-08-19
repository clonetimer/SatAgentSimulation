from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.experience import (
    EXPERIENCE_SCHEMA_VERSION,
    ExperienceScope,
    ExperienceStore,
    build_experience_record,
)
from sat_sim.experience_cli import main as experience_main


SCOPE = ExperienceScope(tenant_id="training", project_id="sat-baseline")


def _capture(store: ExperienceStore, **overrides):
    values = {
        "request_text": "simulate ADCS nominal operation",
        "scope": SCOPE,
        "validation_result": "PASS",
        "artifacts": {
            "task_spec": {"capability_id": "adcs.attitude_control", "mode": "nominal"},
            "run_bundle": {"status": "PASS"},
        },
        "capability_ids": ("adcs.attitude_control",),
        "modes": ("nominal",),
    }
    values.update(overrides)
    return store.capture(**values)


def test_record_matches_committed_json_schema():
    record = build_experience_record(
        scope=SCOPE,
        request_sha256="a" * 64,
        validation_result="PASS",
    )
    schema_path = (
        Path(__file__).parents[1]
        / "src"
        / "sat_sim"
        / "schemas"
        / "experience_record.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(record.model_dump(mode="json"))
    assert record.schema_version == EXPERIENCE_SCHEMA_VERSION
    assert record.verify_integrity()


def test_capture_deduplicates_and_verifies(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    first, first_created = _capture(store)
    second, second_created = _capture(store)

    assert first_created is True
    assert second_created is False
    assert second.experience_id == first.experience_id
    assert len(store.list(scope=SCOPE)) == 1
    assert store.verify(first.experience_id, scope=SCOPE)["ok"] is True


def test_sensitive_request_is_redacted_and_only_original_digest_is_retained(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(
        store,
        request_text="simulate nominal token=top-secret-value",
    )

    request_object = store._object_path(record.artifact_hashes["request"]).read_text()
    assert request_object == "simulate nominal token=[REDACTED]"
    assert "top-secret-value" not in request_object
    assert record.sensitive_digest is not None


def test_artifacts_are_redacted_and_named_hashes_are_populated(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(
        store,
        artifacts={
            "task_spec": {
                "capability_id": "adcs.attitude_control",
                "password": "password=hunter2",
            },
            "run_bundle": {"status": "PASS", "token": "token=hidden"},
        },
    )

    task_spec = json.loads(store._object_path(record.task_spec_sha256).read_text())
    assert task_spec["password"] == "password=[REDACTED]"
    assert record.run_bundle_sha256 == record.artifact_hashes["run_bundle"]
    assert record.sensitive_digest is not None


def test_scope_isolation_is_fail_closed(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(store)
    other = ExperienceScope(tenant_id="other", project_id=SCOPE.project_id)

    assert store.list(scope=other) == []
    with pytest.raises(KeyError):
        store.get(record.experience_id, scope=other)
    with pytest.raises(KeyError):
        store.revoke(record.experience_id, scope=other, actor="reviewer", reason="denied")


def test_conflicting_validation_is_recorded(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    passed, _ = _capture(store, validation_result="PASS")
    failed, created = _capture(store, validation_result="FAIL")

    assert created is True
    with store._connect() as db:
        conflicts = db.execute(
            "SELECT * FROM conflicts WHERE experience_id=?",
            (failed.experience_id,),
        ).fetchall()
    assert len(conflicts) == 1
    assert conflicts[0]["conflicting_experience_id"] == passed.experience_id


def test_search_filters_indexed_facets_and_scope(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    matched, _ = _capture(
        store,
        effects=("wheel_friction",),
        errors=({"code": "RW_SPEED_LIMIT"},),
    )
    _capture(
        store,
        request_text="simulate EPS degradation",
        capability_ids=("eps.power_balance",),
        modes=("degradation",),
        effects=("battery_capacity_fade",),
    )

    result = store.search(
        scope=SCOPE,
        capability_id="adcs.attitude_control",
        mode="nominal",
        effect="wheel_friction",
        error_code="RW_SPEED_LIMIT",
        validation_result="PASS",
    )
    assert [item["experience_id"] for item in result] == [matched.experience_id]
    assert store.search(
        scope=ExperienceScope(tenant_id="other", project_id=SCOPE.project_id),
        capability_id="adcs.attitude_control",
    ) == []


def test_capture_sealed_run_bundle_extracts_full_trajectory(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "input").mkdir(parents=True)
    (bundle / "validation").mkdir()
    payloads = {
        "input/task_spec.json": {
            "model": {
                "capability_id": "component.reaction_wheel.v1",
                "target": {"mode": "fault"},
            },
            "events": {
                "faults": [{"effect": "wheel_friction"}],
                "degradations": [],
            },
        },
        "input/execution_plan.json": {"plan_sha256": "1" * 64},
        "run_record.json": {
            "run_id": "run-1",
            "created_at": "2026-07-29T01:00:00Z",
            "updated_at": "2026-07-29T01:00:03Z",
            "attempts": [
                {"failure": {"code": "WORKER_TIMEOUT"}},
                {"failure": None},
            ],
        },
        "validation/validation_outcome.json": {"result": "PASS"},
        "validation/claim_report.json": {"claim_level": "analysis_only"},
        "bundle_manifest.json": {"files": {"run_record.json": "2" * 64}},
    }
    for relative, payload in payloads.items():
        (bundle / relative).write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        "sat_sim.run_bundle.verify_run_bundle",
        lambda root: {"ok": True},
    )

    store = ExperienceStore(tmp_path / "store")
    record, created = store.capture_run_bundle(
        bundle,
        request_text="simulate reaction wheel friction",
        scope=SCOPE,
        model_identity={"model": "Qwen3.5-9B"},
    )

    assert created is True
    assert record.capability_ids == ("component.reaction_wheel.v1",)
    assert record.modes == ("fault",)
    assert record.effects == ("wheel_friction",)
    assert record.retry_count == 1
    assert record.errors[0]["code"] == "WORKER_TIMEOUT"
    assert record.task_spec_sha256 == record.artifact_hashes["task_spec"]
    assert record.execution_plan_sha256 == record.artifact_hashes["execution_plan"]
    assert record.run_bundle_sha256 == record.artifact_hashes["run_bundle"]
    assert store.verify(record.experience_id, scope=SCOPE)["ok"] is True


def test_capture_run_bundle_fails_closed_on_integrity_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sat_sim.run_bundle.verify_run_bundle",
        lambda root: {"ok": False, "errors": ["tampered"]},
    )
    store = ExperienceStore(tmp_path / "store")

    with pytest.raises(ValueError, match="RUN_BUNDLE_INTEGRITY_FAILED"):
        store.capture_run_bundle(
            tmp_path / "bundle",
            request_text="untrusted",
            scope=SCOPE,
        )
    assert store.list(scope=SCOPE) == []


def test_revoke_hides_record_and_preserves_audit_integrity(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(store)
    store.revoke(
        record.experience_id,
        scope=SCOPE,
        actor="named-reviewer",
        reason="superseded evidence",
    )

    assert store.list(scope=SCOPE) == []
    listed = store.list(scope=SCOPE, include_revoked=True)
    assert listed[0]["trust_level"] == "revoked"
    assert listed[0]["revoked"] == 1
    assert store.verify(record.experience_id, scope=SCOPE)["audit_chain_ok"] is True


def test_retention_purge_requires_both_expiry_and_revocation(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    expired, _ = _capture(
        store,
        retention_until="2026-01-01T00:00:00Z",
    )
    retained, _ = _capture(
        store,
        request_text="simulate retained trajectory",
        retention_until="2026-01-01T00:00:00Z",
    )
    no_policy, _ = _capture(
        store,
        request_text="simulate no retention policy",
    )
    store.revoke(
        expired.experience_id,
        scope=SCOPE,
        actor="records-officer",
        reason="retention elapsed",
    )
    store.revoke(
        no_policy.experience_id,
        scope=SCOPE,
        actor="records-officer",
        reason="revoked but retained",
    )

    result = store.purge_expired_revoked(
        scope=SCOPE,
        actor="records-officer",
        as_of="2026-07-29T00:00:00Z",
    )

    assert result["purged_experience_ids"] == [expired.experience_id]
    with pytest.raises(KeyError):
        store.get(expired.experience_id, scope=SCOPE)
    assert store.get(retained.experience_id, scope=SCOPE)
    assert store.get(no_policy.experience_id, scope=SCOPE)


def test_artifact_tamper_is_detected(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(store)
    store._object_path(record.artifact_hashes["run_bundle"]).write_bytes(b"tampered")

    result = store.verify(record.experience_id, scope=SCOPE)
    assert result["ok"] is False
    assert result["mismatched_artifacts"] == ["run_bundle"]


def test_concurrent_capture_creates_one_record(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _capture(store), range(16)))

    assert sum(created for _, created in results) == 1
    assert len({record.experience_id for record, _ in results}) == 1
    assert len(store.list(scope=SCOPE)) == 1


def test_concurrent_reads_preserve_single_audit_chain(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(store)
    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(lambda _: store.search(scope=SCOPE), range(100)))

    assert store.verify(record.experience_id, scope=SCOPE)["audit_chain_ok"] is True


def test_v1_store_is_migrated_to_v2(tmp_path):
    root = tmp_path / "store"
    store = ExperienceStore(root)
    original, _ = _capture(store)
    with store._connect() as db:
        db.execute("DROP INDEX idx_experience_dedup")
        db.execute("ALTER TABLE experiences DROP COLUMN dedup_sha256")
        db.execute("UPDATE store_meta SET value='1' WHERE key='schema_version'")

    migrated = ExperienceStore(root)
    with migrated._connect() as db:
        version = db.execute(
            "SELECT value FROM store_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        columns = {
            row["name"] for row in db.execute("PRAGMA table_info(experiences)").fetchall()
        }
    assert version == "2"
    assert "dedup_sha256" in columns
    duplicate, created = _capture(migrated)
    assert created is False
    assert duplicate.experience_id == original.experience_id


def test_backup_restores_after_live_store_corruption(tmp_path):
    store = ExperienceStore(tmp_path / "store")
    record, _ = _capture(store)
    archive = store.backup(tmp_path / "experience-backup.zip")
    store.database_path.write_bytes(b"not a sqlite database")

    restored = ExperienceStore.restore(archive, tmp_path / "restored")
    assert restored.verify(record.experience_id, scope=SCOPE)["ok"] is True
    assert len(restored.list(scope=SCOPE)) == 1


def test_cli_capture_verify_revoke_backup_and_restore(tmp_path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "request_text": "simulate EPS nominal operation",
                "validation_result": "PASS",
                "capability_ids": ["eps.power_balance"],
                "modes": ["nominal"],
                "artifacts": {"run_bundle": {"status": "PASS"}},
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "cli-store"
    common = ["--store", str(root)]
    scope = ["--tenant", "training", "--project", "sat-baseline"]

    assert experience_main([*common, "capture", *scope, "--manifest", str(manifest)]) == 0
    captured = json.loads(capsys.readouterr().out)
    experience_id = captured["record"]["experience_id"]
    assert experience_main([*common, "verify", *scope, experience_id]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert experience_main(
        [
            *common,
            "revoke",
            *scope,
            experience_id,
            "--actor",
            "named-reviewer",
            "--reason",
            "test",
        ]
    ) == 0
    capsys.readouterr()

    archive = tmp_path / "cli-backup.zip"
    assert experience_main([*common, "backup", str(archive)]) == 0
    capsys.readouterr()
    restored = tmp_path / "cli-restored"
    assert experience_main(
        ["--store", str(restored), "restore", str(archive)]
    ) == 0
    assert json.loads(capsys.readouterr().out)["restored"] is True


def test_fastapi_experience_endpoints_preserve_scope_and_revocation(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "api",
        embedded_worker=False,
        auth_mode="disabled",
    )
    record, _ = _capture(app.state.experience_store)
    query = "?tenant_id=training&project_id=sat-baseline"
    other_query = "?tenant_id=other&project_id=sat-baseline"

    with TestClient(app) as client:
        response = client.get(f"/experiences{query}")
        assert response.status_code == 200
        assert response.json()["experiences"][0]["experience_id"] == record.experience_id

        assert client.get(f"/experiences{other_query}").json()["count"] == 0
        assert client.get(
            f"/experiences/{record.experience_id}{other_query}"
        ).status_code == 404

        verified = client.get(f"/experiences/{record.experience_id}/verify{query}")
        assert verified.status_code == 200
        assert verified.json()["verification"]["ok"] is True

        revoked = client.post(
            f"/experiences/{record.experience_id}/revoke",
            json={
                "tenant_id": "training",
                "project_id": "sat-baseline",
                "reason": "superseded",
            },
        )
        assert revoked.status_code == 200
        assert client.get(f"/experiences{query}").json()["count"] == 0
