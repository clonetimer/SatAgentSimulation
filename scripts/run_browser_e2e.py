#!/usr/bin/env python3
"""
用途：执行主工作台浏览器端到端测试。
参数：--output-dir。
输出：保存浏览器截图、日志和结果。
"""
from __future__ import annotations

import argparse
import json
import shutil
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from sat_sim.api import create_app

SCHEMA_VERSION = "browser-e2e.v1"


def _browser_executable(playwright) -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    candidate = Path(playwright.chromium.executable_path)
    return str(candidate) if candidate.is_file() else None


def run(output_dir: str | Path = "reports/release_browser_e2e") -> dict:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report = {"schema_version": SCHEMA_VERSION, "status": "NOT_EXECUTED_DEPENDENCY_MISSING"}
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    project = Path(__file__).resolve().parents[1]
    web = project / "src" / "sat_sim" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")
    javascript = (web / "app.js").read_text(encoding="utf-8")
    polyfill = """<script>(()=>{const f=()=>{const d=new Map();return{getItem:k=>d.get(String(k))??null,setItem:(k,v)=>d.set(String(k),String(v)),removeItem:k=>d.delete(String(k))}};Object.defineProperty(window,'localStorage',{value:f()});Object.defineProperty(window,'sessionStorage',{value:f()});})();</script>"""
    html = html.replace("<title>卫星仿真工作台</title>", "<title>卫星仿真工作台</title>" + polyfill)
    html = re.sub(r'<link rel="stylesheet" href="/assets/styles\.css(?:\?v=[^"]+)?">', f'<base href="https://workbench.test/"><style>{css}</style>', html)
    html = re.sub(r'<script src="/assets/app\.js(?:\?v=[^"]+)?" defer></script>', lambda _match: f"<script>{javascript}</script>", html)
    console_errors: list[str] = []
    page_errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sat-sim-browser-") as tmp:
        temp = Path(tmp)
        with TestClient(create_app(runs_root=temp / "runs", artifacts_root=temp / "artifacts", embedded_worker=False, auth_mode="disabled")) as client:
            with sync_playwright() as playwright:
                executable = _browser_executable(playwright)
                if not executable:
                    report = {"schema_version": SCHEMA_VERSION, "status": "NOT_EXECUTED_BROWSER_MISSING"}
                    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return report
                browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page(viewport={"width": 1760, "height": 1080})

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
                page.set_content(html, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_selector(".app-sidebar", timeout=30000)
                page.wait_for_selector(".capability-card.active", timeout=30000)
                labels = page.locator(".sidebar-nav .nav-button b").all_inner_texts()
                sidebar_box = page.locator(".app-sidebar").bounding_box()
                creator_box = page.locator("#creatorView").bounding_box()
                page.screenshot(path=str(output / "left-directory.png"), full_page=True)

                page.click("button[data-view='diagnostics']")
                page.wait_for_selector("#diagnosticView:not(.hidden)", timeout=30000)
                page.wait_for_function("document.querySelectorAll('.diagnostic-summary-card').length >= 4", timeout=30000)
                diagnostic_cards = page.locator(".diagnostic-summary-card").count()
                page.screenshot(path=str(output / "diagnostics.png"), full_page=True)

                page.click("button[data-view='logs']")
                page.wait_for_selector("#logCenterView:not(.hidden)", timeout=30000)
                page.wait_for_function("document.querySelector('#logSummary').textContent.includes('匹配')", timeout=30000)
                log_summary = page.locator("#logSummary").inner_text()
                page.screenshot(path=str(output / "logs.png"), full_page=True)

                checks = {
                    "left_directory_labels": labels == ["仿真创建", "任务中心", "实验中心", "运行中心", "系统诊断", "运行日志", "模型服务"],
                    "sidebar_is_left_of_workspace": bool(sidebar_box and creator_box and sidebar_box["x"] < creator_box["x"]),
                    "diagnostic_cards_rendered": diagnostic_cards >= 4,
                    "logs_rendered": "匹配" in log_summary,
                    "console_clean": not console_errors,
                    "page_clean": not page_errors,
                }
                report = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "PASS" if all(checks.values()) else "FAIL",
                    "checks": checks,
                    "directory_labels": labels,
                    "diagnostic_card_count": diagnostic_cards,
                    "log_summary": log_summary,
                    "console_errors": console_errors,
                    "page_errors": page_errors,
                    "screenshots": ["left-directory.png", "diagnostics.png", "logs.png"],
                }
                browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="reports/release_browser_e2e")
    args = parser.parse_args()
    report = run(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
