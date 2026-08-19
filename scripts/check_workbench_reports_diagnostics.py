#!/usr/bin/env python3
"""
用途：检查工作台目录、报告、诊断和日志接口。
参数：无命令行参数。
输出：生成工作台诊断审计 JSON。
"""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from sat_sim import __version__
from sat_sim.api import API_VERSION, WEB_WORKBENCH_VERSION, create_app
from sat_sim.security import required_roles_for_request
from sat_sim.release_closure import RELEASE_ID, RELEASE_VERSION

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "release_sidebar_report_diagnostic_audit.json"


def _record(checks: list[dict[str, Any]], check_id: str, passed: bool, evidence: Any) -> None:
    checks.append({"id": check_id, "status": "PASS" if passed else "FAIL", "evidence": evidence})


def main() -> int:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sat-sim-audit-") as temp_dir:
        root = Path(temp_dir)
        app = create_app(
            runs_root=root / "runs",
            artifacts_root=root / "artifacts",
            embedded_worker=False,
            auth_mode="disabled",
        )
        with TestClient(app) as client:
            home = client.get("/")
            labels = ["仿真创建", "任务中心", "实验中心", "运行中心", "系统诊断", "运行日志", "模型服务"]
            _record(checks, "web_home_available", home.status_code == 200, home.status_code)
            _record(checks, "global_left_sidebar_present", 'class="app-sidebar"' in home.text, "app-sidebar")
            _record(checks, "left_directory_has_seven_entries", all(label in home.text for label in labels), labels)
            _record(checks, "run_center_present", 'id="runCenterView"' in home.text, "runCenterView")
            _record(checks, "diagnostic_center_present", 'id="diagnosticView"' in home.text, "diagnosticView")
            _record(checks, "log_center_present", 'id="logCenterView"' in home.text, "logCenterView")

            health_response = client.get("/health")
            health = health_response.json()
            _record(checks, "health_available", health_response.status_code == 200, health_response.status_code)
            _record(checks, "api_version", health.get("api_version") == API_VERSION, health.get("api_version"))
            _record(checks, "workbench_version", health.get("workbench_version") == WEB_WORKBENCH_VERSION, health.get("workbench_version"))
            _record(checks, "package_version", __version__ == RELEASE_VERSION, __version__)

            log_response = client.get("/logs")
            logs = log_response.json().get("logs", {})
            _record(checks, "structured_logs_available", log_response.status_code == 200 and logs.get("total", 0) >= 1, logs.get("total"))
            _record(checks, "request_log_recorded", any(row.get("event") == "request_complete" for row in logs.get("rows", [])), "request_complete")

            diagnostic_response = client.get("/diagnostics/bundle")
            diagnostic_ok = diagnostic_response.status_code == 200
            diagnostic_names: set[str] = set()
            diagnostic_manifest: dict[str, Any] = {}
            if diagnostic_ok:
                with zipfile.ZipFile(io.BytesIO(diagnostic_response.content)) as archive:
                    diagnostic_names = set(archive.namelist())
                    diagnostic_manifest = json.loads(archive.read("manifest.json"))
            _record(checks, "diagnostic_bundle_available", diagnostic_ok, diagnostic_response.status_code)
            _record(checks, "diagnostic_bundle_redacted", diagnostic_manifest.get("secret_values_exposed") is False, diagnostic_manifest)
            _record(checks, "diagnostic_bundle_safe_paths", bool(diagnostic_names) and all(".." not in Path(name).parts for name in diagnostic_names), len(diagnostic_names))

            template_response = client.post("/scenario-templates/whole_spacecraft_unified_native/instantiate", json={})
            task_spec = template_response.json()["task_spec"]
            task_spec["simulation"].update(duration_s=2, step_s=1, sample_s=1)
            run_response = client.post(
                "/runs",
                json={"task_spec": task_spec, "execute": True, "execution_mode": "sync", "max_attempts": 1},
            )
            run_ok = run_response.status_code == 200
            run_id = run_response.json().get("prepared_run", {}).get("run_id") if run_ok else None
            _record(checks, "real_basilisk_run_completed", run_ok and bool(run_id), {"status": run_response.status_code, "run_id": run_id})
            if run_id:
                run_root = root / "runs" / run_id
                _record(checks, "html_report_generated_before_seal", (run_root / "results/report.html").is_file() and (run_root / "SEALED.json").is_file(), str(run_root))
                report_response = client.get(f"/runs/{run_id}/report.html")
                _record(checks, "html_report_endpoint", report_response.status_code == 200 and "卫星仿真平台 · 自动报告" in report_response.text, report_response.status_code)
                run_payload = client.get(f"/runs/{run_id}").json().get("run", {})
                _record(checks, "run_bundle_integrity", run_payload.get("integrity", {}).get("ok") is True, run_payload.get("integrity"))
                run_diagnostic = client.get(f"/diagnostics/bundle?run_id={run_id}")
                names: set[str] = set()
                if run_diagnostic.status_code == 200:
                    with zipfile.ZipFile(io.BytesIO(run_diagnostic.content)) as archive:
                        names = set(archive.namelist())
                _record(checks, "run_diagnostic_contains_evidence", {"run/input/task_spec.json", "run/results/metrics.json"}.issubset(names), sorted(names))

    _record(checks, "logs_rbac_operator_admin", required_roles_for_request("GET", "/logs") == frozenset({"operator", "admin"}), sorted(required_roles_for_request("GET", "/logs")))
    _record(checks, "diagnostics_rbac_operator_admin", required_roles_for_request("GET", "/diagnostics/summary") == frozenset({"operator", "admin"}), sorted(required_roles_for_request("GET", "/diagnostics/summary")))

    passed = sum(item["status"] == "PASS" for item in checks)
    payload = {
        "schema_version": "sidebar-report-diagnostic-audit.v1",
        "package_version": __version__,
        "release_id": RELEASE_ID,
        "status": "PASS" if passed == len(checks) else "FAIL",
        "summary": {"total": len(checks), "passed": passed, "failed": len(checks) - passed},
        "checks": checks,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
