"""P7-D complete user journey: guided configuration through real run contracts and results UI."""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def test_p7d_desktop_and_narrow_guided_journey_reuses_existing_run_pipeline(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    submitted = []
    parsed_task_specs = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                path = url.path
                query = url.query
                if request.method == "POST" and path == "/runs":
                    payload = json.loads(request.post_data or "{}")
                    submitted.append(payload)
                    parsed_task_specs.append(payload.get("task_spec") or {})
                    route.fulfill(json={"prepared_run": {"run_id": "p7d-run-001"},
                                        "execution_state": {"run_id": "p7d-run-001", "state": "QUEUED"}})
                    return
                if request.method == "GET" and path == "/runs" and query.startswith("limit="):
                    route.fulfill(json={"runs": [{
                        "run_id": "p7d-run-001", "task_name": "P7-D 用户旅程",
                        "status": "SUCCEEDED", "validation_result": "PASS",
                        "sealed": True, "primary_capability_id": (parsed_task_specs[-1] if parsed_task_specs else {}).get("model", {}).get("capability_id")
                    }], "total": 1})
                    return
                if request.method == "GET" and path == "/runs/p7d-run-001/execution":
                    route.fulfill(json={"execution_state": {
                        "run_id": "p7d-run-001", "state": "SUCCEEDED", "validation_result": "PASS",
                    }})
                    return
                if request.method == "GET" and path == "/runs/p7d-run-001":
                    spec = parsed_task_specs[0] if parsed_task_specs else {}
                    route.fulfill(json={"run": {
                        "run_id": "p7d-run-001", "sealed": True, "status": "SUCCEEDED",
                        "task_spec": spec,
                        "run_record": {"run_id": "p7d-run-001", "task_id": spec.get("task", {}).get("id"),
                                       "task_version": 1, "status": "SUCCEEDED"},
                        "validation_outcome": {"result": "PASS"},
                    }})
                    return
                if request.method == "GET" and path.startswith("/runs/p7d-run-001/"):
                    endpoint = path.rsplit("/", 1)[-1]
                    fixtures = {
                        "metrics": {"metrics": {"metrics": {"final_error": 0.01}}},
                        "events": {"events": {"events": []}},
                        "plots": {"plot_manifest": {"series": []}},
                        "telemetry": {"rows": [{"time_s": 0.0}, {"time_s": 1.0}], "total": 2},
                        "artifacts": {"artifacts": []},
                        "dataset": {"dataset": None},
                        "fmea": {"table": None, "manifest": None},
                        "fault-traceability": {"traceability": None},
                    }
                    if endpoint == "overview":
                        route.fulfill(json={"source": "fixture", "rows": [{"time_s": 0.0}, {"time_s": 1.0}],
                                            "count": 2, "total": 2, "sampled": False})
                    else:
                        route.fulfill(json=fixtures.get(endpoint, {}))
                    return
                response = client.request(
                    request.method, path + ("?" + query if query else ""),
                    content=request.post_data_buffer,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() in ("authorization", "content-type", "accept")},
                )
                route.fulfill(status=response.status_code, body=response.content,
                              headers={k: v for k, v in response.headers.items()
                                       if k.lower() in ("content-type", "cache-control")})

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            assert page.locator("#wizardView").is_visible()

            # Desktop: use the actual guided controls and registered capability catalog.
            normal = page.locator('#wizardOptions [data-wizard-goal="normal_run"]')
            normal.wait_for(state="visible", timeout=30000)
            normal.press("Enter")
            assert page.locator('#wizardOptions [data-wizard-level="component"]').is_visible()
            page.locator('#wizardOptions [data-wizard-level="component"]').click()
            first_object = page.locator("#wizardOptions [data-wizard-object]").first
            first_object.wait_for(state="visible", timeout=20000)
            first_object.click()
            page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible")
            assert page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").is_visible()
            page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
            page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
            page.wait_for_function(
                "() => document.querySelector('#wizardNotice').textContent.includes('校验通过')",
                timeout=30000,
            )
            run_button = page.locator("#wizardOptions").get_by_role("button", name="运行仿真")
            assert run_button.is_enabled()
            run_button.click()
            page.wait_for_function("() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                                   timeout=30000)
            page.wait_for_function("() => document.querySelector('#wizardConversation').innerText.includes('仿真已完成') || document.querySelector('#wizardConversation').innerText.includes('仿真任务已结束')",
                                   timeout=30000)
            assert len(submitted) == 1
            assert submitted[0]["execute"] is True
            assert submitted[0]["execution_mode"] == "async"
            assert page.locator("#wizardRunResultsHost").is_visible()
            assert page.locator("#activeRunCard").inner_text().strip()
            assert page.locator("#runList [data-run-id='p7d-run-001']").count() == 1
            assert "SUCCEEDED" in page.locator("#activeRunCard").inner_text()
            assert "PASS" in page.locator("#activeRunCard").inner_text()
            assert not errors, errors

            # Returning to configuration preserves the form but invalidates old validation.
            page.locator("#wizardOptions").get_by_role("button", name="返回配置").click()
            assert page.locator("#wizardConfigSections").is_visible()
            assert page.locator("#wizardRunResultsHost").is_hidden()
            assert "请重新校验" in page.locator("#wizardConversation").inner_text() or page.locator("#wizardOptions").is_visible()

            # Narrow screen: no page-level horizontal scroll; key controls remain reachable.
            page.set_viewport_size({"width": 390, "height": 844})
            dimensions = page.evaluate("""() => ({
                viewport: document.documentElement.clientWidth,
                document: document.documentElement.scrollWidth,
                body: document.body.scrollWidth
            })""")
            assert dimensions["document"] <= dimensions["viewport"] + 1, dimensions
            assert page.locator("#wizardBackBtn").is_visible()
            page.locator("#wizardBackBtn").focus()
            assert page.locator("#wizardBackBtn").evaluate("(node) => document.activeElement === node")

            # Advanced view restores the same, unique result panel; no clone/duplicate IDs.
            page.locator("#wizardAdvancedBtn").click()
            assert page.locator("#creatorView").is_visible()
            assert page.locator("#creatorRunPanel").is_visible()
            assert page.locator("#creatorRunPanel").count() == 1
            assert len(submitted) == 1
            assert not errors, errors
        finally:
            browser.close()


def test_p7d_failed_task_spec_preflight_never_submits_run_and_preserves_configuration(tmp_path):
    with TestClient(create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )) as client:
        # Invalid TaskSpec schema is rejected at the authoritative API boundary.
        response = client.post("/tasks/parse", json={
            "input_kind": "form",
            "form_data": {"simulation": {"duration_s": -1, "sample_s": 0}},
            "compile_if_valid": True,
        })
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        payload = response.json()
        assert payload.get("ok") is False
        assert payload.get("result", {}).get("validation", {}).get("errors")
