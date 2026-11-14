from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.run_bundle import _plot_manifest_payload


def test_p9e_plot_manifest_maps_component_and_subsystem_at_distinct_scopes():
    component = _plot_manifest_payload({
        "model": {"capability_id": "component.reaction_wheel.v1"},
        "outputs": {"plots": ["adcs.reaction_wheel.speed_rad_s_0"]},
    }, [{"time_s": 0.0, "adcs.reaction_wheel.speed_rad_s_0": 10.0}])
    row = component["series"][0]
    assert component["schema_version"] == "sat-sim.plot-manifest.v3"
    assert row["object_id"] == "component.reaction_wheel"
    assert row["association_scope"] == "component"
    assert row["association_source"] == "workbench_quantity_dictionary.v2"
    assert row["unit"] == "rad/s"

    whole = _plot_manifest_payload({
        "model": {"capability_id": "whole_spacecraft.unified_native.v1"},
        "outputs": {"plots": ["adcs.rw.speed_rad_s_0"]},
    }, [{"time_s": 0.0, "adcs.rw.speed_rad_s_0": 10.0}])
    row = whole["series"][0]
    assert row["object_id"] == "subsystem.adcs"
    assert row["association_scope"] == "subsystem"
    assert row["association_source"] == "capability_output_hierarchy"


def test_p9e_does_not_guess_component_binding_for_unknown_component_output():
    from sat_sim.run_bundle import _plot_series_object_binding

    result = _plot_series_object_binding(
        {"model": {"capability_id": "component.reaction_wheel.v1"}},
        "adcs.rw.unreviewed_field",
    )
    assert result["object_id"] is None
    assert result["association_scope"] == "unmapped"
    assert result["association_reason"] == "FIELD_NOT_REGISTERED_FOR_COMPONENT"


def test_p9e_existing_run_result_chain_is_reused_and_scene_panel_is_read_only():
    root = Path(__file__).resolve().parents[1] / "src" / "sat_sim" / "web"
    index = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    panel = (root / "object-config-panel.js").read_text(encoding="utf-8")
    resolver = (root / "object-run-results.js").read_text(encoding="utf-8")
    assert 'id="p9eRunResultHost"' in index
    assert 'src="/assets/object-run-results.js?v=p9e-1"' in index
    assert "window.P9EObjectRunResults?.resolve" in app
    assert "renderP9DPanel();" in app[app.index("async function loadRun("):app.index("function renderActiveRun(")]
    assert "verify_run_bundle" not in panel
    assert "run.integrity?.ok !== true" in resolver
    assert "binding.scope === 'component'" in resolver
    assert "parent_scope_only" in resolver


def test_p9e_chromium_renders_run_bundle_results_inside_the_selected_object_panel(tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    if os.environ.get("SAT_SIM_P9E_BROWSER_TEST") != "1":
        pytest.skip("Browser journey runs on the Linux Chromium acceptance job.")
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
            page.locator("#p9eRunResultHost").wait_for(state="visible", timeout=20000)
            page.locator("#p9dObjectTree .p9d-tree-item").first.wait_for(state="visible", timeout=20000)
            page.evaluate("""async () => {
              const response = await fetch('/workbench/scene/catalog');
              const payload = await response.json();
              window.P9DObjectConfigPanel.render({
                tree: payload.tree,
                selectedId: 'component.reaction_wheel',
                schema: null,
                formData: null,
                validated: false,
                onSelect: () => {},
                onChange: () => {},
                runResult: {
                  status: 'available',
                  runId: 'sealed-run-p9e-001',
                  objectId: 'component.reaction_wheel',
                  objectName: '反作用轮组',
                  series: [{
                    field: 'adcs.reaction_wheel.speed_rad_s_0',
                    label: '反作用轮 1 · 角速度',
                    unit: 'rad/s',
                    association_scope: 'component',
                    association_source: 'workbench_quantity_dictionary.v2',
                    sample: {time_s: 12, value: 42.5}
                  }],
                  relatedSeries: [],
                  telemetryRows: 20,
                  sampled: false,
                  totalTelemetryRows: 20
                }
              });
            }""")
            host = page.locator("#p9eRunResultHost")
            assert "sealed-run-p9e-001" in host.inner_text()
            assert "反作用轮 1" in host.inner_text()
            assert "42.5 rad/s" in host.inner_text()
            assert "12 s" in host.inner_text()
            assert "单机精确映射" in host.inner_text()
            assert "已审定单机参量字典" in host.inner_text()
            assert not failures, failures
        finally:
            browser.close()
