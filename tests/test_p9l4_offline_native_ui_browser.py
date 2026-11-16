"""P9-L4 genuine offline journey: wizard UI -> Basilisk Run -> sealed scene replay."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest


def _get_json(base: str, path: str) -> dict:
    with urlopen(base + path, timeout=20) as response:
        return json.load(response)


def test_real_whole_spacecraft_offline_run_created_in_ui_and_restored_in_scene(tmp_path):
    """The user creates and runs a finite task in the actual UI; no API fixture."""
    if os.environ.get("SAT_SIM_P9L4_NATIVE_UI_TEST") != "1":
        pytest.skip("Dedicated official Basilisk + Chromium P9-L4 job only")

    import Basilisk
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    runs_root = tmp_path / "runs"
    evidence = Path(os.environ.get("SAT_SIM_P9L4_EVIDENCE", str(tmp_path)))
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
            page.goto(base, wait_until="domcontentloaded")

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

            # Runtime fields are rendered in the guided step; dynamicForm belongs to the inactive advanced workspace.
            duration = page.locator('[data-wizard-field="simulation.duration_s"]')
            expect(duration).to_be_visible()
            duration.fill("2")
            duration.press("Tab")
            page.locator("#wizardOptions").get_by_role("button", name="下一步：检查配置").click()
            page.locator("#wizardOptions").get_by_role("button", name="校验配置").click()
            page.wait_for_function(
                "() => document.querySelector('#wizardNotice')?.textContent.includes('校验通过')",
                timeout=60000,
            )
            report["task_spec_validated"] = True

            with page.expect_request(
                lambda request: request.method == "POST" and request.url.endswith("/runs"),
                timeout=30000,
            ) as submitted_request:
                page.locator("#wizardOptions").get_by_role("button", name="运行仿真").click()
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
            run_rows.first.wait_for(state="visible", timeout=30000)
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
