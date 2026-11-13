from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _sealed_run(root: Path, *, status: str = "SUCCEEDED", validation: str = "PASS") -> bytes:
    _write(root / "runtime" / "prepared_run.json", {
        "primary_capability_id": "component.reaction_wheel.v1",
    })
    _write(root / "run_record.json", {
        "run_id": root.name, "status": status, "validation_result": validation,
    })
    _write(root / "validation" / "validation_outcome.json", {"result": validation})
    telemetry = "\n".join([
        json.dumps({"time_s": 0.0, "adcs.reaction_wheel.speed_rad_s_0": 1.0}),
        json.dumps({"time_s": 1.0, "adcs.reaction_wheel.speed_rad_s_0": 2.0}),
        json.dumps({"time_s": 2.0, "adcs.reaction_wheel.speed_rad_s_0": 3.0}),
    ]) + "\n"
    _write(root / "results" / "telemetry.jsonl", telemetry)
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {"bundle_manifest.json", "SEALED.json"}:
            continue
        raw = path.read_bytes()
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
        }
    manifest_path = root / "bundle_manifest.json"
    _write(manifest_path, {"run_id": root.name, "files": files})
    _write(root / "SEALED.json", {
        "run_id": root.name,
        "bundle_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    })
    return (root / "results" / "telemetry.jsonl").read_bytes()


def _plan(source: dict) -> dict:
    return {
        "schema_version": "evaluation-plan.v1",
        "plan_id": "rw-speed-review", "version": "1",
        "display_name_zh": "反作用轮转速评估",
        "window_s": [0.0, 2.0],
        "metrics": [{
            "metric_id": "rw-speed-rms", "source_id": source["source_id"],
            "formula": "sample_rms.v1", "minimum_samples": 3,
        }],
    }


def _app(tmp_path: Path, runs: Path, **kwargs):
    return create_app(runs_root=runs, artifacts_root=tmp_path / "artifacts",
                      embedded_worker=False, **kwargs)


def test_sources_are_only_sealed_validated_recorded_outputs(tmp_path):
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        response = client.get("/runs/run-001/evaluation-sources")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["sealed"] is True
        source = next(row for row in payload["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        assert source["display_name_zh"] == "反作用轮1 · 角速度"
        assert source["unit"] == "rad/s"
        assert source["sample_count"] == 3
        assert source["owner_object_id"] == "component.reaction_wheel"


def test_report_is_server_built_persisted_idempotent_and_read_only(tmp_path):
    runs = tmp_path / "runs"
    telemetry_before = _sealed_run(runs / "run-001")
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        source = next(row for row in client.get("/runs/run-001/evaluation-sources").json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        body = _plan(source)
        created = client.post("/runs/run-001/evaluations", json=body)
        assert created.status_code == 201, created.text
        report = created.json()["report"]
        assert report["source_run_id"] == "run-001"
        assert report["plan_name_zh"] == "反作用轮转速评估"
        assert report["metrics"][0]["display_name_zh"] == "反作用轮1 · 角速度（均方根）"
        assert report["metrics"][0]["unit"] == "rad/s"
        assert report["metrics"][0]["value"] == pytest.approx((14 / 3) ** 0.5)
        assert report["metrics"][0]["verdict"] == "NOT_REQUESTED"
        repeated = client.post("/runs/run-001/evaluations", json=body)
        assert repeated.status_code == 200
        assert repeated.json()["report"]["report_id"] == report["report_id"]
        assert len(client.get("/runs/run-001/evaluations").json()["reports"]) == 1
        assert client.get(f"/evaluations/{report['report_id']}").json()["report"] == report
    assert (runs / "run-001" / "results" / "telemetry.jsonl").read_bytes() == telemetry_before
    assert json.loads((runs / "run-001" / "run_record.json").read_text())["status"] == "SUCCEEDED"


def test_client_cannot_forge_source_binding(tmp_path):
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        source = next(row for row in client.get("/runs/run-001/evaluation-sources").json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        body = _plan(source)
        body["metrics"][0]["source_id"] = "forged-source"
        response = client.post("/runs/run-001/evaluations", json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["reason_code"] == "EVALUATION_SOURCE_BINDING_MISMATCH"


def test_failed_or_unvalidated_runs_cannot_be_evaluated(tmp_path):
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-failed", status="FAILED", validation="FAIL")
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        response = client.get("/runs/run-failed/evaluation-sources")
        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "EVALUATION_RUN_NOT_ELIGIBLE"


def test_reports_are_scoped_to_authenticated_principal(tmp_path):
    from sat_sim.security import token_sha256

    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    viewer_token, operator_token = "evaluation-viewer-secret", "evaluation-operator-secret"
    auth_path = tmp_path / "auth.json"
    _write(auth_path, {
        "schema_version": "auth-config.v1",
        "tokens": [
            {"token_id": "viewer", "principal_id": "viewer-1", "roles": ["viewer"],
             "token_sha256": token_sha256(viewer_token)},
            {"token_id": "operator", "principal_id": "operator-1", "roles": ["operator"],
             "token_sha256": token_sha256(operator_token)},
        ],
    })
    with TestClient(_app(tmp_path, runs, auth_mode="token", auth_config=auth_path)) as client:
        viewer = {"Authorization": f"Bearer {viewer_token}"}
        operator = {"Authorization": f"Bearer {operator_token}"}
        source = next(row for row in client.get("/runs/run-001/evaluation-sources", headers=viewer).json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        denied = client.post("/runs/run-001/evaluations", json=_plan(source), headers=viewer)
        assert denied.status_code == 403
        created = client.post("/runs/run-001/evaluations", json=_plan(source), headers=operator)
        assert created.status_code == 201, created.text
        report_id = created.json()["report"]["report_id"]
        assert client.get(f"/evaluations/{report_id}", headers=viewer).status_code == 404
        assert client.get(f"/evaluations/{report_id}", headers=operator).status_code == 200
        assert client.get("/runs/run-001/evaluations", headers=viewer).json()["reports"] == []


def test_missing_samples_are_not_selectable_or_filled_with_zero(tmp_path):
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    telemetry = runs / "run-001" / "results" / "telemetry.jsonl"
    _write(telemetry, "\n".join([
        json.dumps({"time_s": 0.0, "adcs.reaction_wheel.speed_rad_s_0": 1.0}),
        json.dumps({"time_s": 1.0}),
        json.dumps({"time_s": 2.0, "adcs.reaction_wheel.speed_rad_s_0": 3.0}),
    ]) + "\n")
    manifest_path = runs / "run-001" / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = telemetry.read_bytes()
    manifest["files"]["results/telemetry.jsonl"] = {
        "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
    }
    _write(manifest_path, manifest)
    _write(runs / "run-001" / "SEALED.json", {
        "run_id": "run-001",
        "bundle_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    })
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        source = next(row for row in client.get("/runs/run-001/evaluation-sources").json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        assert source["evaluable"] is False
        assert source["missing_count"] == 1
        response = client.post("/runs/run-001/evaluations", json=_plan(source))
        assert response.status_code == 422
        assert response.json()["detail"]["reason_code"] == "EVALUATION_SOURCE_BINDING_MISMATCH"



def test_evaluation_matrix_is_bounded_before_telemetry_materialization(tmp_path):
    from sat_sim.evaluation_service import EvaluationRunError, build_recorded_evaluation

    source = {
        "source_id": "source-1",
        "owner_object_id": "component.reaction_wheel",
        "source_capability_id": "component.reaction_wheel.v1",
        "source_field": "adcs.reaction_wheel.speed_rad_s_0",
        "display_name_zh": "反作用轮1 · 角速度",
        "unit": "rad/s",
        "value_type": "number",
        "evaluable": True,
        "sample_count": 20_000,
        "total_samples": 20_000,
    }
    plan = {
        "schema_version": "evaluation-plan.v1",
        "plan_id": "large-evaluation",
        "version": "1",
        "window_s": [0.0, 20_000.0],
        "metrics": [
            {"metric_id": f"metric-{index}", "source_id": "source-1",
             "formula": "sample_mean.v1", "minimum_samples": 1}
            for index in range(64)
        ],
    }
    run = {
        "run_id": "run-large",
        "run_status": "SUCCEEDED",
        "validation_result": "PASS",
        "telemetry_path": tmp_path / "telemetry-does-not-need-to-exist.jsonl",
    }
    with pytest.raises(EvaluationRunError) as error:
        build_recorded_evaluation(run, [source], plan)
    assert error.value.reason_code == "EVALUATION_WORKLOAD_LIMIT_EXCEEDED"


def test_inconsistent_source_sample_counts_are_rejected_before_evaluation(tmp_path):
    from sat_sim.evaluation_service import EvaluationRunError, build_recorded_evaluation

    source = {
        "source_id": "source-1",
        "owner_object_id": "component.reaction_wheel",
        "source_capability_id": "component.reaction_wheel.v1",
        "source_field": "adcs.reaction_wheel.speed_rad_s_0",
        "display_name_zh": "反作用轮1 · 角速度",
        "unit": "rad/s",
        "value_type": "number",
        "evaluable": True,
        "sample_count": 2,
        "total_samples": 3,
    }
    plan = {
        "schema_version": "evaluation-plan.v1",
        "plan_id": "inconsistent-source",
        "version": "1",
        "window_s": [0.0, 2.0],
        "metrics": [{"metric_id": "metric-1", "source_id": "source-1",
                     "formula": "sample_mean.v1", "minimum_samples": 1}],
    }
    run = {
        "run_id": "run-inconsistent",
        "run_status": "SUCCEEDED",
        "validation_result": "PASS",
        "telemetry_path": tmp_path / "telemetry-does-not-need-to-exist.jsonl",
    }
    with pytest.raises(EvaluationRunError) as error:
        build_recorded_evaluation(run, [source], plan)
    assert error.value.reason_code == "EVALUATION_SOURCE_SAMPLE_COUNTS_INCONSISTENT"


def test_report_creates_a_separate_reviewable_task_draft_without_launching(tmp_path):
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    with TestClient(_app(tmp_path, runs, auth_mode="disabled")) as client:
        source = next(row for row in client.get("/runs/run-001/evaluation-sources").json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        created = client.post("/runs/run-001/evaluations", json=_plan(source))
        assert created.status_code == 201, created.text
        report_id = created.json()["report"]["report_id"]
        original_spec = json.loads((runs / "run-001" / "input" / "task_spec.json").read_text()) if (runs / "run-001" / "input" / "task_spec.json").exists() else None
        response = client.post(f"/evaluations/{report_id}/configuration-draft", json={})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["requires_user_review"] is True
        assert payload["execution_started"] is False
        task = payload["task"]
        assert task["status"] == "DRAFT"
        assert task["source"] == "evaluation_feedback"
        assert payload["task_spec"]["task"]["id"] == task["task_id"]
        feedback = payload["task_spec"]["metadata"]["evaluation_feedback"]
        assert feedback["report_id"] == report_id
        assert feedback["source_run_id"] == "run-001"
        assert feedback["requires_user_review"] is True
        assert client.get(f"/task-center/tasks/{task['task_id']}").status_code == 200
        assert client.get("/runs").json()["count"] == 0
        assert (runs / "run-001" / "run_record.json").read_text() == json.dumps(
            {"run_id": "run-001", "status": "SUCCEEDED", "validation_result": "PASS"},
            ensure_ascii=False,
        )
        if original_spec is not None:
            assert json.loads((runs / "run-001" / "input" / "task_spec.json").read_text()) == original_spec


def test_configuration_draft_cannot_be_created_from_another_principals_report(tmp_path):
    from sat_sim.security import token_sha256

    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    owner_token, other_token = "evaluation-owner-secret", "evaluation-other-secret"
    auth_path = tmp_path / "auth.json"
    _write(auth_path, {
        "schema_version": "auth-config.v1",
        "tokens": [
            {"token_id": "owner", "principal_id": "owner-1", "roles": ["operator"],
             "token_sha256": token_sha256(owner_token)},
            {"token_id": "other", "principal_id": "other-1", "roles": ["operator"],
             "token_sha256": token_sha256(other_token)},
        ],
    })
    with TestClient(_app(tmp_path, runs, auth_mode="token", auth_config=auth_path)) as client:
        owner = {"Authorization": f"Bearer {owner_token}"}
        other = {"Authorization": f"Bearer {other_token}"}
        source = next(row for row in client.get("/runs/run-001/evaluation-sources", headers=owner).json()["sources"]
                      if row["source_field"] == "adcs.reaction_wheel.speed_rad_s_0")
        created = client.post("/runs/run-001/evaluations", json=_plan(source), headers=owner)
        assert created.status_code == 201, created.text
        report_id = created.json()["report"]["report_id"]
        denied = client.post(f"/evaluations/{report_id}/configuration-draft", json={}, headers=other)
        assert denied.status_code == 404
