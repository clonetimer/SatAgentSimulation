"""P9-L4 genuine offline journey: wizard UI -> Basilisk Run -> sealed scene replay."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import pytest


def _get_json(base: str, path: str) -> dict:
    with urlopen(base + path, timeout=20) as response:
        return json.load(response)


@pytest.mark.parametrize("entry", ["default", "custom", "json", "scene", "graph", "natural_language"])
def test_real_whole_spacecraft_offline_run_created_in_ui_and_restored_in_scene(tmp_path, entry):
    """The user creates and runs a finite task in the actual UI; no API fixture."""
    if os.environ.get("SAT_SIM_P9L4_NATIVE_UI_TEST") != "1":
        pytest.skip("Dedicated official Basilisk + Chromium P9-L4 job only")
    if entry == "natural_language" and not os.environ.get("SAT_SIM_DT_MODEL_URL"):
        pytest.skip("Requires an actual local model service")

    custom_rates = entry != "default"
    import Basilisk
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    runs_root = tmp_path / "runs"
    evidence = Path(os.environ.get("SAT_SIM_P9L4_EVIDENCE", str(tmp_path)))
    if custom_rates:
        evidence = evidence / entry
    evidence.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "SAT_SIM_INTERACTIVE_ENABLED": "1",
        "SAT_SIM_SUPERVISED_NATIVE_RUNTIME": "0",
        "SAT_SIM_AUTH_MODE": "disabled",
        "SAT_SIM_EMBEDDED_WORKER": "1",
        "SAT_SIM_RUNS_ROOT": str(runs_root),
        "SAT_SIM_API_ARTIFACTS_ROOT": str(tmp_path / "api"),
    }
    for key in (
        "SAT_SIM_QUEUE_DATABASE", "SAT_SIM_TASK_CENTER_DATABASE",
        "SAT_SIM_EXPERIMENT_DATABASE", "SAT_SIM_AUDIT_LOG",
        "SAT_SIM_WORKBENCH_LOG", "SAT_SIM_MODEL_PROVIDERS_FILE",
    ):
        env.pop(key, None)

    report = {
        "status": "RUNNING",
        "mode": "offline_finite_whole_spacecraft",
        "basilisk_version": Basilisk.__version__,
        "creation_origin": "real Chromium product wizard",
        "execution_origin": "POST /runs via existing embedded execution queue",
        "telemetry_injection": False,
        "page_errors": [],
        "console_errors": [],
        "request_failures": [],
    }
    server_log = (evidence / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sat_sim.api:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=Path(__file__).resolve().parents[1],
        env=env, stdout=server_log, stderr=subprocess.STDOUT,
    )
    browser = None
    page = None
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "P9L4_OFFLINE_API_EXITED_BEFORE_READY"
            try:
                _get_json(base, "/health")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("P9L4_OFFLINE_API_STARTUP_TIMEOUT")
                time.sleep(0.2)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"),
                args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
            )
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("console", lambda message: report["console_errors"].append(message.text)
                    if message.type == "error" else None)
            page.on("requestfailed", lambda request: report["request_failures"].append({
                "url": request.url, "failure": request.failure,
            }))
            page.on("dialog", lambda dialog: dialog.accept())
            if entry == "natural_language":
                from dt_creation_browser import register_local_model
                register_local_model(page, base)
            page.goto(base, wait_until="domcontentloaded")

            source_spec = None
            rates = dict(zip("xyz", [-0.003, 0.0, 0.006] if custom_rates else [0.01, -0.004, 0.002]))
            if entry == "graph":
                from dt_creation_browser import configure_graph_case
                configure_graph_case(page)
                configured_step_s = 0.1
                submitted_duration_s = 20
                report["task_spec_validated"] = True
                run_button = page.locator("#graphRunBtn")
            else:
                if entry == "json":
                    from dt_creation_browser import apply_json_case
                    source_spec = apply_json_case(page)
                elif entry == "natural_language":
                    from dt_creation_browser import apply_natural_language_case
                    source_spec = apply_natural_language_case(page, evidence)
                else:
                    if entry == "scene":
                        page.locator("#wizardSceneShortcutBtn").click()
                        page.locator("#sceneCreateTaskBtn").click()
                        page.locator('[data-scene-create="offline"]').click()
                    else:
                        page.locator('#wizardOptions [data-wizard-goal="normal_run"]').click()
                    page.locator('#wizardOptions [data-wizard-level="whole_spacecraft"]').wait_for(
                        state="visible", timeout=30000
                    )
                    page.locator('#wizardOptions [data-wizard-level="whole_spacecraft"]').click()
                    whole_spacecraft = page.locator("#wizardOptions [data-wizard-object]").first
                    whole_spacecraft.wait_for(state="visible", timeout=30000)
                    report["selected_object_label"] = whole_spacecraft.inner_text()
                    whole_spacecraft.click()
                    page.locator('#wizardOptions [data-wizard-mode="offline"]').wait_for(
                        state="visible", timeout=30000
                    )
                    page.locator('#wizardOptions [data-wizard-mode="offline"]').click()
                expect(page.locator("#wizardConfigSections")).to_be_visible()
                if custom_rates and source_spec is None:
                    panel = page.locator("#wizardParametersPanel")
                    if panel.get_attribute("open") is None:
                        panel.locator(":scope > summary").click()
                    page.locator('[data-wizard-parameter-mode="all"]').click()
                    fields = {"simulation.step_s": 0.1}
                    fields.update({f"parameters.values.initial_body_rate_{axis}_rad_s": value
                                   for axis, value in rates.items()})
                    for path, value in fields.items():
                        page.locator("#wizardParameterSearch").fill(path)
                        control = page.locator(f'[data-path="{path}"]')
                        control.fill(str(value))
                        page.locator("#wizardParameterSearch").focus()
                    page.locator("#wizardParameterSearch").fill("")

                # Runtime fields are rendered in the guided step; dynamicForm belongs to the inactive advanced workspace.
                duration = page.locator('[data-wizard-field="simulation.duration_s"]')
                expect(duration).to_be_visible()
                if source_spec is None:
                    duration.fill("20")
                    duration.press("Tab")
                sample = page.locator('[data-wizard-field="simulation.sample_s"]')
                expect(sample).to_be_visible()
                if source_spec is None:
                    sample.fill("0.5" if custom_rates else "10")
                    sample.press("Tab")
                submitted_duration_s = duration.input_value()
                submitted_sample_s = sample.input_value()
                configured_step_s = 0.1 if custom_rates else 10.0
                report["configured_duration_s"] = submitted_duration_s
                report["configured_sample_s"] = submitted_sample_s
                report["configured_step_s"] = configured_step_s
                assert configured_step_s <= float(submitted_sample_s) <= float(submitted_duration_s)
                page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
                page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
                page.wait_for_function(
                    """() => {
                      const text = document.querySelector('#wizardNotice')?.textContent || '';
                      return text.length > 0 && !text.includes('正在校验配置');
                    }""",
                    timeout=60000,
                )
                validation_notice = page.locator("#wizardNotice").inner_text()
                validation_summary = page.locator("#formValidationSummary").inner_text()
                report["validation_notice"] = validation_notice
                report["validation_summary"] = validation_summary
                assert "校验通过" in validation_notice, {
                    "notice": validation_notice,
                    "summary": validation_summary,
                    "selected_capability": page.locator("#selectedCapabilityName").inner_text(),
                    "duration_s": submitted_duration_s,
                }
                report["task_spec_validated"] = True

                run_button = page.locator("#wizardOptions").get_by_role("button", name="运行仿真")

            with page.expect_request(
                lambda request: request.method == "POST" and request.url.endswith("/runs"),
                timeout=30000,
            ) as submitted_request:
                run_button.click()
            request_payload = json.loads(submitted_request.value.post_data or "{}")
            report["submitted_execution_mode"] = request_payload.get("execution_mode")
            report["submitted_execute"] = request_payload.get("execute")
            assert request_payload.get("execute") is True
            assert request_payload.get("execution_mode") == "async"
            assert request_payload.get("task_spec", {}).get("model", {}).get("capability_id") == "whole_spacecraft.unified_native.v1"

            page.wait_for_function(
                """() => {
                  const card = document.querySelector('#activeRunCard');
                  const text = card?.innerText || '';
                  return card && /SUCCEEDED/.test(text) && /PASS/.test(text);
                }""",
                timeout=240000,
            )
            run_rows = page.locator("#runList [data-run-id]")
            run_rows.first.wait_for(state="attached", timeout=30000)
            run_id = run_rows.first.get_attribute("data-run-id")
            assert run_id
            run = _get_json(base, f"/runs/{run_id}")
            assert run.get("run", {}).get("sealed") is True
            assert run.get("run", {}).get("integrity", {}).get("ok") is True
            assert str(run.get("run", {}).get("run_record", {}).get("status", "")).upper() == "SUCCEEDED"
            report["run_id"] = run_id
            report["run_status"] = "SUCCEEDED"
            report["sealed"] = True
            report["integrity"] = "PASS"
            submitted = request_payload["task_spec"]
            if source_spec:
                for key in ("simulation", "model", "parameters", "events", "outputs"):
                    assert submitted[key] == source_spec[key], key
            values = submitted["parameters"]["values"]
            assert submitted["simulation"]["step_s"] == configured_step_s
            assert submitted["simulation"]["random_seed"] == 0
            summary = json.loads((runs_root / run_id / "results/summary.json").read_bytes())
            report["submitted_epoch_utc"] = values.get("epoch_utc")
            report["native_epoch_utc"] = summary["orbital_environment"]["epoch_utc"]
            assert datetime.fromisoformat(report["native_epoch_utc"].replace("Z", "+00:00")) == datetime(2026, 1, 1, tzinfo=timezone.utc)
            trace = runs_root / run_id / "results" / "telemetry.jsonl"
            frames = [json.loads(line) for line in trace.read_bytes().splitlines()]
            assert frames[0]["time_s"] == 0
            assert frames[-1]["time_s"] == float(submitted_duration_s)
            for axis, expected in rates.items():
                assert values[f"initial_body_rate_{axis}_rad_s"] == expected
                assert frames[0][f"attitude.truth.omega_BN_B_{axis}"] == pytest.approx(expected, rel=0, abs=1e-14)
            report["initial_rate_body_rad_s"] = {axis: frames[0][f"attitude.truth.omega_BN_B_{axis}"] for axis in rates}
            report["frame_count"] = len(frames)
            report["last_sim_time_s"] = frames[-1]["time_s"]

            if entry != "graph":
                page.locator("#wizardAdvancedBtn").click()
            page.locator('.sidebar-nav button[data-view="scene"]').click()
            expect(page.locator("#satelliteSceneView")).to_be_visible()
            page.locator('[data-scene-mode="orbit"]').click()
            page.locator("#satelliteSceneCanvas canvas").wait_for(state="visible", timeout=60000)
            page.wait_for_function(
                "rid => document.querySelector('#sceneFrameSource')?.textContent === ('run:' + rid)",
                arg=run_id, timeout=60000,
            )
            assert page.locator("#sceneDataSource").get_attribute("data-state") == "sealed"
            assert page.locator("#sceneFrameQuality").inner_text() == "SEALED"
            assert page.locator("#sceneTruthStatus").inner_text().startswith("有效")
            assert page.locator("#sceneMrpValue").inner_text() != "—"
            assert page.locator("#sceneQuaternionValue").inner_text() != "—"
            report["scene_frame_source"] = page.locator("#sceneFrameSource").inner_text()
            report["scene_quality"] = page.locator("#sceneFrameQuality").inner_text()
            report["scene_truth"] = page.locator("#sceneTruthStatus").inner_text()
            if entry == "default":
                component_page = browser.new_page()
                component_page.goto(base)
                component_page.locator('[data-wizard-goal="component_native"]').click()
                component_page.locator('[data-wizard-object="component.reaction_wheel"]').click()
                expect(component_page.locator("#wizardConfigSections")).to_be_visible()
                expect(component_page.locator("#wizardConversation")).to_contain_text("有限时长")
                assert component_page.locator('[data-wizard-mode$="realtime"]').count() == 0
                for continuous in (False, True):
                    rejected = component_page.request.post(base + "/interactive/sessions", data={
                        "session_id": "wheel-negative", "capability_id": "component.reaction_wheel.v1",
                        "task_spec": {}, "continuous": continuous})
                    assert rejected.status == 422
                    assert rejected.json()["detail"]["reason_code"] == "INTERACTIVE_CAPABILITY_NOT_APPROVED"
                assert component_page.request.get(base + "/interactive/sessions").json()["sessions"] == []
                report["wheel_realtime_restriction"] = "both rejected; no session created"
                component_page.close()
            assert not report["page_errors"], report["page_errors"]
            assert not report["console_errors"], report["console_errors"]
            assert not report["request_failures"], report["request_failures"]
            report["status"] = "PASS"
            page.screenshot(path=str(evidence / "offline-ui-native-sealed-scene.png"), full_page=True)
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = repr(error)
        if page is not None:
            try:
                page.screenshot(path=str(evidence / "offline-ui-native-failure.png"), full_page=True)
            except Exception:
                pass
        raise
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if server.poll() is None:
            import psutil
            try:
                children = psutil.Process(server.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children = []
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        server_log.close()
        (evidence / "offline-ui-native-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def test_wizard_json_input_survives_initial_capability_selection(tmp_path):
    from urllib.parse import urlsplit

    from fastapi.testclient import TestClient
    from playwright.sync_api import expect, sync_playwright
    from sat_sim.api import create_app
    from sat_sim.form_schema import capability_form_schema
    from sat_sim.unified_agent import normalize_form_task_spec

    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable and os.environ.get("SAT_SIM_P9L4_NATIVE_UI_TEST") != "1":
        pytest.skip("Requires Chromium")

    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page()
            pending = []

            def serve(route):
                request = route.request
                url = urlsplit(request.url)
                response = client.request(
                    request.method, url.path + ("?" + url.query if url.query else ""),
                    content=request.post_data_buffer,
                    headers={"content-type": request.headers.get("content-type", "application/json")},
                )
                route.fulfill(status=response.status_code, body=response.content,
                              content_type=response.headers.get("content-type", "application/octet-stream"))

            def proxy(route):
                if urlsplit(route.request.url).path == "/auth/status":
                    pending.append(route)
                else:
                    serve(route)

            page.route("https://workbench.test/**", proxy)
            with page.expect_request("**/auth/status"):
                page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator('[data-wizard-goal="taskspec"]').click()
            editor = page.locator("#taskSpecEditor")
            expect(editor).to_be_visible()
            expect(editor).not_to_be_editable()
            serve(pending.pop())
            expect(editor).to_be_editable(timeout=30000)
            spec = normalize_form_task_spec(capability_form_schema("component.thermal_node.v1")["default_form"])
            spec["task"]["id"] = "user-pasted"
            editor.fill(json.dumps(spec))
            page.locator("#wizardValidateTaskSpecBtn").click()
            expect(page.locator("#wizardNotice")).to_contain_text("TaskSpec 校验通过")
            assert json.loads(editor.input_value())["task"]["id"] == "user-pasted"
        finally:
            browser.close()
