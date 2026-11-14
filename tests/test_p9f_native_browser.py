"""Full native UI-to-Globe E2E for bounded and continuous Basilisk sessions.

The browser creates the session through the product UI, starts the real runtime,
keeps the existing WebSocket/REST telemetry transport, and observes Globe.gl's
actual object/path setters. No frame or position is injected into the page.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
POSITION_FIELDS = tuple(f"orbit.position_m_{axis}" for axis in "xyz")

GLOBE_OBSERVER = r"""
() => {
  const original = window.Globe;
  if (typeof original !== 'function') throw new Error('LOCAL_GLOBE_RUNTIME_NOT_READY');
  window.Globe = new Proxy(original, {
    construct(target, args) {
      const globe = Reflect.construct(target, args, target);
      const observed = {
        objectRows: null,
        pathRows: null,
        objectAltitudeAccessor: null,
        pathPointAltitudeAccessor: null
      };
      const bindings = [
        ['objectsData', 'objectRows'],
        ['pathsData', 'pathRows'],
        ['objectAltitude', 'objectAltitudeAccessor'],
        ['pathPointAlt', 'pathPointAltitudeAccessor']
      ];
      for (const [methodName, propertyName] of bindings) {
        const originalMethod = globe[methodName]?.bind(globe);
        if (typeof originalMethod !== 'function') continue;
        globe[methodName] = function(value) {
          if (arguments.length) observed[propertyName] = structuredClone(value);
          return arguments.length ? originalMethod(value) : observed[propertyName];
        };
      }
      window.__p9fObservedGlobe = observed;
      return globe;
    }
  });
}
"""


def _set_control(page, selector: str, value: str) -> None:
    control = page.locator(selector)
    tag = control.evaluate("(node) => node.tagName")
    if tag == "SELECT":
        control.select_option(str(value))
    else:
        control.fill(str(value))


def _json_request(base: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


@pytest.mark.parametrize("mode", ["bounded", "continuous"])
def test_native_user_created_session_drives_actual_globe_renderer(mode, tmp_path):
    if os.environ.get("SAT_SIM_P9F_NATIVE_BROWSER_TEST") != "1":
        pytest.skip("Dedicated genuine Basilisk/Chromium acceptance job only")
    import Basilisk
    from playwright.sync_api import sync_playwright

    evidence = Path(os.environ.get("SAT_SIM_P9F_EVIDENCE", str(tmp_path))) / mode
    evidence.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "SAT_SIM_INTERACTIVE_ENABLED": "1",
        "SAT_SIM_SUPERVISED_NATIVE_RUNTIME": "1" if mode == "continuous" else "0",
        "SAT_SIM_AUTH_MODE": "disabled",
        "SAT_SIM_EMBEDDED_WORKER": "0",
        "SAT_SIM_RUNS_ROOT": str(tmp_path / "runs"),
        "SAT_SIM_API_ARTIFACTS_ROOT": str(tmp_path / "api"),
    }
    for key in (
        "SAT_SIM_QUEUE_DATABASE", "SAT_SIM_TASK_CENTER_DATABASE",
        "SAT_SIM_EXPERIMENT_DATABASE", "SAT_SIM_AUDIT_LOG",
        "SAT_SIM_WORKBENCH_LOG", "SAT_SIM_MODEL_PROVIDERS_FILE",
    ):
        env.pop(key, None)

    report = {
        "status": "RUNNING",
        "mode": mode,
        "basilisk_version": Basilisk.__version__,
        "transport": "product UI + real HTTP/WebSocket/REST",
        "telemetry_injection": False,
        "page_errors": [],
        "socket_messages": [],
        "samples": [],
    }
    server_log = (evidence / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "sat_sim.api:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    browser = None
    page = None
    session_id = None
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "NATIVE_API_EXITED_BEFORE_READY"
            try:
                _json_request(base, "/health")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("NATIVE_API_STARTUP_TIMEOUT")
                time.sleep(0.2)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
            )
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("dialog", lambda dialog: dialog.accept())

            def observe_socket(websocket):
                websocket.on("framereceived", lambda data: report["socket_messages"].append(data))

            page.on("websocket", observe_socket)
            page.goto(base, wait_until="domcontentloaded")
            page.evaluate(GLOBE_OBSERVER)
            assert page.evaluate("typeof window.Globe") == "function"

            page.locator("#wizardAdvancedBtn").click()
            nav = "#continuousNavButton" if mode == "continuous" else "#interactiveNavButton"
            page.locator(nav).wait_for(state="visible", timeout=30000)
            page.locator(nav).click()

            if mode == "continuous":
                session_id = "p9f-native-browser-continuous"
                _set_control(page, "#continuousSessionId", session_id)
                _set_control(page, "#continuousQuantum", "0.2")
                _set_control(page, "#continuousHorizon", "120")
                create = page.locator('#continuousCreateForm button[type="submit"]')
                create_path = "/interactive/continuous/sessions"
                state_selector = "#continuousState"
                start_selector = '[data-continuous-action="start"]'
            else:
                page.locator("#interactiveCapability").select_option("whole_spacecraft.unified_native.v1")
                _set_control(page, "#interactiveDuration", "120")
                _set_control(page, "#interactiveQuantum", "0.5")
                page.locator("#interactivePaced").check()
                create = page.locator("#createInteractiveSessionBtn")
                create_path = "/interactive/sessions"
                state_selector = "#interactiveState"
                start_selector = "#interactiveStartBtn"

            creation_requests: list[dict] = []

            def record_creation(request):
                if request.method == "POST" and request.url.split("?", 1)[0].endswith(create_path):
                    if request.post_data:
                        creation_requests.append(json.loads(request.post_data))

            page.on("request", record_creation)
            with page.expect_response(
                lambda response: response.request.method == "POST"
                and response.url.split("?", 1)[0].endswith(create_path),
                timeout=60000,
            ) as response_info:
                create.click()
            creation_response = response_info.value
            created = creation_response.json()
            assert creation_response.status == 201, created
            session_id = created["session_id"]
            report["session_id"] = session_id
            assert creation_requests, "UI_SESSION_CREATE_REQUEST_NOT_CAPTURED"

            detail = _json_request(base, f"/interactive/sessions/{quote(session_id)}")
            assert detail["orbit_time_system"] == "UTC"
            assert detail["orbit_frame"] == "N_INERTIAL"
            assert detail["orbit_epoch_utc"]
            catalog_streams = detail["telemetry_catalog"]
            position_streams = [
                stream for stream in catalog_streams
                if set(POSITION_FIELDS) <= set(stream["fields"])
            ]
            assert position_streams, "NATIVE_SESSION_CATALOG_MISSING_THREE_AXIS_POSITION"
            report["telemetry_catalog"] = catalog_streams

            if mode == "bounded":
                task_spec = creation_requests[-1]["task_spec"]
                requested_streams = task_spec["outputs"]["telemetry_streams"]
                assert requested_streams
                assert all(set(POSITION_FIELDS) <= set(stream["fields"]) for stream in requested_streams), (
                    "BOUNDED_UI_DID_NOT_REQUEST_POSITION_FIELDS"
                )

            page.wait_for_function(
                "selector => { const button = document.querySelector(selector); return !!button && !button.disabled; }",
                arg="#continuousCreateForm button[type=submit]" if mode == "continuous" else "#createInteractiveSessionBtn",
                timeout=30000,
            )
            page.locator(start_selector).wait_for(state="visible")
            page.wait_for_function(
                "selector => { const button = document.querySelector(selector); return !!button && !button.disabled; }",
                arg=start_selector,
                timeout=30000,
            )
            page.locator(start_selector).click()
            page.wait_for_function(
                "selector => (document.querySelector(selector)?.textContent || '').includes('RUNNING')",
                arg=state_selector,
                timeout=30000,
            )

            # This is the product's actual scene navigation action. The active
            # real session and its existing telemetry transport must survive.
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator("#satelliteSceneCanvas canvas").wait_for(state="visible", timeout=30000)
            page.wait_for_function("() => !!window.__p9fObservedGlobe", timeout=30000)
            page.wait_for_function(
                "sid => window.__p9fObservedGlobe?.objectRows?.some(row => row.session_id === sid && row.sim_time_s > 0)",
                arg=session_id,
                timeout=60000,
            )
            page.wait_for_function(
                "sid => window.__p9fObservedGlobe?.pathRows?.some(segment => segment.points?.length >= 2)",
                arg=session_id,
                timeout=60000,
            )

            rendered = page.evaluate("""() => {
              const observed = window.__p9fObservedGlobe;
              const object = observed?.objectRows?.find(row => row.session_id);
              const paths = observed?.pathRows || [];
              return {
                object,
                pathRows: paths,
                objectAltitudeAccessor: observed?.objectAltitudeAccessor,
                pathPointAltitudeAccessor: observed?.pathPointAltitudeAccessor,
              };
            }""")
            assert rendered["object"]["session_id"] == session_id
            assert rendered["object"]["stream"] in {row["stream_id"] for row in position_streams}
            assert rendered["object"]["sequence"] >= 0
            assert rendered["object"]["sim_time_s"] > 0
            assert rendered["objectAltitudeAccessor"] == "alt"
            assert rendered["pathPointAltitudeAccessor"] == "alt"
            assert any(len(segment.get("points", [])) >= 2 for segment in rendered["pathRows"])
            assert "Basilisk" in page.locator("#satelliteSceneStatus").inner_text()
            assert "演示数据" not in page.locator("#satelliteSceneStatus").inner_text()

            # Resolve the exact rendered sequence back to the server's real
            # telemetry endpoint; no synthetic frame is supplied to the page.
            stream_id = rendered["object"]["stream"]
            frame_payload = _json_request(
                base,
                f"/interactive/sessions/{quote(session_id)}/telemetry/{quote(stream_id)}"
                f"?after_sequence={rendered['object']['sequence'] - 1}",
            )
            matching = next(
                frame for frame in frame_payload["frames"]
                if frame["sequence"] == rendered["object"]["sequence"]
            )
            assert matching["session_id"] == session_id
            assert matching["stream"] == stream_id
            assert matching["quality"] == "GOOD"
            assert set(POSITION_FIELDS) <= set(matching["values"])
            report["samples"].append({"rendered": rendered["object"], "native_frame": matching})
            report["page_errors"] = list(report["page_errors"])
            assert not report["page_errors"], report["page_errors"]
            if mode == "bounded":
                assert any('"type":"telemetry"' in str(item) or '"type": "telemetry"' in str(item)
                           for item in report["socket_messages"]), "BOUNDED_BROWSER_WEBSOCKET_DID_NOT_DELIVER_TELEMETRY"
            report["status"] = "PASS"
            page.screenshot(path=str(evidence / "native-scene.png"), full_page=True)
    except Exception as error:
        report["status"] = "FAIL"
        report["error"] = repr(error)
        if page is not None:
            try:
                page.screenshot(path=str(evidence / "failure.png"), full_page=True)
            except Exception:
                pass
        raise
    finally:
        if session_id:
            try:
                _json_request(
                    base,
                    f"/interactive/sessions/{quote(session_id)}/control",
                    {"action": "stop"},
                )
            except Exception:
                pass
        if browser is not None:
            browser.close()
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        server_log.close()
        (evidence / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
