from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.dataset_writer import _filter_rows


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_whole_spacecraft_trace_keeps_hidden_position_support_fields_without_changing_plot_selection():
    rows = [
        {
            "time_s": 0.0,
            "eps.battery_soc": 0.7,
            "orbit.position_m_x": 7_000_000.0,
            "orbit.position_m_y": 0.0,
            "orbit.position_m_z": 0.0,
        }
    ]
    filtered = _filter_rows(
        rows,
        ["eps.battery_soc"],
        plots=["eps.battery_soc"],
        include_fields=("orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z"),
    )
    assert set(filtered[0]) == {
        "time_s", "eps.battery_soc",
        "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",
    }


def test_realtime_whole_spacecraft_contract_publishes_epoch_frame_and_position_channels():
    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim"
    api = (root / "interactive" / "api.py").read_text(encoding="utf-8")
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    continuous = (root / "web" / "continuous.js").read_text(encoding="utf-8")
    assert '"orbit_frame": "N_INERTIAL"' in api
    assert '"orbit_epoch_utc": str(epoch)' in api
    assert '"orbit_time_system": time_system' in api
    for field in ("orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z"):
        assert field in api
        assert field in app
    assert "state.interactiveSession.orbit_epoch_utc" in app
    assert "frame: state.interactiveSession.orbit_frame" in app
    assert "timeSystem: state.interactiveSession.orbit_time_system" in app
    assert "streamId: frame.stream" in app
    assert "epochUtc: info.orbit_epoch_utc" in continuous
    assert "frame: info.orbit_frame" in continuous
    assert "timeSystem: info.orbit_time_system" in continuous
    assert "streamId: selectedStream" in continuous
    assert "window.SatScene?.updateTelemetry" in app
    assert "window.SatScene?.updateTelemetry" in continuous
    assert "sceneRealtimeSource" in app
    assert "verifiedWholeSpacecraftRun" in app
    assert "enteringRunResults" in app
    assert "keepSceneTelemetry" in app
    assert "sceneNeedsInteractiveTelemetry" in app
    assert "name === 'scene' && state.sceneRealtimeSource === 'continuous' && state.interactiveAvailable" in app


def test_finite_run_scene_support_is_sealed_whole_spacecraft_only():
    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim"
    writer = (root / "dataset_writer.py").read_text(encoding="utf-8")
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    assert 'if capability_id == "whole_spacecraft.unified_native.v1" else ()' in writer
    assert "include_fields=scene_support_fields" in writer
    assert "state.activeRun?.sealed === true" in app
    assert "state.activeRun?.integrity?.ok === true" in app
    assert "state.activeRun?.task_spec?.model?.capability_id === 'whole_spacecraft.unified_native.v1'" in app
    assert "orbit.position_m_x" in app and "overviewFields" in app
    assert "const plotFieldLimit = 16" in app
    assert "if len(names) > 19:" in (root / "api.py").read_text(encoding="utf-8")
    assert "if len(fields) > 19 or any(" in (root / "telemetry_overview.py").read_text(encoding="utf-8")


def test_telemetry_driven_globe_browser_acceptance(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser")
    with _client(tmp_path) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            failures: list[str] = []
            external: list[str] = []

            def proxy(route):
                parsed = urlparse(route.request.url)
                if parsed.scheme in {"http", "https"} and parsed.netloc != "workbench.test":
                    external.append(route.request.url)
                    route.abort()
                    return
                response = client.get(parsed.path, params=dict(parse_qsl(parsed.query)))
                headers = {
                    key: value for key, value in response.headers.items()
                    if key.lower() in ("content-type", "content-security-policy", "x-content-type-options", "referrer-policy")
                }
                route.fulfill(status=response.status_code, body=response.content, headers=headers)

            page.on("pageerror", lambda error: failures.append(str(error)))
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#wizardSceneShortcutBtn").click()
            status = page.locator("#satelliteSceneStatus")
            status.filter(has_text="场景已就绪").wait_for(timeout=30000)
            result = page.evaluate("""() => {
              const epochUtc = '2026-01-01T00:00:00Z';
              const first = window.SatScene.updateTelemetry({
                session_id: 'p9f-browser-session', stream: 'orbit.live', sequence: 1, quality: 'GOOD', sim_time_s: 0,
                values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0}
              }, {epochUtc, timeSystem: 'UTC', frame: 'N_INERTIAL', sourceId: 'p9f-browser-session', streamId: 'orbit.live', sourceLabel: '浏览器验收实时遥测'});
              const second = window.SatScene.updateTelemetry({
                session_id: 'p9f-browser-session', stream: 'orbit.live', sequence: 2, quality: 'GOOD', sim_time_s: 10,
                values: {'orbit.position_m_x': 6999000, 'orbit.position_m_y': 100000, 'orbit.position_m_z': 50000}
              }, {epochUtc, frame: 'N_INERTIAL', sourceId: 'p9f-browser-session', sourceLabel: '浏览器验收实时遥测'});
              return {first, second};
            }""")
            assert result == {"first": True, "second": True}
            assert "浏览器验收实时遥测" in status.inner_text()
            assert "仿真时间 10.00 s" in status.inner_text()
            assert "高度" in status.inner_text()
            assert "演示数据" not in status.inner_text()
            assert page.locator("#satelliteSceneCanvas canvas").count() == 1
            assert not external, external
            assert not failures, failures
        finally:
            browser.close()
