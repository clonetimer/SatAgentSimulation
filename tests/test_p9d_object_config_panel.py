from __future__ import annotations

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema


def test_scene_configuration_catalog_is_real_and_read_only(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        before = client.get("/health").json()
        response = client.get("/workbench/scene/catalog")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        tree = payload["tree"]
        assert tree["schema_version"] == "workbench-object-tree.v2"
        assert tree["tree"]["object_id"] == "whole_spacecraft"
        adcs = next(row for row in tree["tree"]["children"] if row.get("object_id") == "subsystem.adcs")
        wheel = next(row for row in adcs["children"] if row.get("object_id") == "component.reaction_wheel")
        parameters = next(row for row in wheel["children"] if row["kind"] == "parameter_group")
        inertia = next(row for row in parameters["children"] if row["source_field"] == "wheel_inertia_kg_m2")
        assert inertia["unit"] == "kg*m^2"
        schema = capability_form_schema("component.reaction_wheel.v1")
        source_field = next(row for row in schema["fields"] if row["path"].endswith(".wheel_inertia_kg_m2"))
        assert source_field["minimum"] is not None
        assert (source_field["default"] if inertia.get("array_index") is None else source_field["default"][inertia["array_index"]]) == inertia["default"]
        assert source_field["unit"] == inertia["unit"]
        after = client.get("/health").json()
        assert before["features"] == after["features"]


def test_p9d_ui_uses_existing_task_spec_validation_and_no_fake_anchor():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "web"
    index = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    panel = (root / "object-config-panel.js").read_text(encoding="utf-8")
    assert 'id="p9dObjectTree"' in index
    assert 'id="p9dParameterHost"' in index
    assert "api('/workbench/scene/catalog')" in app
    assert "previewForm()" in app
    assert "api('/tasks/parse'" in app
    assert "state.formData" in app
    assert "hasSpatialAnchor" in app
    assert "spatial_anchor" in panel or "spatial_anchor" in app
    assert "window.P9DObjectConfigPanel" in app

def test_p9d_browser_parameter_edit_uses_the_existing_taskspec_parse_route(tmp_path):
    import json
    import os
    from urllib.parse import urlsplit

    import pytest
    playwright = pytest.importorskip("playwright.sync_api")
    if os.environ.get("SAT_SIM_BROWSER_TEST") != "1":
        pytest.skip("Browser journey runs on the Linux Chromium acceptance job.")
    runs = tmp_path / "runs"
    app = create_app(runs_root=runs, artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    parse_requests: list[dict] = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True, executable_path=pw.chromium.executable_path,
            args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
        )
        try:
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            errors: list[str] = []

            def proxy(route):
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.scheme in {"http", "https"} and parsed.netloc != "workbench.test":
                    route.abort()
                    return
                body = request.post_data_buffer
                if parsed.path == "/tasks/parse" and body:
                    parse_requests.append(json.loads(body.decode("utf-8")))
                response = client.request(
                    request.method, parsed.path + (("?" + parsed.query) if parsed.query else ""),
                    content=body,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() in ("authorization", "content-type", "accept")},
                )
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() in ("content-type", "content-security-policy", "x-content-type-options", "referrer-policy")}
                route.fulfill(status=response.status_code, body=response.content, headers=headers)

            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/*", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.locator("#p9dObjectTree .p9d-tree-item").first.wait_for(state="attached", timeout=30000)
            page.locator('.nav-button[data-view="scene"]').click()
            page.locator("#p9dObjectTree .p9d-tree-item").first.wait_for(state="visible", timeout=15000)
            wheel = page.locator("#p9dObjectTree .p9d-tree-item").filter(has_text="反作用轮").first
            wheel.click()
            parameter = page.locator("#p9dParameterHost label.p9d-parameter").filter(has_text="转动惯量").first
            parameter.wait_for(timeout=15000)
            editor = parameter.locator("input, textarea").first
            editor.fill("0.12")
            editor.dispatch_event("change")
            assert page.locator("#p9dValidationStatus").inner_text() == "配置尚未校验"
            page.locator("#p9dValidateBtn").click()
            page.wait_for_timeout(500)
            assert parse_requests, "the configuration panel did not call the existing TaskSpec parse route"
            form = parse_requests[-1]["form_data"]
            assert form["parameters"]["values"]["wheel_inertia_kg_m2"] == 0.12
            assert not errors, errors
        finally:
            browser.close()
