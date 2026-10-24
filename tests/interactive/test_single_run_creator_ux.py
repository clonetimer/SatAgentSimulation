"""Finite one-shot workbench UX and read-only native preflight acceptance.

The browser-only test intentionally does NOT claim that fake UI routing is
physical Basilisk output. Real native finite-duration execution has its own
tests/interactive/test_unified_segmented_execution.py gate.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _client(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_single_run_readiness_never_claims_a_physical_run_passed(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/workbench/single-run/readiness")
        page = client.get("/").text
        js = client.get("/assets/app.js").text
        css = client.get("/assets/styles.css").text
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["schema_version"] == "sat-sim.single-run-workbench-readiness.v1"
    assert payload["simulation_mode"] == "finite_single_run"
    assert payload["execution_protocol"] == "taskspec_prepare_then_persistent_async_run"
    assert payload["native_basilisk"]["preflight_only"] is True
    assert type(payload["native_basilisk"]["ready"]) is bool
    assert payload["native_execution_result_proven"] is False
    assert payload["automatic_cross_process_resume_supported"] is False
    assert payload["automatic_tc_replay_allowed"] is False
    assert payload["endpoints"]["submit"] == "/runs"
    assert payload["endpoints"]["track"] == "/runs/{run_id}/execution"
    for control in ("singleRunGuide", "singleRunReadiness", "singleRunSelectBtn",
                    "singleRunValidateBtn", "singleRunResultsBtn", "singleRunProgress",
                    "singleRunConfigSummary", "singleRunJumpParametersBtn",
                    "singleRunJumpEventsBtn", "singleRunJumpOutputsBtn", "formSearchEmpty"):
        assert f'id="{control}"' in page
    assert "singleRunSubmitting" in js
    assert "if (state.singleRunSubmitting) return;" in js
    assert "state.activeRunId !== runId" in js
    assert ".single-run-steps" in css
    assert ".single-run-config-summary" in css
    assert "配置已修改；上次校验已失效" in js
    assert "旧校验结果已丢弃" in js
    assert "runLoadRequestId" in js
    assert "requestId !== state.runLoadRequestId" in js
    assert "applyChartTimeWindow" in js
    assert ".chart-window-controls" in css
    for control in ("chartTimeStart", "chartTimeEnd", "chartApplyWindowBtn",
                    "chartResetWindowBtn", "chartWindowStatus"):
        assert f'id="{control}"' in page
    assert "collectEventInputIssues" in js
    assert "必须填写整数" in js
    assert "数组必须包含有效数值" in js
    assert ".event-field-invalid" in css


def test_single_run_browser_path_has_visible_steps_and_no_auto_execute(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    calls = []
    parse_calls = []
    with _client(tmp_path) as client, pw.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1480, "height": 950}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                req = route.request
                u = urlsplit(req.url)
                assert u.netloc == "workbench.test"
                path = u.path + ("?" + u.query if u.query else "")
                if req.method == "POST" and u.path == "/runs":
                    calls.append("POST /runs")
                if req.method == "POST" and u.path == "/tasks/parse":
                    parse_calls.append("POST /tasks/parse")
                response = client.request(
                    req.method, path,
                    content=req.post_data.encode("utf-8") if req.post_data else None,
                    headers={k: v for k, v in req.headers.items()
                             if k.lower() in ("authorization", "content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code, body=response.content,
                    headers={k: v for k, v in response.headers.items()
                             if k.lower() in ("content-type", "cache-control")},
                )

            page.route("https://workbench.test/**", route_request)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#singleRunGuide[data-stage='configure']").wait_for(timeout=30000)
            assert page.locator("#singleRunGuide .single-run-step").count() == 3
            assert "Basilisk" in page.locator("#singleRunReadiness").inner_text()
            assert page.locator("#runBtn").is_enabled()
            assert "运行时长" in page.locator("#singleRunConfigSummary").inner_text()
            assert "结果输出" in page.locator("#singleRunConfigSummary").inner_text()

            page.locator("#singleRunSelectBtn").click()
            assert page.locator("#capabilitySearch").evaluate(
                "(field) => document.activeElement === field"
            )

            page.locator("#singleRunResultsBtn").click()
            assert page.locator("#runCenterView").is_visible()
            page.locator(".nav-button[data-view='creator']").click()
            assert page.locator("#creatorView").is_visible()
            assert page.locator("#singleRunGuide").is_visible()

            page.locator("#singleRunValidateBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            # A schema preflight never creates a persisted physics execution.
            assert calls == []
            assert page.locator("#singleRunProgress").inner_text().strip()

            # The overview must reflect real edited values, not placeholder
            # estimates. Configuration edits do not POST a physical run.
            duration = page.locator("#dynamicForm [data-path='simulation.duration_s']")
            assert duration.count() == 1
            previous_duration = duration.input_value()
            new_duration = "61" if previous_duration == "60" else "60"
            duration.fill(new_duration)
            duration.press("Tab")
            assert f"{new_duration} s" in page.locator("#singleRunConfigSummary").inner_text()
            assert page.locator("#singleRunGuide").get_attribute("data-stage") == "configure"
            page.locator("#formFieldSearch").fill("__no_such_registered_parameter__")
            assert page.locator("#formSearchEmpty").is_visible()
            page.locator("#formFieldSearch").fill("")
            assert page.locator("#formSearchEmpty").is_hidden()
            page.locator("#singleRunJumpEventsBtn").click()
            assert page.locator("#effectKind").is_visible()
            page.locator("#singleRunJumpOutputsBtn").click()
            assert page.locator("#plotOptions").is_visible()
            assert calls == []

            # Fault event fields must reject invalid numbers instead of
            # silently retaining the last valid TaskSpec parameter.
            page.locator("#effectKind").select_option("fault")
            assert page.locator("#addEffectBtn").is_enabled()
            page.locator("#addEffectBtn").click()
            first_event = page.locator("#eventList .event-card").first
            start = first_event.locator('[data-event-field="start_s"]')
            before_invalid_parse = len(parse_calls)
            start.fill("-1")
            start.press("Tab")
            assert start.get_attribute("aria-invalid") == "true"
            assert first_event.locator(".event-input-error:visible").count() >= 1
            page.locator("#singleRunValidateBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            assert "事件配置" in page.locator("#formValidationSummary").inner_text()
            assert len(parse_calls) == before_invalid_parse
            page.locator("#runBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            assert calls == []
            assert len(parse_calls) == before_invalid_parse

            start.fill("2")
            start.press("Tab")
            end = first_event.locator('[data-event-field="end_s"]')
            end.fill("1")
            end.press("Tab")
            page.locator("#singleRunValidateBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            assert "结束时间不能早于开始时间" in page.locator("#formValidationSummary").inner_text()
            assert len(parse_calls) == before_invalid_parse
            end.fill("3")
            end.press("Tab")
            assert calls == []

            # The fidelity ADCS variant (not the unified whole-spacecraft
            # variant) exposes the vector3_or_number gyro bias event field.
            # This is UI-only validation, not acceptance of proxy physics.
            page.locator("#levelFilter").select_option("subsystem")
            page.locator("#capabilityList [data-object-id='subsystem.adcs']").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            fidelity_option = page.locator("#variantSelect option[value='subsystem.adcs_fidelity.v1']")
            if fidelity_option.count() == 0:
                page.locator("#showCompatibilityCapabilities").check()
                fidelity_option.wait_for(timeout=20000)
            page.locator("#variantSelect").select_option("subsystem.adcs_fidelity.v1")
            page.wait_for_function(
                "() => [...document.querySelectorAll('#effectSelect option')].some(o => o.value === 'adcs_gyro_bias_step')",
                timeout=20000,
            )
            page.locator("#effectKind").select_option("fault")
            page.locator("#effectSelect").select_option("adcs_gyro_bias_step")
            page.locator("#addEffectBtn").click()
            vector = page.locator("#eventList .event-card").last.locator('[data-event-field="parameters.bias_deg_s"]')
            assert vector.count() == 1
            vector.fill("1,,2")
            vector.press("Tab")
            assert vector.get_attribute("aria-invalid") == "true"
            page.locator("#singleRunValidateBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#busyOverlay').classList.contains('hidden')",
                timeout=20000,
            )
            assert "数组元素不能为空" in page.locator("#formValidationSummary").inner_text()
            assert len(parse_calls) == before_invalid_parse
            vector.fill("[0.1, 0, 0]")
            vector.press("Tab")
            assert vector.get_attribute("aria-invalid") == "false"
            assert calls == []

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("#singleRunGuide").is_visible()
            assert page.locator("#singleRunGuide .single-run-step").count() == 3
            assert not errors, errors
        finally:
            browser.close()


def test_single_run_history_chart_window_and_failure_ui(tmp_path):
    """Synthetic transport fixtures exercise UI only, never physics acceptance."""
    pw = pytest.importorskip("playwright.sync_api")
    payloads = {
        "ui-a": {
            "name": "UI 曲线验收 A", "status": "SUCCEEDED", "validation": "PASS",
            "error": "", "times": [0, 1, 2, 3], "values": [4, 3, 2, 1],
        },
        "ui-b": {
            "name": "UI 失败验收 B", "status": "FAILED", "validation": "NOT_EVALUATED",
            "error": "UI-only synthetic failure", "times": [10, 20, 30],
            "values": [2, 3, 1],
        },
    }

    with _client(tmp_path) as client, pw.sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1480, "height": 950}, locale="zh-CN")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route_request(route):
                request = route.request
                url = urlsplit(request.url)
                path = url.path
                if request.method == "GET" and path == "/runs" and url.query == "limit=30":
                    route.fulfill(json={"runs": [
                        {"run_id": rid, "task_name": item["name"], "status": item["status"],
                         "validation_result": item["validation"]}
                        for rid, item in payloads.items()
                    ]})
                    return

                parts = path.strip("/").split("/")
                if request.method == "GET" and len(parts) >= 2 and parts[0] == "runs" and parts[1] in payloads:
                    rid = parts[1]
                    item = payloads[rid]
                    if len(parts) == 2:
                        response = {"run": {
                            "sealed": item["status"] == "SUCCEEDED",
                            "task_spec": {"task": {"name": item["name"]}},
                            "run_record": {"run_id": rid, "task_id": rid, "status": item["status"],
                                           "error": item["error"]},
                            "validation_outcome": {"result": item["validation"]},
                            "integrity": {"ok": item["status"] == "SUCCEEDED"},
                        }}
                    elif parts[2] == "plots":
                        response = {"plot_manifest": {
                            "time_axis": {"field": "time_s", "label": "仿真时间"},
                            "series": [{"field": "adcs.pointing_error_deg", "label": "姿态误差"}],
                        }}
                    elif parts[2] == "telemetry":
                        response = {"rows": [
                            {"time_s": t, "adcs.pointing_error_deg": v}
                            for t, v in zip(item["times"], item["values"])
                        ]}
                    elif parts[2] == "metrics":
                        response = {"metrics": {}}
                    elif parts[2] == "artifacts":
                        response = {"artifacts": []}
                    else:
                        response = {}
                    route.fulfill(json=response)
                    return

                assert request.method != "POST" or path != "/runs"
                forwarded = client.request(
                    request.method, path + (f"?{url.query}" if url.query else ""),
                    content=request.post_data.encode("utf-8") if request.post_data else None,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() in ("authorization", "content-type", "accept")},
                )
                route.fulfill(
                    status=forwarded.status_code, body=forwarded.content,
                    headers={k: v for k, v in forwarded.headers.items()
                             if k.lower() in ("content-type", "cache-control")},
                )

            page.route("https://workbench.test/**", route_request)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#runList .run-list-item").first.wait_for(timeout=30000)
            page.locator("#runList [data-run-id='ui-a']").click()
            page.locator("#activeRunCard .run-id").get_by_text("UI 曲线验收 A").wait_for(timeout=20000)
            assert page.locator("#runList [data-run-id='ui-a']").get_attribute("aria-current") == "true"
            page.locator(".result-tab[data-result-tab='charts']").click()
            assert "4 / 4" in page.locator("#chartWindowStatus").inner_text()

            page.locator("#chartTimeStart").fill("1")
            page.locator("#chartTimeEnd").fill("2")
            page.locator("#chartApplyWindowBtn").click()
            assert "2 / 4" in page.locator("#chartWindowStatus").inner_text()
            assert "局部范围" in page.locator("#chartWindowStatus").inner_text()
            assert page.locator("#chartResetWindowBtn").is_enabled()

            page.locator("#chartTimeStart").fill("-1")
            page.locator("#chartApplyWindowBtn").click()
            assert "范围无效" in page.locator("#chartWindowStatus").inner_text()
            page.locator("#chartResetWindowBtn").click()
            assert "4 / 4" in page.locator("#chartWindowStatus").inner_text()
            assert page.locator("#chartResetWindowBtn").is_disabled()

            page.locator("#runList [data-run-id='ui-b']").click()
            page.locator("#activeRunCard .run-id").get_by_text("UI 失败验收 B").wait_for(timeout=20000)
            assert "UI-only synthetic failure" in page.locator("#activeRunCard").inner_text()
            assert page.locator("#runList [data-run-id='ui-b']").get_attribute("aria-current") == "true"
            page.locator(".result-tab[data-result-tab='charts']").click()
            assert "3 / 3" in page.locator("#chartWindowStatus").inner_text()
            assert page.locator("#chartTimeStart").input_value() == "10"
            assert not errors, errors
        finally:
            browser.close()
