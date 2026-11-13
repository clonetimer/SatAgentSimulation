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


def test_scene_workspace_contract_and_local_assets(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert 'data-view="scene"' in index.text
        assert 'id="satelliteSceneView"' in index.text
        assert 'id="satelliteSceneCanvas"' in index.text
        assert '"three":"/assets/vendor/three/build/three.module.js"' in index.text
        assert 'type="module" src="/assets/satellite-scene.js' in index.text

        three = client.get("/assets/vendor/three/build/three.module.js")
        gltf_loader = client.get("/assets/vendor/three/examples/jsm/loaders/GLTFLoader.js")
        controls = client.get("/assets/vendor/three/examples/jsm/controls/OrbitControls.js")
        west = client.get("/assets/vendor/earth/naturalearth-ii-west.jpg")
        east = client.get("/assets/vendor/earth/naturalearth-ii-east.jpg")
        model = client.get("/assets/satellite-demo.gltf")
        manifest = client.get("/assets/vendor/scene-runtime-manifest.json")

        assert three.status_code == 200 and len(three.content) > 100_000
        assert gltf_loader.status_code == 200
        assert controls.status_code == 200
        assert west.status_code == 200 and east.status_code == 200
        assert model.status_code == 200
        assert model.json()["asset"]["extras"]["license"] == "CC0-1.0"
        assert manifest.status_code == 200
        assert manifest.json()["three"]["version"] == "0.187.0"

        csp = index.headers["content-security-policy"]
        assert "'unsafe-eval'" not in csp
        assert "'wasm-unsafe-eval'" not in csp
        assert "worker-src 'self'" in csp


def test_scene_module_uses_local_assets_and_explicit_demo_provenance():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    assert "naturalearth-ii-west.jpg" in scene
    assert "naturalearth-ii-east.jpg" in scene
    assert "satellite-demo.gltf" in scene
    assert "不代表真实仿真遥测" in scene
    assert "window.SatScene = { enter, exit: destroy, track, freeCamera, home }" in scene
    assert "setInterval" not in scene
    assert "eval(" not in scene
    assert "unsafe-eval" not in scene


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
        page = browser.new_page(viewport={"width": 1440, "height": 960})
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
        page.on("console", lambda message: failures.append(f"console error: {message.text}") if message.type == "error" else None)
        page.route("https://workbench.test/**", proxy)
        page.goto("https://workbench.test/", wait_until="domcontentloaded")
        page.locator("#wizardSceneShortcutBtn").click()
        page.locator("#satelliteSceneStatus").filter(has_text="场景已就绪").wait_for(timeout=30000)
        page.locator("#satelliteSceneCanvas canvas").first.wait_for(timeout=30000)
        assert "演示数据" in page.locator("#satelliteSceneStatus").inner_text()
        page.locator('[data-scene-action="track"]').click()
        page.locator('[data-scene-action="free"]').click()
        page.locator('[data-scene-action="home"]').click()
        assert not failures, failures
        browser.close()
