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


def test_guided_component_reaction_wheel_can_apply_official_basilisk_preset(tmp_path):
    # The general workbench browser suite intentionally has no Basilisk;
    # the dedicated native-rw workflow installs and version-checks 2.11.0.
    pytest.importorskip("Basilisk", reason="requires official Basilisk; covered by native-rw-component-probe")
    from importlib.metadata import version
    assert version("bsk") == "2.11.0"
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=True,
        auth_mode="disabled",
    )
    errors = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                request_path = url.path + ("?" + url.query if url.query else "")
                if request.method == "POST" and url.path == "/runs":
                    # Preserve the real UI async submission contract. The
                    # embedded worker executes the official finite Run.
                    payload = json.loads(request.post_data or "{}")
                    assert payload.get("execute") is True, payload
                    assert payload.get("execution_mode") == "async", payload
                    response = client.post(request_path, json=payload)
                else:
                    response = client.request(
                        request.method,
                        request_path,
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="normal_run"]').click()
            page.locator('#wizardOptions [data-wizard-level="component"]').click()
            page.locator('#wizardOptions [data-wizard-object="component.reaction_wheel"]').click()
            page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)

            parameters = page.locator("#wizardParametersPanel")
            if parameters.get_attribute("open") is None:
                parameters.locator(":scope > summary").click()
            preset = page.locator("#wizardNativeRwPreset")
            preset.wait_for(state="visible", timeout=10000)
            assert "官方 Basilisk 原生反作用轮" in preset.inner_text()
            preset.get_by_role("button", name="应用原生单轮配置").click()

            backend = page.locator('#wizardParameterHost #dynamicForm [data-field-path="parameters.values.physics_backend"] select')
            backend.wait_for(state="visible", timeout=15000)
            assert backend.input_value() == "basilisk_native"
            assert page.locator('#wizardParameterHost #dynamicForm [data-section-id="parameters_backend"]').get_attribute("open") is not None
            assert "仅支持单轮标称工况" in page.locator("#wizardNotice").inner_text()
            # Applying a native preset narrows the workflow to the finite
            # offline mode; it must not leave unsupported realtime choices.
            page.locator("#wizardBackBtn").click()
            page.locator('#wizardOptions [data-wizard-mode="offline"]').wait_for(state="visible", timeout=10000)
            assert page.locator("#wizardOptions [data-wizard-mode]").count() == 1
            assert page.locator('#wizardOptions [data-wizard-mode="limited_realtime"]').count() == 0
            page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=10000)
            # The compact event editor is nested inside the guided event
            # disclosure. Verify its actual disabled state without forcing
            # the hidden nested disclosure open.
            assert not page.locator("#eventBackendLimitNote").evaluate("(node) => node.classList.contains('hidden')")
            assert page.locator("#effectKind").is_disabled()
            assert page.locator("#effectSelect").is_disabled()
            assert page.locator("#addEffectBtn").is_disabled()

            page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
            page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
            page.wait_for_function(
                "() => document.querySelector('#wizardNotice')?.textContent.includes('配置校验通过')",
                timeout=30000,
            )
            page.locator("#wizardOptions").get_by_role("button", name="运行仿真").click()
            page.locator("#wizardRunResultsHost").wait_for(state="visible", timeout=120000)
            active_run = page.locator("#activeRunCard")
            active_run.wait_for(state="visible", timeout=30000)
            page.wait_for_function(
                "() => document.querySelector('#activeRunCard')?.innerText.includes('SUCCEEDED')",
                timeout=120000,
            )
            run_metadata = active_run.locator(".compact-run-metadata")
            run_metadata.locator(":scope > summary").click()
            assert "Basilisk 官方原生" in active_run.inner_text()
            assert "Basilisk.simulation.reactionWheelStateEffector" in active_run.inner_text()

            page.locator('[data-result-tab="charts"]').click()
            page.locator("#telemetryChart").wait_for(state="visible", timeout=15000)
            legend = page.locator("#chartLegend")
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            legend_text = legend.inner_text()
            assert "反作用轮1 · 角速度" in legend_text, legend_text
            assert "反作用轮1 · 实际电机力矩" in legend_text, legend_text

            # Re-open the sealed run from Run Center through the visible
            # guided-workspace shortcut, not a hidden sidebar control.
            runs_payload = client.get("/runs?limit=200")
            assert runs_payload.status_code == 200, runs_payload.text
            native_runs = [
                row for row in runs_payload.json().get("runs", [])
                if row.get("primary_capability_id") == "component.reaction_wheel.v1"
                and row.get("status") == "SUCCEEDED"
            ]
            assert len(native_runs) == 1, native_runs
            run_id = native_runs[0]["run_id"]
            # Verify the durable bundle contract, not just the Run Center
            # list status. The UI must replay the exact sealed artifact.
            detail_response = client.get(f"/runs/{run_id}")
            assert detail_response.status_code == 200, detail_response.text
            durable = detail_response.json()["run"]
            assert durable["sealed"] is True, durable
            assert durable["integrity"]["ok"] is True, durable.get("integrity")
            assert durable["validation_outcome"]["result"] == "PASS", durable.get("validation_outcome")
            assert durable["run_record"]["status"] == "SUCCEEDED", durable.get("run_record")
            assert durable["task_spec"]["model"]["capability_id"] == "component.reaction_wheel.v1"
            assert durable["summary"]["physics_backend"] == "basilisk_native"
            history_button = page.locator("#wizardOpenRunCenterBtn")
            history_button.wait_for(state="visible", timeout=10000)
            history_button.click()
            page.locator("#runCenterView").wait_for(state="visible", timeout=15000)
            run_row = page.locator("#runCenterTableBody tr").filter(has_text=run_id)
            run_row.wait_for(state="visible", timeout=15000)
            run_row.get_by_role("button", name="查看").click()
            page.locator("#creatorView").wait_for(state="visible", timeout=20000)
            replayed = page.locator("#activeRunCard")
            replayed.wait_for(state="visible", timeout=15000)
            replay_metadata = replayed.locator(".compact-run-metadata")
            replay_metadata.locator(":scope > summary").click()
            assert "Basilisk 官方原生" in replayed.inner_text()
            assert "Basilisk.simulation.reactionWheelStateEffector" in replayed.inner_text()
            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.innerText.includes('反作用轮1 · 实际电机力矩')",
                timeout=15000,
            )
            assert not errors, errors
        finally:
            browser.close()


def test_guided_single_component_shortcut_selects_verified_native_reaction_wheel(tmp_path):
    """The novice workflow must select a real component and load its native preset."""
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
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            failures = []
            external_requests = []
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.on("response", lambda response: failures.append(
                f"HTTP {response.status}: {response.url}"
            ) if response.status >= 400 and (
                ("/assets/" in response.url and "/assets/vendor/globe/" not in response.url)
                or "/forms/" in response.url
                or "/scenario-templates" in response.url
                or "/workbench/" in response.url
            ) else None)

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                if url.scheme in {"http", "https"} and url.netloc != "workbench.test":
                    external_requests.append(request.url)
                    route.abort()
                    return
                response = client.request(
                    request.method,
                    url.path + ("?" + url.query if url.query else ""),
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/octet-stream")},
                )

            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#wizardOptions [data-wizard-goal='component_native']").click()
            page.locator("#wizardOptions [data-wizard-object='component.reaction_wheel']").wait_for(timeout=20000)
            assert page.locator("#wizardOptions [data-wizard-object]").count() == 1
            page.locator("#wizardOptions [data-wizard-object='component.reaction_wheel']").click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=20000)
            assert "官方 Basilisk 原生反作用轮模型" in page.locator("#wizardConversation").inner_text()
            assert "原生故障、退化及实时单机运行尚未开放" in page.locator("#wizardConversation").inner_text()
            assert page.locator("#wizardOptions [data-wizard-mode]").count() == 0

            assert page.locator("#wizardParametersPanel").evaluate("(node) => node.open")
            form = page.locator("#wizardParameterHost #dynamicForm")
            form.wait_for(state="attached", timeout=10000)
            backend = form.locator('[data-field-path="parameters.values.physics_backend"] select')
            assert backend.input_value() == "basilisk_native"
            assert backend.locator("option").count() == 2
            assert "Basilisk 原生物理" in backend.locator('option[value="basilisk_native"]').inner_text()

            wheels = form.locator('[data-field-path="parameters.values.num_wheels"] input')
            assert wheels.input_value() == "1"
            torque = form.locator('[data-field-path="parameters.values.max_motor_torque_nm"] input')
            assert float(torque.input_value()) == pytest.approx(0.005)
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()


def test_guided_native_rw_preflight_shows_planner_error_and_blocks_run(tmp_path):
    """A native-only limit must be visible to users before any Run is submitted."""
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
    submitted_runs = []
    errors = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                request_path = url.path + ("?" + url.query if url.query else "")
                if request.method == "POST" and url.path == "/runs":
                    submitted_runs.append(json.loads(request.post_data or "{}"))
                response = client.request(
                    request.method,
                    request_path,
                    content=request.post_data_buffer,
                    headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="component_native"]').click()
            page.locator('#wizardOptions [data-wizard-object="component.reaction_wheel"]').wait_for(timeout=15000)
            page.locator('#wizardOptions [data-wizard-object="component.reaction_wheel"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)

            duration = page.locator(
                '#wizardParameterHost #dynamicForm [data-field-path="simulation.duration_s"] input'
            )
            duration.wait_for(state="visible", timeout=10000)
            duration.fill("121")

            page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
            page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
            page.wait_for_function(
                "() => document.querySelector('#formValidationSummary')?.textContent.includes('duration_s')",
                timeout=30000,
            )
            summary = page.locator("#formValidationSummary").inner_text()
            assert "120" in summary, summary
            assert "native_scope" in summary or "原生" in summary or "native RW" in summary, summary
            run_button = page.locator("#wizardOptions").get_by_role("button", name="运行仿真")
            assert run_button.is_disabled()
            assert not submitted_runs, submitted_runs
            assert client.get("/runs?limit=20").json().get("runs", []) == []
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


def test_experiment_variant_opens_exact_run_evidence_and_returns_to_experiment(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    requested_runs = []
    submitted_runs = []
    with TestClient(app) as client:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, executable_path=executable)
            try:
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    path = url.path
                    if request.method == "GET" and path == "/experiments":
                        route.fulfill(json={"experiments": [{"experiment_id": "p7c-exp", "name": "P7-C 精确 Run 跳转", "experiment_type": "sweep", "variant_count": 2, "status": "COMPLETED"}]})
                        return
                    if request.method == "GET" and path == "/experiments/p7c-exp":
                        route.fulfill(json={"experiment": {
                            "experiment_id": "p7c-exp", "name": "P7-C 精确 Run 跳转",
                            "base_task_spec": {"task": {"id": "task-p7c"}},
                        }})
                        return
                    if request.method == "GET" and path == "/experiments/p7c-exp/comparison":
                        route.fulfill(json={"comparison": {
                            "metrics": ["final_error"], "rows": [
                                {"variant_index": 1, "run_id": "run-p7c-selected",
                                 "status": "SUCCEEDED", "assertion_status": "PASS",
                                 "values": {"final_error": 0.012}},
                                {"variant_index": 0, "run_id": "run-p7c-other",
                                 "status": "SUCCEEDED", "assertion_status": "PASS",
                                 "values": {"final_error": 0.021}},
                            ],
                        }})
                        return
                    if request.method == "GET" and path == "/runs/run-p7c-selected":
                        requested_runs.append(path)
                        route.fulfill(json={"run": {
                            "run_id": "run-p7c-selected", "sealed": True, "status": "SUCCEEDED",
                            "task_spec": {"task": {"id": "task-p7c"}, "model": {"capability_id": "component.reaction_wheel.v1"}},
                            "run_record": {"task_id": "task-p7c", "task_version": 3},
                            "validation_outcome": {"result": "PASS"},
                        }})
                        return
                    if request.method == "POST" and path == "/runs":
                        submitted_runs.append(path)
                        route.fulfill(status=500, json={"detail": "C2 navigation must not submit a new run"})
                        return
                    if path.endswith("/metrics"):
                        route.fulfill(json={"metrics": {"metrics": {}}}); return
                    if path.endswith("/events"):
                        route.fulfill(json={"events": {}}); return
                    if path.endswith("/plots"):
                        route.fulfill(json={"plot_manifest": {"series": []}}); return
                    if path.endswith("/telemetry/overview"):
                        route.fulfill(json={"source": "fixture", "rows": [], "count": 0, "sampled": False}); return
                    if path.endswith("/telemetry"):
                        route.fulfill(json={"rows": [], "total": 0}); return
                    if path.endswith("/artifacts"):
                        route.fulfill(json={"artifacts": []}); return
                    if path.endswith("/dataset"):
                        route.fulfill(json={"dataset": None}); return
                    if path.endswith("/fmea"):
                        route.fulfill(json={"table": None, "manifest": None}); return
                    if path.endswith("/fault-traceability"):
                        route.fulfill(json={"traceability": None}); return
                    response = client.request(
                        request.method, path + ("?" + url.query if url.query else ""),
                        content=request.post_data_buffer,
                        headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                    )
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers.get("content-type", "application/json")})

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")
                page.locator('#wizardAdditionalTools summary').click()
                page.locator('#wizardAdditionalTools [data-wizard-goal="experiments"]').click()
                page.locator("#experimentCenterView").wait_for(state="visible")
                page.locator("#experimentList").wait_for()
                page.evaluate("""() => {
                    window.__p7cTestRenderExperiment = true;
                }""")
                # Render the comparison from the real UI handler using the selected experiment card.
                page.locator("#experimentList").get_by_role("button", name="查看对比").first.click()
                comparison = page.locator("#experimentComparisonPanel")
                comparison.wait_for(state="visible", timeout=10000)
                row = comparison.locator("tbody tr").filter(has_text="run-p7c-selected")
                row.get_by_role("button", name="查看运行证据").click()
                page.locator("#diagnosticView").wait_for(state="visible", timeout=20000)
                evidence = page.locator("#runEvidencePanel")
                evidence.wait_for(state="visible", timeout=10000)
                assert "run-p7c-selected" in evidence.inner_text()
                assert "V3" in evidence.inner_text()
                assert "PASS" in evidence.inner_text()
                assert requested_runs == ["/runs/run-p7c-selected"]
                assert not submitted_runs
                context = page.locator("#diagnosticView [data-p7-context]")
                assert "返回位置：实验中心" in context.inner_text()
                page.locator("#runEvidencePanel").get_by_role("button", name="查看完整运行曲线").click()
                page.locator("#creatorView").wait_for(state="visible")
                creator_context = page.locator("#creatorView [data-p7-context]")
                assert "返回位置：实验中心" in creator_context.inner_text()
                creator_context.get_by_role("button", name="返回实验中心").click()
                page.locator("#experimentCenterView").wait_for(state="visible")
                assert not submitted_runs
                assert not errors, errors
            finally:
                browser.close()


def test_guided_native_sensor_workbench_runs_imu_star_tracker_and_magnetometer(tmp_path):
    """Each admitted N2 sensor must use its registered native template and real sealed Run."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk; covered by native-sensor-component gate")
    from importlib.metadata import version
    assert version("bsk") == "2.11.0"
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    cases = (
        (
            "component.imu",
            "Basilisk.simulation.imuSensor.ImuSensor",
            ("adcs.imu.gyro_x_rad_s", "adcs.imu.accel_x_m_s2"),
        ),
        (
            "component.star_tracker",
            "Basilisk.simulation.starTracker.StarTracker",
            ("adcs.star_tracker.sigma_x", "adcs.star_tracker.valid"),
        ),
        (
            "component.magnetometer",
            "Basilisk.simulation.magnetometer.Magnetometer",
            ("adcs.magnetometer.field_x_t", "adcs.magnetometer.field_norm_t"),
        ),
    )
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=True,
        auth_mode="disabled",
    )
    errors = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            for object_id, provenance, expected_fields in cases:
                page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="zh-CN")
                page.on("pageerror", lambda error: errors.append(str(error)))

                def proxy(route):
                    request = route.request
                    url = urlsplit(request.url)
                    request_path = url.path + ("?" + url.query if url.query else "")
                    if request.method == "POST" and url.path == "/runs":
                        payload = json.loads(request.post_data or "{}")
                        assert payload.get("execute") is True, payload
                        assert payload.get("execution_mode") == "async", payload
                        response = client.post(request_path, json=payload)
                    else:
                        response = client.request(
                            request.method,
                            request_path,
                            content=request.post_data_buffer,
                            headers={k: v for k, v in request.headers.items() if k in ("content-type", "accept")},
                        )
                    route.fulfill(
                        status=response.status_code,
                        body=response.content,
                        headers={"content-type": response.headers.get("content-type", "application/json")},
                    )

                page.route("https://workbench.test/**", proxy)
                page.goto("https://workbench.test/", wait_until="networkidle")
                page.locator('#wizardOptions [data-wizard-goal="component_native"]').click()
                page.locator(f'#wizardOptions [data-wizard-object="{object_id}"]').wait_for(timeout=15000)
                page.locator(f'#wizardOptions [data-wizard-object="{object_id}"]').click()
                page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)

                form = page.locator("#wizardParameterHost #dynamicForm")
                backend = form.locator('[data-field-path="parameters.values.physics_backend"] select')
                backend.wait_for(state="visible", timeout=15000)
                assert backend.input_value() == "basilisk_native"
                assert page.locator("#wizardEventHost").evaluate("(node) => node.classList.contains('hidden')")
                assert provenance.replace("Basilisk.simulation.", "") in page.locator("#wizardConversation").inner_text()

                page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
                page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
                page.wait_for_function(
                    "() => document.querySelector('#wizardNotice')?.textContent.includes('配置校验通过')",
                    timeout=30000,
                )
                page.locator("#wizardOptions").get_by_role("button", name="运行仿真").click()
                page.locator("#wizardRunResultsHost").wait_for(state="visible", timeout=120000)
                page.wait_for_function(
                    "() => document.querySelector('#activeRunCard')?.innerText.includes('SUCCEEDED')",
                    timeout=120000,
                )

                run_rows = client.get("/runs?limit=200").json().get("runs", [])
                matching = [
                    row for row in run_rows
                    if row.get("primary_capability_id") == {
                        "component.imu": "component.imu.v1",
                        "component.star_tracker": "component.star_tracker.v1",
                        "component.magnetometer": "component.magnetometer.v1",
                    }[object_id]
                    and row.get("status") == "SUCCEEDED"
                ]
                assert len(matching) == 1, (object_id, matching)
                run_id = matching[0]["run_id"]
                detail = client.get(f"/runs/{run_id}")
                assert detail.status_code == 200, detail.text
                durable = detail.json()["run"]
                assert durable["sealed"] is True
                assert durable["integrity"]["ok"] is True
                assert durable["validation_outcome"]["result"] == "PASS"
                assert durable["summary"]["physics_backend"] == "basilisk_native"
                assert durable["summary"]["adapter_metadata"]["native_provenance"] == provenance

                telemetry = client.get(f"/runs/{run_id}/telemetry?limit=100")
                assert telemetry.status_code == 200, telemetry.text
                assert telemetry.json()["rows"]
                for field in expected_fields:
                    assert field in telemetry.json()["rows"][-1], (object_id, field, telemetry.json()["rows"][-1])
                page.locator('[data-result-tab="charts"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#chartLegend')?.children.length > 0",
                    timeout=15000,
                )
                page.close()
            assert not errors, errors
        finally:
            browser.close()
