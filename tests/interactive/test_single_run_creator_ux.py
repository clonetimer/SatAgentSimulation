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
                    "singleRunValidateBtn", "singleRunResultsBtn", "singleRunProgress"):
        assert f'id="{control}"' in page
    assert "singleRunSubmitting" in js
    assert "if (state.singleRunSubmitting) return;" in js
    assert "state.activeRunId !== runId" in js
    assert ".single-run-steps" in css


def test_single_run_browser_path_has_visible_steps_and_no_auto_execute(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    calls = []
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

            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator("#singleRunGuide").is_visible()
            assert page.locator("#singleRunGuide .single-run-step").count() == 3
            assert not errors, errors
        finally:
            browser.close()
