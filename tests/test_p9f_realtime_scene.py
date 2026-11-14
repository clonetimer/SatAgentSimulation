from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def test_p9f_both_realtime_modes_feed_the_same_scene_bridge_and_keep_scope_explicit():
    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim"
    app = (root / "web" / "app.js").read_text(encoding="utf-8")
    continuous = (root / "web" / "continuous.js").read_text(encoding="utf-8")
    scene = (root / "web" / "satellite-scene.js").read_text(encoding="utf-8")
    api = (root / "interactive" / "api.py").read_text(encoding="utf-8")
    assert "syncInteractiveSceneSession" in app
    assert "updateRealtimeFrame" in app
    assert "sceneRealtimeSource" in app
    assert "keepInteractiveForScene" in app
    assert "sceneTelemetryStream" in continuous
    assert "sceneTelemetryRequest" in continuous
    assert "syncScene" in continuous
    assert "keepContinuousForScene" in app
    assert "**realtime_scene_metadata(session.spec)" in api
    assert "inertialPositionToEarthFixed" in scene
    assert "frame.quality !== 'GOOD'" in scene
    assert "ORBIT_POSITION_VECTOR_INVALID" in scene or "inertialPositionToEarthFixed" in scene
    assert "J2000_INERTIAL" in scene
    assert "不以演示轨道替代真实位置" in scene


def test_p9f_chromium_accepts_limited_and_continuous_frames_and_rejects_stale_data(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    if os.environ.get("SAT_SIM_P9F_BROWSER_TEST") != "1":
        pytest.skip("Live-scene browser journey runs on the Linux Chromium acceptance job.")
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=pw.chromium.executable_path,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            failures: list[str] = []

            def proxy(route):
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.scheme in {"http", "https"} and parsed.netloc != "workbench.test":
                    route.abort()
                    return
                response = client.request(
                    request.method, parsed.path + (("?" + parsed.query) if parsed.query else ""),
                    content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() in ("authorization", "content-type", "accept")},
                )
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() in ("content-type", "content-security-policy", "x-content-type-options", "referrer-policy")}
                route.fulfill(status=response.status_code, body=response.content, headers=headers)

            page.on("pageerror", lambda error: failures.append(str(error)))
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator("#satelliteSceneStatus").filter(has_text="场景已就绪").wait_for(timeout=30000)

            limited = page.evaluate("""() => {
              window.SatScene.setRealtimeSession({
                sessionId:'limited-live-001', mode:'bounded',
                epochUtc:'2000-01-01T12:00:00Z', timeSystem:'UTC',
                positionFrame:'J2000_INERTIAL', streamId:'whole.continuous'
              });
              return window.SatScene.updateRealtimeFrame({
                sessionId:'limited-live-001', streamId:'whole.continuous',
                frame:{
                  session_id:'limited-live-001', stream:'whole.continuous',
                  sequence:1, sim_time_s:0, quality:'GOOD',
                  values:{'orbit.position_m_x':7000000,'orbit.position_m_y':0,'orbit.position_m_z':0}
                }
              });
            }""")
            assert limited is True
            status = page.locator("#satelliteSceneStatus").inner_text()
            assert "真实遥测" in status and "限时实时" in status
            assert "lon=79.539°" in status

            continuous = page.evaluate("""() => {
              window.SatScene.setRealtimeSession({
                sessionId:'continuous-live-002', mode:'continuous',
                epochUtc:'2000-01-01T12:00:00Z', timeSystem:'UTC',
                positionFrame:'J2000_INERTIAL', streamId:'whole.continuous'
              });
              return window.SatScene.updateRealtimeFrame({
                sessionId:'continuous-live-002', streamId:'whole.continuous',
                frame:{
                  session_id:'continuous-live-002', stream:'whole.continuous',
                  sequence:1, sim_time_s:30, quality:'GOOD',
                  values:{'orbit.position_m_x':0,'orbit.position_m_y':7000000,'orbit.position_m_z':0}
                }
              });
            }""")
            assert continuous is True
            status = page.locator("#satelliteSceneStatus").inner_text()
            assert "持续实时" in status and "continuous-live-002" in status

            stale = page.evaluate("""() => window.SatScene.updateRealtimeFrame({
              sessionId:'continuous-live-002', streamId:'whole.continuous',
              frame:{
                session_id:'continuous-live-002', stream:'whole.continuous',
                sequence:1, sim_time_s:10, quality:'GOOD',
                values:{'orbit.position_m_x':7000000,'orbit.position_m_y':0,'orbit.position_m_z':0}
              }
            })""")
            assert stale is False

            invalid = page.evaluate("""() => window.SatScene.updateRealtimeFrame({
              sessionId:'continuous-live-002', streamId:'whole.continuous',
              frame:{
                session_id:'continuous-live-002', stream:'whole.continuous',
                sequence:2, sim_time_s:40, quality:'GOOD',
                values:{'orbit.position_m_x':7000000,'orbit.position_m_z':0}
              }
            })""")
            assert invalid is False
            assert "ORBIT_POSITION_VECTOR_INVALID" in page.locator("#satelliteSceneStatus").inner_text()
            page.evaluate("() => window.SatScene.clearRealtimeSession()")
            assert "恢复为演示数据" in page.locator("#satelliteSceneStatus").inner_text()
            assert not failures, failures
        finally:
            browser.close()
