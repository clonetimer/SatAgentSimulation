"""DT-4.1: SHA-pinned real RW GLB, fail-closed serving and browser geometry."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.reference_rw_cad import RW_EXPECTED_SHA256, RW_EXPECTED_SIZE, verified_reference_rw_glb

ROOT = Path(__file__).parents[1]
WEB = ROOT / "src/sat_sim/web"


def _client(tmp_path):
    return TestClient(create_app(runs_root=tmp_path / "runs",
                                 artifacts_root=tmp_path / "artifacts",
                                 embedded_worker=False, auth_mode="disabled"))


def test_fixed_rw_asset_missing_and_hash_mismatch_fail_closed(tmp_path, monkeypatch):
    file = tmp_path / "B4_RW_Assembly_v55.glb"
    monkeypatch.setenv("SAT_SIM_BIRDS4_RW_GLB", str(file))
    with _client(tmp_path) as client:
        missing = client.get("/assets/reference-cad/birds4/rw.glb")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "DT41_RW_REFERENCE_ASSET_NOT_STAGED"
        file.write_bytes(b"not an RW model")
        mismatched_size = client.get("/assets/reference-cad/birds4/rw.glb")
        assert mismatched_size.status_code == 409
        assert mismatched_size.json()["detail"] == "DT41_RW_REFERENCE_SIZE_MISMATCH"
        file.write_bytes(b"X" * RW_EXPECTED_SIZE)
        mismatched_hash = client.get("/assets/reference-cad/birds4/rw.glb")
        assert mismatched_hash.status_code == 409
        assert mismatched_hash.json()["detail"] == "DT41_RW_REFERENCE_SHA256_MISMATCH"


def test_rw_reference_layer_default_off_and_independent_contract():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scene = (WEB / "satellite-scene.js").read_text(encoding="utf-8")
    overlay = (WEB / "rw-reference-overlay.js").read_text(encoding="utf-8")
    assert 'id="simulationRwReferenceToggle"' in html
    assert 'aria-pressed="false">显示 RW 参考外形' in html
    assert 'id="simulationRwReferenceFile"' in html
    assert 'src="/assets/rw-reference-overlay.js' in html
    assert "布局来自 TaskSpec，非 CAD 实装" in html
    assert "不建立正式节点映射或遥测绑定" in html
    assert "P9M4_REFERENCE_REGISTRY_PROMOTION_FORBIDDEN" in (ROOT/"src/sat_sim/api.py").read_text(encoding="utf-8")
    assert "const SOURCE_SHA256 = '" + RW_EXPECTED_SHA256 + "'" in overlay
    assert "geometry.mode === 'per_wheel'" in overlay
    assert "预设半径推算位置" in overlay
    assert "source.clone(true)" in overlay
    assert "new THREE.Vector3(0,1,0)" in overlay
    assert "new THREE.Vector3(...axis)" in overlay
    assert "referenceOnly:true, origin:'TaskSpec', notAsBuilt:true" in overlay
    assert "marker.rotation.y" in overlay
    assert "world_transform_m_used:false" in overlay
    assert "assembly-tree.json" not in overlay
    assert "simulationGeometryGroup" not in overlay
    assert "getRwReferenceDisplayScene" in scene


def test_pin_verified_glb_endpoint_returns_exact_standalone_asset(tmp_path):
    path = Path(os.environ.get("SAT_SIM_BIRDS4_RW_GLB") or
                ROOT / "build/birds4/output/B4_RW_Assembly_v55.glb")
    if not path.is_file():
        pytest.skip("Standalone BIRDS-4 RW GLB is not locally staged")
    assert path.stat().st_size == RW_EXPECTED_SIZE
    assert hashlib.sha256(path.read_bytes()).hexdigest() == RW_EXPECTED_SHA256
    assert verified_reference_rw_glb() == path.resolve()
    with _client(tmp_path) as client:
        response = client.get("/assets/reference-cad/birds4/rw.glb")
        assert response.status_code == 200
        assert response.content == path.read_bytes()
        assert response.headers["x-reference-glb-sha256"] == RW_EXPECTED_SHA256
        assert response.headers["content-type"].startswith("model/gltf-binary")


def test_dt41_real_browser_rw_overlay_and_rejection(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    glb_path = Path(os.environ.get("SAT_SIM_BIRDS4_RW_GLB") or
                    ROOT / "build/birds4/output/B4_RW_Assembly_v55.glb").resolve()
    if not executable or not glb_path.is_file():
        pytest.skip("Requires pinned RW artifact and installed Chromium")
    assert hashlib.sha256(glb_path.read_bytes()).hexdigest() == RW_EXPECTED_SHA256
    monkeypatch.setenv("SAT_SIM_BIRDS4_RW_GLB", str(glb_path))
    evidence = Path(os.environ.get("SAT_SIM_SCENE_EVIDENCE_DIR", tmp_path))
    evidence.mkdir(parents=True, exist_ok=True)

    with _client(tmp_path) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=executable,
            args=["--enable-webgl","--use-gl=swiftshader","--ignore-gpu-blocklist"])
        try:
            page = browser.new_page(viewport={"width":1440,"height":960})
            failures = []
            def proxy(route):
                parsed = urlparse(route.request.url)
                if parsed.netloc != "workbench.test":
                    failures.append("external request: " + route.request.url)
                    route.abort()
                    return
                response = client.get(parsed.path,params=dict(parse_qsl(parsed.query)))
                headers = {k:v for k,v in response.headers.items()
                           if k.lower() in ("content-type","content-security-policy",
                                             "x-content-type-options","referrer-policy")}
                route.fulfill(status=response.status_code,body=response.content,headers=headers)
            page.on("pageerror", lambda error:failures.append(str(error)))
            page.on("console",lambda msg:failures.append(msg.text) if msg.type=="error" else None)
            page.route("**/*",proxy)
            page.goto("https://workbench.test/",wait_until="domcontentloaded")
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator("#simulationGeometryCanvas").wait_for(state="visible",timeout=30000)
            page.wait_for_function("() => Boolean(window.RwReferenceOverlay && window.SatScene)",timeout=30000)
            initial = page.evaluate("window.RwReferenceOverlay.getDiagnostics()")
            assert initial["layer_enabled"] is False
            assert initial["instance_count"] == 0
            assert page.locator("#simulationRwReferenceToggle").is_disabled()
            # A legacy preset is a legitimate physical configuration, but its
            # radius-derived positions are not rw_mount_b_* coordinates. Keep
            # the original engineering viewer, forbid the reference-CAD copy.
            legacy = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
              capabilityId:'whole_spacecraft.unified_native.v1',validated:true,
              values:{parameters:{values:{
                rw_configuration:'orthogonal_3',rw_geometry_mode:'legacy_axis_offset',
                rw_mount_radius_m:0.09,
                spacecraft_mass_kg:10,spacecraft_inertia_xx_kg_m2:0.2,
                spacecraft_inertia_yy_kg_m2:0.3,spacecraft_inertia_zz_kg_m2:0.4
              }}}
            })""")
            assert legacy is True
            assert page.locator("#simulationRwReferenceToggle").is_disabled()
            assert "预设半径推算位置" in page.locator("#simulationRwReferenceStatus").inner_text()
            assert page.evaluate("window.RwReferenceOverlay.getDiagnostics().instance_count") == 0
            # Validation has already occurred on the backend in the normal
            # workspace. Inject the accepted per-wheel view payload, not an unverified GLB.
            accepted = page.evaluate("""() => window.SatScene.setSimulationConfiguration({
              capabilityId:'whole_spacecraft.unified_native.v1',validated:true,
              values:{parameters:{values:{
                rw_configuration:'orthogonal_3',rw_geometry_mode:'per_wheel',
                rw_mount_radius_m:0,rw_mount_b_x_m_0:0.12,
                rw_mount_b_y_m_1:-0.08,rw_mount_b_z_m_2:0.07,
                spacecraft_mass_kg:10,spacecraft_inertia_xx_kg_m2:0.2,
                spacecraft_inertia_yy_kg_m2:0.3,spacecraft_inertia_zz_kg_m2:0.4
              }}}
            })""")
            assert accepted is True
            assert page.locator("#simulationGeometryBadge").inner_text() == (
                "TaskSpec 校验通过 · whole_spacecraft.unified_native.v1"
            )
            assert page.locator("#simulationRwReferenceToggle").is_enabled()
            page.locator("#simulationRwReferenceToggle").click()
            try:
                page.wait_for_function("""() => {
                  const status = document.getElementById('simulationRwReferenceStatus');
                  return status?.dataset.state === 'verified'
                    || status?.dataset.state === 'error';
                }""", timeout=60000)
            except Exception as exc:
                raise AssertionError(
                    "DT41_REFERENCE_LOAD_TIMED_OUT: "
                    f"status={page.locator('#simulationRwReferenceStatus').inner_text()!r} "
                    f"page_errors={failures!r} "
                    f"diagnostics={page.evaluate('window.RwReferenceOverlay.getDiagnostics()')!r}"
                ) from exc
            load_status = page.locator("#simulationRwReferenceStatus").inner_text()
            load_kind = page.locator("#simulationRwReferenceStatus").get_attribute("data-state")
            assert load_kind == "verified" and "已验证" in load_status, (
                f"DT41_REFERENCE_LOAD_FAILED status={load_status!r} "
                f"kind={load_kind!r} page_errors={failures!r} "
                f"diagnostics={page.evaluate('window.RwReferenceOverlay.getDiagnostics()')!r}"
            )
            actual = page.evaluate("window.RwReferenceOverlay.getDiagnostics()")
            assert actual["verified"] is True
            assert actual["sha256"] == RW_EXPECTED_SHA256
            assert actual["layer_enabled"] is True
            assert actual["layer_visible"] is True
            assert actual["instance_count"] == 3
            assert actual["mesh_count_per_instance"] == 24
            assert actual["instance_origins"] == ["TaskSpec"] * 3
            assert actual["installed_b_positions_from_taskspec"] == [
                [0.12,0,0],[0,-0.08,0],[0,0,0.07]]
            assert actual["spin_axes_b_from_taskspec"] == [
                [1,0,0],[0,1,0],[0,0,1]]
            assert actual["world_transform_m_used"] is False
            assert actual["as_built_approved"] is False
            page.evaluate("window.RwReferenceOverlay.setPhases([1,2,3])")
            assert page.evaluate("window.RwReferenceOverlay.getDiagnostics().rotor_visual_phases_rad") == [1,2,3]
            assert "布局来自 TaskSpec，非 CAD 实装" in page.locator(".dt41-rw-reference-notice").inner_text()
            page.screenshot(path=str(evidence/"dt41-real-rw-reference-three-copies.png"),full_page=True)
            page.locator("#simulationRwReferenceToggle").click()
            off = page.evaluate("window.RwReferenceOverlay.getDiagnostics()")
            assert off["layer_enabled"] is False and off["instance_count"] == 0
            # A same-sized but modified file must not be accepted even when its
            # basename is the same as the pinned real CAD file.
            wrong = tmp_path / "B4_RW_Assembly_v55.glb"
            broken = bytearray(glb_path.read_bytes()); broken[-1] ^= 1
            wrong.write_bytes(broken)
            page.locator("#simulationRwReferenceFile").set_input_files(str(wrong))
            page.locator("#simulationRwReferenceStatus").filter(has_text="SHA-256").wait_for(timeout=30000)
            invalid = page.evaluate("window.RwReferenceOverlay.getDiagnostics()")
            assert invalid["layer_enabled"] is False
            assert invalid["instance_count"] == 0
            assert not failures, failures
        finally:
            browser.close()
