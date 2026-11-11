"""Real Chromium interaction smoke test for the no-code continuous console.

Only the isolated browser/control-plane fixture uses a protocol double;
real Basilisk physics and TC/TM runs have independent native CI gates.
"""
from __future__ import annotations

import os
import json
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


def _navigation_snapshot(page, selector, label, tmp_path, records):
    proof = page.locator(selector).evaluate("""(node) => {
        const details=[]; for(let p=node.parentElement;p;p=p.parentElement) {
            if(p.tagName==='DETAILS') details.push({id:p.id,open:p.open,summary:p.querySelector(':scope > summary')?.textContent});
        }
        return {textContent:node.textContent,innerText:node.innerText,details,
            checkVisibility:typeof node.checkVisibility==='function' ? node.checkVisibility() : null};
    }""")
    proof.update(label=label, selector=selector, playwright_visible=page.locator(selector).is_visible())
    records.append(proof)
    (tmp_path / "r32-navigation-proof.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")
    return proof


def _reveal_with_keyboard(page, selector, label, tmp_path, records):
    """Follow real disclosure summaries; never set .open or force an action."""
    _navigation_snapshot(page, selector, label + "-before", tmp_path, records)
    for _ in range(10):
        closed = page.locator(selector).evaluate("""(node) => {
            const all=[...document.querySelectorAll('details')], found=[];
            for(let p=node.parentElement;p;p=p.parentElement) {
                if(p.tagName==='DETAILS' && !p.open) found.push({index:all.indexOf(p),id:p.id});
            }
            return found.reverse();
        }""")
        if not closed:
            break
        index = closed[0]["index"]
        summary = page.locator("details").nth(index).locator(":scope > summary")
        assert summary.is_visible()
        summary.focus()
        assert summary.evaluate("(node) => document.activeElement === node")
        summary.press("Enter")
        page.wait_for_function("(index) => document.querySelectorAll('details')[index].open", arg=index)
    visible = page.locator(selector).is_visible()
    if not visible:
        visibility = page.locator(selector).evaluate("""node => {
            const chain=[]; for(let p=node;p;p=p.parentElement) chain.push({
              tag:p.tagName,id:p.id,className:p.className,
              display:getComputedStyle(p).display,visibility:getComputedStyle(p).visibility,
              hidden:p.hidden,rect:(()=>{const r=p.getBoundingClientRect();return [r.width,r.height]})()
            });
            return chain;
        }""")
        raise AssertionError(f"{selector} remains hidden after disclosure: {visibility}")
    return _navigation_snapshot(page, selector, label + "-after", tmp_path, records)


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
                                    executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"),
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1050},
                                    locale="zh-CN")
            failures = []
            navigation_records = []
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
            dialog_messages = []
            page.on("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.accept()))
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            # Exercise the new default wizard's limited-realtime branch first.
            page.locator("#wizardView").wait_for(state="visible")
            page.get_by_role("button", name="运行正常仿真").click()
            page.get_by_role("button", name="整星").click()
            page.locator("#wizardOptions [data-wizard-object='whole_spacecraft']").click()
            page.locator("#wizardOptions [data-wizard-mode='limited_realtime']").wait_for(state="visible", timeout=20000)
            assert page.locator("#wizardOptions [data-wizard-mode='continuous_realtime']").count() == 1
            page.locator("#wizardOptions [data-wizard-mode='limited_realtime']").click()
            page.locator("#interactiveView").wait_for(state="visible", timeout=15000)
            assert page.locator("#wizardView").is_visible()
            assert page.locator("#interactiveView").evaluate("(node) => node.closest('#wizardView') !== null")
            page.locator("#interactiveWizardBackBtn").click()
            page.locator("#wizardOptions [data-wizard-mode='offline']").wait_for(state="visible")
            page.locator("#wizardOptions [data-wizard-mode='offline']").click()
            page.locator("#wizardConfigSections").wait_for(state="visible")
            # P2: the wizard exposes three disclosure levels without removing
            # or rebuilding the authoritative Schema-backed form.
            page.locator("#wizardParametersPanel > summary").click()
            form = page.locator("#wizardParameterHost #dynamicForm")
            form.wait_for(state="attached")
            registered_field_count = form.locator(".form-field").count()
            assert registered_field_count > 0
            page.locator('[data-wizard-parameter-mode="runtime"]').click()
            assert form.get_attribute("data-wizard-parameter-mode") == "runtime"
            assert not form.is_visible()
            assert form.locator(".form-field").count() == registered_field_count
            page.locator('#wizardParametersPanel button[data-wizard-parameter-mode="recommended"]').click()
            assert form.get_attribute("data-wizard-parameter-mode") == "recommended"
            assert form.is_visible()
            page.locator('#wizardParametersPanel button[data-wizard-parameter-mode="all"]').click()
            assert form.get_attribute("data-wizard-parameter-mode") == "all"
            assert page.locator('#wizardParametersPanel button[data-wizard-parameter-mode="all"]').get_attribute("aria-pressed") == "true"
            assert form.locator(".parameter-section[open]").count() > 0
            page.locator('#wizardParametersPanel button[data-wizard-parameter-mode="recommended"]').click()
            page.locator("#wizardParameterSearch").fill("orbit")
            assert form.locator(".form-field").count() > 0
            page.locator("#wizardParameterSearch").fill("")
            # P3: purpose-first observability keeps QoI and time-series separate,
            # and only exposes fields supplied by the selected capability schema.
            qoi_rows = page.locator("#wizardQoiChoices .wizard-qoi-row")
            curve_rows = page.locator("#wizardCurveChoices .wizard-curve-row")
            assert qoi_rows.count() > 0
            assert curve_rows.count() > 0
            assert page.locator("#wizardOutputPurposes [data-output-purpose='attitude']").count() == 1
            page.locator("#wizardOutputPurposes [data-output-purpose='attitude']").click()
            assert page.locator("#wizardOutputPurposes [data-output-purpose='attitude']").get_attribute("aria-pressed") == "true"
            assert page.locator("#wizardCurveChoices .wizard-curve-card").count() > 0
            technical_name = page.locator("#wizardCurveChoices .wizard-curve-technical code").first.evaluate("(node) => node.textContent")
            page.locator("#wizardOutputSearch").fill(technical_name)
            assert page.locator("#wizardCurveChoices .wizard-curve-technical code").count() == 1
            assert "匹配" in page.locator("#wizardOutputSearchCount").inner_text()
            page.locator("#wizardOutputSearch").fill("")
            page.locator("#wizardOutputPurposes [data-output-purpose='all']").click()
            page.locator("#wizardRestoreOutputDefaultsBtn").click()
            assert "恢复当前模型 Schema 提供的推荐输出组合" in page.locator("#wizardNotice").inner_text()
            assert page.locator("#wizardQoiChoices input:checked").count() > 0
            assert page.locator("#wizardCurveChoices .wizard-curve-row input:checked").count() > 0
            page.locator("#wizardBackBtn").click()
            page.locator("#wizardOptions [data-wizard-mode='offline']").wait_for(state="visible")
            # The default entry is now the rule-driven wizard; this regression
            # exercises the advanced realtime controls and explicitly enters
            # the retained engineering workbench before editing its form.
            page.locator("#wizardAdvancedBtn").click()
            # Edit the current offline whole-spacecraft form, then import the
            # same approved native value into each realtime UI. Browser test
            # checks only UI/API wiring; it never claims physical equivalence.
            page.locator("#singleRunGuide[data-stage='configure']").wait_for(timeout=30000)
            catalog_toggle = page.locator("#toggleMobileCatalogBtn")
            if catalog_toggle.get_attribute("aria-expanded") != "true":
                catalog_toggle.focus()
                catalog_toggle.press("Enter")
            page.locator("#capabilityList [data-object-id='whole_spacecraft']").click()
            offline_orbit = page.locator("#dynamicForm [data-path='parameters.values.orbit_radius_m']")
            offline_orbit.wait_for(state="attached", timeout=20000)
            offline_orbit.fill("7230000")
            offline_orbit.press("Tab")
            assert page.locator("#simulationModeChooser").is_visible()
            page.locator("#modeBoundedRealtimeBtn").click()
            page.locator("#interactiveView").wait_for(state="visible", timeout=15000)
            assert page.locator("#wizardView").is_visible()
            assert page.locator("#interactiveView").evaluate("(node) => node.closest('#wizardView') !== null")
            assert not page.locator("#interactiveContinuous").is_checked()
            assert "参数审核完成" in page.locator("#simulationModeChooserStatus").inner_text()
            page.locator("#interactiveOfflineConfigStatus").get_by_text("已导入并冻结当前离线参数修改快照", exact=False).wait_for(timeout=15000)
            assert "orbit_radius_m" in page.locator("#interactiveOfflineConfigStatus").inner_text()
            assert running == []  # Review must not auto-start a physical run.
            page.locator("#wizardRealtimeBackBtn").click()
            page.locator("#wizardAdvancedBtn").click()
            page.locator("#modeContinuousRealtimeBtn").click()
            page.locator("#continuousView").wait_for(state="visible", timeout=15000)
            assert page.locator("#wizardView").is_visible()
            assert page.locator("#continuousView").evaluate("(node) => node.closest('#wizardView') !== null")
            assert "参数审核完成" in page.locator("#simulationModeChooserStatus").inner_text()
            page.locator("#continuousOfflineConfigStatus").get_by_text("已导入并冻结当前离线参数修改快照", exact=False).wait_for(timeout=15000)
            assert page.locator('[data-native-key="orbit_radius_m"]').input_value() == "7230000"
            assert running == []  # Import is configuration only.
            assert page.locator("#continuousView").is_visible()
            assert page.locator("#interactiveView").is_hidden()
            assert page.locator("#continuousNavButton").get_attribute("class").find("active") != -1
            assert page.locator("#continuousState").inner_text() == "未选择"
            page.get_by_text("已连接服务器遥控目录：10 类指令", exact=False).wait_for(timeout=15000)
            assert page.locator("#continuousOperation option").count() == 10
            # A catalogue alone is insufficient without a selected session.
            assert page.locator("#continuousCommandSubmit").is_disabled()
            # The same backend command catalogue drives the continuous console.
            page.locator("#continuousOperation").select_option("eps.load.set")
            assert page.locator("#continuousCommandSubmit").is_disabled()
            assert page.locator("#continuousCatalogFields [data-command-field='load_id']").count() == 1
            assert page.locator("#continuousCatalogFields [data-command-field='enabled']").count() == 1
            assert "subsystem.eps" in page.locator("#continuousCommandHelp").inner_text()
            page.locator("#continuousOperation").select_option("comm_data.downlink.set")
            assert page.locator("#continuousCatalogFields [data-command-field='rate_bps']").count() == 1
            page.locator("#continuousOperation").select_option("comm_data.fault.inject")
            assert "fault_operator" in page.locator("#continuousCommandHelp").inner_text()
            page.locator("#continuousOperation").select_option("adcs.target.set")

            page.locator("#continuousSessionId").fill("browser-r7d")
            # Reviewed import intentionally expands the native form.\n            assert page.locator("details.continuous-native-config").evaluate("(el) => el.open")
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
                "() => document.querySelector('#continuousState').dataset.state === 'READY'",
                timeout=15000,
            )
            assert len(running) == 1
            assert page.locator("#continuousState").inner_text() == "准备就绪"
            assert page.locator("#continuousState").get_attribute("title") == "READY"
            assert page.locator("#continuousCommandSubmit").is_enabled()
            original = client.get("/interactive/continuous/sessions/browser-r7d/configuration")
            assert original.status_code == 200, original.text
            assert original.json()["native_initial_values"]["orbit_radius_m"] == 7200000.
            assert original.json()["native_initial_values"]["battery_capacity_wh"] == 210.
            assert original.json()["native_initial_values"]["initial_soc"] == .7
            assert original.json()["native_initial_values"]["rw_initial_speed_rad_s_0"] == -75.
            assert original.json()["native_initial_values"]["rw_max_torque_nm"] == .08
            assert "rw_initial_speed_rad_s_3" not in original.json()["native_initial_values"]
            assert original.json()["editable_after_prepare"] is False
            folded_proof = _navigation_snapshot(page, "#continuousInitialConfigStatus", "created-form-folded", tmp_path, navigation_records)
            assert any(item["id"] == "compactContinuousCreate" and not item["open"] for item in folded_proof["details"])
            assert "配置在模型创建后不可修改" in folded_proof["textContent"]
            assert folded_proof["innerText"] == ""
            _reveal_with_keyboard(page, "#continuousInitialConfigStatus", "created-form-status", tmp_path, navigation_records)
            assert "配置在模型创建后不可修改" in page.locator("#continuousInitialConfigStatus").inner_text()
            _reveal_with_keyboard(page, "#continuousWriterEpoch", "runtime-diagnostics", tmp_path, navigation_records)
            assert page.locator("#continuousWriterEpoch").inner_text() == "1"
            _reveal_with_keyboard(page, "#continuousEnduranceInterval", "endurance-menu", tmp_path, navigation_records)
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
            active_window = client.get(
                "/interactive/sessions/browser-r7d/telemetry-history/whole.continuous",
                params={"start_s": 0, "end_s": 60, "fields": "orbit.radius_m"},
            )
            assert active_window.status_code == 409
            assert active_window.json()["detail"]["reason_code"] == (
                "HISTORY_REQUIRES_PAUSED_OR_TERMINAL_SESSION"
            )
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
                "() => document.querySelector('#continuousState').dataset.state === 'PAUSED'",
                timeout=12000,
            )
            assert page.locator("#continuousState").inner_text() == "已暂停"
            page.locator(".nav-button[data-view=wizard]:not([data-wizard-shortcut])").click()
            assert any("实时会话仍处于活动状态" in message for message in dialog_messages)
            page.locator(".nav-button[data-view=continuous]").click()
            page.locator("#continuousView").wait_for(state="visible", timeout=15000)
            assert page.locator("#continuousState").inner_text() == "已暂停"
            assert page.locator("#continuousState").get_attribute("title") == "PAUSED"
            # A paused session permits verified historical retrieval from
            # durable workspace, not from the browser's 280-frame replay ring.
            _reveal_with_keyboard(page, "#continuousHistoryQueryBtn", "history-menu", tmp_path, navigation_records)
            page.locator("#continuousHistoryStart").fill("0")
            page.locator("#continuousHistoryEnd").fill("60")
            page.locator("#continuousHistoryQueryBtn").click()
            page.locator("#continuousHistoryStatus").get_by_text(
                "哈希链已验证", exact=False,
            ).wait_for(timeout=15000)
            assert "无抽样/插值" in page.locator("#continuousHistoryStatus").inner_text()
            assert "orbit.radius_m" in page.locator("#continuousHistoryLegend").inner_text()
            assert page.locator("#continuousHistoryChart").is_visible()
            raw = client.get(
                "/interactive/sessions/browser-r7d/telemetry-history/whole.continuous",
                params={
                    "start_s": 0, "end_s": 60, "fields": "orbit.radius_m,adcs.pointing_error_deg",
                },
            )
            assert raw.status_code == 200, raw.text
            assert raw.json()["exact_original_samples"] is True
            assert raw.json()["frame_count"] > 0
            assert raw.json()["source"] == "hash_verified_append_only_workspace"
            _reveal_with_keyboard(page, "#continuousRecoveryAuditBtn", "recovery-menu", tmp_path, navigation_records)
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
                "() => document.querySelector('#continuousState').dataset.state === 'COMPLETED'",
                timeout=15000,
            )
            assert not page.locator("#continuousStartBtn").is_enabled()
            page.locator("#continuousOperation").select_option("eps.load.set")
            assert page.locator("#continuousCommandSubmit").is_disabled()

            # New four-wheel session: RW4 selector stays disabled until real
            # stream-shaped data arrives, then can be chosen for the chart.
            _reveal_with_keyboard(page, "#continuousSessionId", "new-rw4-session-form", tmp_path, navigation_records)
            page.locator("#continuousSessionId").fill("browser-rw4")
            page.locator('[data-native-key="rw_configuration"]').select_option("pyramid_4")
            page.locator('[data-native-key="rw_initial_speed_rad_s_3"]').fill("77")
            page.locator("#continuousCreateForm button[type=submit]").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').dataset.state === 'READY'",
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
            with page.expect_response(lambda response: response.url.endswith("/endurance/status")):
                page.locator("#continuousRefreshBtn").click()
            assert page.locator("#continuousSignal").input_value() == "adcs.rw.speed_rad_s_3"
            page.locator("[data-continuous-action='stop']").click()
            page.wait_for_function(
                "() => document.querySelector('#continuousState').dataset.state === 'COMPLETED'",
                timeout=15000,
            )
            assert failures == [], failures
        finally:
            browser.close()


@pytest.mark.parametrize("catalog_available", [True, False])
def test_command_availability_when_catalog_arrives_after_session(
    tmp_path, monkeypatch, catalog_available,
):
    """Delay real HTTP routing, not product state; no native physics claim."""
    pw = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime",
                        lambda spec: (UiProtocolDouble(), None))
    app = create_app(artifacts_root=tmp_path/"api", runs_root=tmp_path/"runs",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client, pw.sync_playwright() as p:
        created = client.post("/interactive/continuous/sessions", json={
            "session_id": "late-catalog", "quantum_s": .2, "rate": 1.,
            "paced": True, "initial_horizon_s": 60.,
        })
        assert created.status_code == 201, created.text
        browser = p.chromium.launch(headless=True,
            executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"))
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1050})
            page.on("dialog", lambda dialog: dialog.accept())
            held = []
            held_commands = []
            delay_catalog = False
            def proxy(route):
                request = route.request
                url = urlsplit(request.url)
                if delay_catalog and url.path == "/interactive/commands/catalog":
                    held.append(route)
                    return
                if request.method == "POST" and url.path.endswith("/commands"):
                    held_commands.append(route)
                    return
                response = client.request(request.method, url.path+("?"+url.query if url.query else ""),
                    content=request.post_data_buffer,
                    headers={k:v for k,v in request.headers.items() if k in ("content-type", "accept")})
                route.fulfill(status=response.status_code, body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/json")})
            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            # Enter the retained engineering workspace through the visible wizard action;
            # the guided shell intentionally hides the legacy sidebar in realtime mode.
            page.locator("#wizardAdvancedBtn").click()
            page.locator("#continuousNavButton").wait_for(state="visible", timeout=30000)
            delay_catalog = True
            page.locator("#continuousNavButton").click(timeout=30000)
            page.wait_for_function("() => document.querySelector('#continuousState').dataset.state === 'READY'")
            assert held, "Catalogue request must still be pending after READY rendering"
            # READY must not override a pending/unavailable command catalogue.
            assert page.locator("#continuousCommandSubmit").is_disabled()
            if catalog_available:
                response = client.get("/interactive/commands/catalog")
                held.pop().fulfill(status=200, body=response.content,
                                   headers={"content-type": "application/json"})
                pw.expect(page.locator("#continuousCommandSubmit")).to_be_enabled()
                page.locator("#continuousExecuteAt").fill("0")
                page.locator("#continuousCommandSubmit").click()
                pw.expect(page.locator("#continuousCommandSubmit")).to_be_disabled()
                assert len(held_commands) == 1
                page.locator("#continuousOperation").select_option("eps.load.set")
                # Changing fields cannot re-enable a command still in flight.
                assert page.locator("#continuousCommandSubmit").is_disabled()
                pending = held_commands.pop()
                request = pending.request
                response = client.post(urlsplit(request.url).path,
                    content=request.post_data_buffer, headers={"content-type":"application/json"})
                pending.fulfill(status=response.status_code, body=response.content,
                                headers={"content-type":"application/json"})
                pw.expect(page.locator("#continuousCommandSubmit")).to_be_enabled()
                page.locator("[data-continuous-action='stop']").click()
                page.wait_for_function("() => document.querySelector('#continuousState').dataset.state === 'COMPLETED'")
                page.locator("#continuousOperation").select_option("eps.load.set")
                assert page.locator("#continuousCommandSubmit").is_disabled()
            else:
                held.pop().fulfill(status=503, body='{"detail":"catalog unavailable"}',
                                   headers={"content-type": "application/json"})
                pw.expect(page.locator("#continuousCommandCatalogStatus")).to_contain_text("遥控目录不可用")
                page.locator("#continuousOperation").select_option("adcs.target.set")
                assert page.locator("#continuousCommandSubmit").is_disabled()
        finally:
            browser.close()


def test_fourth_wheel_catalog_does_not_enable_unobserved_channel(tmp_path, monkeypatch):
    """A configured channel is not a measurement; delay its first HTTP response."""
    pw = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime",
                        lambda spec: (UiProtocolDouble(rw4=True), None))
    app = create_app(artifacts_root=tmp_path/"api", runs_root=tmp_path/"runs",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client, pw.sync_playwright() as p:
        created = client.post("/interactive/continuous/sessions", json={
            "session_id":"rw4-before-tm", "quantum_s":.2, "rate":1., "paced":True,
            "initial_horizon_s":60., "initial_configuration":{"adcs":{"rw_configuration":"pyramid_4"}},
        })
        assert created.status_code == 201, created.text
        browser = p.chromium.launch(headless=True,
            executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"))
        try:
            page = browser.new_page(viewport={"width":1600,"height":1050})
            page.on("dialog", lambda dialog: dialog.accept())
            pending = []
            hold_telemetry = True
            def proxy(route):
                request = route.request; url = urlsplit(request.url)
                if hold_telemetry and "/rw4-before-tm/telemetry/" in url.path:
                    pending.append(route); return
                response = client.request(request.method,url.path+("?"+url.query if url.query else ""),
                    content=request.post_data_buffer,headers={"content-type":"application/json"})
                route.fulfill(status=response.status_code,body=response.content,
                              headers={"content-type":response.headers.get("content-type","application/json")})
            page.route("https://workbench.test/**",proxy)
            page.goto("https://workbench.test/",wait_until="domcontentloaded")
            page.locator("#wizardAdvancedBtn").click()
            page.locator("#continuousNavButton").click(timeout=30000)
            page.wait_for_function("() => document.querySelector('#continuousStream').options.length > 0")
            # Contract has arrived, but no frame has reached this page yet.
            assert page.locator("#continuousRw4SignalOption").evaluate("el => el.disabled")
            assert pending
            hold_telemetry = False
            for route in pending:
                url=urlsplit(route.request.url)
                response=client.get(url.path+("?"+url.query if url.query else ""))
                route.fulfill(status=response.status_code,body=response.content,
                              headers={"content-type":"application/json"})
            page.wait_for_function("() => document.querySelector('#continuousState').dataset.state === 'READY'")
            assert page.locator("#continuousRw4SignalOption").evaluate("el => el.disabled")
            page.locator("#continuousStartBtn").click()
            page.wait_for_function("() => !document.querySelector('#continuousRw4SignalOption').disabled")
            page.locator("#continuousSignal").select_option("adcs.rw.speed_rad_s_3")
            assert page.locator("#continuousSignal").input_value() == "adcs.rw.speed_rad_s_3"
            page.locator("[data-continuous-action='stop']").click()
            page.wait_for_function("() => document.querySelector('#continuousState').dataset.state === 'COMPLETED'")
        finally:
            browser.close()
