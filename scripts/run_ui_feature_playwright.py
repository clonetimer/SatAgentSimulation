#!/usr/bin/env python3
"""Run browser-level acceptance checks for the satellite simulation workbench UI.

用途：验证卫星仿真工作台 UI 的关键浏览器交互与导出流程。
参数：通过命令行参数选择输出目录、浏览器和验收运行配置。
输出：生成浏览器验收报告、截图及相关运行证据。
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from sat_sim.api import create_app  # noqa: E402


SCHEMA_VERSION = "sat-sim.ui-playwright-acceptance.v1"
FIXTURE_RUN_ID = "ui-fixture-run"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _create_run_fixture(runs_root: Path) -> None:
    root = runs_root / FIXTURE_RUN_ID
    _write_json(root / "input" / "task_spec.json", {
        "schema_version": "v1",
        "task": {"id": "ui-fixture-task", "name": "UI 验收运行"},
        "model": {"capability_id": "whole_spacecraft.unified_native.v1"},
        "outputs": {"qoi": ["eps.battery_soc"], "plots": ["eps.battery_soc"]},
    })
    _write_json(root / "runtime" / "prepared_run.json", {
        "run_id": FIXTURE_RUN_ID,
        "task_id": "ui-fixture-task",
        "primary_capability_id": "whole_spacecraft.unified_native.v1",
        "prepared_at": "2026-08-04T00:00:00Z",
    })
    _write_json(root / "run_record.json", {
        "run_id": FIXTURE_RUN_ID,
        "task_id": "ui-fixture-task",
        "status": "SUCCEEDED",
        "validation_result": "PASS",
        "updated_at": "2026-08-04T00:00:02Z",
    })
    _write_json(root / "validation" / "validation_outcome.json", {
        "result": "PASS",
        "reason_code": "VALIDATION_PASSED",
        "message_zh": "UI 验收 Fixture 验证通过。",
    })
    _write_json(root / "results" / "metrics.json", {"metrics": {"eps.battery_soc": 0.82}})
    _write_json(root / "results" / "events.json", {"declared": [], "observed": []})
    _write_json(root / "results" / "plot_manifest.json", {
        "time_axis": {"field": "time_s", "label": "仿真时间"},
        "series": [{"field": "eps.battery_soc", "label": "蓄电池荷电状态"}],
    })
    telemetry = "\n".join([
        json.dumps({"time_s": 0.0, "eps.battery_soc": 0.90}),
        json.dumps({"time_s": 1.0, "eps.battery_soc": 0.86}),
        json.dumps({"time_s": 2.0, "eps.battery_soc": 0.82}),
    ]) + "\n"
    telemetry_path = root / "results" / "telemetry.jsonl"
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)
    telemetry_path.write_text(telemetry, encoding="utf-8")
    dataset_root = root / "results" / "dataset"
    _write_json(dataset_root / "manifest.json", {
        "manifest_version": "sat.dataset.v1",
        "dataset_id": "ui-fixture-task",
        "status": "complete",
        "quality": {"trace_rows": 3},
        "files": {"trace": "trace.csv", "summary": "summary.json"},
    })
    (dataset_root / "trace.csv").write_text(
        "time_s,eps.battery_soc\n0,0.90\n1,0.86\n2,0.82\n", encoding="utf-8"
    )
    _write_json(dataset_root / "summary.json", {"status": "complete", "final_soc": 0.82})
    _write_json(root / "SEALED.json", {"sealed": True})


def _browser_executable(playwright: Any) -> str | None:
    candidate = Path(playwright.chromium.executable_path)
    if candidate.is_file():
        return str(candidate)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run(output_dir: str | Path) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    screenshots = output / "screenshots"
    downloads = output / "downloads"
    for directory in (screenshots, downloads):
        if directory.exists():
            shutil.rmtree(directory)
    screenshots.mkdir(parents=True, exist_ok=True)
    downloads.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    console_errors: list[str] = []
    page_errors: list[str] = []
    expected_optional_404s: list[str] = []
    unexpected_http_errors: list[dict[str, Any]] = []
    fatal_error: str | None = None
    browser_version: str | None = None

    def check(check_id: str, condition: bool, evidence: Any = None) -> None:
        checks.append({"check_id": check_id, "status": "PASS" if condition else "FAIL", "evidence": evidence})

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        fatal_error = f"Playwright unavailable: {exc}"
    else:
        with tempfile.TemporaryDirectory(prefix="sat-sim-ui-playwright-") as temporary:
            temp = Path(temporary)
            runs_root = temp / "runs"
            _create_run_fixture(runs_root)
            app = create_app(
                runs_root=runs_root,
                artifacts_root=temp / "artifacts",
                embedded_worker=False,
                auth_mode="disabled",
            )
            with TestClient(app) as client, sync_playwright() as playwright:
                executable = _browser_executable(playwright)
                if not executable:
                    fatal_error = "Playwright Chromium executable not found"
                else:
                    browser = playwright.chromium.launch(
                        headless=True,
                        executable_path=executable,
                        args=["--no-sandbox", "--disable-dev-shm-usage"],
                    )
                    browser_version = browser.version
                    context = browser.new_context(
                        viewport={"width": 1760, "height": 1080},
                        accept_downloads=True,
                        locale="zh-CN",
                    )
                    page = context.new_page()

                    def proxy(route: Any) -> None:
                        request = route.request
                        parsed = urlsplit(request.url)
                        if parsed.netloc != "workbench.test":
                            route.abort()
                            return
                        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                        headers = {
                            key: value for key, value in request.headers.items()
                            if key.lower() in {"content-type", "accept", "authorization"}
                        }
                        response = client.request(
                            request.method,
                            path,
                            content=request.post_data.encode("utf-8") if request.post_data else None,
                            headers=headers,
                        )
                        response_headers = {
                            key: value for key, value in response.headers.items()
                            if key.lower() in {"content-type", "content-disposition", "cache-control"}
                        }
                        route.fulfill(status=response.status_code, body=response.content, headers=response_headers)

                    page.route("https://workbench.test/**", proxy)
                    def capture_console(message: Any) -> None:
                        if message.type != "error":
                            return
                        # Chromium emits a generic console entry for every HTTP 404.
                        # Response classification below retains URL/status evidence and
                        # distinguishes optional feature probes from unexpected failures.
                        if message.text.startswith("Failed to load resource:"):
                            return
                        console_errors.append(message.text)

                    def capture_response(response: Any) -> None:
                        if response.status < 400:
                            return
                        path = urlsplit(response.url).path
                        optional = response.status == 404 and (
                            path in {"/interactive/sessions", "/interactive/commands/catalog"}
                            or path in {f"/runs/{FIXTURE_RUN_ID}/fmea", f"/runs/{FIXTURE_RUN_ID}/fault-traceability"}
                        )
                        if optional:
                            expected_optional_404s.append(path)
                        else:
                            unexpected_http_errors.append({"url": response.url, "status": response.status})

                    page.on("console", capture_console)
                    page.on("pageerror", lambda error: page_errors.append(str(error)))
                    page.on("response", capture_response)

                    try:
                        page.goto("https://workbench.test/", wait_until="networkidle", timeout=45_000)
                        page.wait_for_selector(".capability-card.active", timeout=30_000)
                        page.screenshot(path=str(screenshots / "01-workbench-default.png"), full_page=True)

                        check("shell.title", page.title() == "卫星仿真工作台", page.title())
                        nav_labels = page.locator(".sidebar-nav .nav-button:not(.hidden) b").all_inner_texts()
                        check("shell.navigation", nav_labels == ["仿真创建", "任务中心", "实验中心", "运行中心", "系统诊断", "运行日志", "模型服务"], nav_labels)
                        check("capability.active", page.locator(".capability-card.active").count() == 1)
                        check("form.schema_rendered", page.locator("#dynamicForm .schema-section").count() > 0, page.locator("#dynamicForm .schema-section").count())

                        page.select_option("#fontScaleSelect", "130")
                        page.wait_for_function("document.documentElement.dataset.fontScale === '130'")
                        zoom = page.evaluate("getComputedStyle(document.body).zoom")
                        stored_scale = page.evaluate("localStorage.getItem('sat-sim-font-scale')")
                        check("display.font_scale_applied", zoom in {"1.3", "130%"}, {"zoom": zoom, "stored": stored_scale})
                        check("display.font_scale_saved", stored_scale == "130", stored_scale)
                        page.screenshot(path=str(screenshots / "02-font-scale-130.png"), full_page=True)
                        page.reload(wait_until="networkidle", timeout=45_000)
                        page.wait_for_selector(".capability-card.active", timeout=30_000)
                        check("display.font_scale_persisted", page.input_value("#fontScaleSelect") == "130", page.input_value("#fontScaleSelect"))
                        page.select_option("#fontScaleSelect", "100")

                        hierarchy_text = page.locator("#plotOptions").inner_text()
                        leaf_codes = page.locator("#plotOptions .check-option code")
                        leaf_count = leaf_codes.count()
                        leaf_values = [leaf_codes.nth(index).inner_text() for index in range(min(leaf_count, 50))]
                        check("outputs.hierarchy_present", page.locator("#plotOptions .output-scope-group").count() > 0)
                        check("outputs.concrete_leaves", leaf_count > 0 and all(not value.endswith(".*") for value in leaf_values), leaf_values[:10])
                        check("outputs.no_pseudo_parameters", "全部字段" not in hierarchy_text and "其他观测量" not in hierarchy_text)
                        first_leaf = leaf_values[0] if leaf_values else ""
                        if first_leaf:
                            page.fill("#plotOptions .output-hierarchy-toolbar input[type='search']", first_leaf)
                            page.wait_for_timeout(150)
                        check("outputs.search", not first_leaf or page.locator("#plotOptions .check-option code", has_text=first_leaf).count() > 0, first_leaf)

                        panel = page.locator("#creatorRunPanel")
                        width_before = panel.bounding_box()["width"]
                        page.click("#expandRunPanelBtn")
                        page.wait_for_function("document.querySelector('#creatorView').classList.contains('run-results-expanded')")
                        page.wait_for_timeout(250)
                        width_after = panel.bounding_box()["width"]
                        check("results.expand", width_after > width_before + 100, {"before": width_before, "after": width_after})
                        check("results.expand_saved", page.evaluate("localStorage.getItem('sat-sim-run-results-expanded')") == "true")
                        page.screenshot(path=str(screenshots / "03-results-expanded.png"), full_page=True)

                        for tab, panel_id in (
                            ("overview", "resultOverview"), ("charts", "resultCharts"), ("events", "resultEvents"),
                            ("traceability", "resultTraceability"), ("artifacts", "resultArtifacts"),
                        ):
                            page.click(f".result-tab[data-result-tab='{tab}']")
                            active = page.locator(f"#{panel_id}").evaluate("element => element.classList.contains('active')")
                            check(f"results.tab.{tab}", bool(active))

                        page.click(f".run-list-item:has-text('{FIXTURE_RUN_ID}')")
                        page.wait_for_function("document.querySelector('#exportDatasetBtn') && !document.querySelector('#exportDatasetBtn').classList.contains('hidden')")
                        check("run.fixture_loaded", FIXTURE_RUN_ID in page.locator("#activeRunCard").inner_text())
                        check("dataset.export_visible", page.locator("#exportDatasetBtn").is_visible(), page.locator("#exportDatasetBtn").get_attribute("title"))
                        with page.expect_download(timeout=15_000) as download_info:
                            page.click("#exportDatasetBtn")
                        download = download_info.value
                        download_path = downloads / download.suggested_filename
                        download.save_as(str(download_path))
                        archive_names: list[str] = []
                        with zipfile.ZipFile(io.BytesIO(download_path.read_bytes())) as archive:
                            archive_names = archive.namelist()
                        check("dataset.download_name", download.suggested_filename == f"{FIXTURE_RUN_ID}_dataset.zip", download.suggested_filename)
                        check("dataset.download_contents", {"dataset/manifest.json", "dataset/trace.csv", "dataset/summary.json"}.issubset(set(archive_names)), archive_names)

                        # Chromium headless can terminate the browser process when Escape exits
                        # native fullscreen. Force the product's supported embedded-browser
                        # fallback so entry, layout and Escape exit remain deterministic in CI.
                        page.evaluate("Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {configurable: true, value: undefined})")
                        page.click("#fullscreenRunPanelBtn")
                        page.wait_for_function("document.fullscreenElement === document.querySelector('#creatorRunPanel') || document.querySelector('#creatorRunPanel').classList.contains('run-panel-fullscreen-fallback')")
                        fullscreen_mode = page.evaluate("document.fullscreenElement ? 'native' : 'fallback'")
                        check("results.fullscreen_enter", fullscreen_mode == "fallback", fullscreen_mode)
                        fullscreen_background = panel.evaluate("element => getComputedStyle(element).backgroundColor")
                        opaque_background = not fullscreen_background.startswith("rgba(") or fullscreen_background.endswith(", 1)")
                        check("results.fullscreen_opaque_background", opaque_background, fullscreen_background)
                        page.screenshot(path=str(screenshots / "04-results-fullscreen.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_function("!document.fullscreenElement && !document.querySelector('#creatorRunPanel').classList.contains('run-panel-fullscreen-fallback')")
                        check("results.fullscreen_exit", True, "Escape")

                        centers = {
                            "tasks": ("taskCenterView", "任务中心"),
                            "experiments": ("experimentCenterView", "实验中心"),
                            "runs": ("runCenterView", "运行中心"),
                            "diagnostics": ("diagnosticView", "系统诊断"),
                            "logs": ("logCenterView", "运行日志"),
                        }
                        for view, (view_id, heading) in centers.items():
                            page.click(f".nav-button[data-view='{view}']")
                            page.wait_for_selector(f"#{view_id}:not(.hidden)")
                            check(f"navigation.{view}", heading in page.locator(f"#{view_id}").inner_text())
                        page.screenshot(path=str(screenshots / "05-log-center.png"), full_page=True)

                        page.click("#openModelSettingsBtn")
                        page.wait_for_selector("#modelSettingsModal:not(.hidden)")
                        provider_options = page.locator("#localModelType option").all_inner_texts()
                        check("models.modal", {"Ollama", "LM Studio", "vLLM / Qwen"}.issubset(set(provider_options)), provider_options)
                        page.click("#closeModelSettingsBtn")
                        check("models.modal_close", page.locator("#modelSettingsModal").evaluate("element => element.classList.contains('hidden')"))

                        page.click(".nav-button[data-view='experiments']")
                        page.click("#newExperimentBtn")
                        page.wait_for_selector("#experimentModal:not(.hidden)")
                        page.select_option("#experimentType", "monte_carlo")
                        check("experiments.monte_carlo", page.locator(".mc-only:not(.hidden)").count() > 0)
                        page.click("#closeExperimentBtn")

                        page.set_viewport_size({"width": 720, "height": 1000})
                        page.click(".nav-button[data-view='creator']")
                        page.wait_for_selector("#creatorView:not(.hidden)")
                        expand_display = page.locator("#expandRunPanelBtn").evaluate("element => getComputedStyle(element).display")
                        check("responsive.mobile_expand_hidden", expand_display == "none", expand_display)
                        check("responsive.mobile_result_visible", page.locator("#creatorRunPanel").is_visible())
                        page.screenshot(path=str(screenshots / "06-mobile-720.png"), full_page=True)
                    except Exception as exc:  # preserve partial evidence and report the failure
                        fatal_error = f"{type(exc).__name__}: {exc}"
                        try:
                            page.screenshot(path=str(screenshots / "99-failure.png"), full_page=True)
                        except Exception:
                            pass
                    finally:
                        context.close()
                        browser.close()

    check("browser.console_clean", not console_errors, console_errors)
    check("browser.page_errors_clean", not page_errors, page_errors)
    check("browser.http_errors_clean", not unexpected_http_errors, unexpected_http_errors)
    passed = sum(item["status"] == "PASS" for item in checks)
    failed = sum(item["status"] == "FAIL" for item in checks)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": "PASS" if fatal_error is None and failed == 0 else "FAIL",
        "browser": {"engine": "chromium", "version": browser_version},
        "summary": {"total": len(checks), "passed": passed, "failed": failed},
        "checks": checks,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "expected_optional_404s": sorted(set(expected_optional_404s)),
        "unexpected_http_errors": unexpected_http_errors,
        "fatal_error": fatal_error,
        "screenshots": sorted(path.name for path in screenshots.glob("*.png")),
        "downloads": sorted(path.name for path in downloads.glob("*")),
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="reports/ui_playwright")
    args = parser.parse_args()
    report = run(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
