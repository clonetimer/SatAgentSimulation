from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim.api import API_VERSION, WEB_WORKBENCH_VERSION, create_app
from sat_sim.security import required_roles_for_request


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            runs_root=tmp_path / "runs",
            artifacts_root=tmp_path / "artifacts",
            embedded_worker=False,
            auth_mode="disabled",
        )
    )


def test_release_left_directory_assets_and_versions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/")
        assert response.status_code == 200
        text = response.text
        assert 'class="app-sidebar"' in text
        for label in ("仿真创建", "任务中心", "实验中心", "运行中心", "系统诊断", "运行日志", "模型服务"):
            assert label in text
        assert 'id="runCenterView"' in text
        assert 'id="diagnosticView"' in text
        assert 'id="logCenterView"' in text
        health = client.get("/health").json()
        assert health["api_version"] == API_VERSION == "workbench-api.v1"
        assert health["workbench_version"] == WEB_WORKBENCH_VERSION == "sidebar-observability-workbench.v1"


def test_release_logs_and_redacted_diagnostic_bundle(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.get("/health")
        logs = client.get("/logs").json()["logs"]
        assert logs["total"] >= 1
        assert any(row["event"] == "request_complete" for row in logs["rows"])
        response = client.get("/diagnostics/bundle")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            assert "manifest.json" in names
            assert "system/dependency_versions.json" in names
            assert "control_plane/provider_catalog_redacted.json" in names
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["secret_values_exposed"] is False
            assert all(".." not in Path(name).parts for name in names)


def test_release_report_is_generated_before_bundle_seal(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        task_spec = client.post("/scenario-templates/whole_nominal/instantiate", json={}).json()["task_spec"]
        task_spec["simulation"].update(duration_s=2, step_s=1, sample_s=1)
        response = client.post(
            "/runs",
            json={"task_spec": task_spec, "execute": True, "execution_mode": "sync", "max_attempts": 1},
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["prepared_run"]["run_id"]
        root = tmp_path / "runs" / run_id
        assert (root / "results" / "report.html").is_file()
        assert (root / "results" / "report_manifest.json").is_file()
        assert (root / "SEALED.json").is_file()
        report = client.get(f"/runs/{run_id}/report.html")
        assert report.status_code == 200
        assert "卫星仿真平台 · 自动报告" in report.text
        assert "浏览器“打印/另存为 PDF”" in report.text
        run = client.get(f"/runs/{run_id}").json()["run"]
        assert run["integrity"]["ok"] is True
        diagnostic = client.get(f"/diagnostics/bundle?run_id={run_id}")
        with zipfile.ZipFile(io.BytesIO(diagnostic.content)) as archive:
            assert "run/input/task_spec.json" in archive.namelist()
            assert "run/results/metrics.json" in archive.namelist()


def test_release_observability_endpoints_require_operator_or_admin() -> None:
    assert required_roles_for_request("GET", "/logs") == frozenset({"operator", "admin"})
    assert required_roles_for_request("GET", "/diagnostics/summary") == frozenset({"operator", "admin"})
