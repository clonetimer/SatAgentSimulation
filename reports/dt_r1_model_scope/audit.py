"""Read a single sealed Run through its public API and the Run Center UI."""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sat_sim.api import create_app
from sat_sim.run_bundle import verify_run_bundle


def audit(bundle, chrome):
    assert verify_run_bundle(bundle)["ok"]
    claim = json.loads((bundle / "validation/claim_report.json").read_bytes())
    assurance = json.loads((bundle / "runtime/environment.json").read_bytes())["assurance"]
    sealed_report = (bundle / "results/report.html").read_bytes()
    base = f"/runs/{bundle.name}"
    with TemporaryDirectory() as temp:
        app = create_app(runs_root=bundle.parent, artifacts_root=Path(temp),
                         embedded_worker=False, auth_mode="disabled")
        with TestClient(app) as client, sync_playwright() as pw:
            run = client.get(base).json()["run"]
            report_api = client.get(base + "/report").json()
            assert run["run_id"] == report_api["run_id"] == bundle.name
            assert run["integrity"]["ok"] and run["sealed"]
            assert run["claim_report"] == report_api["claim_report"] == claim
            assert client.get(base + "/report.html").content == sealed_report
            browser = pw.chromium.launch(headless=True, executable_path=chrome)
            page = browser.new_page(viewport={"width": 1440, "height": 960}, locale="zh-CN")
            def serve(route):
                url = urlsplit(route.request.url)
                if url.netloc != "workbench.test" or route.request.method != "GET":
                    return route.abort()
                response = client.get(url.path + ("?" + url.query if url.query else ""))
                route.fulfill(status=response.status_code, body=response.content,
                              content_type=response.headers.get("content-type"))
            page.route("**/*", serve)
            page.goto("https://workbench.test/#view=runs")
            row = page.locator("#runCenterTableBody tr").filter(has_text=bundle.name)
            row.get_by_role("button", name="查看", exact=True).click()
            card = page.locator("#validationCard")
            card.locator(".validation-coverage").wait_for(state="visible", timeout=12000)
            scope = card.locator("details")
            collapsed = card.inner_text()
            if scope.count():
                scope.locator("summary").click()
            expanded = card.inner_text()
            # Read the exact bytes served for the sealed report through Chrome.
            page.goto("https://workbench.test" + base + "/report.html")
            boundary = page.locator("section").filter(has=page.get_by_role("heading", name="七、模型边界")).inner_text()
            version = browser.version
            browser.close()
    assert verify_run_bundle(bundle)["ok"]
    return dict(run_id=bundle.name, browser=version, claim=claim, assurance=assurance,
                validation=run["validation_outcome"]["result"], api_claims_equal=True,
                api_model_scope=run.get("model_scope"), report_api_scope=report_api.get("model_scope"),
                card_collapsed=collapsed, card_expanded=expanded, report_boundary=boundary,
                sealed_report_sha256=hashlib.sha256(sealed_report).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("chrome")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = audit(args.bundle.resolve(), args.chrome)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
