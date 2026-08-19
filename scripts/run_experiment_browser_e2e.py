#!/usr/bin/env python3
"""
用途：执行实验中心浏览器端到端测试。
参数：--output-dir。
输出：保存实验中心 E2E 证据。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from sat_sim.api import create_app

SCHEMA_VERSION = "experiment-browser-e2e.v1"


def _browser_executable(playwright) -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    candidate = Path(playwright.chromium.executable_path)
    return str(candidate) if candidate.is_file() else None


def run(output_dir: str | Path = "reports/scenario_browser_e2e") -> dict:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report = {"schema_version": SCHEMA_VERSION, "status": "NOT_EXECUTED_DEPENDENCY_MISSING", "missing_dependency": "playwright"}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    project = Path(__file__).resolve().parents[1]
    web = project / "src" / "sat_sim" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")
    javascript = (web / "app.js").read_text(encoding="utf-8")
    storage_polyfill = """<script>(()=>{const s=()=>{const d=new Map();return{getItem:k=>d.has(String(k))?d.get(String(k)):null,setItem:(k,v)=>d.set(String(k),String(v)),removeItem:k=>d.delete(String(k)),clear:()=>d.clear()}};Object.defineProperty(window,'localStorage',{configurable:true,value:s()});Object.defineProperty(window,'sessionStorage',{configurable:true,value:s()});})();</script>"""
    html = html.replace("<title>卫星仿真工作台</title>", "<title>卫星仿真工作台</title>" + storage_polyfill)
    html = html.replace('<link rel="stylesheet" href="/assets/styles.css">', f'<base href="https://workbench.test/"><style>{css}</style>')
    html = html.replace('<script src="/assets/app.js" defer></script>', f"<script>{javascript}</script>")

    console_errors: list[str] = []
    page_errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sat-sim-experiment-browser-") as tmp:
        temp = Path(tmp)
        with TestClient(create_app(runs_root=temp / "runs", artifacts_root=temp / "artifacts", embedded_worker=False, auth_mode="disabled")) as client:
            with sync_playwright() as playwright:
                executable = _browser_executable(playwright)
                if not executable:
                    report = {"schema_version": SCHEMA_VERSION, "status": "NOT_EXECUTED_BROWSER_MISSING"}
                    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return report
                browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(viewport={"width": 1700, "height": 1050})

                def proxy(route) -> None:
                    request = route.request
                    parsed = urlsplit(request.url)
                    if parsed.netloc != "workbench.test":
                        route.abort()
                        return
                    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                    headers = {k: v for k, v in request.headers.items() if k.lower() in {"content-type", "accept"}}
                    response = client.request(request.method, path, content=request.post_data.encode("utf-8") if request.post_data else None, headers=headers)
                    route.fulfill(status=response.status_code, body=response.content, headers={"content-type": response.headers.get("content-type", "application/octet-stream")})

                page.route("https://workbench.test/**", proxy)
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.set_content(html, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector(".capability-card.active", timeout=30_000)

                template_options = page.locator("#scenarioTemplateSelect option").all_inner_texts()
                page.select_option("#scenarioTemplateSelect", "whole_nominal")
                template_payload = client.post("/scenario-templates/whole_nominal/instantiate", json={}).json()
                applied_name = template_payload["task_spec"]["task"]["name"]
                page.screenshot(path=str(output / "scenario-template.png"), full_page=True)

                created = client.post("/experiments", json={
                    "name": "浏览器参数扫描",
                    "base_task_spec": template_payload["task_spec"],
                    "sweep": {"simulation.duration_s": [20, 40]},
                    "assertions": [{"metric": "status", "operator": "==", "value": "PASS"}],
                }).json()
                page.click("button[data-view='experiments']")
                page.wait_for_selector("#experimentCenterView:not(.hidden)")
                page.click("#refreshExperimentsBtn")
                page.wait_for_function("document.querySelectorAll('.experiment-item').length === 1", timeout=30_000)
                experiment_count = page.locator(".experiment-item").count()
                experiment_summary = page.locator("#experimentSummary").inner_text()
                page.screenshot(path=str(output / "experiment-center.png"), full_page=True)

                page.locator(".experiment-item button", has_text="查看对比").click()
                page.wait_for_selector("#experimentComparisonPanel:not(.hidden)", timeout=30_000)
                comparison_summary = page.locator("#experimentComparisonSummary").inner_text()
                page.screenshot(path=str(output / "experiment-comparison.png"), full_page=True)

                experiment_id = created["experiment"]["experiment_id"]
                detail = client.get(f"/experiments/{experiment_id}").json()
                variant_count = len(detail["members"])
                first_spec = detail["members"][0]["task_spec"]
                second_spec = detail["members"][1]["task_spec"]
                checks = {
                    "template_count_12": len(template_options) == 12,  # placeholder + 11 whole-spacecraft templates
                    "template_applied": applied_name == "完整整星正常运行",
                    "experiment_created": experiment_count == 1 and variant_count == 2,
                    "sweep_values_applied": first_spec["simulation"]["duration_s"] == 20 and second_spec["simulation"]["duration_s"] == 40,
                    "comparison_panel_rendered": "尚无已完成运行" in comparison_summary,
                    "console_clean": not console_errors,
                    "page_clean": not page_errors,
                }
                report = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "PASS" if all(checks.values()) else "FAIL",
                    "checks": checks,
                    "template_options": template_options,
                    "applied_task_name": applied_name,
                    "experiment_count": experiment_count,
                    "variant_count": variant_count,
                    "experiment_summary": experiment_summary,
                    "comparison_summary": comparison_summary,
                    "console_errors": console_errors,
                    "page_errors": page_errors,
                    "screenshots": ["scenario-template.png", "experiment-center.png", "experiment-comparison.png"],
                }
                browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="reports/scenario_browser_e2e")
    args = parser.parse_args()
    report = run(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
