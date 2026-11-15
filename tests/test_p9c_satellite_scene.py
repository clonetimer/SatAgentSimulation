from __future__ import annotations

import json
import os
import struct
import time
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


def test_simulation_geometry_view_requires_validated_whole_spacecraft_configuration():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    html = (root / "index.html").read_text(encoding="utf-8")
    viewer = (root / "birds-cad-viewer.js").read_text(encoding="utf-8")
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-scene-mode="simulation"' in html
    assert 'id="simulationGeometryCanvas"' in html
    assert 'id="simulationGeometryRows"' in html
    assert "setSimulationConfiguration" in app and "validated: true" in app
    assert "whole_spacecraft." in scene
    assert "hasOwnProperty.call(values,'rw_configuration')" in scene
    assert "hasOwnProperty.call(values,'rw_geometry_mode')" in scene
    assert "rw_mount_b_" in scene and "rw_axis_b_" in scene
    assert "rWB_B" in html and "gsHat_B" in html
    assert "不是型号 as-built 安装证明" in html
    assert "simulationRoot.hidden=!simulation" in viewer


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
                semantic_node = page.locator(".p9d-tree-item").first
                semantic_node.wait_for(state="visible", timeout=15000)
                selected_name = semantic_node.inner_text()
                semantic_node.click()
                page.locator("#sceneObjectName").filter(has_text=selected_name).wait_for(timeout=10000)
                assert "未建立 CAD / GLB 节点映射" in page.locator("#sceneObjectMapping").inner_text()
                assert "未审定，不生成三维位置" in page.locator("#sceneObjectAnchor").inner_text()
                simulation_tab = page.locator('[data-scene-mode="simulation"]')
                simulation_tab.click()
                page.locator("#simulationGeometryCanvas").wait_for(state="visible")
                rejected = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
                    capabilityId: 'whole_spacecraft.unified_native.v1',
                    values: {parameters: {values: {rw_configuration: 'pyramid_4', rw_geometry_mode: 'per_wheel', rw_mount_radius_m: 0, rw_mount_b_x_m_0: 0.12}}},
                    validated: false
                })""")
                assert rejected is False
                accepted = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
                    capabilityId: 'whole_spacecraft.unified_native.v1',
                    values: {parameters: {values: {rw_configuration: 'pyramid_4', rw_geometry_mode: 'per_wheel', rw_mount_radius_m: 0, rw_mount_b_x_m_0: 0.12}}},
                    validated: true
                })""")
                assert accepted is True
                assert page.locator("#simulationGeometryRows tr").count() == 4
                assert "TaskSpec 校验通过" in page.locator("#simulationGeometryBadge").inner_text()
                page.evaluate("window.SatScene.clearSimulationConfiguration()")
                assert page.locator("#simulationGeometryRows tr").count() == 1
                assert "等待整星 TaskSpec 校验" in page.locator("#simulationGeometryBadge").inner_text()
                page.locator('[data-scene-mode="orbit"]').click()
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


def _write_triangle_glb(path: Path) -> None:
    """Create a tiny valid glTF 2.0 binary fixture for real browser loader tests."""
    positions = struct.pack(
        "<9f",
        -0.05, -0.05, 0.0,
         0.05, -0.05, 0.0,
         0.00,  0.05, 0.0,
    )
    normals = struct.pack("<9f", 0, 0, 1, 0, 0, 1, 0, 0, 1)
    indices = struct.pack("<3H", 0, 1, 2)
    binary = positions + normals + indices
    document = {
        "asset": {"version": "2.0", "generator": "SatAgentSimulation browser fixture"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "BIRDS4_fixture_root", "mesh": 0}],
        "meshes": [{
            "name": "FixtureTriangle",
            "primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "material": 0, "mode": 4}],
        }],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.12, 0.72, 0.94, 1], "metallicFactor": 0.12, "roughnessFactor": 0.38}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(normals), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions) + len(normals), "byteLength": len(indices), "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [-0.05, -0.05, 0.0], "max": [0.05, 0.05, 0.0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0.0, 0.0, 1.0], "max": [0.0, 0.0, 1.0]},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
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

    real_glb = os.environ.get("SAT_SIM_BIRDS4_REAL_GLB_PATH")
    if real_glb:
        glb_path = Path(real_glb).resolve()
        assert glb_path.is_file(), glb_path
        assert glb_path.stat().st_size > 10 * 1024 * 1024, "real BIRDS-4 assembly GLB must be a full-size model"
    else:
        glb_path = tmp_path / "birds4-fixture.glb"
        _write_triangle_glb(glb_path)
    model_name = glb_path.name
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
            assert page.locator("#satelliteSceneTitle").inner_text() == "BIRDS-4 整星 CAD 参考"
            assert page.locator(".satellite-scene-actions").is_hidden()
            assert page.locator(".p9d-config-panel").is_hidden()
            grid_columns = page.locator(".p9d-scene-layout").evaluate("(node) => getComputedStyle(node).gridTemplateColumns")
            assert len(grid_columns.split()) == 1, grid_columns
            viewer = page.locator("#birdsCadViewer")
            viewer.wait_for(state="visible", timeout=10000)
            load_started = time.monotonic()
            page.locator("#birdsCadFile").set_input_files(str(glb_path))
            page.locator("#birdsCadStatus").filter(has_text=f"已加载 {model_name}").wait_for(timeout=180000)
            load_seconds = time.monotonic() - load_started
            # Preserve the untouched initial fit separately from later inspection
            # actions (which deliberately focus individual assembly occurrences).
            page.wait_for_timeout(250)
            initial_render_state = page.evaluate("window.BirdsCadViewer.getDiagnostics()")
            if evidence_dir and real_glb:
                output = Path(evidence_dir)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "birds4-cad-viewer-real-overview-desktop.png"), full_page=True)
                (output / "birds4-real-model-overview-performance.json").write_text(
                    json.dumps({
                        "model": model_name,
                        "file_size_bytes": glb_path.stat().st_size,
                        "load_seconds": round(load_seconds, 3),
                        "initial_render": initial_render_state,
                        "browser_heap_used_bytes": page.evaluate("performance.memory?.usedJSHeapSize ?? null"),
                        "desktop_viewport": {"width": 1440, "height": 960},
                    }, indent=2), encoding="utf-8"
                )
            tree_nodes = page.locator("#birdsCadTree .birds-cad-tree-node")
            assert tree_nodes.count() >= 1
            if real_glb:
                # Real assemblies start with their top-level groups collapsed.
                # Exercise the same disclosure interaction a user performs before
                # selecting a component; never click a hidden descendant.
                first_group = page.locator("#birdsCadTree details.birds-cad-tree-group").first
                if first_group.count():
                    first_group.locator(":scope > summary").click()
                visible_node = page.locator("#birdsCadTree .birds-cad-tree-node:visible").first
                visible_node.wait_for(state="visible")
                visible_node.click()
            else:
                page.locator('#birdsCadTree [aria-label="选择 BIRDS4_fixture_root"]').first.click()
            inspector_text = page.locator("#birdsCadInspector").inner_text()
            assert inspector_text.strip()
            render_state = page.evaluate("window.BirdsCadViewer.getDiagnostics()")
            assert render_state["visible_meshes"] >= 1, render_state
            assert render_state["triangles"] >= 1, render_state
            if real_glb:
                assert render_state["scene_nodes"] >= 500, render_state
                assert render_state["visible_meshes"] >= 100, render_state
                assert render_state["triangles"] >= 1000, render_state

            search = page.locator("#birdsCadSearch")
            search.fill("B4" if real_glb else "fixture")
            if real_glb:
                page.wait_for_function("document.querySelectorAll('#birdsCadTree .birds-cad-tree-node').length > 0")
            else:
                page.locator('#birdsCadTree [aria-label="选择 BIRDS4_fixture_root"]').wait_for()
            visibility = page.locator("#birdsCadTree .birds-cad-visibility").first
            visibility.click()
            assert visibility.get_attribute("aria-pressed") == "true"
            page.locator('[data-cad-tree-action="restore"]').click()
            assert visibility.get_attribute("aria-pressed") == "false"
            page.locator('[data-cad-tree-action="isolate"]').click()
            for view in ("fit", "axes", "home", "x", "-x", "y", "-y", "z", "-z"):
                page.locator(f'[data-cad-view="{view}"]').click()
            tree_nodes.first.dblclick()
            canvas = viewer.locator(".birds-cad-viewport canvas")
            box = canvas.bounding_box()
            assert box is not None
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(cx, cy)
            page.mouse.down()
            page.mouse.move(cx + 24, cy + 12, steps=3)
            page.mouse.up()
            page.mouse.wheel(0, -120)
            assert not external_requests, external_requests
            assert not failures, failures
            if evidence_dir:
                output = Path(evidence_dir)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / ("birds4-cad-viewer-real-desktop.png" if real_glb else "birds4-cad-viewer-desktop.png")), full_page=True)
                metrics = {
                    "model": model_name,
                    "real_assembly": bool(real_glb),
                    "file_size_bytes": glb_path.stat().st_size,
                    "load_seconds": round(load_seconds, 3),
                    "render": render_state,
                    "browser_heap_used_bytes": page.evaluate("performance.memory?.usedJSHeapSize ?? null"),
                    "desktop_viewport": {"width": 1440, "height": 960},
                }
                (output / ("birds4-real-model-performance.json" if real_glb else "birds4-fixture-performance.json")).write_text(
                    json.dumps(metrics, indent=2), encoding="utf-8"
                )

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            layout = page.locator("#birdsCadViewer").evaluate(
                "(node) => ({width: node.clientWidth, scrollWidth: node.scrollWidth, viewport: node.querySelector('.birds-cad-viewport').clientWidth})"
            )
            assert layout["width"] > 0
            assert layout["scrollWidth"] <= layout["width"] + 2, layout
            assert layout["viewport"] >= 280, layout
            if evidence_dir:
                page.screenshot(path=str(Path(evidence_dir) / ("birds4-cad-viewer-real-mobile.png" if real_glb else "birds4-cad-viewer-mobile.png")), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 960})
            orbit_tab.click()
            assert page.locator("#birdsCadViewer").is_hidden()
            assert page.locator("#satelliteSceneTitle").inner_text() == "卫星数字场景"
            assert page.locator(".satellite-scene-actions").is_visible()
            assert page.locator(".p9d-config-panel").is_visible()
            assert len(page.locator(".p9d-scene-layout").evaluate("(node) => getComputedStyle(node).gridTemplateColumns").split()) == 2
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()

def test_scene_object_selection_preserves_physical_and_assembly_semantics_without_fabricating_anchors():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    for field in ("physicalClass", "sceneTarget", "spatialAnchor", "executionCapabilityId",
                  "configurationScope", "instanceSemantics", "inventoryCount"):
        assert field in app and field in scene
    assert "notifySceneObjectSelection" in app
    assert "未审定，不生成三维位置" in scene
    assert "未建立 CAD / GLB 节点映射" in scene
    assert "不代表实装设备数量" in scene
    for element_id in ("sceneObjectName", "sceneObjectClass", "sceneObjectTarget", "sceneObjectConfigScope",
                       "sceneObjectAnchor", "sceneObjectMapping", "sceneObjectInventory", "sceneObjectSemanticsNote"):
        assert f'id="{element_id}"' in html
