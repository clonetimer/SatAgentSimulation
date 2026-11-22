"""Measure the Chrome orbit scene with the synthetic DT-5 million-row fixture."""
import argparse
import json
import math
import platform
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from sat_sim.api import create_app


def measure(root, chrome):
    app = create_app(runs_root=root, artifacts_root=root / "scene-profile-artifacts",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=chrome)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 960})
            def serve(route):
                url = urlsplit(route.request.url)
                if url.netloc != "workbench.test":
                    return route.abort()
                result = client.get(url.path + ("?" + url.query if url.query else ""))
                route.fulfill(status=result.status_code, body=result.content,
                              content_type=result.headers.get("content-type"))
            page.route("**/*", serve)
            page.goto("https://workbench.test/")
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator('[data-scene-mode="orbit"]').click()
            canvas = page.locator("#satelliteSceneCanvas canvas")
            canvas.wait_for(state="visible")
            page.wait_for_function("!document.querySelector('[data-scene-action=track]').disabled")
            result = page.evaluate("""async () => {
                const canvas = document.querySelector('#satelliteSceneCanvas canvas');
                const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                const response = await fetch('/runs/million_fixture/telemetry/overview?max_points=10000');
                if (!response.ok) throw Error('overview ' + response.status);
                const data = await response.json();
                const begin = performance.now();
                const ok = window.SatScene.setRunTelemetry(data.rows, {verified:true,runId:'million_fixture',
                    epochUtc:'2026-01-01T00:00:00Z',frame:'N_INERTIAL',timeSystem:'UTC'});
                if (!ok || data.total !== 1000000) throw Error('fixture not bound');
                const bind_ms = performance.now() - begin;
                const status = document.querySelector('#satelliteSceneStatus').textContent;
                const intervals = [];
                let previous = await new Promise(requestAnimationFrame);
                for (let i=0; i<180; i++) {
                    const now = await new Promise(requestAnimationFrame);
                    intervals.push(now - previous);
                    previous = now;
                }
                return {total:data.total,overview_rows:data.count,bind_ms,status,
                    renderer:ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
                    canvas:[canvas.width,canvas.height],intervals_ms:intervals};
            }""")
            result.update(browser=browser.version, os=platform.platform())
            values = sorted(result["intervals_ms"])
            result["raf_ms"] = {f"p{q}":values[math.ceil(len(values)*q/100)-1] for q in (50,95,99)}
            box = canvas.bounding_box()
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.mouse.move(x, y)
            page.mouse.down()
            try:
                page.evaluate("""() => {
                    window.dtFrames = [];
                    window.dtFrameActive = true;
                    let last;
                    function tick(now) {
                        if (!window.dtFrameActive) return;
                        if (last !== undefined) window.dtFrames.push(now-last);
                        last = now;
                        requestAnimationFrame(tick);
                    }
                    requestAnimationFrame(tick);
                }""")
                for i in range(120):
                    page.mouse.move(x + 150 * math.sin(i / 20), y + 70 * math.sin(i / 15))
                drag = page.evaluate("() => {window.dtFrameActive=false; return window.dtFrames;}")
            finally:
                page.mouse.up()
            assert len(drag) >= 10
            result["drag_intervals_ms"] = drag
            result["drag_raf_ms"] = {f"p{q}": sorted(drag)[math.ceil(len(drag)*q/100)-1] for q in (50,95,99)}
            return result
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("chrome")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = measure(args.runs_root, args.chrome)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({k:v for k,v in result.items() if not k.endswith("intervals_ms")})
