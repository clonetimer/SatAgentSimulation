"""Guided task workspace prioritizes common actions and preserves task history."""
import hashlib
import shutil
import json
import os
from pathlib import Path
import zipfile
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
            page.wait_for_function(
                "() => document.querySelector('#wizardParameterHost #dynamicForm [data-field-path=\"parameters.values.physics_backend\"] select')?.value === 'basilisk_native'",
                timeout=15000,
            )
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
            # The first admitted native device is also covered through the
            # visible browser download control, not only through the API test.
            bundle_button = page.locator("#downloadRunBundleBtn")
            bundle_button.wait_for(state="visible", timeout=15000)
            with page.expect_download(timeout=30000) as bundle_download_info:
                bundle_button.click()
            bundle_download = bundle_download_info.value
            bundle_path = tmp_path / "reaction_wheel_run_bundle.zip"
            bundle_download.save_as(str(bundle_path))
            with zipfile.ZipFile(bundle_path) as archive:
                bundle_prefix = f"{run_id}/"
                manifest_name = f"{bundle_prefix}bundle_manifest.json"
                seal_name = f"{bundle_prefix}SEALED.json"
                archive_names = set(archive.namelist())
                assert manifest_name in archive_names
                assert seal_name in archive_names
                manifest_bytes = archive.read(manifest_name)
                manifest = json.loads(manifest_bytes)
                seal = json.loads(archive.read(seal_name))
                assert manifest["run_id"] == run_id
                assert seal["run_id"] == run_id
                assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                expected_archive_paths = {f"{bundle_prefix}{relative}" for relative in manifest["files"]}
                assert archive_names == expected_archive_paths | {manifest_name, seal_name}
                assert f"{bundle_prefix}runtime/execution.lock" not in archive_names
                for relative, entry in manifest["files"].items():
                    payload = archive.read(f"{bundle_prefix}{relative}")
                    assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
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
            assert page.locator("#wizardOptions [data-wizard-object]").count() == 9
            for native_object in (
                "component.reaction_wheel", "component.imu", "component.star_tracker", "component.magnetometer",
                "component.battery", "component.solar_panel", "component.thruster", "component.fuel_tank",
                "component.thermal_node",
            ):
                assert page.locator(f"#wizardOptions [data-wizard-object='{native_object}']").is_visible()
            page.locator("#wizardOptions [data-wizard-object='component.reaction_wheel']").click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=20000)
            assert "官方 Basilisk ReactionWheelStateEffector" in page.locator("#wizardConversation").inner_text()
            assert "原生故障、退化、约束及实时单机运行尚未开放" in page.locator("#wizardConversation").inner_text()
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


def test_guided_native_sensor_workbench_runs_all_verified_adcs_eps_propulsion_thermal_devices(tmp_path):
    """Each admitted native device must use its registered template and real sealed Run."""
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
            "IMU",
            ("adcs.imu.gyro_x_rad_s", "adcs.imu.accel_x_m_s2"),
        ),
        (
            "component.star_tracker",
            "Basilisk.simulation.starTracker.StarTracker",
            "星敏感器",
            ("adcs.star_tracker.sigma_x", "adcs.star_tracker.valid"),
        ),
        (
            "component.magnetometer",
            "Basilisk.simulation.magnetometer.Magnetometer",
            "磁强计",
            ("adcs.magnetometer.field_x_t", "adcs.magnetometer.field_norm_t"),
        ),
        (
            "component.battery",
            "components.battery.builder.run_battery_native_case",
            "蓄电池",
            ("eps.battery.soc", "eps.battery.net_power_w"),
        ),
        (
            "component.solar_panel",
            "Basilisk.simulation.simpleSolarPanel.SimpleSolarPanel",
            "太阳电池阵",
            ("eps.solar.array_power_w", "eps.solar.incidence_cos"),
        ),
        (
            "component.thruster",
            "Basilisk.simulation.thrusterDynamicEffector.ThrusterDynamicEffector",
            "推力器",
            ("propulsion.thruster.effective_thrust_n", "propulsion.thruster.cumulative_impulse_ns"),
        ),
        (
            "component.fuel_tank",
            "Basilisk.simulation.fuelTank.FuelTank",
            "燃料贮箱",
            ("propulsion.fuel_tank.propellant_mass_kg", "propulsion.fuel_tank.mass_rate_kg_s"),
        ),
        (
            "component.thermal_node",
            "Basilisk.simulation.sensorThermal.SensorThermal",
            "热节点",
            ("thermal.node.temp_k", "thermal.node.temp_c"),
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
            for object_id, provenance, display_name, expected_fields in cases:
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
                assert page.locator("#wizardFaultPanel").evaluate("(node) => node.classList.contains('hidden')")
                assert page.locator("#wizardNativeAdcsFaultNote").evaluate("(node) => node.classList.contains('hidden')")
                assert display_name in page.locator("#wizardConversation").inner_text()

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
                        "component.battery": "component.battery.v1",
                        "component.solar_panel": "component.solar_panel.v1",
                        "component.thruster": "component.thruster.v1",
                        "component.fuel_tank": "component.fuel_tank.v1",
                        "component.thermal_node": "component.thermal_node.v1",
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
                adapter_metadata = durable["summary"].get("adapter_metadata") or {}
                assert adapter_metadata["physics_backend"] == "basilisk_native"
                assert adapter_metadata["native_provenance"] == provenance

                telemetry = client.get(f"/runs/{run_id}/telemetry?limit=100")
                assert telemetry.status_code == 200, telemetry.text
                assert telemetry.json()["rows"]
                for field in expected_fields:
                    assert field in telemetry.json()["rows"][-1], (object_id, field, telemetry.json()["rows"][-1])
                plots = client.get(f"/runs/{run_id}/plots")
                assert plots.status_code == 200, plots.text
                plot_manifest = plots.json()["plot_manifest"]
                assert plot_manifest["series"], (object_id, plots.json())
                assert any(
                    item["field"] == expected_fields[0]
                    for item in plot_manifest["series"]
                ), (object_id, expected_fields[0], plot_manifest["series"])

                # Export the exact sealed Run Bundle from the user-facing result panel.
                # The archive must contain every manifest-listed byte, plus the original
                # manifest and seal; a diagnostic subset is not sufficient here.
                bundle_button = page.locator("#downloadRunBundleBtn")
                bundle_button.wait_for(state="visible", timeout=15000)
                with page.expect_download(timeout=30000) as bundle_download_info:
                    bundle_button.click()
                bundle_download = bundle_download_info.value
                bundle_path = tmp_path / f"{object_id.replace('.', '_')}_run_bundle.zip"
                bundle_download.save_as(str(bundle_path))
                with zipfile.ZipFile(bundle_path) as archive:
                    bundle_prefix = f"{run_id}/"
                    manifest_name = f"{bundle_prefix}bundle_manifest.json"
                    seal_name = f"{bundle_prefix}SEALED.json"
                    archive_names = set(archive.namelist())
                    assert manifest_name in archive_names
                    assert seal_name in archive_names
                    manifest_bytes = archive.read(manifest_name)
                    manifest = json.loads(manifest_bytes)
                    seal = json.loads(archive.read(seal_name))
                    assert manifest["run_id"] == run_id
                    assert seal["run_id"] == run_id
                    assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                    expected_archive_paths = {f"{bundle_prefix}{relative}" for relative in manifest["files"]}
                    assert archive_names == expected_archive_paths | {manifest_name, seal_name}
                    assert f"{bundle_prefix}runtime/execution.lock" not in archive_names
                    for relative, entry in manifest["files"].items():
                        payload = archive.read(f"{bundle_prefix}{relative}")
                        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]

                page.locator('[data-result-tab="charts"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#chartLegend')?.children.length > 0",
                    timeout=15000,
                )
                # Re-open the same durable Run from the visible guided Run Center
                # entry, then verify provenance and chart data survive replay.
                page.locator("#wizardOpenRunCenterBtn").wait_for(state="visible", timeout=10000)
                page.locator("#wizardOpenRunCenterBtn").click()
                page.locator("#runCenterView").wait_for(state="visible", timeout=15000)
                row = page.locator("#runCenterTableBody tr").filter(has_text=run_id)
                row.wait_for(state="visible", timeout=15000)
                row.get_by_role("button", name="查看").click()
                page.locator("#creatorView").wait_for(state="visible", timeout=20000)
                replayed = page.locator("#activeRunCard")
                replayed.wait_for(state="visible", timeout=15000)
                replay_metadata = replayed.locator(".compact-run-metadata")
                replay_metadata.locator(":scope > summary").click()
                assert "Basilisk 官方原生" in replayed.inner_text()
                assert provenance in replayed.inner_text()
                page.locator('[data-result-tab="charts"]').click()
                page.wait_for_function(
                    "() => document.querySelector('#chartLegend')?.children.length > 0",
                    timeout=15000,
                )
                page.close()
            assert not errors, errors
        finally:
            browser.close()


def test_guided_native_subsystem_workbench_runs_adcs_and_exports_verified_run_bundle(tmp_path):
    """ADCS must close its real measured attitude loop and export the same sealed Run in Chromium."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
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
            page.locator('#wizardOptions [data-wizard-goal="subsystem_native"]').click()
            object_option = page.locator('#wizardOptions [data-wizard-object="subsystem.adcs"]')
            object_option.wait_for(timeout=15000)
            object_option.click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)
            fault_panel = page.locator("#wizardFaultPanel")
            assert not fault_panel.evaluate("(node) => node.classList.contains('hidden')")
            assert not page.locator("#wizardNativeAdcsFaultNote").evaluate("(node) => node.classList.contains('hidden')")
            if fault_panel.get_attribute("open") is None:
                fault_panel.locator(":scope > summary").click()
            effect_kind = page.locator("#effectKind")
            assert effect_kind.evaluate("(node) => Array.from(node.options).filter(option => !option.hidden && !option.disabled).map(option => option.value)") == ["fault", "degradation"]
            fault_effects = page.locator("#effectSelect").evaluate("(node) => Array.from(node.options).map(option => option.value)")
            assert set(fault_effects) == {"adcs_rw_jamming", "adcs_rw_motor_failure", "gyro_bias_step"}
            effect_kind.select_option("degradation")
            degradation_effects = page.locator("#effectSelect").evaluate("(node) => Array.from(node.options).map(option => option.value)")
            assert set(degradation_effects) == {"adcs_rw_torque_authority_loss", "gyro_noise_increase"}
            assert "Magnetometer" in page.locator("#wizardConversation").inner_text()
            assert "本体系 B" in page.locator("#wizardConversation").inner_text()

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
                if row.get("primary_capability_id") == "subsystem.adcs_unified_native.v1"
                and row.get("status") == "SUCCEEDED"
            ]
            assert len(matching) == 1, matching
            run_id = matching[0]["run_id"]
            detail = client.get(f"/runs/{run_id}")
            assert detail.status_code == 200, detail.text
            durable = detail.json()["run"]
            assert durable["task_spec"]["simulation"]["backend"] == "basilisk"
            assert durable["task_spec"]["model"]["capability_id"] == "subsystem.adcs_unified_native.v1"
            assert durable["sealed"] is True
            assert durable["integrity"]["ok"] is True
            assert durable["validation_outcome"]["result"] == "PASS"
            assert durable["summary"]["adcs_closed_loop_improved"] is True
            assert durable["summary"]["adcs_fusion"]["magnetometer_used_sample_count"] > 0
            assert durable["summary"]["adcs_fusion"]["body_rate_valid_sample_count"] > 0
            assert durable["summary"]["external_or_proxy_module_count"] == 0

            telemetry = client.get(f"/runs/{run_id}/telemetry?limit=500")
            assert telemetry.status_code == 200, telemetry.text
            assert telemetry.json()["rows"]
            final_row = telemetry.json()["rows"][-1]
            for field in (
                "adcs.pointing_error_deg",
                "adcs.fusion.estimated_mrp_BN_x",
                "adcs.fusion.estimated_omega_BN_B_x",
                "adcs.fusion.magnetometer_used",
                "adcs.fusion.body_rate_valid",
                "adcs.sensor.imu_sample_valid",
            ):
                assert field in final_row, (field, final_row)
            assert final_row["adcs.fusion.body_rate_valid"] == 1, final_row
            streams = client.get(f"/runs/{run_id}/telemetry-streams")
            assert streams.status_code == 200, streams.text
            stream_ids = {item["stream_id"] for item in streams.json()["multi_rate_telemetry"]["streams"]}
            assert {"adcs_fast", "adcs_housekeeping"} <= stream_ids
            plots = client.get(f"/runs/{run_id}/plots")
            assert plots.status_code == 200, plots.text
            plot_fields = {item["field"] for item in plots.json()["plot_manifest"]["series"]}
            assert {"adcs.pointing_error_deg", "adcs.fusion.estimated_mrp_BN_x"} <= plot_fields
            fmea = client.get(f"/runs/{run_id}/fmea")
            assert fmea.status_code == 200, fmea.text

            with page.expect_download(timeout=30000) as bundle_download_info:
                page.locator("#downloadRunBundleBtn").click()
            bundle_download = bundle_download_info.value
            bundle_path = tmp_path / "adcs_run_bundle.zip"
            bundle_download.save_as(str(bundle_path))
            with zipfile.ZipFile(bundle_path) as archive:
                prefix = f"{run_id}/"
                manifest_name = f"{prefix}bundle_manifest.json"
                seal_name = f"{prefix}SEALED.json"
                names = set(archive.namelist())
                manifest_bytes = archive.read(manifest_name)
                manifest = json.loads(manifest_bytes)
                seal = json.loads(archive.read(seal_name))
                expected = {f"{prefix}{relative}" for relative in manifest["files"]}
                assert names == expected | {manifest_name, seal_name}
                assert seal["run_id"] == run_id
                assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                for relative, entry in manifest["files"].items():
                    assert hashlib.sha256(archive.read(f"{prefix}{relative}")).hexdigest() == entry["sha256"]



            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            page.locator("#wizardOpenRunCenterBtn").wait_for(state="visible", timeout=10000)
            page.locator("#wizardOpenRunCenterBtn").click()
            page.locator("#runCenterView").wait_for(state="visible", timeout=15000)
            row = page.locator("#runCenterTableBody tr").filter(has_text=run_id)
            row.wait_for(state="visible", timeout=15000)
            row.get_by_role("button", name="查看").click()
            page.locator("#creatorView").wait_for(state="visible", timeout=20000)
            replayed = page.locator("#activeRunCard")
            replayed.wait_for(state="visible", timeout=15000)
            run_metadata = replayed.locator("details.compact-run-metadata")
            run_metadata.locator("summary").click()
            assert run_id in replayed.inner_text()
            assert "adcs" in replayed.inner_text().lower()
            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            page.close()
        finally:
            browser.close()
    assert not errors, errors


def test_guided_native_adcs_wizard_runs_verified_torque_authority_degradation(tmp_path):
    """The ADCS wizard must submit only a qualified degradation and preserve its evidence."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
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
                    payload = json.loads(request.post_data or "{}")
                    assert payload.get("execute") is True, payload
                    assert payload.get("execution_mode") == "async", payload
                    response = client.post(request_path, json=payload)
                else:
                    response = client.request(
                        request.method,
                        request_path,
                        content=request.post_data_buffer,
                        headers={key: value for key, value in request.headers.items() if key in ("content-type", "accept")},
                    )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="subsystem_native"]').click()
            page.locator('#wizardOptions [data-wizard-object="subsystem.adcs"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)
            fault_panel = page.locator("#wizardFaultPanel")
            assert not fault_panel.evaluate("(node) => node.classList.contains('hidden')")
            if fault_panel.get_attribute("open") is None:
                fault_panel.locator(":scope > summary").click()

            page.locator("#effectKind").select_option("degradation")
            page.locator("#effectSelect").select_option("adcs_rw_torque_authority_loss")
            page.locator("#addEffectBtn").click()
            event_card = page.locator("#eventList .event-card").filter(has_text="ADCS反作用轮力矩权限下降")
            event_card.wait_for(state="visible", timeout=10000)

            def set_event_number(field: str, value: str) -> None:
                control = event_card.locator(f'input[data-event-field="{field}"]')
                control.fill(value)
                control.dispatch_event("change")

            set_event_number("start_s", "10")
            set_event_number("end_s", "20")
            set_event_number("parameters.torque_scale", "0.25")
            assert event_card.locator('input[data-event-field="parameters.torque_scale"]').input_value() == "0.25"

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

            runs = client.get("/runs?limit=200").json().get("runs", [])
            matching = [
                row for row in runs
                if row.get("primary_capability_id") == "subsystem.adcs_unified_native.v1"
                and row.get("status") == "SUCCEEDED"
            ]
            assert len(matching) == 1, matching
            run_id = matching[0]["run_id"]
            detail = client.get(f"/runs/{run_id}")
            assert detail.status_code == 200, detail.text
            durable = detail.json()["run"]
            assert durable["sealed"] is True
            assert durable["integrity"]["ok"] is True
            assert durable["validation_outcome"]["result"] == "PASS"
            events = durable["task_spec"]["events"]["degradations"]
            assert len(events) == 1
            assert events[0]["effect"] == "adcs_rw_torque_authority_loss"
            assert events[0]["start_s"] == 10.0
            assert events[0]["end_s"] == 20.0
            assert events[0]["parameters"]["torque_scale"] == pytest.approx(0.25)
            injection = durable["validation_outcome"]["injection_evidence"]
            assert len(injection) == 1, injection
            assert injection[0]["requested_effect"] == "adcs_rw_torque_authority_loss"
            assert injection[0]["delivery_result"] == "PASS"
            assert injection[0]["effect_result"] == "PASS"
            assert injection[0]["active_sample_count"] > 0

            telemetry = client.get(f"/runs/{run_id}/telemetry?limit=500")
            assert telemetry.status_code == 200, telemetry.text
            active_rows = [
                row for row in telemetry.json()["rows"]
                if 10.0 <= float(row["time_s"]) <= 20.0
                and "adcs_rw_torque_authority_loss" in row.get("event.active_effects", "")
            ]
            assert active_rows
            assert all(row["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.25, abs=1.0e-8) for row in active_rows)

            with page.expect_download(timeout=30000) as download_info:
                page.locator("#downloadRunBundleBtn").click()
            download = download_info.value
            archive_path = tmp_path / "adcs_torque_authority_run_bundle.zip"
            download.save_as(str(archive_path))
            with zipfile.ZipFile(archive_path) as archive:
                prefix = f"{run_id}/"
                manifest_name = f"{prefix}bundle_manifest.json"
                seal_name = f"{prefix}SEALED.json"
                names = set(archive.namelist())
                manifest_bytes = archive.read(manifest_name)
                manifest = json.loads(manifest_bytes)
                seal = json.loads(archive.read(seal_name))
                expected = {f"{prefix}{relative}" for relative in manifest["files"]}
                assert names == expected | {manifest_name, seal_name}
                assert seal["run_id"] == run_id
                assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                for relative, entry in manifest["files"].items():
                    assert hashlib.sha256(archive.read(f"{prefix}{relative}")).hexdigest() == entry["sha256"]
            page.close()
        finally:
            browser.close()
    assert not errors, errors


@pytest.mark.parametrize(
    "object_id,capability_id,source_text,stream_ids,telemetry_fields,plot_fields,project_module_count,event_kind,event_effect",
    [
        (
            "subsystem.eps",
            "subsystem.eps.unified_native.v1",
            "SimpleBattery",
            {"eps_fast", "eps_housekeeping"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w", "eps.pdu_payload_channel_enabled", "eps.pdu_shed_reason"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w"},
            None,
            None,
            None,
        ),
        (
            "subsystem.eps",
            "subsystem.eps.unified_native.v1",
            "SimpleBattery",
            {"eps_fast", "eps_housekeeping"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w", "eps.pdu_payload_channel_enabled", "eps.pdu_shed_reason", "eps.battery_capacity_j", "eps.nominal_battery_capacity_j"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w", "eps.payload_load_enabled_w"},
            None,
            "fault",
            "generation_storage_distribution_failure",
        ),
        (
            "subsystem.eps",
            "subsystem.eps.unified_native.v1",
            "SimpleBattery",
            {"eps_fast", "eps_housekeeping"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w", "eps.battery_capacity_j", "eps.nominal_battery_capacity_j", "eps.nominal_solar_power_w"},
            {"eps.battery_soc", "eps.net_power_w", "eps.solar_power_w"},
            None,
            "degradation",
            "energy_margin_degradation",
        ),
        (
            "subsystem.comm_data",
            "subsystem.comm_data.unified_native.v1",
            "SimpleInstrument",
            {"data_fast", "data_summary"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps", "comm_data.nominal_transmitter_storage_node_baud_bps"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps"},
            0,
            None,
            None,
        ),
        (
            "subsystem.comm_data",
            "subsystem.comm_data.unified_native.v1",
            "SimpleInstrument",
            {"data_fast", "data_summary"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps", "comm_data.nominal_transmitter_storage_node_baud_bps"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps"},
            0,
            "fault",
            "transmitter_rate_loss",
        ),
        (
            "subsystem.comm_data",
            "subsystem.comm_data.unified_native.v1",
            "SimpleInstrument",
            {"data_fast", "data_summary"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps", "comm_data.nominal_transmitter_storage_node_baud_bps"},
            {"comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_storage_node_rate_abs_bps"},
            0,
            "degradation",
            "transmitter_rate_degradation",
        ),
    ],
)
def test_guided_native_subsystem_workbench_runs_eps_and_comm_data_and_exports_verified_run_bundle(
    tmp_path,
    object_id,
    capability_id,
    source_text,
    stream_ids,
    telemetry_fields,
    plot_fields,
    project_module_count,
    event_kind,
    event_effect,
):
    """EPS and Comm/Data must use registered Basilisk subsystem templates in the guided Run Center."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
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
                    payload = json.loads(request.post_data or "{}")
                    assert payload.get("execute") is True, payload
                    assert payload.get("execution_mode") == "async", payload
                    response = client.post(request_path, json=payload)
                else:
                    response = client.request(
                        request.method,
                        request_path,
                        content=request.post_data_buffer,
                        headers={key: value for key, value in request.headers.items() if key in ("content-type", "accept")},
                    )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="subsystem_native"]').click()

            visible_objects = page.locator("#wizardOptions [data-wizard-object]")
            visible_objects.first.wait_for(state="visible", timeout=15000)
            assert {
                visible_objects.nth(index).get_attribute("data-wizard-object")
                for index in range(visible_objects.count())
            } == {"subsystem.adcs", "subsystem.eps", "subsystem.comm_data"}

            object_option = page.locator(f'#wizardOptions [data-wizard-object="{object_id}"]')
            object_option.click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)
            assert source_text in page.locator("#wizardConversation").inner_text()

            # O2-D exposes only the two formally qualified effects for each subsystem.
            fault_panel = page.locator("#wizardFaultPanel")
            assert not fault_panel.evaluate("(node) => node.classList.contains('hidden')")
            assert not page.locator("#wizardEventHost").evaluate("(node) => node.classList.contains('hidden')")
            assert page.locator("#wizardNativeAdcsFaultNote").evaluate("(node) => node.classList.contains('hidden')")
            if fault_panel.get_attribute("open") is None:
                fault_panel.locator(":scope > summary").click()

            allowed_effects = {
                "fault": {
                    "subsystem.eps": {"generation_storage_distribution_failure"},
                    "subsystem.comm_data": {"transmitter_rate_loss"},
                },
                "degradation": {
                    "subsystem.eps": {"energy_margin_degradation"},
                    "subsystem.comm_data": {"transmitter_rate_degradation"},
                },
            }
            for kind, by_object in allowed_effects.items():
                page.locator("#effectKind").select_option(kind)
                visible_effects = set(page.locator("#effectSelect option").evaluate_all("(items) => items.map((item) => item.value)"))
                assert visible_effects == by_object[object_id], (kind, visible_effects)
            if event_kind:
                page.locator("#effectKind").select_option(event_kind)
                page.locator("#effectSelect").select_option(event_effect)
                page.locator("#addEffectBtn").click()
                event_card = page.locator("#eventList .event-card").first
                event_card.wait_for(state="visible", timeout=10000)
                assert event_card.get_attribute("data-event-kind") == event_kind

                def set_event_number(field: str, value: str) -> None:
                    control = event_card.locator(f'input[data-event-field="{field}"]')
                    control.fill(value)
                    control.dispatch_event("change")

                set_event_number("start_s", "5")
                set_event_number("end_s", "15")
                magnitude_input = event_card.locator('input[data-event-field="magnitude"]')
                if magnitude_input.count():
                    magnitude_input.fill("0.8")
                    magnitude_input.dispatch_event("change")

            form = page.locator("#wizardParameterHost #dynamicForm")
            form.wait_for(state="attached", timeout=15000)
            for plot_field in sorted(plot_fields):
                plot_search = page.locator("#wizardOutputSearch")
                plot_search.fill(plot_field)
                plot_card = page.locator("#wizardCurveChoices .wizard-curve-card").filter(has_text=plot_field)
                plot_card.wait_for(state="visible", timeout=10000)
                curve_detail = plot_card.locator("details.wizard-curve-detail")
                if curve_detail.get_attribute("open") is None:
                    curve_detail.locator(":scope > summary").click()
                plot_option = curve_detail.locator("label.wizard-curve-row").filter(has_text=plot_field)
                plot_option.wait_for(state="visible", timeout=10000)
                plot_checkbox = plot_option.locator('input[type="checkbox"]')
                if not plot_checkbox.is_checked():
                    plot_checkbox.check()
                plot_search.fill("")
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
                if row.get("primary_capability_id") == capability_id
                and row.get("status") == "SUCCEEDED"
            ]
            assert len(matching) == 1, matching
            run_id = matching[0]["run_id"]
            detail = client.get(f"/runs/{run_id}")
            assert detail.status_code == 200, detail.text
            durable = detail.json()["run"]
            assert durable["task_spec"]["simulation"]["level"] == "subsystem"
            assert durable["task_spec"]["simulation"]["backend"] == "basilisk"
            assert durable["task_spec"]["assurance"]["allow_proxy"] is False
            assert durable["task_spec"]["model"]["capability_id"] == capability_id
            assert durable["sealed"] is True
            assert durable["integrity"]["ok"] is True
            assert durable["validation_outcome"]["result"] == "PASS"
            if event_kind:
                event_key = "faults" if event_kind == "fault" else "degradations"
                events = durable["task_spec"]["events"][event_key]
                assert len(events) == 1, events
                assert events[0]["effect"] == event_effect
                assert events[0]["start_s"] == 5.0
                assert events[0]["end_s"] == 15.0
                if "magnitude" in events[0]:
                    assert events[0]["magnitude"] == pytest.approx(0.8)
                injection = durable["validation_outcome"]["injection_evidence"]
                assert len(injection) == 1, injection
                assert injection[0]["requested_effect"] == event_effect
                assert injection[0]["delivery_result"] == "PASS"
                assert injection[0]["effect_result"] == "PASS"
                assert "RUNTIME_EFFECT_VERIFIED" in injection[0]["reason_codes"]
            summary = durable["summary"]
            assert summary["runtime_truth_status"] == "instantiated_connected_recorded"
            assert summary["official_module_count"] >= 3
            if project_module_count is None:
                assert summary["project_native_module_count"] >= 1
            else:
                assert summary["project_native_module_count"] == project_module_count
            assert summary["external_or_proxy_module_count"] == 0

            telemetry = client.get(f"/runs/{run_id}/telemetry?limit=500")
            assert telemetry.status_code == 200, telemetry.text
            rows = telemetry.json()["rows"]
            assert rows
            assert telemetry_fields <= set(rows[-1]), (telemetry_fields, rows[-1])
            if event_kind:
                active_label = "label.fault_active" if event_kind == "fault" else "label.degradation_active"
                active_rows = [
                    row for row in rows
                    if 5.0 <= float(row["time_s"]) <= 15.0 and row.get(active_label) is True
                ]
                assert active_rows, rows
                if object_id == "subsystem.eps" and event_kind == "fault":
                    assert any(
                        row["eps.pdu_payload_channel_enabled"] is False
                        and row["eps.payload_load_enabled_w"] <= 0.0
                        and "payload_channel_trip" in row["eps.pdu_shed_reason"]
                        for row in active_rows
                    )
                elif object_id == "subsystem.eps":
                    assert any(row["eps.battery_capacity_j"] < row["eps.nominal_battery_capacity_j"] for row in active_rows)
                    assert any(row["eps.solar_power_w"] < row["eps.nominal_solar_power_w"] for row in active_rows)
                else:
                    assert any(
                        row["comm_data.transmitter_storage_node_rate_abs_bps"]
                        < row["comm_data.nominal_transmitter_storage_node_baud_bps"]
                        for row in active_rows
                    )

            streams = client.get(f"/runs/{run_id}/telemetry-streams")
            assert streams.status_code == 200, streams.text
            stream_manifest = streams.json()["multi_rate_telemetry"]
            assert {item["stream_id"] for item in stream_manifest["streams"]} == stream_ids
            assert stream_manifest["interpolation_used"] is False
            for stream_id in stream_ids:
                stream = client.get(f"/runs/{run_id}/telemetry-streams/{stream_id}?limit=5")
                assert stream.status_code == 200, stream.text
                assert stream.json()["stream"]["stream_id"] == stream_id
                assert stream.json()["count"] > 0

            plots = client.get(f"/runs/{run_id}/plots")
            assert plots.status_code == 200, plots.text
            actual_plot_fields = {item["field"] for item in plots.json()["plot_manifest"]["series"]}
            assert plot_fields <= actual_plot_fields, (plot_fields, actual_plot_fields)
            fmea = client.get(f"/runs/{run_id}/fmea")
            assert fmea.status_code == 200, fmea.text
            assert "no risk score is fabricated" in fmea.json()["manifest"]["warning"]

            with page.expect_download(timeout=30000) as bundle_download_info:
                page.locator("#downloadRunBundleBtn").click()
            bundle_download = bundle_download_info.value
            bundle_path = tmp_path / f"{object_id.replace('.', '_')}_run_bundle.zip"
            bundle_download.save_as(str(bundle_path))
            with zipfile.ZipFile(bundle_path) as archive:
                prefix = f"{run_id}/"
                manifest_name = f"{prefix}bundle_manifest.json"
                seal_name = f"{prefix}SEALED.json"
                archive_names = set(archive.namelist())
                manifest_bytes = archive.read(manifest_name)
                manifest = json.loads(manifest_bytes)
                seal = json.loads(archive.read(seal_name))
                expected = {f"{prefix}{relative}" for relative in manifest["files"]}
                assert archive_names == expected | {manifest_name, seal_name}
                assert seal["run_id"] == run_id
                assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                for relative, entry in manifest["files"].items():
                    assert hashlib.sha256(archive.read(f"{prefix}{relative}")).hexdigest() == entry["sha256"]

            # Reopen the same sealed Run through Run Center; do not create a second run.
            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            page.locator("#wizardOpenRunCenterBtn").wait_for(state="visible", timeout=10000)
            page.locator("#wizardOpenRunCenterBtn").click()
            page.locator("#runCenterView").wait_for(state="visible", timeout=15000)
            history_row = page.locator("#runCenterTableBody tr").filter(has_text=run_id)
            history_row.wait_for(state="visible", timeout=15000)
            history_row.get_by_role("button", name="查看").click()
            page.locator("#creatorView").wait_for(state="visible", timeout=20000)
            replayed = page.locator("#activeRunCard")
            replayed.wait_for(state="visible", timeout=15000)
            run_metadata = replayed.locator("details.compact-run-metadata")
            run_metadata.locator("summary").click()
            assert run_id in replayed.inner_text()
            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            page.close()
        finally:
            browser.close()
    assert not errors, errors


def test_guided_whole_spacecraft_verified_event_catalog_hides_unverified_events(tmp_path):
    """The whole-spacecraft wizard must expose only its own verified effect profiles."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    from importlib.metadata import version
    assert version("bsk") == "2.11.0"
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
                response = client.request(
                    request.method,
                    request_path,
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items() if key in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="fault_run"]').click()
            page.locator('#wizardOptions [data-wizard-level="whole_spacecraft"]').click()
            object_option = page.locator('#wizardOptions [data-wizard-object="whole_spacecraft"]')
            object_option.wait_for(state="visible", timeout=15000)
            object_option.click()

            fault_options = page.locator("#wizardOptions [data-wizard-fault]")
            fault_options.first.wait_for(state="visible", timeout=15000)
            assert set(fault_options.evaluate_all("(items) => items.map((item) => item.getAttribute('data-wizard-fault'))")) == {
                "payload_instrument_off",
                "comm_data_downlink_link_loss",
                "eps_battery_capacity_loss",
                "adcs_rw_jamming",
                "adcs_rw_motor_failure",
                "gyro_bias_step",
                "propulsion_thruster_ignition_failure",
            }

            page.locator('#wizardOptions [data-wizard-fault="payload_instrument_off"]').click()
            page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)
            fault_panel = page.locator("#wizardFaultPanel")
            assert not fault_panel.evaluate("(node) => node.classList.contains('hidden')")
            assert fault_panel.get_attribute("open") is not None

            expected = {
                "fault": {
                    "payload_instrument_off",
                    "comm_data_downlink_link_loss",
                    "eps_battery_capacity_loss",
                    "adcs_rw_jamming",
                    "adcs_rw_motor_failure",
                    "gyro_bias_step",
                    "propulsion_thruster_ignition_failure",
                },
                "degradation": {
                    "solar_panel_efficiency_loss",
                    "thermal_radiator_rejection_loss",
                    "gyro_noise_increase",
                    "adcs_rw_torque_authority_loss",
                    "rw_friction_degradation",
                    "propulsion_burn_impulse_loss",
                },
                "constraint": {
                    "power_safe_mode_threshold",
                    "adcs_reaction_wheel_speed_limit",
                },
            }
            for kind, expected_effects in expected.items():
                page.locator("#effectKind").select_option(kind)
                actual = set(page.locator("#effectSelect option").evaluate_all("(items) => items.map((item) => item.value)"))
                assert actual == expected_effects, (kind, actual)
                assert "adcs_imu_signal_loss" not in actual
            assert page.locator("#eventList .event-card").count() == 1
            assert not errors, errors
            page.close()
        finally:
            browser.close()




@pytest.mark.parametrize(
    ("case_id", "event_kind", "event_effect", "event_parameters"),
    [
        ("nominal", None, None, {}),
        ("adcs_rw_jamming", "fault", "adcs_rw_jamming", {"wheel_index": 0, "brake_torque_nm": 0.2}),
        ("adcs_rw_motor_failure", "fault", "adcs_rw_motor_failure", {"wheel_index": 0}),
        ("gyro_bias_step", "fault", "gyro_bias_step", {"bias_step_deg_s": [0.5, 0.0, 0.0]}),
        ("gyro_noise_increase", "degradation", "gyro_noise_increase", {"noise_scale": 100.0}),
        ("adcs_rw_torque_authority_loss", "degradation", "adcs_rw_torque_authority_loss", {"wheel_index": 0, "torque_scale": 0.25}),
        ("rw_friction_degradation", "degradation", "rw_friction_degradation", {"wheel_index": 0, "drag_nms": 0.012}),
        ("adcs_reaction_wheel_speed_limit", "constraint", "adcs_reaction_wheel_speed_limit", {"wheel_index": 0, "max_speed_rad_s": 5.0}),
        ("propulsion_thruster_ignition_failure", "fault", "propulsion_thruster_ignition_failure", {}),
        ("propulsion_burn_impulse_loss", "degradation", "propulsion_burn_impulse_loss", {"remaining_impulse_ratio": 0.25}),
    ],
    ids=[
        "nominal",
        "adcs_rw_jamming",
        "adcs_rw_motor_failure",
        "gyro_bias_step",
        "gyro_noise_increase",
        "adcs_rw_torque_authority_loss",
        "rw_friction_degradation",
        "adcs_reaction_wheel_speed_limit",
        "propulsion_thruster_ignition_failure",
        "propulsion_burn_impulse_loss",
    ],
)
def test_guided_whole_spacecraft_event_run_bundle(
    tmp_path,
    case_id,
    event_kind,
    event_effect,
    event_parameters,
):
    """Each whole-spacecraft event must complete a real Chromium-to-Basilisk sealed Run."""
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
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
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                request_path = url.path + ("?" + url.query if url.query else "")
                response = client.request(
                    request.method,
                    request_path,
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items() if key in ("content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")},
                )

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('#wizardOptions [data-wizard-goal="fault_run"]').click()
            page.locator('#wizardOptions [data-wizard-level="whole_spacecraft"]').click()
            object_option = page.locator('#wizardOptions [data-wizard-object="whole_spacecraft"]')
            object_option.wait_for(state="visible", timeout=15000)
            object_option.click()

            fault_options = page.locator("#wizardOptions [data-wizard-fault]")
            fault_options.first.wait_for(state="visible", timeout=15000)
            assert set(fault_options.evaluate_all(
                "(items) => items.map((item) => item.getAttribute('data-wizard-fault'))"
            )) == {
                "payload_instrument_off",
                "comm_data_downlink_link_loss",
                "eps_battery_capacity_loss",
                "adcs_rw_jamming",
                "adcs_rw_motor_failure",
                "gyro_bias_step",
                "propulsion_thruster_ignition_failure",
            }

            # Use one verified quick-start fault only to enter the configuration step.
            page.locator('#wizardOptions [data-wizard-fault="payload_instrument_off"]').click()
            page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
            page.locator("#wizardConfigSections").wait_for(state="visible", timeout=15000)
            fault_panel = page.locator("#wizardFaultPanel")
            assert not fault_panel.evaluate("(node) => node.classList.contains('hidden')")
            if fault_panel.get_attribute("open") is None:
                fault_panel.locator(":scope > summary").click()

            expected_catalog = {
                "fault": {
                    "payload_instrument_off",
                    "comm_data_downlink_link_loss",
                    "eps_battery_capacity_loss",
                    "adcs_rw_jamming",
                    "adcs_rw_motor_failure",
                    "gyro_bias_step",
                    "propulsion_thruster_ignition_failure",
                },
                "degradation": {
                    "solar_panel_efficiency_loss",
                    "thermal_radiator_rejection_loss",
                    "gyro_noise_increase",
                    "adcs_rw_torque_authority_loss",
                    "rw_friction_degradation",
                    "propulsion_burn_impulse_loss",
                },
                "constraint": {
                    "power_safe_mode_threshold",
                    "adcs_reaction_wheel_speed_limit",
                },
            }
            for kind, expected_effects in expected_catalog.items():
                page.locator("#effectKind").select_option(kind)
                actual = set(page.locator("#effectSelect option").evaluate_all(
                    "(items) => items.map((item) => item.value)"
                ))
                assert actual == expected_effects, (kind, actual)
                assert "adcs_imu_signal_loss" not in actual

            # Remove the quick-start event. The journey below must run only its
            # requested effect, or no event at all for the nominal control.
            starter = page.locator("#eventList .event-card").first
            starter.wait_for(state="visible", timeout=10000)
            starter.locator("button.remove-event").click()
            assert page.locator("#eventList .event-card").count() == 0

            if event_effect:
                page.locator("#effectKind").select_option(event_kind)
                page.locator("#effectSelect").select_option(event_effect)
                page.locator("#addEffectBtn").click()
                event_card = page.locator("#eventList .event-card").first
                event_card.wait_for(state="visible", timeout=10000)
                assert event_card.get_attribute("data-event-kind") == event_kind

                def set_event_field(field: str, value) -> None:
                    control = event_card.locator(f'input[data-event-field="{field}"]')
                    control.wait_for(state="visible", timeout=10000)
                    serialized = json.dumps(value) if isinstance(value, (list, tuple)) else str(value)
                    control.fill(serialized)
                    control.dispatch_event("change")

                set_event_field("start_s", 4.0)
                set_event_field("end_s", 10.0)
                for name, value in event_parameters.items():
                    set_event_field(f"parameters.{name}", value)

            parameters_panel = page.locator("#wizardParametersPanel")
            if parameters_panel.get_attribute("open") is None:
                parameters_panel.locator(":scope > summary").click()
            # simulation.step_s is intentionally classified as an advanced field.
            # Use the same explicit "全部参数" interaction as a real operator so
            # the advanced control is rendered open rather than force-opening DOM nodes.
            page.locator('#wizardParametersPanel [data-wizard-parameter-mode="all"]').click()
            form = page.locator("#wizardParameterHost #dynamicForm")
            form.wait_for(state="attached", timeout=15000)
            step_field = page.locator(
                '#wizardParameterHost #dynamicForm [data-field-path="simulation.step_s"] input'
            )
            step_field.wait_for(state="visible", timeout=10000)

            def set_form_value(field_path: str, value) -> None:
                wrapper = page.locator(
                    f'#wizardParameterHost #dynamicForm [data-field-path="{field_path}"]'
                )
                wrapper.wait_for(state="attached", timeout=10000)
                wrapper.evaluate(
                    "(node) => { for (let parent = node; parent; parent = parent.parentElement) "
                    "if (parent.tagName === 'DETAILS') parent.open = true; }"
                )
                control = wrapper.locator("input, select, textarea").first
                control.wait_for(state="visible", timeout=10000)
                if isinstance(value, bool):
                    if value and not control.is_checked():
                        control.check()
                    elif not value and control.is_checked():
                        control.uncheck()
                else:
                    control.fill(str(value))
                    control.dispatch_event("change")

            set_form_value("simulation.duration_s", 14.0)
            set_form_value("simulation.step_s", 0.2)
            set_form_value("simulation.sample_s", 1.0)

            if case_id.startswith("propulsion_"):
                parameter_values = {
                    "initial_soc": 0.9,
                    "propulsion_enabled": True,
                    "propulsion_burn_start_s": 5.0,
                    "propulsion_burn_on_time_s": 8.0,
                    "propulsion_thrust_n": 5.0,
                    "propulsion_isp_s": 200.0,
                    "propulsion_initial_propellant_kg": 1.0,
                    "propulsion_tank_capacity_kg": 2.0,
                    "propulsion_min_burn_soc": 0.05,
                }
            else:
                parameter_values = {
                    "initial_soc": 0.75,
                    "rw_initial_speed_rad_s_0": 10.471975511965978,
                    "rw_initial_speed_rad_s_1": 20.943951023931955,
                    "rw_initial_speed_rad_s_2": 31.41592653589793,
                }
            for name, value in parameter_values.items():
                set_form_value(f"parameters.values.{name}", value)

            plot_fields_by_case = {
                "nominal": ["orbit.radius_m", "eps.battery_soc"],
                "adcs_rw_jamming": ["adcs.rw.effective_torque_ratio_0"],
                "adcs_rw_motor_failure": ["adcs.rw.effective_max_torque_nm_0"],
                "gyro_bias_step": ["adcs.sensor.gyro_bias_norm_rad_s"],
                "gyro_noise_increase": ["adcs.sensor.gyro_noise_norm_rad_s"],
                "adcs_rw_torque_authority_loss": ["adcs.rw.effective_torque_ratio_0"],
                "rw_friction_degradation": ["adcs.rw.effective_drag_nms_0"],
                "adcs_reaction_wheel_speed_limit": ["adcs.rw.effective_max_speed_rad_s_0"],
                "propulsion_thruster_ignition_failure": [
                    "propulsion.burn_command_on_time_s",
                    "propulsion.total_thrust_n",
                    "propulsion.propellant_used_kg",
                ],
                "propulsion_burn_impulse_loss": [
                    "propulsion.burn_command_on_time_s",
                    "propulsion.nominal_burn_command_on_time_s",
                    "propulsion.total_thrust_n",
                    "propulsion.propellant_used_kg",
                ],
            }
            plot_fields = plot_fields_by_case[case_id]
            for plot_field in sorted(plot_fields):
                plot_search = page.locator("#wizardOutputSearch")
                plot_search.fill(plot_field)
                plot_card = page.locator("#wizardCurveChoices .wizard-curve-card").filter(has_text=plot_field)
                plot_card.wait_for(state="visible", timeout=10000)
                curve_detail = plot_card.locator("details.wizard-curve-detail")
                if curve_detail.get_attribute("open") is None:
                    curve_detail.locator(":scope > summary").click()
                plot_option = curve_detail.locator("label.wizard-curve-row").filter(has_text=plot_field)
                plot_option.wait_for(state="visible", timeout=10000)
                plot_checkbox = plot_option.locator('input[type="checkbox"]')
                if not plot_checkbox.is_checked():
                    plot_checkbox.check()
                plot_search.fill("")

            # FMEA is an explicit optional output. Enable it through the guided
            # output control so the resulting TaskSpec and Run Bundle include it.
            fmea_toggle = page.locator("#wizardFmeaEnabled")
            fmea_toggle.wait_for(state="visible", timeout=10000)
            if not fmea_toggle.is_checked():
                fmea_toggle.check()

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
                if row.get("primary_capability_id") == "whole_spacecraft.unified_native.v1"
                and row.get("status") == "SUCCEEDED"
            ]
            assert len(matching) == 1, matching
            run_id = matching[0]["run_id"]
            detail = client.get(f"/runs/{run_id}")
            assert detail.status_code == 200, detail.text
            durable = detail.json()["run"]
            assert durable["task_spec"]["model"]["capability_id"] == "whole_spacecraft.unified_native.v1"
            assert durable["task_spec"]["simulation"]["level"] == "whole_spacecraft"
            assert durable["task_spec"]["simulation"]["backend"] == "basilisk"
            assert durable["task_spec"]["assurance"]["allow_proxy"] is False
            assert durable["sealed"] is True
            assert durable["integrity"]["ok"] is True
            assert durable["validation_outcome"]["result"] == "PASS"
            assert durable["task_spec"]["outputs"]["fmea"]["enabled"] is True
            assert set(durable["task_spec"]["outputs"]["fmea"]["formats"]) == {"csv", "json"}

            if event_effect:
                event_key = {
                    "fault": "faults",
                    "degradation": "degradations",
                    "constraint": "constraints",
                }[event_kind]
                events = durable["task_spec"]["events"][event_key]
                assert len(events) == 1, events
                assert events[0]["effect"] == event_effect
                from sat_sim.capability_registry import get_capability
                whole_contract = get_capability("whole_spacecraft.unified_native.v1").data
                expected_owner = next(
                    item["owner"]
                    for item in whole_contract["operator"]["effects"]
                    if item["effect_id"] == event_effect
                )
                assert events[0]["target"] == expected_owner
                assert events[0]["start_s"] == 4.0
                assert events[0]["end_s"] == 10.0
                assert events[0]["parameters"] == event_parameters
                evidence = durable["validation_outcome"]["injection_evidence"]
                assert len(evidence) == 1, evidence
                assert evidence[0]["requested_effect"] == event_effect
                assert evidence[0]["delivery_result"] == "PASS"
                assert evidence[0]["effect_result"] == "PASS"
                assert "RUNTIME_EFFECT_VERIFIED" in evidence[0]["reason_codes"]
            else:
                assert not durable["task_spec"]["events"]["faults"]
                assert not durable["task_spec"]["events"]["degradations"]
                assert not durable["task_spec"]["events"]["constraints"]
                assert durable["validation_outcome"]["injection_evidence"] == []

            summary = durable["summary"]
            assert summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
            assert summary["external_or_proxy_module_count"] == 0

            telemetry = client.get(f"/runs/{run_id}/telemetry?limit=500")
            assert telemetry.status_code == 200, telemetry.text
            rows = telemetry.json()["rows"]
            assert rows
            if event_effect:
                active_label = {
                    "fault": "label.fault_active",
                    "degradation": "label.degradation_active",
                    "constraint": "label.constraint_active",
                }[event_kind]
                active_rows = [
                    row for row in rows
                    if 4.0 <= float(row["time_s"]) <= 10.0 and row.get(active_label) is True
                ]
                assert active_rows, rows
                assert any(event_effect in str(row.get("event.active_effects", "")) for row in active_rows)
                active = active_rows[-1]
                if case_id == "propulsion_burn_impulse_loss":
                    # The 25% impulse profile shortens the 5 s nominal burn to
                    # 2 s. Sample inside that physical burn window, not at the
                    # later event-window end where thrust must already be zero.
                    active = min(active_rows, key=lambda row: abs(float(row["time_s"]) - 6.0))
                    assert 5.0 <= float(active["time_s"]) < 7.0
                if case_id == "adcs_rw_jamming":
                    assert active["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.0, abs=1e-12)
                elif case_id == "adcs_rw_motor_failure":
                    assert active["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(0.0, abs=1e-12)
                elif case_id == "gyro_bias_step":
                    assert active["adcs.sensor.gyro_bias_norm_rad_s"] > 0.0
                elif case_id == "gyro_noise_increase":
                    assert active["adcs.sensor.gyro_noise_norm_rad_s"] > 0.0
                elif case_id == "adcs_rw_torque_authority_loss":
                    assert active["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.25)
                elif case_id == "rw_friction_degradation":
                    assert active["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.012, rel=1e-9)
                elif case_id == "adcs_reaction_wheel_speed_limit":
                    assert active["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(5.0)
                    assert any(
                        abs(row["adcs.rw.speed_rad_s_0"]) > 5.0
                        and abs(row["adcs.control.applied_torque_nm_0"]) > 0.0
                        for row in active_rows
                    )
                elif case_id == "propulsion_thruster_ignition_failure":
                    assert active["propulsion.burn_command_on_time_s"] == pytest.approx(0.0, abs=1e-12)
                    assert active["propulsion.nominal_burn_command_on_time_s"] == pytest.approx(8.0)
                    assert active["propulsion.total_thrust_n"] == pytest.approx(0.0, abs=1e-12)
                    assert active["propulsion.propellant_used_kg"] == pytest.approx(0.0, abs=1e-12)
                elif case_id == "propulsion_burn_impulse_loss":
                    assert active["propulsion.burn_command_on_time_s"] == pytest.approx(2.0)
                    assert active["propulsion.nominal_burn_command_on_time_s"] == pytest.approx(8.0)
                    assert active["propulsion.total_thrust_n"] > 0.0
                    assert active["propulsion.propellant_used_kg"] > 0.0
            else:
                assert not any(
                    row.get("label.fault_active") or row.get("label.degradation_active") or row.get("label.constraint_active")
                    for row in rows
                )

            plots = client.get(f"/runs/{run_id}/plots")
            assert plots.status_code == 200, plots.text
            actual_plot_fields = {item["field"] for item in plots.json()["plot_manifest"]["series"]}
            assert set(plot_fields) <= actual_plot_fields, (plot_fields, actual_plot_fields)
            fmea = client.get(f"/runs/{run_id}/fmea")
            assert fmea.status_code == 200, fmea.text
            assert "no risk score is fabricated" in fmea.json()["manifest"]["warning"]
            assert fmea.json()["run_id"] == run_id

            with page.expect_download(timeout=30000) as bundle_download_info:
                page.locator("#downloadRunBundleBtn").click()
            bundle_download = bundle_download_info.value
            bundle_path = tmp_path / f"{case_id}_whole_spacecraft_run_bundle.zip"
            bundle_download.save_as(str(bundle_path))
            with zipfile.ZipFile(bundle_path) as archive:
                prefix = f"{run_id}/"
                manifest_name = f"{prefix}bundle_manifest.json"
                seal_name = f"{prefix}SEALED.json"
                archive_names = set(archive.namelist())
                manifest_bytes = archive.read(manifest_name)
                manifest = json.loads(manifest_bytes)
                seal = json.loads(archive.read(seal_name))
                expected = {f"{prefix}{relative}" for relative in manifest["files"]}
                assert archive_names == expected | {manifest_name, seal_name}
                assert seal["run_id"] == run_id
                assert seal["bundle_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
                for relative, entry in manifest["files"].items():
                    assert hashlib.sha256(archive.read(f"{prefix}{relative}")).hexdigest() == entry["sha256"]

            # Keep the browser-downloaded original ZIP outside tmp_path so the
            # official O3-D artifact can independently re-check its seal and hashes.
            browser_archive_dir = Path("reports/native_whole_spacecraft_events/browser_run_bundles")
            browser_archive_dir.mkdir(parents=True, exist_ok=True)
            archived_zip = browser_archive_dir / f"{case_id}.zip"
            shutil.copy2(bundle_path, archived_zip)
            archived_zip_sha256 = hashlib.sha256(archived_zip.read_bytes()).hexdigest()
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
            sidecar = {
                "schema_version": "p9-o3.browser-run-bundle.v1",
                "case_id": case_id,
                "run_id": run_id,
                "event_kind": event_kind,
                "event_effect": event_effect,
                "bundle_zip_sha256": archived_zip_sha256,
                "bundle_manifest_sha256": manifest_sha256,
                "sealed": (
                    seal.get("run_id") == run_id
                    and seal.get("bundle_manifest_sha256") == manifest_sha256
                ),
                "integrity_ok": durable["integrity"]["ok"],
            }
            assert sidecar["sealed"] is True and sidecar["integrity_ok"] is True, sidecar
            (browser_archive_dir / f"{case_id}.json").write_text(
                json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            page.locator("#wizardOpenRunCenterBtn").wait_for(state="visible", timeout=10000)
            page.locator("#wizardOpenRunCenterBtn").click()
            page.locator("#runCenterView").wait_for(state="visible", timeout=15000)
            history_row = page.locator("#runCenterTableBody tr").filter(has_text=run_id)
            history_row.wait_for(state="visible", timeout=15000)
            history_row.get_by_role("button", name="查看").click()
            page.locator("#creatorView").wait_for(state="visible", timeout=20000)
            replayed = page.locator("#activeRunCard")
            replayed.wait_for(state="visible", timeout=15000)
            run_metadata = replayed.locator("details.compact-run-metadata")
            run_metadata.locator("summary").click()
            assert run_id in replayed.inner_text()
            page.locator('[data-result-tab="charts"]').click()
            page.wait_for_function(
                "() => document.querySelector('#chartLegend')?.children.length > 0",
                timeout=15000,
            )
            assert not errors, errors
            page.close()
        finally:
            browser.close()
    assert not errors, errors
