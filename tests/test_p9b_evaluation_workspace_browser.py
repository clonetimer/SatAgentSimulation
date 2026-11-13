from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def _sealed_run(root: Path) -> None:
    _write(root / "runtime" / "prepared_run.json", {
        "primary_capability_id": "component.reaction_wheel.v1",
    })
    _write(root / "run_record.json", {
        "run_id": root.name, "status": "SUCCEEDED", "validation_result": "PASS",
    })
    _write(root / "validation" / "validation_outcome.json", {"result": "PASS"})
    _write(root / "results" / "telemetry.jsonl", "\n".join([
        json.dumps({"time_s": 0.0, "adcs.reaction_wheel.speed_rad_s_0": 1.0}),
        json.dumps({"time_s": 1.0, "adcs.reaction_wheel.speed_rad_s_0": 2.0}),
        json.dumps({"time_s": 2.0, "adcs.reaction_wheel.speed_rad_s_0": 3.0}),
    ]) + "\n")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"bundle_manifest.json", "SEALED.json"}:
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


def test_evaluation_workspace_desktop_and_mobile_journey(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable or not Path(executable).is_file():
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to the installed Chromium executable")
    runs = tmp_path / "runs"
    _sealed_run(runs / "run-001")
    app = create_app(runs_root=runs, artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                parsed = urlsplit(request.url)
                response = client.request(
                    request.method, parsed.path + ("?" + parsed.query if parsed.query else ""),
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() in ("authorization", "content-type", "accept")},
                )
                route.fulfill(status=response.status_code, body=response.content,
                              headers={key: value for key, value in response.headers.items()
                                       if key.lower() in ("content-type", "cache-control")})

            page.route("http://127.0.0.1/**", proxy)
            page.evaluate("""() => {
              const makeStorage = () => {
                const values = new Map();
                return {
                  getItem: (key) => values.has(String(key)) ? values.get(String(key)) : null,
                  setItem: (key, value) => values.set(String(key), String(value)),
                  removeItem: (key) => values.delete(String(key)),
                  clear: () => values.clear(),
                };
              };
              Object.defineProperty(window, 'localStorage', {configurable: true, value: makeStorage()});
              Object.defineProperty(window, 'sessionStorage', {configurable: true, value: makeStorage()});
            }""")
            html = client.get("/").text.replace("<head>", '<head><base href="http://127.0.0.1/">', 1)
            page.set_default_timeout(12000)
            page.set_default_navigation_timeout(12000)
            page.set_content(html, wait_until="domcontentloaded", timeout=12000)
            page.locator('#wizardAdditionalTools > summary').click()
            page.locator('[data-wizard-goal="runs"]').click()
            page.locator('.nav-button[data-view="evaluations"]').wait_for(state="visible")
            page.locator('.nav-button[data-view="evaluations"]').click()
            page.locator("#evaluationRunSelect option").first.wait_for(state="attached", timeout=20000)
            page.wait_for_function(
                "() => document.querySelectorAll('#evaluationSourceList [data-add-source]:not([disabled])').length > 0",
                timeout=20000,
            )
            assert "run-001" in page.locator("#evaluationRunSummary").inner_text()
            page.locator("#evaluationSourceList [data-add-source]:not([disabled])").first.click()
            assert page.locator("#evaluationMetricList .evaluation-metric-card").count() == 1
            page.locator("#evaluationPlanName").fill("反作用轮转速评估")
            page.locator("#evaluationMetricList select[data-metric-formula]").select_option("sample_rms.v1")
            page.locator("#createEvaluationBtn").click()
            page.locator("#evaluationReportPanel").wait_for(state="visible", timeout=20000)
            assert "反作用轮转速评估" in page.locator("#evaluationReportMeta").inner_text()
            assert "2.160247" in page.locator("#evaluationReportMetrics").inner_text()
            assert page.locator("#evaluationHistoryList .evaluation-history-item").count() == 1
            visible = page.locator("#evaluationView").inner_text()
            for forbidden in ("TaskSpec", "snapshot", "JSON", "API", "调试语句", "开发痕迹"):
                assert forbidden not in visible

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            assert page.locator("#evaluationRunSelect").is_visible()
            assert page.locator("#createEvaluationBtn").is_visible()
            assert errors == [], errors
        finally:
            browser.close()
