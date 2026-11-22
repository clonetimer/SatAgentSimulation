"""Real Chromium chart smoke at 10,000 observed samples; fixture is not physics evidence."""
from __future__ import annotations

import os
import json
import platform
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.mark.parametrize("whole_scene", [False, True], ids=["component-chart", "whole-scene-chart"])
def test_finite_run_chart_renders_ten_thousand_actual_samples(tmp_path, whole_scene):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app

    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")

    run_id = "dt5-chart-10000-whole" if whole_scene else "dt5-chart-10000-component"
    rows = [{"time_s": i * .1, "sample_index": i, "signal": -42 if i == 9876 else i / 1000}
            for i in range(10_000)]
    if whole_scene:
        for row in rows:
            row.update({
                "orbit.position_m_x": 7_000_000.0,
                "orbit.position_m_y": row["sample_index"] * 10.0,
                "orbit.position_m_z": 0.0,
                "attitude.truth.mrp_BN_x": 0.0,
                "attitude.truth.mrp_BN_y": 0.0,
                "attitude.truth.mrp_BN_z": 0.0,
                "attitude.truth.quaternion_BN_q0": 1.0,
                "attitude.truth.quaternion_BN_q1": 0.0,
                "attitude.truth.quaternion_BN_q2": 0.0,
                "attitude.truth.quaternion_BN_q3": 0.0,
                "attitude.truth.valid": 1,
            })
    source = tmp_path / "runs" / run_id / "results" / "telemetry.jsonl"
    source.parent.mkdir(parents=True)
    with source.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    overview_queries = []
    errors = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                uri = urlsplit(request.url)
                path = uri.path
                if path == "/runs":
                    route.fulfill(json={"runs": [{"run_id": run_id, "status": "SUCCEEDED",
                                                  "task_name": "DT5 chart fixture"}]})
                elif path == f"/runs/{run_id}":
                    route.fulfill(json={"run": {
                        "run_id": run_id, "sealed": True, "integrity": {"ok": True},
                        "task_spec": {"task": {"id": run_id},
                                      "model": {"capability_id": "whole_spacecraft.unified_native.v1"
                                                if whole_scene else "component.battery.v1"},
                                      "simulation": {"epoch_utc": "2026-11-22T00:00:00Z", "time_system": "UTC"}},
                        "run_record": {"status": "SUCCEEDED"},
                        "available_outputs": {"fmea": False, "fault_traceability": False},
                    }})
                elif path == f"/runs/{run_id}/plots":
                    route.fulfill(json={"plot_manifest": {"time_axis": {"field": "time_s"},
                                                          "series": [{"field": "signal"}]}})
                elif path == f"/runs/{run_id}/telemetry/overview":
                    overview_queries.append(parse_qs(uri.query))
                    response = client.get(path + "?" + uri.query)
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers["content-type"]})
                elif path == f"/runs/{run_id}/telemetry":
                    route.fulfill(json={"rows": rows[:2000], "total": len(rows), "truncated": True})
                elif path == f"/runs/{run_id}/metrics":
                    route.fulfill(json={"metrics": {}, "metric_metadata": {}})
                elif path == f"/runs/{run_id}/events":
                    route.fulfill(json={"events": []})
                elif path == f"/runs/{run_id}/artifacts":
                    route.fulfill(json={"artifacts": []})
                elif path == f"/runs/{run_id}/dataset":
                    route.fulfill(json={"dataset": None})
                else:
                    response = client.request(request.method, path + ("?" + uri.query if uri.query else ""),
                                              content=request.post_data_buffer,
                                              headers={key: value for key, value in request.headers.items()
                                                       if key.lower() in ("content-type", "accept")})
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers.get("content-type", "application/json")})

            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="networkidle")
            page.locator('[data-view="creator"]').first.click()
            page.locator(f'#runList [data-run-id="{run_id}"]').wait_for()
            start = time.perf_counter()
            page.locator(f'#runList [data-run-id="{run_id}"]').click()
            page.locator('#chartTelemetryCoverage').filter(has_text="10000 条真实样本").wait_for(
                state="attached", timeout=30_000)
            load_s = time.perf_counter() - start
            start = time.perf_counter()
            page.locator('[data-result-tab="charts"]').click()
            page.locator('#chartWindowStatus').filter(has_text="10000 / 10000").wait_for(timeout=30_000)
            chart_s = time.perf_counter() - start
            assert overview_queries and overview_queries[-1]["max_points"] == ["10000"]
            assert "min=-42" in page.locator("#chartLegend").inner_text()
            assert errors == [], errors
            measurement = {
                "schema_version": "dt5-chart-10000-browser.v1",
                "measurement_kind": "real_API_and_UI_fixture_data_not_native_physics",
                "browser_version": browser.version, "platform": platform.platform(),
                "viewport": {"width": 1440, "height": 960},
                "rows": len(rows), "max_points_requested": 10_000,
                "scene_context": "whole_spacecraft_UI_fixture" if whole_scene else "component_UI_fixture",
                "load_s": round(load_s, 6), "chart_tab_s": round(chart_s, 6),
                "sampled": False, "retained_extreme_index": 9876,
                "slo_qualification": "NOT_EVALUATED_SINGLE_SAMPLE",
            }
            evidence_dir = os.environ.get("SAT_SIM_SCENE_EVIDENCE_DIR")
            if evidence_dir:
                destination = Path(evidence_dir) / f"dt5-chart-10000-{'whole' if whole_scene else 'component'}.json"
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(measurement, indent=2) + "\n", encoding="utf-8")
            print(measurement)
        finally:
            browser.close()
