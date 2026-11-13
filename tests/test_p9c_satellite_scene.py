from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    return TestClient(app)


def test_scene_workspace_contract_and_local_runtime_assets(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert 'data-view="scene"' in index.text
        assert 'id="satelliteSceneView"' in index.text
        assert 'id="satelliteSceneCanvas"' in index.text
        assert 'window.CESIUM_BASE_URL = "/assets/vendor/cesium/"' in index.text
        assert 'src="/assets/vendor/cesium/Cesium.js"' in index.text
        assert 'src="/assets/satellite-scene.js' in index.text
        runtime = client.get("/assets/vendor/cesium/Cesium.js")
        widgets = client.get("/assets/vendor/cesium/Widgets/widgets.css")
        earth = client.get("/assets/vendor/cesium/Assets/Textures/NaturalEarthII/tilemapresource.xml")
        assert runtime.status_code == 200 and len(runtime.content) > 100_000
        assert widgets.status_code == 200
        assert earth.status_code == 200
        assert "worker-src 'self' blob:" in index.headers["content-security-policy"]


def test_scene_module_declares_demo_provenance_and_safe_lifecycle():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    assert "演示数据，不代表真实仿真遥测" in scene
    assert "演示轨道（非仿真结果）" in scene
    assert "window.SatScene = { enter, exit: destroy, track, freeCamera, home }" in scene
    assert "viewer.destroy()" in scene
    assert "setInterval" not in scene


def test_scene_browser_loads_offline_globe_and_camera_controls(tmp_path: Path):
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
        page = browser.new_page()
        failures: list[str] = []

        def proxy(route):
            parsed = urlparse(route.request.url)
            response = client.get(parsed.path, params=dict(parse_qsl(parsed.query)))
            headers = {
                key: value for key, value in response.headers.items()
                if key.lower() in ("content-type", "content-security-policy", "x-content-type-options", "referrer-policy")
            }
            route.fulfill(status=response.status_code, body=response.content, headers=headers)

        page.on("pageerror", lambda error: failures.append(str(error)))
        page.on("requestfailed", lambda request: failures.append(f"request failed: {request.url} {request.failure}"))
        page.on("response", lambda response: failures.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
        page.route("https://workbench.test/**", proxy)
        page.goto("https://workbench.test/", wait_until="domcontentloaded")
        page.wait_for_function("() => document.readyState === 'complete'")
        runtime_state = page.evaluate("""() => ({
            cesium: typeof window.Cesium,
            scene: typeof window.SatScene,
            scripts: Array.from(document.scripts).map((item) => ({src: item.src, type: item.type})),
            sceneStatus: document.querySelector('#satelliteSceneStatus')?.textContent
        })""")
        assert runtime_state["cesium"] == "object" and runtime_state["scene"] == "object", {
            "runtime": runtime_state, "browser_errors": failures
        }
        page.locator('[data-view="scene"]').click()
        page.locator("#satelliteSceneStatus").wait_for()
        page.wait_for_function(
            "() => document.querySelectorAll('#satelliteSceneCanvas canvas').length > 0 && "
            "document.querySelector('#satelliteSceneStatus')?.dataset.kind === 'success'",
            timeout=60000,
        )
        assert "演示数据" in page.locator("#satelliteSceneStatus").inner_text()
        page.locator('[data-scene-action="track"]').click()
        page.locator('[data-scene-action="free"]').click()
        page.locator('[data-scene-action="home"]').click()
        assert not failures, failures
        browser.close()
