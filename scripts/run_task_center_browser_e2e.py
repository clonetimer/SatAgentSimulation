#!/usr/bin/env python3
"""
用途：执行任务中心浏览器端到端测试。
参数：--output-dir。
输出：保存任务中心 E2E 证据。
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

SCHEMA_VERSION = "task-center-browser-e2e.v1"


def _browser_executable(playwright) -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path
    candidate = Path(playwright.chromium.executable_path)
    return str(candidate) if candidate.is_file() else None


def run(output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "NOT_EXECUTED_DEPENDENCY_MISSING",
            "missing_dependency": "playwright",
            "claim_guardrail": "No browser pass is claimed when Playwright is unavailable.",
        }
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    project = Path(__file__).resolve().parents[1]
    web = project / "src" / "sat_sim" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")
    javascript = (web / "app.js").read_text(encoding="utf-8")
    storage_polyfill = """<script>
      (() => {
        const makeStorage = () => {
          const data = new Map();
          return {
            getItem: (key) => data.has(String(key)) ? data.get(String(key)) : null,
            setItem: (key, value) => data.set(String(key), String(value)),
            removeItem: (key) => data.delete(String(key)),
            clear: () => data.clear(),
          };
        };
        Object.defineProperty(window, 'localStorage', {configurable: true, value: makeStorage()});
        Object.defineProperty(window, 'sessionStorage', {configurable: true, value: makeStorage()});
      })();
    </script>"""
    html = html.replace("<title>卫星仿真工作台</title>", "<title>卫星仿真工作台</title>" + storage_polyfill)
    html = html.replace(
        '<link rel="stylesheet" href="/assets/styles.css">',
        f'<base href="https://workbench.test/"><style>{css}</style>',
    ).replace('<script src="/assets/app.js" defer></script>', f"<script>{javascript}</script>")

    console_errors: list[str] = []
    page_errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="sat-sim-task-center-browser-") as tmp:
        temp = Path(tmp)
        client = TestClient(create_app(
            runs_root=temp / "runs",
            artifacts_root=temp / "artifacts",
            embedded_worker=False,
            auth_mode="disabled",
        ))
        with sync_playwright() as playwright:
            executable = _browser_executable(playwright)
            if not executable:
                report = {
                    "schema_version": SCHEMA_VERSION,
                    "status": "NOT_EXECUTED_BROWSER_MISSING",
                    "claim_guardrail": "No browser pass is claimed when a Chromium executable is unavailable.",
                }
                (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return report
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=executable,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(viewport={"width": 1700, "height": 1050}, device_scale_factor=1)

            def proxy(route) -> None:
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.netloc != "workbench.test":
                    route.abort()
                    return
                path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                headers = {
                    key: value
                    for key, value in request.headers.items()
                    if key.lower() in {"content-type", "accept"}
                }
                response = client.request(
                    request.method,
                    path,
                    content=request.post_data.encode("utf-8") if request.post_data else None,
                    headers=headers,
                )
                route.fulfill(
                    status=response.status_code,
                    body=response.content,
                    headers={"content-type": response.headers.get("content-type", "application/octet-stream")},
                )

            page.route("https://workbench.test/**", proxy)
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.set_content(html, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_selector(".capability-card.active", timeout=120_000)

            count_text = page.locator("#capabilityCount").inner_text()
            group_texts = page.locator(".cap-group-title").all_inner_texts()
            whole_cards = page.locator('.capability-card[data-object-id="whole_spacecraft"]').count()
            subsystem_cards = page.locator('.capability-card[data-object-id^="subsystem."]').count()
            component_cards = page.locator('.capability-card[data-object-id^="component."]').count()
            chinese_names = page.locator(".capability-card .cap-title").all_inner_texts()
            whole_variants = page.locator("#variantSelect option").count()

            page.locator("#themeSelect").select_option("light")
            page.wait_for_function("document.documentElement.dataset.theme === 'light'")
            input_style = page.locator("#capabilitySearch").evaluate(
                "el => ({background:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color,webkit:getComputedStyle(el).webkitTextFillColor})"
            )
            page.screenshot(path=str(output / "light-chinese-catalog.png"), full_page=True)

            page.locator("#openModelSettingsBtn").click()
            page.wait_for_selector("#modelSettingsModal:not(.hidden)")
            model_types = page.locator("#localModelType option").all_inner_texts()
            model_actions = page.locator("#modelSettingsModal .model-settings-actions button").all_inner_texts()
            discovered_select = page.locator("#localModelDiscovered").count()
            page.screenshot(path=str(output / "local-model-settings.png"), full_page=True)
            page.locator("#closeModelSettingsBtn").click()

            page.locator("#saveTaskBtn").click()
            page.wait_for_function("document.querySelector('#notice')?.textContent.includes('另存为新任务')", timeout=120_000)
            page.locator('.nav-button[data-view="tasks"]').click()
            page.wait_for_selector("#taskCenterView:not(.hidden)")
            page.wait_for_function("document.querySelectorAll('#taskTableBody tr').length >= 1", timeout=120_000)
            task_rows = page.locator("#taskTableBody tr").count()
            task_summary = page.locator("#taskSummary").inner_text()
            task_list = client.get("/task-center/tasks").json()["tasks"]
            task_id = task_list[0]["task_id"]
            opened = client.get(f"/task-center/tasks/{task_id}").json()["task"]
            spec = opened["task_spec"]
            spec["simulation"]["duration_s"] = float(spec["simulation"]["duration_s"]) + 1.0
            client.put(f"/task-center/tasks/{task_id}", json={"task_spec": spec, "status": "READY", "change_summary": "浏览器测试修改"})
            page.locator("#refreshTasksBtn").click()
            page.wait_for_function("document.querySelector('#taskTableBody')?.textContent.includes('2 个版本')", timeout=120_000)
            page.locator("#taskTableBody tr").first.locator("button", has_text="详情").click()
            page.wait_for_selector("#taskDetailsModal:not(.hidden)")
            version_rows = page.locator("#taskVersionTableBody tr").count()
            detail_summary = page.locator("#taskDetailsSummary").inner_text()
            page.screenshot(path=str(output / "task-lifecycle.png"), full_page=True)
            page.locator("#closeTaskDetailsBtn").click()
            page.screenshot(path=str(output / "task-center.png"), full_page=True)

            checks = {
                "count_text_exact": count_text == "1 个整星 · 6 个分系统 · 24 个部件",
                "whole_card_count": whole_cards == 1,
                "subsystem_card_count": subsystem_cards == 6,
                "component_card_count": component_cards == 24,
                "whole_variant_count": whole_variants == 5,
                "chinese_names_present": all(name in chinese_names for name in ["整星数字样机", "姿态与轨道控制", "蓄电池", "反作用轮", "推力器"]),
                "light_input_background": input_style["background"] in {"rgb(255, 255, 255)", "rgba(255, 255, 255, 1)"},
                "light_input_text_visible": input_style["color"] != input_style["background"] and input_style["webkit"] != input_style["background"],
                "local_model_types": all(label in model_types for label in ["Ollama", "LM Studio", "vLLM / Qwen", "其他 OpenAI 兼容服务"]),
                "local_model_discovery_controls": discovered_select == 1 and "自动发现模型" in model_actions and "保存并完整测试" in model_actions,
                "task_center_saved": task_rows >= 1 and "共 1 个仿真任务" in task_summary,
                "task_version_history": version_rows == 2 and "2 个版本" in detail_summary,
                "console_clean": not console_errors,
                "page_clean": not page_errors,
            }
            report = {
                "schema_version": SCHEMA_VERSION,
                "status": "PASS" if all(checks.values()) else "FAIL",
                "checks": checks,
                "count_text": count_text,
                "group_titles": group_texts,
                "visible_counts": {"whole_spacecraft": whole_cards, "subsystem": subsystem_cards, "component": component_cards},
                "whole_variant_count": whole_variants,
                "light_input_computed_style": input_style,
                "local_model_types": model_types,
                "local_model_actions": model_actions,
                "task_rows": task_rows,
                "version_rows": version_rows,
                "task_detail_summary": detail_summary,
                "task_summary": task_summary,
                "console_errors": console_errors,
                "page_errors": page_errors,
                "screenshots": ["light-chinese-catalog.png", "local-model-settings.png", "task-lifecycle.png", "task-center.png"],
            }
            browser.close()
        client.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="reports/taskcenter_browser_e2e")
    args = parser.parse_args()
    report = run(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
