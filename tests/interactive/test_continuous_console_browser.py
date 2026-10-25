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
    def __init__(self, *, rw4=False):
        self.current_time_s = 0.
        self.commands = []
        self.rw4 = rw4
    def prepare(self):
        pass
    def apply_command(self, command):
        self.commands.append(command.operation)
    def advance_to(self, stop_time_s):
        self.current_time_s = stop_time_s
        values = {
            "time_s": stop_time_s,
            "orbit.radius_m": 7000000.,
            "adcs.pointing_error_deg": 0.12,
            "eps.battery_soc": None,  # Browser regression: absent TM is not 0% SOC.
        }
        # Protocol double solely for browser selection tests, not native physics.
        if self.rw4:
            values["adcs.rw.speed_rad_s_3"] = 77.
        return (values,)
    def audit_native_checkpoint_coverage(self):
        # UI-only test double. Actual Basilisk ownership is tested in native CI.
        from sat_sim.interactive.native_checkpoint_audit import create_coverage_report
        nanos = round(self.current_time_s * 1.e9)
        return create_coverage_report(
            sim_time_s=self.current_time_s,
            observations={"spacecraft": {"position_m": [7000000., 0., 0.]}},
            unavailable={},
            native_worker_ident=1,
            scheduler_phase={
                "current_nanos": nanos, "next_task_time_nanos": nanos+200000000,
                "until_next_task_nanos": 200000000,
                "native_clock_restore_proven": False,
                "task_queue_restore_proven": False,
                "event_queue_restore_proven": False,
            },
        )
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
        rw4 = spec.task_spec["parameters"]["values"].get("rw_configuration") == "pyramid_4"
        runtime = UiProtocolDouble(rw4=rw4)
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
            # Edit the current offline whole-spacecraft form, then import the
            # same approved native value into each realtime UI. Browser test
            # checks only UI/API wiring; it never claims physical equivalence.
            page.locator("#singleRunGuide[data-stage='configure']").wait_for(timeout=30000)
            page.locator("#capabilityList [data-object-id='whole_spacecraft']").click()
            offline_orbit = page.locator("#dynamicForm [data-path='parameters.values.orbit_radius_m']")
            offline_orbit.wait_for(state="attached", timeout=20000)
            offline_orbit.fill("7230000")
            offline_orbit.press("Tab")
            page.locator("#interactiveNavButton").click()
            page.locator("#interactiveImportOfflineConfigBtn").click()
            page.get_by_text("已导入并冻结当前离线参数修改快照", exact=False).wait_for(timeout=15000)
            assert "orbit_radius_m" in page.locator("#interactiveOfflineConfigStatus").inner_text()
            assert running == []  # Review must not auto-start a physical run.
            page.locator("#continuousNavButton").wait_for(state="visible", timeout=30000)
            page.locator("#continuousNavButton").click()
            page.locator("#continuousImportOfflineConfigBtn").click()
            page.get_by_text("已导入并冻结当前离线参数修改快照", exact=False).wait_for(timeout=15000)
            assert page.locator('[data-native-key="orbit_radius_m"]').input_value() == "7230000"
            assert running == []  # Import is configuration only.
            assert page.locator("#continuousView").is_visible()
            assert page.locator("#interactiveView").is_hidden()
            assert page.locator("#continuousNavButton").get_attribute("class").find("active") != -1
            assert page.locator("#continuousState").inner_text() == "未选择"
            page.get_by_text("已连接服务器遥控目录：10 类指令", exact=False).wait_for(timeout=15000)
            assert page.locator("#continuousOperation option").count() == 10
            assert page.locator("#continuousCommandSubmit").is_enabled()
            # The same backend command catalogue drives the continuous console.
            page.locator("#continuousOperation").select_option("eps.load.set")
            assert page.locator("#continuousCatalogFields [data-command-field='load_id']").count() == 1
            assert page.locator("#continuousCatalogFields [data-command-field='enabled']").count() == 1
            assert "subsystem.eps" in page.locator("#continuousCommandHelp").inner_text()
            page.locator("#continuousOperation").select_option("comm_data.downlink.set")
            assert page.locator("#continuousCatalogFields [data-command-field='rate_bps']").count() == 1
            page.locator("#continuousOperation").select_option("comm_data.fault.inject")
            assert "fault_operator" in page.locator("#continuousCommandHelp").inner_text()
            page.locator("#continuousOperation").select_option("adcs.target.set")

            page.locator("#continuousSessionId").fill("browser-r7d")
            page.locator("details.continuous-native-config > summary").click()
            page.locator('[data-native-key="orbit_radius_m"]').fill("7200000")
            page.locator('[data-native-key="battery_capacity_wh"]').fill("210")
            page.locator('[data-native-key="initial_soc"]').fill("0.7")
            page.locator('[data-native-key="rw_configuration"]').select_option("pyramid_4")
            assert page.locator('[data-native-key="rw_initial_speed_rad_s_3"]').is_enabled()
            page.locator('[data-native-key="rw_initial_speed_rad_s_3"]').fill("125")
            page.locator('[data-native-key="rw_configuration"]').select_option("orthogonal_3")
            assert page.locator('[data-native-key="rw_initial_speed_rad_s_3"]').is_disabled()
            page.locator('[data-native-key="rw_initial_speed_rad_s_0"]').fill("-75")
            page.locator('[data-native-key="rw_max_torque_nm"]').fill("0.08")
            page.locator("#continuousQuantum").select_option("0.2")
            page.locator("#continuousPaced").check()
            page.locator("#continuousCreateForm button[type=submit]").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'READY'",
                timeout=15000,
            )
            assert len(running) == 1
            original = client.get("/interactive/continuous/sessions/browser-r7d/configuration")
            assert original.status_code == 200, original.text
            assert original.json()["native_initial_values"]["orbit_radius_m"] == 7200000.
            assert original.json()["native_initial_values"]["battery_capacity_wh"] == 210.
            assert original.json()["native_initial_values"]["initial_soc"] == .7
            assert original.json()["native_initial_values"]["rw_initial_speed_rad_s_0"] == -75.
            assert original.json()["native_initial_values"]["rw_max_torque_nm"] == .08
            assert "rw_initial_speed_rad_s_3" not in original.json()["native_initial_values"]
            assert original.json()["editable_after_prepare"] is False
            assert "配置在模型创建后不可修改" in page.locator("#continuousInitialConfigStatus").inner_text()
            assert page.locator("#continuousWriterEpoch").inner_text() == "1"
            page.locator("#continuousEnduranceInterval").select_option("1")
            page.locator("#continuousEnduranceStart").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousEnduranceStatus').textContent === 'RECORDING'",
                timeout=12000,
            )
            page.locator("#continuousOperation").select_option("eps.load.set")
            page.locator("#continuousCatalogFields [data-command-field='load_id']").select_option("heater")
            page.locator("#continuousCatalogFields [data-command-field='enabled']").check()
            page.locator("#continuousExecuteAt").fill("0.2")
            page.locator("#continuousCommandForm button[type=submit]").click()
            page.locator("#continuousAckList").get_by_text("QUEUED", exact=False).first.wait_for(timeout=10000)
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
            assert "eps.load.set" in running[0].commands
            # This protocol fixture deliberately sends null SOC; never show 0%.
            assert page.locator("#continuousSoc").inner_text() == "—"
            page.locator("#continuousSignal").select_option("eps.battery_soc")
            # Null samples are rejected before numeric scale computation, no JS errors.
            assert page.locator("#continuousChart").is_visible()
            page.locator("#continuousSignal").select_option("orbit.radius_m")
            assert page.locator("#continuousChart").is_visible()
            rw4_option = page.locator("#continuousRw4SignalOption")
            assert rw4_option.evaluate("(el) => el.disabled"), rw4_option.evaluate(
                "(el) => el.outerHTML"
            )
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
            assert page.locator("#continuousRecoveryAuditBtn").is_disabled()
            page.locator("[data-continuous-action='pause']").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'PAUSED'",
                timeout=12000,
            )
            page.locator("#continuousRecoveryAuditBtn").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousRecoveryVerdict').textContent === 'BLOCKED'",
                timeout=12000,
            )
            assert page.locator("#continuousRecoveryOwnerTotal").inner_text() == "12"
            assert page.locator("#continuousRecoveryOwnerRestorable").inner_text() == "0"
            assert page.locator("#continuousRecoveryExportBtn").is_enabled()
            assert page.locator("#continuousRecoveryOwners .continuous-log-item").count() == 12
            page.locator("[data-continuous-action='stop']").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'COMPLETED'",
                timeout=15000,
            )
            assert not page.locator("#continuousStartBtn").is_enabled()

            # New four-wheel session: RW4 selector stays disabled until real
            # stream-shaped data arrives, then can be chosen for the chart.
            page.locator("#continuousSessionId").fill("browser-rw4")
            page.locator('[data-native-key="rw_configuration"]').select_option("pyramid_4")
            page.locator('[data-native-key="rw_initial_speed_rad_s_3"]').fill("77")
            page.locator("#continuousCreateForm button[type=submit]").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'READY'",
                timeout=15000,
            )
            rw4_option = page.locator("#continuousRw4SignalOption")
            assert rw4_option.evaluate("(el) => el.disabled"), rw4_option.evaluate(
                "(el) => el.outerHTML"
            )
            assert running[-1].rw4 is True
            page.locator("#continuousStartBtn").click()
            page.wait_for_function(
                "() => !document.querySelector('#continuousRw4SignalOption').disabled",
                timeout=15000,
            )
            page.locator("#continuousSignal").select_option("adcs.rw.speed_rad_s_3")
            assert page.locator("#continuousSignal").input_value() == "adcs.rw.speed_rad_s_3"
            page.locator("[data-continuous-action='stop']").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').textContent === 'COMPLETED'",
                timeout=15000,
            )
            assert failures == [], failures
        finally:
            browser.close()
