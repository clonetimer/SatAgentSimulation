"""Guided task workspace prioritizes common actions and preserves task history."""
import json
import os
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from sat_sim.api import create_app


def _task_spec():
    return {
        "schema_version": "1.0.0",
        "task": {"id": "guided-task-ux", "name": "整星姿态控制验证"},
        "simulation": {"duration_s": 30.0, "sample_s": 0.5, "level": "component"},
        "model": {"capability_id": "component.reaction_wheel.v1", "target": {"mode": "nominal"}},
        "parameters": {"profile": "demo", "values": {}},
        "events": {"faults": [], "degradations": []},
        "outputs": {"qoi": [], "plots": [], "output_root": "datasets/guided-task-ux"},
        "assurance": {"parameter_profile": "demo"},
    }


def test_guided_task_workspace_discloses_secondary_actions_and_opens_details(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        saved = client.post("/task-center/tasks", json={
            "task_id": "guided-task-ux", "task_spec": _task_spec(), "status": "READY",
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["task"]["name"] == "整星姿态控制验证"
        assert saved.json()["task"]["capability_id"] == "component.reaction_wheel.v1"
        listed = client.get("/task-center/tasks?limit=200")
        assert listed.status_code == 200, listed.text
        assert any(
            task.get("task_id") == "guided-task-ux"
            and task.get("name") == "整星姿态控制验证"
            and task.get("capability_id") == "component.reaction_wheel.v1"
            for task in listed.json().get("tasks", [])
        )
        errors = []
        task_list_payloads = []
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    response = client.request(
                        request.method,
                        url.path + ("?" + url.query if url.query else ""),
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                    if request.method == "GET" and url.path == "/task-center/tasks":
                        task_list_payloads.append(response.json())
                    route.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "application/octet-stream")},
                    )

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")

                def original_task_card():
                    for candidate in page.locator("#wizardTaskList .wizard-task-card").all():
                        title = candidate.locator(".wizard-task-main strong").inner_text().strip()
                        if title == "整星姿态控制验证":
                            return candidate
                    raise AssertionError("Original task card was not rendered")

                page.locator('#wizardOptions [data-wizard-goal="history"]').click()
                page.locator("#wizardTaskWorkspace").wait_for(state="visible", timeout=10000)
                try:
                    page.wait_for_function(
                        "document.querySelectorAll('#wizardTaskList .wizard-task-card').length >= 1",
                        timeout=10000,
                    )
                    card = original_task_card()
                    card.wait_for(state="visible", timeout=10000)
                except Exception as error:
                    raise AssertionError(
                        "Task history did not render the original task; "
                        f"summary={page.locator('#wizardTaskSummary').inner_text()!r}; "
                        f"API task payloads={task_list_payloads!r}"
                    ) from error
                assert card.get_by_role("button", name="任务详情").is_visible()
                assert card.get_by_role("button", name="编辑配置").is_visible()
                assert card.get_by_role("button", name="运行任务").is_visible()

                more = card.locator("details.wizard-task-more")
                assert more.get_attribute("open") is None
                more.locator("summary").click()
                assert more.get_attribute("open") is not None
                assert more.get_by_role("button", name="克隆任务").is_visible()
                assert more.get_by_role("button", name="删除任务").is_visible()

                more.get_by_role("button", name="克隆任务").click()
                page.wait_for_function("document.querySelectorAll('#wizardTaskList .wizard-task-card').length === 2")
                assert "已克隆任务" in page.locator("#wizardNotice").inner_text()

                card = original_task_card()
                card.get_by_role("button", name="任务详情").click()
                page.locator("#taskDetailsModal").wait_for(state="visible")
                assert "V1" in page.locator("#taskDetailsSummary").inner_text()
                assert page.locator("#taskVersionTableBody tr").count() >= 1
                page.locator("#closeTaskDetailsBtn").click()

                # Return to the wizard's welcome step, then enter each specialist
                # workspace through the same visible "其他专业工具" cards as users.
                page.locator("#wizardBackBtn").click()
                tools = page.locator("#wizardAdditionalTools")
                tools.wait_for(state="visible")
                if tools.get_attribute("open") is None:
                    tools.locator("summary").click()
                assert tools.get_attribute("open") is not None

                specialist_views = (
                    ("experiments", "experimentCenterView"),
                    ("goals", "goalRunView"),
                    ("diagnostics", "diagnosticView"),
                    ("runs", "runCenterView"),
                )
                for goal, view_id in specialist_views:
                    tools.locator(f'[data-wizard-goal="{goal}"]').click()
                    page.locator(f"#{view_id}").wait_for(state="visible", timeout=10000)
                    context = page.locator(f"#{view_id} [data-p7-context]")
                    assert context.is_visible()
                    assert "来源：仿真向导" in context.inner_text()
                    context.get_by_role("button", name="返回仿真向导").click()
                    assert page.locator("body").get_attribute("data-active-view") == "wizard"
                    assert "当前配置、任务版本和已有运行记录保持不变" in page.locator("#wizardConversation").inner_text()
                    assert tools.is_visible()
                    assert tools.get_attribute("open") is not None

                    # Reopen task history after every round trip to prove the
                    # task list and cloned version records remain reachable.
                    page.locator('#wizardOptions [data-wizard-goal="history"]').click()
                    page.wait_for_function(
                        "document.querySelectorAll('#wizardTaskList .wizard-task-card').length === 2",
                        timeout=10000,
                    )
                    original = original_task_card()
                    original.wait_for(state="visible", timeout=10000)
                    assert page.locator("#wizardTaskList .wizard-task-card").count() == 2
                    page.locator("#wizardBackBtn").click()
                    assert tools.is_visible() and tools.get_attribute("open") is not None

                assert not errors, errors
            finally:
                browser.close()


def test_task_details_dialog_filters_explains_and_compares_real_run_evidence(tmp_path):
    """P7-A UI flow, with Run Bundle HTTP fixtures; no native execution implied."""
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    history = [
        {"run_id": "p7-good-a", "task_version": 1, "status": "SUCCEEDED", "validation_result": "PASS"},
        {"run_id": "p7-good-b", "task_version": 2, "status": "SUCCEEDED", "validation_result": "FAIL"},
        {"run_id": "p7-failed", "task_version": 2, "status": "FAILED", "validation_result": "NOT_EVALUATED"},
    ]
    comparisons = []
    unexpected_runs = []
    with TestClient(app) as client:
        assert client.post("/task-center/tasks", json={
            "task_id": "guided-task-ux", "task_spec": _task_spec(), "status": "READY",
        }).status_code == 200

        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": 1300, "height": 850}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    endpoint = url.path
                    if endpoint == "/task-center/tasks/guided-task-ux/runs":
                        route.fulfill(json={"ok": True, "task_id": "guided-task-ux", "runs": history})
                        return
                    if request.method == "POST" and endpoint == "/runs":
                        unexpected_runs.append(request.post_data)
                        route.fulfill(status=403, json={"detail": "must not execute"})
                        return
                    if endpoint == "/runs/compare" and request.method == "POST":
                        payload = json.loads(request.post_data)
                        comparisons.append(payload)
                        route.fulfill(json={"ok": True, "comparison": {
                            "schema_version": "scenario.run-comparison.v2", "run_count": 2,
                            "metrics": payload["metrics"],
                            "rows": [{"run_id": item, "values": {
                                "qoi.adcs.final_pointing_error_deg": 4.0 if item == "p7-good-a" else 2.0,
                            }} for item in payload["run_ids"]],
                        }})
                        return
                    if endpoint == "/runs/p7-failed/execution":
                        route.fulfill(json={"ok": True, "execution_state": {
                            "state": "FAILED", "error": "BASILISK_RUNTIME_ERROR",
                        }})
                        return
                    if endpoint == "/runs/p7-failed":
                        route.fulfill(json={"ok": True, "run": {
                            "run_record": {"status": "FAILED", "terminal_reason_code": "NATIVE_EXECUTION_FAILED"},
                            "validation_outcome": {"result": "NOT_EVALUATED"},
                        }})
                        return
                    if endpoint.startswith("/runs/p7-good-"):
                        run_id = endpoint.split("/")[2]
                        if endpoint.count("/") == 2:
                            route.fulfill(json={"ok": True, "run": {
                                "run_record": {"status": "SUCCEEDED"}, "sealed": True,
                                "prepared_run": {"primary_capability_id": "component.reaction_wheel.v1"},
                                "integrity": {"ok": True},
                            }})
                            return
                        if endpoint.endswith("/metrics"):
                            route.fulfill(json={"ok": True, "metrics": {
                                "qoi.adcs.final_pointing_error_deg": 4.0 if run_id == "p7-good-a" else 2.0,
                            }, "metric_metadata": {
                                "qoi.adcs.final_pointing_error_deg": {"label_zh": "最终指向误差", "unit": "deg"},
                            }})
                            return
                        if endpoint.endswith("/plots"):
                            route.fulfill(json={"ok": True, "plot_manifest": {
                                "series": [{"field": "adcs.pointing_error_deg", "label": "指向误差", "unit": "deg"}],
                            }})
                            return
                        if endpoint.endswith("/telemetry"):
                            route.fulfill(json={"ok": True, "run_id": run_id, "offset": 0, "total": 2,
                                                "rows": [{"time_s": 0, "adcs.pointing_error_deg": 3.0},
                                                         {"time_s": 1, "adcs.pointing_error_deg": 2.5}]})
                            return
                    response = client.request(
                        request.method, endpoint + ("?" + url.query if url.query else ""),
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers.get("content-type", "application/octet-stream")})

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")
                page.locator('#wizardOptions [data-wizard-goal="history"]').click()
                card = page.locator("#wizardTaskList .wizard-task-card").filter(has_text="整星姿态控制验证")
                card.wait_for(state="visible", timeout=10000)
                card.get_by_role("button", name="任务详情").click()
                dialog = page.locator("#taskDetailsModal")
                dialog.wait_for(state="visible")
                assert page.locator("#taskRunTableBody tr").count() == 3

                page.locator("#taskRunHistoryStatus").select_option("attention")
                assert page.locator("#taskRunTableBody tr").count() == 2
                assert "失败或验收异常" in page.locator("#taskRunHistoryStatus option:checked").inner_text()
                failed_row = page.locator("#taskRunTableBody tr").filter(has_text="p7-failed")
                assert failed_row.locator('input[type="checkbox"]').is_disabled()
                failed_row.get_by_role("button", name="查看原因").click()
                page.wait_for_function("document.querySelector('#taskRunExplanation').textContent.includes('NATIVE_EXECUTION_FAILED')")
                assert "BASILISK_RUNTIME_ERROR" in page.locator("#taskRunExplanation").inner_text()

                page.locator("#taskRunHistoryStatus").select_option("all")
                page.locator('input[data-task-history-run="p7-good-a"]').check()
                page.locator('input[data-task-history-run="p7-good-b"]').check()
                assert page.locator("#taskRunCompareSelectedBtn").is_enabled()
                page.locator("#taskRunCompareSelectedBtn").click()
                page.locator("#taskRunCompareHost #runComparePanel").wait_for(state="visible", timeout=15000)
                page.wait_for_function("document.querySelector('#runCompareSummary').textContent.includes('p7-good-b')")
                assert "最终指向误差" in page.locator("#runCompareBody").inner_text()
                assert comparisons == [{"run_ids": ["p7-good-a", "p7-good-b"],
                                        "metrics": ["qoi.adcs.final_pointing_error_deg"]}]
                assert not unexpected_runs

                page.locator("#closeTaskDetailsBtn").click()
                assert dialog.is_hidden()
                assert page.locator("#runCenterView #runComparePanel").count() == 1
                assert not errors, errors
            finally:
                browser.close()


def test_natural_language_candidate_preview_discard_and_explicit_apply(tmp_path):
    """P7-B: real Chromium reviews a candidate before any current-task mutation."""
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    run_posts = []
    parse_posts = []
    with TestClient(app) as client:
        schema = client.get("/forms/capabilities/component.reaction_wheel.v1").json()["form_schema"]
        parsed = client.post("/tasks/parse", json={
            "input_kind": "form", "form_data": schema["default_form"], "compile_if_valid": True,
        })
        assert parsed.status_code == 200 and parsed.json()["ok"] is True
        candidate = parsed.json()["result"]["task_spec"]
        candidate["task"]["name"] = "自然语言候选预览验收"
        candidate["simulation"]["duration_s"] = 47.0

        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    if request.method == "POST" and url.path == "/tasks/parse":
                        parse_posts.append(json.loads(request.post_data))
                        route.fulfill(json={
                            "ok": True, "request_id": f"p7b-candidate-{len(parse_posts)}",
                            "result": {
                                "task_spec": candidate,
                                "planning": {},
                                "route": {"provider_id": "local-template"},
                            },
                        })
                        return
                    if request.method == "POST" and url.path == "/runs":
                        run_posts.append(request.post_data)
                    response = client.request(
                        request.method, url.path + ("?" + url.query if url.query else ""),
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers.get("content-type", "application/octet-stream")})

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")
                page.locator('#wizardOptions [data-wizard-goal="natural_language"]').click()
                page.locator("#wizardAgentWorkspace").wait_for(state="visible")
                page.locator("#agentInput").fill("建立反作用飞轮仿真，运行47秒，观察飞轮转速。")
                page.locator("#wizardGenerateFromTextBtn").click()
                preview = page.locator("#wizardAgentCandidatePreview")
                preview.wait_for(state="visible", timeout=20000)
                assert "自然语言候选预览验收" in preview.inner_text()
                assert "47 s" in preview.inner_text()
                assert "新任务 · 完整配置" in preview.inner_text()
                assert page.locator("#wizardAgentCandidatePreview .agent-candidate-node").count() == 6
                assert page.locator("#wizardAgentCandidatePreview .agent-candidate-edge").count() == 5
                assert "不代表服务端图校验已通过" in preview.inner_text()
                assert not run_posts

                page.locator("#wizardDiscardAgentCandidateBtn").click()
                assert preview.is_hidden()
                assert "已放弃候选配置" in page.locator("#wizardNotice").inner_text()
                assert not run_posts

                page.locator("#agentInput").fill("重新生成候选，运行47秒。")
                page.locator("#wizardGenerateFromTextBtn").click()
                preview.wait_for(state="visible", timeout=20000)
                page.locator("#wizardApplyAgentCandidateBtn").click()
                page.wait_for_function("document.querySelector('#wizardConfigSections') && !document.querySelector('#wizardConfigSections').classList.contains('hidden')", timeout=20000)
                assert "候选配置已应用" in page.locator("#wizardNotice").inner_text()
                assert not run_posts
                assert len(parse_posts) == 2
                assert not errors, errors
            finally:
                browser.close()
