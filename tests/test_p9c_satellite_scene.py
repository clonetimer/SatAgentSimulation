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


def test_scene_workspace_contract_and_local_globe_runtime(tmp_path: Path):
    with _client(tmp_path) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert 'data-view="scene"' in index.text
        assert 'id="satelliteSceneView"' in index.text
        assert 'id="satelliteSceneCanvas"' in index.text
        assert 'src="/assets/vendor/globe/globe.gl.min.js"' in index.text
        assert '"three":"/assets/vendor/globe/three.module.js"' in index.text
        assert 'type="module" src="/assets/satellite-scene.js' in index.text

        globe = client.get("/assets/vendor/globe/globe.gl.min.js")
        three = client.get("/assets/vendor/globe/three.module.js")
        three_core = client.get("/assets/vendor/globe/three.core.js")
        earth = client.get("/assets/vendor/globe/textures/earth-blue-marble.jpg")
        manifest = client.get("/assets/vendor/globe/vendor-manifest.json")

        assert globe.status_code == 200 and len(globe.content) > 100_000
        assert three.status_code == 200 and len(three.content) > 100_000
        assert three_core.status_code == 200 and len(three_core.content) > 100_000
        assert earth.status_code == 200 and len(earth.content) > 50_000
        assert manifest.status_code == 200
        payload = manifest.json()
        assert payload["packages"]["globe.gl"]["version"] == "2.46.2"
        assert payload["packages"]["three-globe"]["version"] == "2.45.6"
        assert payload["packages"]["three"]["version"] == "0.186.1"
        assert "local" in payload["network_policy"].lower()
        assert "NASA" in payload["textures"]["earth-blue-marble.jpg"]["credit"]
        assert payload["textures"]["earth-blue-marble.jpg"]["license"] == "NASA public-domain imagery"

        csp = index.headers["content-security-policy"]
        assert "'unsafe-eval'" not in csp
        assert "'wasm-unsafe-eval'" not in csp


def test_scene_module_uses_globe_gl_and_explicit_demo_provenance():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    assert "window.Globe" in scene
    assert "earth-blue-marble.jpg" in scene
    assert "objectsData" in scene
    assert "pathsData" in scene
    assert "不代表真实 Basilisk 仿真遥测" in scene
    assert "window.SatScene = { enter, exit: destroy, track, freeCamera, home }" in scene
    assert "setInterval" not in scene
    assert "unsafe-eval" not in scene


def test_scene_browser_loads_offline_globe_and_camera_controls(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser")
    evidence_dir = os.environ.get("SAT_SIM_SCENE_EVIDENCE_DIR")
    with _client(tmp_path) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            failures: list[str] = []
            external_requests: list[str] = []

            def proxy(route):
                parsed = urlparse(route.request.url)
                if parsed.scheme in {"http", "https"} and parsed.netloc != "workbench.test":
                    external_requests.append(route.request.url)
                    route.abort()
                    return
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
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            for cycle in range(3):
                page.locator("#wizardSceneShortcutBtn").click()
                scene_status = page.locator("#satelliteSceneStatus")
                scene_status.filter(has_text="场景已就绪").wait_for(timeout=30000)
                canvas = page.locator("#satelliteSceneCanvas canvas").first
                canvas.wait_for(timeout=30000)
                assert page.locator("#satelliteSceneCanvas canvas").count() == 1
                assert "演示数据" in scene_status.inner_text()
                track = page.locator('[data-scene-action="track"]')
                free = page.locator('[data-scene-action="free"]')
                track.click()
                playwright.expect(track).to_have_attribute("aria-pressed", "true")
                box = canvas.bounding_box()
                assert box is not None
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(x, y)
                page.mouse.down()
                page.mouse.move(x + 50, y + 15, steps=5)
                page.mouse.up()
                playwright.expect(free).to_have_attribute("aria-pressed", "true")
                playwright.expect(track).to_have_attribute("aria-pressed", "false")
                free.click()
                page.locator('[data-scene-action="home"]').click()
                if evidence_dir and cycle == 2:
                    output = Path(evidence_dir)
                    output.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(output / "scene-desktop.png"), full_page=True)
                page.locator("#satelliteSceneView [data-return-wizard]").click()
                page.locator("#wizardView").wait_for(state="visible")
                assert page.locator("#satelliteSceneCanvas canvas").count() == 0
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()
