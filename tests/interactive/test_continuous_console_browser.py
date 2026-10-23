"""Real Chromium interaction smoke test for the no-code continuous console.

Only the isolated browser/control-plane fixture uses a protocol double;
real Basilisk physics and TC/TM runs have independent native CI gates.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


class UiProtocolDouble:
    def __init__(self):
        self.current_time_s = 0.
        self.commands = []
    def prepare(self):
        pass
    def apply_command(self, command):
        self.commands.append(command.operation)
    def advance_to(self, stop_time_s):
        self.current_time_s = stop_time_s
        return ({
            "time_s": stop_time_s,
            "orbit.radius_m": 7000000.,
            "adcs.pointing_error_deg": 0.12,
            "eps.battery_soc": 0.81,
        },)
    def read_delta(self):
        return ()
    def finalize(self):
        return {"summary": {"overall_status": "PASS"}}
    def abort(self):
        return None


def test_chromium_creates_operates_and_stops_continuous_session(tmp_path, monkeypatch):
    playwright_module = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    running = []
    def factory(spec):
        assert spec.continuous
        assert spec.capability_id == "whole_spacecraft.unified_native.v1"
        runtime = UiProtocolDouble()
        running.append(runtime)
        return runtime, None
    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime", factory)
    app = create_app(
        artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs",
        embedded_worker=False, auth_mode="disabled",
    )
    with TestClient(app) as client, playwright_module.sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1050},
                                    locale="zh-CN")
            failures = []
            page.on("pageerror", lambda exc: failures.append(str(exc)))
            def route_request(route):
                request = route.request
                parsed = urlsplit(request.url)
                assert parsed.netloc == "workbench.test"
                path = parsed.path + (("?" + parsed.query) if parsed.query else "")
                response = client.request(
                    request.method, path,
                    content=request.post_data.encode("utf-8") if request.post_data else None,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() in ("authorization", "content-type", "accept")},
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={key: value for key, value in response.headers.items()
                             if key.lower() in ("content-type", "cache-control")},
                )
            page.route("https://workbench.test/**", route_request)
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#continuousNavButton").wait_for(state="visible", timeout=30000)
            page.locator("#continuousNavButton").click()
            assert page.locator("#continuousView").is_visible()
            assert page.locator("#interactiveView").is_hidden()
            assert page.locator("#continuousNavButton").get_attribute("class").find("active") != -1
            assert page.locator("#continuousState").inner_text() == "未选择"

            page.locator("#continuousSessionId").fill("browser-r7d")
            page.locator("#continuousQuantum").select_option("0.2")
            page.locator("#continuousPaced").check()
            page.locator("#continuousCreateForm button[type=submit]").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'READY'",
                timeout=15000,
            )
            assert len(running) == 1
            assert page.locator("#continuousWriterEpoch").inner_text() == "1"
            page.locator("#continuousEnduranceInterval").select_option("1")
            page.locator("#continuousEnduranceStart").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousEnduranceStatus').textContent === 'RECORDING'",
                timeout=12000,
            )
            page.locator("#continuousOperation").select_option("adcs.target.set")
            page.locator("#continuousExecuteAt").fill("0.2")
            page.locator("#continuousCommandForm button[type=submit]").click()
            page.locator("#continuousAckList").get_by_text("QUEUED", exact=False).first.wait_for(
                timeout=10000,
            )
            page.locator("#continuousStartBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousOrbit').textContent.includes('7000000')",
                timeout=15000,
            )
            page.wait_for_function(
                "() => document.querySelector('#continuousAckList').textContent.includes('ACKED')",
                timeout=15000,
            )
            assert "adcs.target.set" in running[0].commands
            assert page.locator("#continuousChart").is_visible()
            page.wait_for_function(
                "() => document.querySelector('#continuousDurableFrames').textContent !== '—'",
                timeout=15000,
            )
            assert float(page.locator("#continuousWallElapsed").inner_text().split(" ")[0]) >= 0.
            assert page.locator("#continuousResidentBytes").inner_text() != "—"
            assert page.locator("#continuousDiskStatus").inner_text().find(" / ") > 0
            assert int(page.locator("#continuousDurableFrames").inner_text()) > 0
            page.wait_for_function(
                "() => Number(document.querySelector('#continuousEnduranceSamples').textContent) >= 2",
                timeout=15000,
            )
            page.locator("#continuousEnduranceStop").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousEnduranceStatus').textContent === 'FINISHED'",
                timeout=12000,
            )
            assert page.locator("#continuousEnduranceEligible").inner_text() == "未达到"
            assert page.locator("#continuousEnduranceEvidence").is_enabled()
            assert page.locator("#continuousEnduranceSummary").is_enabled()
            page.locator("#continuousEnduranceAuditBtn").click()
            page.locator("#continuousEnduranceAuditResult").get_by_text(
                "证据完整，但尚未满足 24h 验收条件", exact=False,
            ).wait_for(timeout=10000)
            assert "原始状态事件" in page.locator("#continuousEnduranceAuditResult").inner_text()
            assert "ACK/Outbox" in page.locator("#continuousEnduranceAuditResult").inner_text()
            page.locator("[data-continuous-action='stop']").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'COMPLETED'",
                timeout=15000,
            )
            assert not page.locator("#continuousStartBtn").is_enabled()
            assert failures == [], failures
        finally:
            browser.close()
