"""V15 visual-flow run-to-result and executable-package browser closure.

The graph compile and ZIP export use the real API. The asynchronous Run status
is a deterministic protocol fixture; this test does not claim native physics.
"""
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest


def test_v15_visual_flow_run_loads_result_and_exports_program(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app

    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    run_id = "v15-visual-flow-browser-run"
    captured = {"task_spec": None, "run_request": None, "program_export": False}
    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )

    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            failures = []
            page.on("pageerror", lambda error: failures.append(str(error)))

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                path = url.path
                query = ("?" + url.query) if url.query else ""
                if request.method == "POST" and path == "/runs":
                    body = json.loads(request.post_data or "{}")
                    captured["run_request"] = body
                    captured["task_spec"] = body.get("task_spec")
                    assert body.get("execute") is True
                    assert body.get("execution_mode") == "async"
                    route.fulfill(status=202, json={
                        "prepared_run": {"run_id": run_id},
                        "execution_state": {"run_id": run_id, "state": "QUEUED"},
                    })
                    return
                if request.method == "GET" and path == "/runs":
                    task = captured["task_spec"] or {}
                    route.fulfill(status=200, json={"total": 1, "runs": [{
                        "run_id": run_id, "status": "SUCCEEDED", "sealed": True,
                        "task_id": task.get("task", {}).get("id"),
                        "task_name": task.get("task", {}).get("name"),
                        "primary_capability_id": task.get("model", {}).get("capability_id"),
                    }]})
                    return
                if path == f"/runs/{run_id}/execution":
                    route.fulfill(status=200, json={"execution_state": {
                        "run_id": run_id, "state": "SUCCEEDED", "validation_result": "PASS",
                    }})
                    return
                if path == f"/runs/{run_id}" and request.method == "GET":
                    task = captured["task_spec"] or {}
                    route.fulfill(status=200, json={"run": {
                        "run_id": run_id, "status": "SUCCEEDED", "sealed": True,
                        "integrity": {"ok": True}, "task_spec": task,
                        "run_record": {
                            "run_id": run_id, "status": "SUCCEEDED",
                            "task_id": task.get("task", {}).get("id"), "task_version": 1,
                        },
                        "validation_outcome": {"result": "PASS"},
                        "available_outputs": {"fmea": False, "fault_traceability": False},
                    }})
                    return
                if path == f"/runs/{run_id}/metrics":
                    route.fulfill(status=200, json={"metrics": {}, "metric_metadata": {}})
                    return
                if path == f"/runs/{run_id}/events":
                    route.fulfill(status=200, json={"events": []})
                    return
                if path == f"/runs/{run_id}/plots":
                    route.fulfill(status=200, json={"plot_manifest": {"series": []}})
                    return
                if path == f"/runs/{run_id}/telemetry/overview":
                    route.fulfill(status=200, json={
                        "source": "browser-acceptance-fixture", "rows": [],
                        "count": 0, "total": 0, "sampled": False,
                    })
                    return
                if path == f"/runs/{run_id}/telemetry":
                    route.fulfill(status=200, json={"rows": [], "total": 0})
                    return
                if path == f"/runs/{run_id}/artifacts":
                    route.fulfill(status=200, json={"artifacts": []})
                    return
                if path == f"/runs/{run_id}/dataset":
                    route.fulfill(status=200, json={"dataset": None})
                    return
                if request.method == "POST" and path == "/visual-composer/run-overlay":
                    route.fulfill(status=200, json={"overlay": None})
                    return
                if request.method == "POST" and path == "/tasks/export-program":
                    captured["program_export"] = True
                response = client.request(
                    request.method, path + query, content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code, body=response.content,
                    headers={key: value for key, value in response.headers.items()
                             if key.lower() in ("content-type", "content-disposition", "x-sat-sim-program-kind")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator(".capability-card.active").wait_for(state="attached")
            page.locator("#wizardAdvancedBtn").click()
            page.locator("#creatorView").wait_for(state="visible")
            page.locator("#creatorGraphTabButton").click()
            page.locator("#graphNodes .graph-node").first.wait_for(state="visible")
            page.locator("#graphValidateBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#graphStatus').classList.contains('success')"
                " && document.querySelector('#graphStatus').textContent.includes('图已校验')",
                timeout=30000,
            )
            assert page.locator("#graphRunBtn").is_enabled()
            page.locator("#graphRunBtn").click()
            page.wait_for_function(
                "(runId) => { const card = document.querySelector('#activeRunCard');"
                " return !!card && card.textContent.includes(runId)"
                " && card.querySelector('.run-status')?.textContent === 'SUCCEEDED'; }",
                arg=run_id, timeout=60000,
            )
            assert captured["run_request"] is not None
            assert captured["task_spec"]["model"]["capability_id"]
            assert page.locator("#activeRunCard").is_visible()
            assert page.locator("#activeRunCard .run-status").inner_text() == "SUCCEEDED"

            project_actions = page.locator("#graphProjectActions")
            if project_actions.get_attribute("open") is None:
                project_actions.locator(":scope > summary").click()
            with page.expect_download(timeout=60000) as download:
                page.locator("#graphExportProgramBtn").click()
            package_path = tmp_path / "v15-visual-flow-program.zip"
            download.value.save_as(str(package_path))
            assert captured["program_export"] is True
            with zipfile.ZipFile(package_path) as archive:
                names = set(archive.namelist())
                root = next(iter(names)).split("/", 1)[0]
                expected = {
                    f"{root}/README.md", f"{root}/program_manifest.json",
                    f"{root}/run.bat", f"{root}/run.sh",
                    f"{root}/run_simulation.py", f"{root}/task_spec.json",
                    f"{root}/visual_graph.json",
                }
                assert expected <= names
            assert failures == [], failures
        finally:
            browser.close()
