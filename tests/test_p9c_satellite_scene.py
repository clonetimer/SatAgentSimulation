from __future__ import annotations

import json
import os
import struct
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
        gltf_loader = client.get("/assets/vendor/globe/loaders/GLTFLoader.js")
        meshopt_decoder = client.get("/assets/vendor/globe/loaders/meshopt_decoder.module.js")
        buffer_geometry_utils = client.get("/assets/vendor/globe/utils/BufferGeometryUtils.js")
        skeleton_utils = client.get("/assets/vendor/globe/utils/SkeletonUtils.js")

        assert globe.status_code == 200 and len(globe.content) > 100_000
        assert three.status_code == 200 and len(three.content) > 100_000
        assert three_core.status_code == 200 and len(three_core.content) > 100_000
        assert earth.status_code == 200 and len(earth.content) > 50_000
        assert gltf_loader.status_code == 200 and len(gltf_loader.content) > 100_000
        assert meshopt_decoder.status_code == 200 and len(meshopt_decoder.content) > 10_000
        assert buffer_geometry_utils.status_code == 200 and len(buffer_geometry_utils.content) > 10_000
        assert skeleton_utils.status_code == 200 and len(skeleton_utils.content) > 10_000
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
    assert "window.SatScene = {" in scene
    assert "updateTelemetry, setRunTelemetry, clearTelemetrySource" in scene
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
                try:
                    scene_status.filter(has_text="场景已就绪").wait_for(timeout=30000)
                except Exception as error:
                    diagnostic = (
                        f"cycle={cycle}; url={page.url}; active_view={page.locator('body').get_attribute('data-active-view')}; "
                        f"scene_status={scene_status.inner_text()!r}; canvas_count={page.locator('#satelliteSceneCanvas canvas').count()}; "
                        f"page_failures={failures!r}; external_requests={external_requests!r}"
                    )
                    page.screenshot(path=str(tmp_path / f"p9c-scene-failure-{cycle}.png"), full_page=True)
                    raise AssertionError(diagnostic) from error
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
            orbit_tab.click()
            assert orbit_tab.get_attribute("aria-pressed") == "true"
            assert page.locator("#birdsCadViewer").is_hidden()
            assert page.locator("#satelliteOrbitMode").is_visible()
        finally:
            browser.close()


def _write_triangle_glb(path: Path) -> None:
    """Create a tiny valid glTF 2.0 binary fixture for real browser loader tests."""
    positions = struct.pack(
        "<9f",
        -0.05, -0.05, 0.0,
         0.05, -0.05, 0.0,
         0.00,  0.05, 0.0,
    )
    indices = struct.pack("<3H", 0, 1, 2)
    binary = positions + indices
    document = {
        "asset": {"version": "2.0", "generator": "SatAgentSimulation browser fixture"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "BIRDS4_fixture_root", "mesh": 0}],
        "meshes": [{
            "name": "FixtureTriangle",
            "primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}],
        }],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [-0.05, -0.05, 0.0], "max": [0.05, 0.05, 0.0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary_chunk = binary + b"\0" * ((4 - len(binary) % 4) % 4)
    body = (
        struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk
        + struct.pack("<I4s", len(binary_chunk), b"BIN\0") + binary_chunk
    )
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def test_birds_cad_viewer_loads_glb_and_supports_real_browser_interactions(tmp_path: Path):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser")

    glb_path = tmp_path / "birds4-fixture.glb"
    _write_triangle_glb(glb_path)
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
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator("#satelliteSceneStatus").filter(has_text="场景已就绪").wait_for(timeout=30000)
            orbit_tab = page.locator('[data-scene-mode="orbit"]')
            cad_tab = page.locator('[data-scene-mode="cad"]')
            assert orbit_tab.get_attribute("aria-pressed") == "true"
            assert page.locator("#birdsCadViewer").is_hidden()
            cad_tab.click()
            assert cad_tab.get_attribute("aria-pressed") == "true"
            viewer = page.locator("#birdsCadViewer")
            viewer.wait_for(state="visible", timeout=10000)
            page.locator("#birdsCadFile").set_input_files(str(glb_path))
            page.locator("#birdsCadStatus").filter(has_text="已加载 birds4-fixture.glb").wait_for(timeout=30000)
            tree_nodes = page.locator("#birdsCadTree .birds-cad-tree-node")
            assert tree_nodes.count() >= 1
            page.locator('#birdsCadTree [aria-label="选择 BIRDS4_fixture_root"]').first.click()
            assert "BIRDS4_fixture_root" in page.locator("#birdsCadInspector").inner_text()

            search = page.locator("#birdsCadSearch")
            search.fill("fixture")
            page.locator('#birdsCadTree [aria-label="选择 BIRDS4_fixture_root"]').wait_for()
            visibility = page.locator("#birdsCadTree .birds-cad-visibility").first
            visibility.click()
            assert visibility.get_attribute("aria-pressed") == "true"
            page.locator('[data-cad-tree-action="restore"]').click()
            assert visibility.get_attribute("aria-pressed") == "false"
            page.locator('[data-cad-tree-action="isolate"]').click()
            for view in ("fit", "axes", "home", "x", "-x", "y", "-y", "z", "-z"):
                page.locator(f'[data-cad-view="{view}"]').click()
            assert not external_requests, external_requests
            assert not failures, failures
            if evidence_dir:
                output = Path(evidence_dir)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "birds4-cad-viewer-desktop.png"), full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            layout = page.locator("#birdsCadViewer").evaluate(
                "(node) => ({width: node.clientWidth, scrollWidth: node.scrollWidth, viewport: node.querySelector('.birds-cad-viewport').clientWidth})"
            )
            assert layout["width"] > 0
            assert layout["scrollWidth"] <= layout["width"] + 2, layout
            assert layout["viewport"] >= 280, layout
            if evidence_dir:
                page.screenshot(path=str(Path(evidence_dir) / "birds4-cad-viewer-mobile.png"), full_page=True)
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()
