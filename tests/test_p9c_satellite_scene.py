from __future__ import annotations

import json
import hashlib
import math
import os
import struct
import subprocess
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
        assert 'id="sceneWorkspaceTaskCenterBtn"' in index.text
        assert 'id="sceneWorkspaceSceneBtn"' in index.text
        assert 'id="sceneWorkspaceCreatorBtn"' in index.text
        assert 'class="scene-editor-controls"' in index.text
        assert 'class="satellite-scene-heading"' not in index.text
        assert 'data-scene-create="offline"' in index.text
        assert 'data-scene-create="interactive"' in index.text
        assert 'data-scene-create="continuous"' in index.text
        assert 'id="sceneTaskCreateModal"' in index.text
        assert 'aria-modal="true"' in index.text
        app = client.get("/assets/app.js")
        assert app.status_code == 200
        assert "switchView('wizard');" in app.text
        assert "navigateToWizardShortcut('normal_run');" in app.text
        assert "navigateToView(target);" in app.text
        assert "closeSceneTaskModal" in app.text
        assert "event.key === 'Escape'" in app.text
        assert "sceneTaskModal.querySelectorAll" in app.text
        assert 'id="sceneTaskContextState"' in index.text
        assert 'id="sceneTaskContextId"' in index.text
        assert 'id="sceneTaskContextTime"' in index.text
        assert 'id="sceneTaskContextQuality"' in index.text
        assert 'id="sceneTaskContextCapability"' in index.text
        assert 'id="sceneTaskContextLifecycle"' in index.text
        assert 'id="sceneTaskContextAdcsQuality"' in index.text
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
    assert "sceneTaskContextState" in scene
    assert "历史 Run · 已封存" in scene
    assert "实时仿真 · 已关联" in scene
    assert "updateTelemetry, updateAdcsTelemetry, setAdcsTelemetryPending, invalidateAdcsTelemetry, createStrictTelemetryTimeJoin" in scene
    assert "setInterval" not in scene
    assert "unsafe-eval" not in scene


def test_scene_source_context_exposes_capability_lifecycle_and_independent_adcs_quality():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    html = (root / "index.html").read_text(encoding="utf-8")
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    continuous = (root / "continuous.js").read_text(encoding="utf-8")
    for element_id in (
        "sceneTaskContextCapability", "sceneTaskContextLifecycle",
        "sceneTaskContextAdcsQuality",
    ):
        assert f'id="{element_id}"' in html
    assert "function setTelemetrySessionContext(context = {})" in scene
    assert "lifecycleStateLabel(state.telemetryLifecycleState" in scene
    assert "state.adcsTelemetryStatus" in scene
    assert "setTelemetrySessionContext?.({" in app
    assert "capabilityId: state.interactiveSession.capability_id" in app
    assert "setTelemetrySessionContext?.({" in continuous
    assert "setAdcsTelemetryPending?.(" in app
    assert "setAdcsTelemetryPending?.(" in continuous
    assert "capabilityId: info.session?.capability_id" in continuous
    assert "lifecycleState: info.session?.state" in continuous


def test_simulation_geometry_view_requires_validated_whole_spacecraft_configuration():
    root = Path(__file__).resolve().parents[1] / "src/sat_sim/web"
    html = (root / "index.html").read_text(encoding="utf-8")
    viewer = (root / "birds-cad-viewer.js").read_text(encoding="utf-8")
    scene = (root / "satellite-scene.js").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-scene-mode="simulation"' in html
    assert 'data-scene-mode="simulation" aria-pressed="true">物理构型' in html
    assert 'data-scene-mode="orbit" aria-pressed="false"' in html
    assert 'id="simulationGeometryMode" class="simulation-geometry-mode" aria-label=' in html
    assert 'id="satelliteOrbitMode" class="satellite-orbit-mode" hidden' in html
    assert "integrateWheelPhaseFromTelemetry" in scene
    assert "setSimulationExplodedView" in scene
    assert "closest?.('#simulationExplodeToggle')" in scene
    assert "simulationExplodedView" in scene
    assert 'id="simulationExplodeToggle"' in html
    assert "physicalPosition" in scene
    assert "function advanceWheelPhaseState(" in scene
    assert "function integrateWheelPhaseFromTelemetry(index, simTimeS, wheelPhases" in scene
    assert "wheelPhases" in scene
    assert "phase?.valid" in scene
    assert "相对累计（非绝对相位）" in scene
    assert 'id="simulationGeometryCanvas"' in html
    assert 'id="simulationGeometryRows"' in html
    assert 'id="simulationSensorRows"' in html
    assert 'id="simulationSensorTelemetryRows"' in html
    assert "adcs.imu.gyro_x_rad_s" in scene
    assert "adcs.magnetometer.field_x_t" in scene
    assert "adcs.star_tracker.valid" in scene
    assert "adcs.sun_sensor.valid" in scene
    assert "adcs.rw.speed_rad_s_0" in scene
    assert "adcs.rw.effective_max_torque_nm_" in scene
    assert "反作用轮 RW" in scene
    assert "updateSimulationWheelMotion" in scene
    assert "speed * time" not in scene
    assert "simulationWheelMeshes.push" in scene
    assert "renderSensorTelemetry" in scene
    for element_id in ("simulationHubMass", "simulationHubIxx", "simulationHubIyy", "simulationHubIzz"):
        assert f'id="{element_id}"' in html
    assert "setSimulationConfiguration" in app and "validated: true" in app
    assert "clearSimulationConfiguration" in app
    assert "whole_spacecraft." in scene
    assert "hasOwnProperty.call(values,'rw_configuration')" in scene
    assert "hasOwnProperty.call(values,'rw_geometry_mode')" in scene
    assert "rw_mount_b_" in scene and "rw_axis_b_" in scene
    assert "rWB_B" in html and "gsHat_B" in html
    assert "sensorPos_B" in scene
    assert "imu_position_b_" in scene
    assert "magnetometer_mount_" in scene
    assert "sun_sensor_normal_b_" in scene
    assert "B→P 321" in scene and "B→S 321" in scene
    assert "magAxes=magnetometerAxesInBodyFrame" in scene
    assert "starAxes=starTrackerAxesInBodyFrame" in scene
    assert "star_tracker_mount_" in scene
    capability = (Path(__file__).resolve().parents[1] / "src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml").read_text(encoding="utf-8")
    for key in ("star_tracker_mount_yaw_deg", "star_tracker_mount_pitch_deg", "star_tracker_mount_roll_deg"):
        assert key in capability
    assert "label: 星敏安装航向角" in capability
    assert "description: 机体系 B 到星敏机壳系 C" in capability
    assert "setOrbitSceneVisible, imuPlatformAxesInBodyFrame, magnetometerAxesInBodyFrame" in scene
    assert "new THREE.Vector3(...normal).normalize()" in scene
    assert "方向参考自 B 原点" in scene
    assert "spacecraft_mass_kg" in scene
    assert "spacecraft_inertia_xx_kg_m2" in scene
    assert "不是型号 as-built 安装证明" in html
    assert "simulationRoot.hidden=!simulation" in viewer
    assert "setOrbitSceneVisible" in viewer
    assert "pauseAnimation" in scene and "resumeAnimation" in scene
    assert "setSimulationGeometryVisible?.(false)" in app
    assert "setOrbitSceneVisible?.(false)" in app


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
                # Each cycle ends with browser Back returning from professional
                # modeling to the scene. Do not click the wizard-only shortcut
                # when the scene is already active on the next iteration.
                if page.locator("body").get_attribute("data-active-view") != "scene":
                    page.locator("#wizardSceneShortcutBtn").click()
                playwright.expect(page.locator(".app-sidebar")).to_be_hidden()
                playwright.expect(page.locator("#workbenchNavigation")).to_be_hidden()
                playwright.expect(page.locator(".scene-workspace-nav")).to_be_visible()
                playwright.expect(page.locator("#sceneWorkspaceTaskCenterBtn")).to_be_visible()
                playwright.expect(page.locator("#sceneWorkspaceCreatorBtn")).to_be_visible()
                if cycle == 0:
                    playwright.expect(page.locator('[data-scene-mode="simulation"]')).to_have_attribute("aria-pressed", "true")
                    playwright.expect(page.locator("#simulationGeometryCanvas")).to_be_visible()
                    playwright.expect(page.locator("#satelliteOrbitMode")).to_be_hidden()
                    playwright.expect(page.locator(".satellite-scene-actions")).to_be_visible()
                    playwright.expect(page.locator("#satelliteSceneTitle")).to_have_text("ADCS 仿真构型")
                # Orbit tests must explicitly enter the orbit mode; the user's
                # physical-configuration default is not reverted for CI.
                page.locator('[data-scene-mode="orbit"]').click()
                scene_status = page.locator("#satelliteSceneStatus")
                try:
                    page.locator("#satelliteSceneCanvas canvas").wait_for(state="visible", timeout=30000)
                    playwright.expect(page.locator('[data-scene-action="track"]')).to_be_enabled(timeout=30000)
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
                # Readiness is a renderer state, not one particular status message.
                # A retained Run may already have supplied a later telemetry status.
                assert "加载超时" not in scene_status.inner_text()
                assert page.locator("#sceneDataSource").get_attribute("data-state") in {"demo", "sealed", "live"}
                semantic_node = page.locator(".p9d-tree-item").first
                semantic_node.wait_for(state="visible", timeout=15000)
                selected_name = semantic_node.inner_text()
                hit_test = semantic_node.evaluate("""node => {
                    const rect = node.getBoundingClientRect();
                    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                    return {
                      target: node.outerHTML,
                      top: top?.outerHTML || null,
                      panelZ: getComputedStyle(node.closest('.p9d-config-panel')).zIndex,
                      sceneZ: getComputedStyle(document.querySelector('.satellite-scene-shell')).zIndex,
                      receivesPointer: Boolean(top && (top === node || node.contains(top))),
                    };
                }""")
                assert hit_test["receivesPointer"], f"OBJECT_TREE_POINTER_INTERCEPTED: {hit_test}"
                semantic_node.click()
                page.locator("#sceneObjectName").filter(has_text=selected_name).wait_for(timeout=10000)
                assert "未建立 CAD / GLB 节点映射" in page.locator("#sceneObjectMapping").inner_text()
                assert "未审定，不生成三维位置" in page.locator("#sceneObjectAnchor").inner_text()
                simulation_tab = page.locator('[data-scene-mode="simulation"]')
                simulation_tab.click()
                page.locator("#simulationGeometryCanvas").wait_for(state="visible")
                rejected = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
                    capabilityId: 'whole_spacecraft.unified_native.v1',
                    values: {parameters: {values: {rw_configuration: 'pyramid_4', rw_geometry_mode: 'per_wheel', rw_mount_radius_m: 0, rw_mount_b_x_m_0: 0.12, spacecraft_mass_kg: 120, spacecraft_inertia_xx_kg_m2: 110, spacecraft_inertia_yy_kg_m2: 90, spacecraft_inertia_zz_kg_m2: 70, imu_position_b_x_m: 0.02, imu_position_b_y_m: -0.01, imu_position_b_z_m: 0.03, imu_mount_yaw_deg: 10, imu_mount_pitch_deg: -2, imu_mount_roll_deg: 1, magnetometer_mount_yaw_deg: 2, magnetometer_mount_pitch_deg: 3, magnetometer_mount_roll_deg: 4, star_tracker_mount_yaw_deg: 90, star_tracker_mount_pitch_deg: 0, star_tracker_mount_roll_deg: 0, sun_sensor_normal_b_x: 0, sun_sensor_normal_b_y: 0, sun_sensor_normal_b_z: -1}}},
                    validated: false
                })""")
                assert rejected is False
                accepted = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
                    capabilityId: 'whole_spacecraft.unified_native.v1',
                    values: {parameters: {values: {rw_configuration: 'pyramid_4', rw_geometry_mode: 'per_wheel', rw_mount_radius_m: 0, rw_mount_b_x_m_0: 0.12, spacecraft_mass_kg: 120, spacecraft_inertia_xx_kg_m2: 110, spacecraft_inertia_yy_kg_m2: 90, spacecraft_inertia_zz_kg_m2: 70, imu_position_b_x_m: 0.02, imu_position_b_y_m: -0.01, imu_position_b_z_m: 0.03, imu_mount_yaw_deg: 10, imu_mount_pitch_deg: -2, imu_mount_roll_deg: 1, magnetometer_mount_yaw_deg: 2, magnetometer_mount_pitch_deg: 3, magnetometer_mount_roll_deg: 4, star_tracker_mount_yaw_deg: 90, star_tracker_mount_pitch_deg: 0, star_tracker_mount_roll_deg: 0, sun_sensor_normal_b_x: 0, sun_sensor_normal_b_y: 0, sun_sensor_normal_b_z: -1}}},
                    validated: true
                })""")
                assert accepted is True
                assert page.locator("#simulationGeometryRows tr").count() == 4
                assert page.locator("#simulationSensorRows tr").count() == 4
                assert "IMU" in page.locator("#simulationSensorRows").inner_text()
                assert "B→P 321" in page.locator("#simulationSensorRows").inner_text()
                assert "B→C 321" in page.locator("#simulationSensorRows").inner_text()
                imu_axes = page.evaluate("window.SatScene.imuPlatformAxesInBodyFrame(90, 0, 0)")
                assert all(abs(a-b) < 1e-9 for a,b in zip(imu_axes[0], [0, 1, 0]))
                assert all(abs(a-b) < 1e-9 for a,b in zip(imu_axes[1], [-1, 0, 0]))
                assert all(abs(a-b) < 1e-9 for a,b in zip(imu_axes[2], [0, 0, 1]))
                assert "B→S 321" in page.locator("#simulationSensorRows").inner_text()
                assert "nHat_B" in page.locator("#simulationSensorRows").inner_text()
                star_axes = page.evaluate("window.SatScene.starTrackerAxesInBodyFrame(90, 0, 0)")
                assert all(abs(a-b) < 1e-9 for a,b in zip(star_axes[0], [0, 1, 0]))
                assert all(abs(a-b) < 1e-9 for a,b in zip(star_axes[1], [-1, 0, 0]))
                assert all(abs(a-b) < 1e-9 for a,b in zip(star_axes[2], [0, 0, 1]))
                mag_axes = page.evaluate("window.SatScene.magnetometerAxesInBodyFrame(90, 0, 0)")
                assert all(abs(a-b) < 1e-9 for a,b in zip(mag_axes[0], [0, 1, 0]))
                assert all(abs(a-b) < 1e-9 for a,b in zip(mag_axes[1], [-1, 0, 0]))
                css_normal = page.evaluate("""() => {
                    const v = [0, 0, -1];
                    return {norm: Math.hypot(...v), z: v[2]};
                }""")
                assert abs(css_normal["norm"] - 1.0) < 1e-9 and css_normal["z"] == -1
                assert "0.0200, -0.0100, 0.0300" in page.locator("#simulationSensorRows").inner_text()
                assert "120.0000 kg" in page.locator("#simulationHubMass").inner_text()
                assert "110.0000 kg·m²" in page.locator("#simulationHubIxx").inner_text()
                assert "TaskSpec 校验通过" in page.locator("#simulationGeometryBadge").inner_text()
                page.evaluate("""() => window.SatScene.setRunTelemetry([{
                    sim_time_s: 12.5, orbit: {position_m: {x: 7000000, y: 0, z: 0}},
                    values: {
                      'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0,
                      'adcs.imu.gyro_x_rad_s': 0.01, 'adcs.imu.gyro_y_rad_s': 0.02, 'adcs.imu.gyro_z_rad_s': 0.03,
                      'adcs.imu.accel_x_m_s2': 1, 'adcs.imu.accel_y_m_s2': 2, 'adcs.imu.accel_z_m_s2': 3,
                      'adcs.magnetometer.field_x_t': 1e-5, 'adcs.magnetometer.field_y_t': 2e-5, 'adcs.magnetometer.field_z_t': 3e-5,
                      'adcs.star_tracker.valid': 1, 'adcs.star_tracker.sigma_x': 0.1, 'adcs.star_tracker.sigma_y': 0.2, 'adcs.star_tracker.sigma_z': 0.3,
                      'adcs.sun_sensor.valid': 1, 'adcs.sun_sensor.intensity': 0.8, 'adcs.sun_sensor.direction_x': 1, 'adcs.sun_sensor.direction_y': 0, 'adcs.sun_sensor.direction_z': 0,
                      'adcs.rw.speed_rad_s_0': 125.5, 'adcs.rw.effective_max_torque_nm_0': 0.25
                    }
                }], {verified: true, runId: 'browser-test-run', epochUtc: '2026-01-01T00:00:00Z', frame: 'N_INERTIAL', timeSystem: 'UTC'})""")
                assert "0.0100" in page.locator("#simulationSensorTelemetryRows").inner_text()
                assert "RW1" in page.locator("#simulationSensorTelemetryRows").inner_text()
                assert "状态 125.50" in page.locator("#simulationSensorTelemetryRows").inner_text()
                assert "限幅 0.2500" in page.locator("#simulationSensorTelemetryRows").inner_text()
                assert page.locator("#sceneTaskContextState").inner_text() == "历史 Run · 已封存"
                assert page.locator("#sceneTaskContextId").inner_text() == "browser-test-run"
                assert page.locator("#sceneTaskContextTime").inner_text() == "12.50 s"
                assert page.locator("#sceneTaskContextQuality").inner_text() == "SEALED"
                phase = page.evaluate("""() => {
                    window.SatScene.setRunTelemetry([
                      {sim_time_s: 0, values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0, 'adcs.rw.speed_rad_s_0': 100}},
                      {sim_time_s: 2, values: {'orbit.position_m_x': 7000000, 'orbit.position_m_y': 100, 'orbit.position_m_z': 0, 'adcs.rw.speed_rad_s_0': 200}}
                    ], {verified:true,runId:'rw-phase-integral',epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',timeSystem:'UTC'});
                    return window.SatScene.integrateWheelPhaseFromTelemetry(0,2);
                }""")
                assert abs(phase - (300.0 % (2 * math.pi))) < 1e-8
                physical_coordinates = page.locator("#simulationGeometryRows").inner_text()
                page.locator("#simulationExplodeToggle").click()
                assert page.locator("#simulationGeometryRows").inner_text() == physical_coordinates
                assert page.locator("#simulationExplodeToggle").get_attribute("aria-pressed") == "true"
                assert "视觉分离" in page.locator("#simulationGeometryStatus").inner_text()
                page.locator("#simulationExplodeToggle").click()
                assert page.locator("#simulationExplodeToggle").get_attribute("aria-pressed") == "false"
                assert page.locator("#simulationGeometryRows").inner_text() == physical_coordinates
                assert "已封存有限 Run 遥测" in page.locator("#simulationSensorTelemetrySource").inner_text()
                page.evaluate("""() => window.SatScene.setRunTelemetry([{
                    sim_time_s: 13.0,
                    values: {
                      'orbit.position_m_x': 7000000, 'orbit.position_m_y': 0, 'orbit.position_m_z': 0,
                      'adcs.imu.gyro_x_rad_s': 0.02, 'adcs.imu.gyro_y_rad_s': 0.03, 'adcs.imu.gyro_z_rad_s': 0.04
                    }
                }], {verified: true, runId: 'browser-test-partial-run', epochUtc: '2026-01-01T00:00:00Z', frame: 'N_INERTIAL', timeSystem: 'UTC'})""")
                partial_text = page.locator("#simulationSensorTelemetryRows").inner_text()
                assert "未提供完整三轴测量" in partial_text
                assert "未提供" in partial_text
                if cycle == 2:
                    if evidence_dir:
                        output = Path(evidence_dir)
                        output.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(output / "simulation-rw-geometry-desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.wait_for_timeout(150)
                    assert page.locator("#simulationGeometryCanvas").bounding_box()["width"] > 280
                    if evidence_dir:
                        page.screenshot(path=str(output / "simulation-rw-geometry-mobile.png"), full_page=True)
                    page.set_viewport_size({"width": 1440, "height": 960})
                page.evaluate("window.SatScene.clearSimulationConfiguration()")
                assert page.locator("#simulationGeometryRows tr").count() == 1
                assert page.locator("#simulationSensorRows tr").count() == 1
                assert page.locator("#simulationHubMass").inner_text() == "—"
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
                if cycle == 2:
                    page.locator("#sceneCreateTaskBtn").click()
                    dialog = page.locator("#sceneTaskCreateModal")
                    playwright.expect(dialog).to_be_visible()
                    playwright.expect(dialog.locator("[data-scene-create]")).to_have_count(3)
                    playwright.expect(dialog.locator('[data-scene-create="offline"]')).to_contain_text("离线仿真任务")
                    playwright.expect(dialog.locator('[data-scene-create="interactive"]')).to_contain_text("限时实时仿真")
                    playwright.expect(dialog.locator('[data-scene-create="continuous"]')).to_contain_text("持续实时仿真")

                    # The guided dialog must keep keyboard focus inside and restore
                    # the invoking control when dismissed.
                    offline_option = dialog.locator('[data-scene-create="offline"]')
                    continuous_option = dialog.locator('[data-scene-create="continuous"]')
                    close_button = dialog.locator("[data-scene-dialog-close]")
                    playwright.expect(offline_option).to_be_focused()
                    close_button.focus()
                    page.keyboard.press("Shift+Tab")
                    playwright.expect(continuous_option).to_be_focused()
                    page.keyboard.press("Tab")
                    playwright.expect(close_button).to_be_focused()
                    close_button.click()
                    playwright.expect(dialog).to_be_hidden()
                    playwright.expect(page.locator("#sceneCreateTaskBtn")).to_be_focused()

                    # This test client deliberately has no real-time service.
                    # Both real-time choices must fail with actionable feedback,
                    # rather than navigating into a dead workspace or starting a session.
                    for mode in ("interactive", "continuous"):
                        page.locator("#sceneCreateTaskBtn").click()
                        dialog.locator(f'[data-scene-create="{mode}"]').click()
                        playwright.expect(dialog).to_be_hidden()
                        playwright.expect(page.locator("#notice")).to_contain_text("实时仿真服务当前不可用")

                    page.locator("#sceneCreateTaskBtn").click()
                    dialog.locator('[data-scene-create="offline"]').click()
                    playwright.expect(page.locator("body")).to_have_attribute("data-active-view", "wizard")
                    playwright.expect(page.locator("#wizardView")).to_be_visible()
                    page.locator("#wizardSceneShortcutBtn").click()
                    playwright.expect(page.locator("body")).to_have_attribute("data-active-view", "scene")
                    page.locator('[data-scene-mode="orbit"]').click()
                    page.locator("#satelliteSceneCanvas canvas").wait_for(state="visible", timeout=30000)
                task_center_button = page.locator("#sceneWorkspaceTaskCenterBtn")
                task_center_hit_test = task_center_button.evaluate("""node => {
                    const rect = node.getBoundingClientRect();
                    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
                    return {
                      top: top?.outerHTML || null,
                      receivesPointer: Boolean(top && (top === node || node.contains(top))),
                    };
                }""")
                assert task_center_hit_test["receivesPointer"], f"SCENE_TASK_CENTER_POINTER_INTERCEPTED: {task_center_hit_test}"
                task_center_button.click()
                page.locator("#wizardView").wait_for(state="visible")
                assert page.locator("#satelliteSceneCanvas canvas").count() == 0
                page.locator("#wizardSceneShortcutBtn").click()
                page.locator("#sceneWorkspaceCreatorBtn").click()
                page.locator("#creatorView").wait_for(state="visible")
                assert page.locator("body").get_attribute("data-active-view") == "creator"
                page.go_back()
                page.locator("#satelliteSceneView").wait_for(state="visible")
                assert page.locator("body").get_attribute("data-active-view") == "scene"
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
            playwright.expect(page.locator('[data-scene-mode="simulation"]')).to_have_attribute("aria-pressed", "true")
            playwright.expect(page.locator("#simulationGeometryCanvas")).to_be_visible()
            playwright.expect(page.locator("#satelliteOrbitMode")).to_be_hidden()
            # CAD is independent of orbit imagery readiness.
            orbit_tab = page.locator('[data-scene-mode="orbit"]')
            cad_tab = page.locator('[data-scene-mode="cad"]')
            assert orbit_tab.get_attribute("aria-pressed") == "false"
            assert page.locator("#birdsCadViewer").is_hidden()
            cad_tab.click()
            assert cad_tab.get_attribute("aria-pressed") == "true"
            assert page.locator("#satelliteSceneTitle").inner_text() == "BIRDS-4 参考 CAD"
            assert page.locator(".satellite-scene-actions").is_hidden()
            assert page.locator(".p9d-config-panel").is_visible()
            assert page.locator(".scene-workspace-nav").is_visible()
            assert page.locator('[data-scene-mode="orbit"]').is_visible()
            grid_columns = page.locator(".p9d-scene-layout").evaluate("(node) => getComputedStyle(node).gridTemplateColumns")
            assert len(grid_columns.split()) == 2, grid_columns
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
            for detail in ("装配层级路径", "几何范围（GLB 根坐标系，m）",
                           "局部变换（父节点坐标）", "参考装配根坐标变换",
                           "尚未审定为航天器 B 系安装锚点或 as-built 实例"):
                assert detail in inspector_text, (detail, inspector_text)
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
            playwright.expect(page.locator("#satelliteSceneCanvas canvas")).to_be_visible(timeout=30000)
            playwright.expect(page.locator('[data-scene-action="track"]')).to_be_enabled(timeout=30000)
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


def test_default_scene_mode_initialization_and_renderer_ownership():
    """Execute the production DOM mode controller without loading WebGL."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--test", str(root / "tests/js/test_p9j_default_scene_mode.cjs")],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_review_group_glb(path: Path) -> None:
    """Turn the triangle fixture into a two-level assembly group with one mesh descendant."""
    _write_triangle_glb(path)
    data = path.read_bytes()
    json_length, json_type = struct.unpack_from("<I4s", data, 12)
    assert json_type == b"JSON"
    document = json.loads(data[20:20 + json_length].decode("utf-8").rstrip(" "))
    document["nodes"] = [
        {"name": "spacecraft", "children": [1]},
        {"name": "battery", "children": [2]},
        {"name": "battery-shell", "mesh": 0},
    ]
    document["scenes"] = [{"nodes": [0]}]
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((4 - len(json_chunk) % 4) % 4)
    binary_chunk = data[20 + json_length:]
    body = struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk + binary_chunk
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)


def test_p9m4_visual_review_requires_exact_glb_digest_before_candidate_focus(tmp_path: Path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to an installed Chromium browser")

    expected_glb = tmp_path / "review-candidate.glb"
    wrong_glb = tmp_path / "wrong-revision.glb"
    _write_review_group_glb(expected_glb)
    _write_triangle_glb(wrong_glb)
    digest = __import__("hashlib").sha256(expected_glb.read_bytes()).hexdigest()
    registry = {
        "schema_version": "engineering-asset-mapping.v1",
        "mappings": [{
            "mapping_id": "reference:battery",
            "object_id": "component.battery",
            "name_zh": "电池组",
            "mapping_kind": "reference_cad",
            "asset_instance_id": None,
            "geometry_asset": {
                "asset_id": "glb:" + digest,
                "sha256": digest,
                "source_node_path": "root/battery",
                "glb_node_binding": None,
                "glb_node_binding_candidate": {
                    "status": "unique_exact_name_parent_match_group_candidate",
                    "candidate_glb_node_index": 1,
                    "candidate_glb_node_name": "battery",
                    "candidate_glb_mesh_index": None,
                    "candidate_geometry_node_count": 1,
                    "candidate_descendant_mesh_nodes": [
                        {"node_index": 2, "name": "battery-shell", "mesh_index": 0},
                    ],
                    "selector_promoted": False,
                    "requires_visual_review": True,
                },
            },
            "geometry_frame": {"identity_status": "unverified"},
            "spatial_anchor": None,
            "telemetry_binding": None,
            "evidence": [],
            "render": {"as_built_eligible": False, "renderable": False, "views": ["reference_cad"]},
        }],
        "intake": {
            "scope": "reference_registration_only_not_as_built_approval",
            "glb_sha256": digest,
        },
        "glb_node_inventory": {"node_count": 3, "mesh_count": 1, "scene_count": 1},
        "summary": {
            "as_built_verified_count": 0,
            "telemetry_binding_count": 0,
            "node_renderable_count": 0,
        },
    }
    registry_path = tmp_path / "engineering-reference-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(registry_path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256",
                       __import__("hashlib").sha256(registry_path.read_bytes()).hexdigest())

    with _client(tmp_path) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
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
            page.on("console", lambda message: failures.append(message.text) if message.type == "error" else None)
            page.on("response", lambda response: failures.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#wizardSceneShortcutBtn").click()
            # Enter the scene through the supported simulation mode first,
            # then switch to CAD, matching the accepted P9-I user journey.
            page.locator('[data-scene-mode="simulation"]').click()
            page.locator("#simulationGeometryCanvas").wait_for(state="visible", timeout=30000)
            page.locator('[data-scene-mode="cad"]').click()
            page.locator("#birdsCadViewer").wait_for(state="visible", timeout=30000)
            panel = page.locator("#birdsCadReviewPanel")
            panel.get_by_text("电池组", exact=True).wait_for(timeout=30000)
            action = panel.get_by_role("button", name="等待 GLB 哈希匹配")
            assert action.is_disabled()

            page.locator("#birdsCadFile").set_input_files(str(wrong_glb))
            page.locator("#birdsCadStatus").filter(has_text="已加载 wrong-revision.glb").wait_for(timeout=60000)
            assert panel.get_by_role("button", name="等待 GLB 哈希匹配").is_disabled()
            assert "SHA-256" in panel.inner_text()

            page.locator("#birdsCadFile").set_input_files(str(expected_glb))
            page.locator("#birdsCadStatus").filter(has_text="已加载 review-candidate.glb").wait_for(timeout=60000)
            page.get_by_text("GLB 哈希一致", exact=True).wait_for(timeout=10000)
            action = panel.get_by_role("button", name="定位候选组")
            assert action.is_enabled()
            panel.get_by_text("查看后代网格清单").click()
            assert "battery-shell" in panel.inner_text()
            action.click()
            page.locator("#birdsCadInspector .birds-cad-inspector-name").filter(has_text="battery").wait_for()
            assert "GLB 原始节点索引\n1" in page.locator("#birdsCadInspector").inner_text()
            assert "battery" in page.locator("#birdsCadStatus").inner_text()
            assert "尚未审定为航天器 B 系安装锚点或 as-built 实例" in page.locator("#birdsCadInspector").inner_text()
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()


def test_p9m4_real_birds4_review_maps_all_reference_groups(tmp_path: Path, monkeypatch):
    """Review the actual pinned BIRDS-4 GLB against the registry built from the same bytes."""
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    registry_path_value = os.environ.get("SAT_SIM_P9M4_REVIEW_REGISTRY_PATH")
    real_glb_value = os.environ.get("SAT_SIM_BIRDS4_REAL_GLB_PATH")
    if not executable or not registry_path_value or not real_glb_value:
        pytest.skip("Requires the P9-M4 registry artifact, pinned BIRDS-4 GLB and Chromium")

    registry_path = Path(registry_path_value).resolve()
    glb_path = Path(real_glb_value).resolve()
    assert registry_path.is_file(), registry_path
    assert glb_path.is_file() and glb_path.stat().st_size > 10 * 1024 * 1024, glb_path
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(glb_path.read_bytes()).hexdigest()
    assert registry["intake"]["glb_sha256"] == digest
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE", str(registry_path))
    monkeypatch.setenv("SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256",
                       hashlib.sha256(registry_path.read_bytes()).hexdigest())

    expected = {
        "component.battery": 14,
        "component.reaction_wheel": 24,
        "component.payload": 1,
    }
    reference_rows = {
        row["object_id"]: row for row in registry["mappings"]
        if row["mapping_kind"] == "reference_cad"
    }
    assert set(reference_rows) == set(expected)
    for object_id, count in expected.items():
        candidate = reference_rows[object_id]["geometry_asset"]["glb_node_binding_candidate"]
        assert candidate["status"] == "unique_exact_name_parent_match_group_candidate"
        assert len(candidate["candidate_descendant_mesh_nodes"]) == count
        assert candidate["selector_promoted"] is False
        assert reference_rows[object_id]["geometry_asset"]["glb_node_binding"] is None
        assert reference_rows[object_id]["render"]["renderable"] is False

    wrong_glb = tmp_path / "wrong-revision.glb"
    _write_triangle_glb(wrong_glb)
    evidence_dir = os.environ.get("SAT_SIM_SCENE_EVIDENCE_DIR")
    with _client(tmp_path) as client, playwright.sync_playwright() as pw:
        api_payload = client.get("/workbench/engineering-mapping").json()
        assert api_payload["ok"] is True, api_payload
        assert api_payload["summary"]["visual_review_candidate_count"] == 3, api_payload
        assert {row["name_zh"] for row in api_payload["mappings"] if row.get("review_candidate")} == {
            "蓄电池", "反作用轮", "载荷仪器"
        }
        browser = pw.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
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
            page.on("console", lambda message: failures.append(message.text) if message.type == "error" else None)
            page.on("response", lambda response: failures.append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator('[data-scene-mode="simulation"]').click()
            page.locator("#simulationGeometryCanvas").wait_for(state="visible", timeout=30000)
            page.locator('[data-scene-mode="cad"]').click()
            page.locator("#birdsCadViewer").wait_for(state="visible", timeout=30000)
            panel = page.locator("#birdsCadReviewPanel")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#birdsCadReviewPanel .birds-cad-review-card').length === 3",
                    timeout=30000,
                )
            except Exception as error:
                raise AssertionError(
                    "Real registry was valid but review cards did not render; "
                    f"viewer_visible={page.locator('#birdsCadViewer').is_visible()}; "
                    f"panel_text={panel.inner_text()}; "
                    f"viewer_api={page.evaluate('Boolean(window.BirdsCadViewer)')}"
                ) from error
            assert panel.get_by_text("蓄电池", exact=True).is_visible()
            assert panel.locator(".birds-cad-review-card").count() == 3
            assert panel.locator(".birds-cad-review-checklist li").count() == 5
            assert "待工程人员目视核对" in panel.inner_text()
            assert "未批准；本审查不建立实装或遥测绑定" in panel.inner_text()
            assert "852" in panel.inner_text()
            action = panel.get_by_role("button", name="等待 GLB 哈希匹配").first
            assert action.is_disabled()

            page.locator("#birdsCadFile").set_input_files(str(wrong_glb))
            page.locator("#birdsCadStatus").filter(has_text="已加载 wrong-revision.glb").wait_for(timeout=60000)
            assert panel.get_by_role("button", name="等待 GLB 哈希匹配").count() == 3
            assert all(button.is_disabled() for button in panel.get_by_role("button").all())

            page.locator("#birdsCadFile").set_input_files(str(glb_path))
            page.locator("#birdsCadStatus").filter(has_text=f"已加载 {glb_path.name}").wait_for(timeout=240000)
            panel.get_by_text("GLB 哈希一致", exact=True).wait_for(timeout=10000)
            assert all(button.is_enabled() for button in panel.get_by_role("button", name="定位候选组").all())

            for object_id, expected_mesh_count in expected.items():
                row = reference_rows[object_id]
                candidate = row["geometry_asset"]["glb_node_binding_candidate"]
                card = panel.locator(".birds-cad-review-card").filter(has_text=row["name_zh"])
                card.get_by_text("查看后代网格清单").click()
                assert len(candidate["candidate_descendant_mesh_nodes"]) == expected_mesh_count
                for descendant in candidate["candidate_descendant_mesh_nodes"]:
                    assert descendant["name"] in card.inner_text()
                card.get_by_role("button", name="定位候选组").click()
                inspector_name = page.locator("#birdsCadInspector .birds-cad-inspector-name")
                inspector_name.filter(has_text=candidate["candidate_glb_node_name"]).wait_for(timeout=10000)
                inspector_text = page.locator("#birdsCadInspector").inner_text()
                assert f"GLB 原始节点索引\n{candidate['candidate_glb_node_index']}" in inspector_text
                assert "尚未审定为航天器 B 系安装锚点或 as-built 实例" in inspector_text
                assert "参考装配根坐标变换" in inspector_text

            if evidence_dir:
                output = Path(evidence_dir)
                output.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(output / "p9m4-d-real-birds4-review-desktop.png"), full_page=True)
                (output / "p9m4-d-real-birds4-review-summary.json").write_text(json.dumps({
                    "glb_sha256": digest,
                    "glb_node_count": registry["glb_node_inventory"]["node_count"],
                    "mesh_count": registry["glb_node_inventory"]["mesh_count"],
                    "reviewed_objects": list(expected),
                    "descendant_mesh_counts": expected,
                    "selector_promotion_count": registry["glb_node_binding_audit"]["selector_promotion_count"],
                    "as_built_verified_count": registry["summary"]["as_built_verified_count"],
                    "telemetry_binding_count": registry["summary"]["telemetry_binding_count"],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
            assert not external_requests, external_requests
            assert not failures, failures
        finally:
            browser.close()
