"""Run with PYTHONPATH pointing to PR 224 src and SAT_SIM_BROWSER_EXECUTABLE set."""
import json
import os
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from sat_sim.api import create_app
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime

KEYS = [f"initial_body_rate_{axis}_rad_s" for axis in "xyz"]
FIELDS = [f"attitude.truth.omega_BN_B_{axis}" for axis in "xyz"]


@pytest.mark.parametrize("case", ["default", "legacy", "custom"])
def test_browser_request_to_native_first_frame(case, tmp_path, monkeypatch):
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    ready = Event()
    first = {}
    actual_advance = UnifiedPersistentRuntime.advance_to

    def observe(runtime, stop):
        rows = actual_advance(runtime, stop)
        if rows and not ready.is_set():
            row = rows[0]
            first.update(time_s=row["time_s"], truth=[row[k] for k in FIELDS],
                         runtime_values={k:runtime.config.values[k] for k in KEYS if k in runtime.config.values})
            ready.set()
        return rows

    monkeypatch.setattr(UnifiedPersistentRuntime, "advance_to", observe)
    app = create_app(artifacts_root=tmp_path/"artifacts", runs_root=tmp_path/"runs",
                     embedded_worker=False, auth_mode="disabled")
    sid = f"dt3-wizard-{case}"
    expected = [-.003, 0., .006] if case == "custom" else [.01, -.004, .002]
    posted = []
    with TestClient(app) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ["SAT_SIM_BROWSER_EXECUTABLE"])
        try:
            page = browser.new_page(viewport={"width":1600,"height":1050}, locale="zh-CN")
            def proxy(route):
                req = route.request
                url = urlsplit(req.url)
                assert url.netloc == "workbench.test"
                data = req.post_data_buffer
                if req.method == "POST" and url.path == "/interactive/continuous/sessions":
                    body = json.loads(data)
                    adcs = body["initial_configuration"]["adcs"]
                    if case == "legacy":
                        for key in KEYS:
                            adcs.pop(key, None)
                        data = json.dumps(body).encode()
                    posted.append({key:adcs[key] for key in KEYS if key in adcs})
                response = client.request(req.method, url.path + ("?"+url.query if url.query else ""),
                                          content=data, headers={k:v for k,v in req.headers.items()
                                                                 if k in ("content-type","accept")})
                route.fulfill(status=response.status_code, body=response.content,
                              content_type=response.headers.get("content-type"))
            page.route("https://workbench.test/**", proxy)
            page.goto("https://workbench.test/", wait_until="domcontentloaded")
            page.get_by_role("button", name="运行正常仿真").click()
            page.get_by_role("button", name="整星").click()
            page.locator("#wizardOptions [data-wizard-object='whole_spacecraft']").click()
            page.locator("#wizardOptions [data-wizard-mode='continuous_realtime']").click()
            page.locator("#continuousSessionId").fill(sid)
            if case == "custom":
                field = page.locator(f'[data-native-key="{KEYS[0]}"]')
                for parent in field.locator("xpath=ancestor::details").all():
                    if not parent.evaluate("el=>el.open"):
                        parent.locator(":scope > summary").click()
                for key,value in zip(KEYS,expected):
                    page.locator(f'[data-native-key="{key}"]').fill(str(value))
            page.locator("#continuousCreateForm button[type=submit]").click()
            page.wait_for_function("document.querySelector('#continuousState').dataset.state==='READY'")
            assert len(posted) == 1
            original = client.get(f"/interactive/continuous/sessions/{sid}/configuration").json()["native_initial_values"]
            accepted = {k:original[k] for k in KEYS if k in original}
            assert accepted == posted[0]
            if case != "legacy":
                assert list(accepted.values()) == expected
            else:
                assert accepted == {}
            page.locator("#continuousStartBtn").click()
            assert ready.wait(20), "No rows returned by the real persistent runtime"
            assert first["runtime_values"] == accepted
            assert first["time_s"] == 0
            assert first["truth"] == pytest.approx(expected, rel=0, abs=1e-14)
            evidence = dict(case=case, submitted=posted[0], accepted=accepted, first=first)
            if directory := os.getenv("DT3_AUDIT_OUTPUT"):
                import inspect
                import subprocess
                from importlib.metadata import version
                checkout = Path(inspect.getfile(UnifiedPersistentRuntime)).resolve().parents[3]
                evidence.update(commit=subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                                                               text=True).strip(),
                                bsk=version("bsk"), chrome=browser.version)
                Path(directory, case + ".json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(json.dumps(evidence))
        finally:
            client.post(f"/interactive/sessions/{sid}/control", json={"action":"stop"})
            browser.close()
