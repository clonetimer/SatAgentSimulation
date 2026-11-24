"""Check a local transient omitted from a million-row overview in Chromium."""
from __future__ import annotations

import os
import json
import platform
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


def test_chart_window_refetches_local_extreme_from_original_run(tmp_path):
    """A local dip omitted by the global overview must appear after window refinement."""
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app

    playwright = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("SAT_SIM_BROWSER_EXECUTABLE")
    if not executable:
        pytest.skip("Set SAT_SIM_BROWSER_EXECUTABLE to installed Chromium")
    run_id = "dt5-window-refetch"
    source = tmp_path / "runs" / run_id / "results" / "telemetry.jsonl"
    source.parent.mkdir(parents=True)
    with source.open("w", encoding="utf-8") as stream:
        for i in range(1_000_000):
            signal = -100 if i == 825_000 else (-40 if i == 515_001 else 0)
            stream.write(json.dumps({"time_s": i, "sample_index": i, "signal": signal}) + "\n")
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    queries = []
    errors = []
    with TestClient(app) as client, playwright.sync_playwright() as pw:
        global_overview = client.get(f"/runs/{run_id}/telemetry/overview?max_points=10000&fields=signal").json()
        assert global_overview.get("sampled") is True, str(global_overview)[:500]
        assert not any(row["sample_index"] == 515_001 for row in global_overview["rows"])
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            page.on("pageerror", lambda error: errors.append(str(error)))

            def proxy(route):
                request = route.request
                uri = urlsplit(request.url)
                path = uri.path
                if path == "/runs":
                    route.fulfill(json={"runs": [{"run_id": run_id, "status": "SUCCEEDED"}]})
                elif path == f"/runs/{run_id}":
                    route.fulfill(json={"run": {
                        "run_id": run_id, "sealed": True, "integrity": {"ok": True},
                        "task_spec": {"task": {"id": run_id}, "model": {"capability_id": "component.battery.v1"}},
                        "run_record": {"status": "SUCCEEDED"},
                        "available_outputs": {"fmea": False, "fault_traceability": False},
                    }})
                elif path == f"/runs/{run_id}/plots":
                    route.fulfill(json={"plot_manifest": {"time_axis": {"field": "time_s"},
                                                          "series": [{"field": "signal"}]}})
                elif path == f"/runs/{run_id}/telemetry/overview":
                    queries.append(parse_qs(uri.query))
                    response = client.get(path + "?" + uri.query)
                    route.fulfill(status=response.status_code, body=response.content,
                                  headers={"content-type": response.headers["content-type"]})
                elif path == f"/runs/{run_id}/telemetry":
                    route.fulfill(json={"rows": [], "total": 1_000_000, "truncated": True})
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
            page.locator(f'#runList [data-run-id="{run_id}"]').click()
            page.locator('[data-result-tab="charts"]').click()
            page.locator('#chartWindowStatus').filter(has_text="原始 1000000 条").wait_for(timeout=30_000)
            assert "min=-100" in page.locator("#chartLegend").inner_text()
            page.locator("details.compact-chart-options summary").click()
            page.locator("#chartTimeStart").fill("515000")
            page.locator("#chartTimeEnd").fill("515002")
            page.locator("#chartApplyWindowBtn").click()
            page.locator('#chartWindowStatus').filter(has_text="原始 3 条已完整加载").wait_for(timeout=30_000)
            assert "min=-40" in page.locator("#chartLegend").inner_text()
            assert queries[-1]["start_s"] == ["515000"] and queries[-1]["end_s"] == ["515002"]
            page.locator("#chartResetWindowBtn").click()
            page.locator('#chartWindowStatus').filter(has_text="原始 1000000 条").wait_for(timeout=30_000)
            assert "min=-100" in page.locator("#chartLegend").inner_text()
            assert errors == [], errors
            report = dict(source_rows=1_000_000, browser=browser.version,
                          platform=platform.platform(), global_min=-100, global_min_index=825000,
                          overview_rows=len(global_overview["rows"]), local_sample_index=515001,
                          local_min=-40, window_original_rows=3, page_errors=errors,
                          zoom_requests=queries, restored_min=-100,
                          fixture_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            output = os.environ.get("DT_PEAK_REPORT")
            if output:
                Path(output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
        finally:
            browser.close()
