"""Full native UI-to-Globe E2E for bounded and continuous Basilisk sessions.

The browser creates the session through the product UI, starts the real runtime,
keeps the existing WebSocket/REST telemetry transport, and observes Globe.gl's
actual object/path setters. No frame or position is injected into the page.
"""
from __future__ import annotations

import json
import math
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
ATTITUDE_FIELDS = (
    "attitude.truth.mrp_BN_x", "attitude.truth.mrp_BN_y", "attitude.truth.mrp_BN_z",
    "attitude.truth.quaternion_BN_q0", "attitude.truth.quaternion_BN_q1",
    "attitude.truth.quaternion_BN_q2", "attitude.truth.quaternion_BN_q3",
    "attitude.truth.valid",
)

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
        objectRotationAccessor: null,
        pathPointAltitudeAccessor: null
      };
      const bindings = [
        ['objectsData', 'objectRows'],
        ['pathsData', 'pathRows'],
        ['objectAltitude', 'objectAltitudeAccessor'],
        ['objectRotation', 'objectRotationAccessor'],
        ['pathPointAlt', 'pathPointAltitudeAccessor']
      ];
      for (const [methodName, propertyName] of bindings) {
        const originalMethod = globe[methodName]?.bind(globe);
        if (typeof originalMethod !== 'function') continue;
        globe[methodName] = function(value) {
          if (arguments.length) {
            if (propertyName === 'objectRows') {
              // Globe.gl may attach THREE objects/functions to source rows.
              // Snapshot only the public telemetry fields; never structuredClone
              // renderer-owned objects or interfere with the real setter.
              observed.objectRows = (Array.isArray(value) ? value : []).map(row => ({
                name: row?.name,
                session_id: row?.session_id,
                stream: row?.stream,
                sequence: row?.sequence,
                quality: row?.quality,
                sim_time_s: row?.sim_time_s,
                lat: row?.lat,
                lng: row?.lng,
                alt: row?.alt,
                altitude_km: row?.altitude_km,
                attitude_truth: row?.attitude_truth,
                attitude_valid: row?.attitude_valid,
                rotation: row?.rotation,
              }));
            } else if (propertyName === 'pathRows') {
              observed.pathRows = (Array.isArray(value) ? value : []).map(segment => ({
                name: segment?.name,
                points: (Array.isArray(segment?.points) ? segment.points : []).map(point => ({
                  lat: point?.lat, lng: point?.lng, alt: point?.alt,
                })),
              }));
            } else {
              observed[propertyName] = value;
            }
          }
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


def _wait_for_running(base: str, mode: str, session_id: str) -> dict:
    deadline = time.monotonic() + 30
    last = None
    while time.monotonic() < deadline:
        if mode == "continuous":
            payload = _json_request(
                base, f"/interactive/continuous/sessions/{quote(session_id)}/status"
            )
            last = payload.get("session") or {}
        else:
            last = _json_request(base, f"/interactive/sessions/{quote(session_id)}")
        state = str(last.get("state") or "").upper()
        if state == "RUNNING":
            return last
        if state in {"FAILED", "ABORTED", "INTERRUPTED", "COMPLETED"}:
            pytest.fail(f"NATIVE_SESSION_TERMINATED_BEFORE_SCENE: {last}")
        time.sleep(0.1)
    pytest.fail(f"NATIVE_SESSION_DID_NOT_REACH_RUNNING: {last}")


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
        "console_errors": [],
        "request_failures": [],
        "globe_asset_responses": [],
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
            page.on("console", lambda message: report["console_errors"].append(message.text)
                    if message.type == "error" else None)
            page.on("requestfailed", lambda request: report["request_failures"].append({
                "url": request.url, "failure": request.failure,
            }))
            page.on("response", lambda response: report["globe_asset_responses"].append({
                "url": response.url, "status": response.status,
            }) if "earth-blue-marble" in response.url else None)
            page.on("dialog", lambda dialog: dialog.accept())

            def observe_socket(websocket):
                websocket.on("framereceived", lambda data: report["socket_messages"].append(data))

            page.on("websocket", observe_socket)
            page.goto(base, wait_until="domcontentloaded")
            page.evaluate(GLOBE_OBSERVER)
            assert page.evaluate("typeof window.Globe") == "function"

            page.locator("#wizardAdvancedBtn").click()
            nav = "#continuousNavButton" if mode == "continuous" else "#interactiveNavButton"
            page.locator(nav + ":not(.hidden)").wait_for(state="attached", timeout=30000)
            # Follow the product's progressive navigation using real keyboard
            # actions; never force a click or inject disclosure/telemetry state.
            more = page.locator("#workbenchMoreNavigation")
            if more.get_attribute("open") is None:
                more.locator(":scope > summary").focus()
                more.locator(":scope > summary").press("Enter")
            realtime = page.locator(".sidebar-nav-group").filter(
                has=page.locator("summary", has_text="实时仿真")
            )
            if realtime.get_attribute("open") is None:
                realtime.locator(":scope > summary").focus()
                realtime.locator(":scope > summary").press("Enter")
            page.locator(nav).wait_for(state="visible", timeout=30000)
            page.locator(nav).click()

            if mode == "continuous":
                session_id = "p9f-native-browser-continuous"
                _set_control(page, "#continuousSessionId", session_id)
                _set_control(page, "#continuousQuantum", "0.2")
                _set_control(page, "#continuousHorizon", "120")
                create = page.locator('#continuousCreateForm button[type="submit"]')
                create_path = "/interactive/continuous/sessions"
                start_selector = '[data-continuous-action="start"]'
            else:
                page.locator("#interactiveCapability").select_option("whole_spacecraft.unified_native.v1")
                _set_control(page, "#interactiveDuration", "120")
                _set_control(page, "#interactiveQuantum", "0.5")
                page.locator("#interactivePaced").check()
                create = page.locator("#createInteractiveSessionBtn")
                create_path = "/interactive/sessions"
                start_selector = "#interactiveStartBtn"

            page.wait_for_function(
                "selector => { const button = document.querySelector(selector); return !!button && !button.disabled; }",
                arg="#continuousCreateForm button[type=submit]" if mode == "continuous" else "#createInteractiveSessionBtn",
                timeout=30000,
            )
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
                if stream["stream_id"] == "whole.orbit"
            ]
            assert len(position_streams) == 1, "NATIVE_SESSION_CATALOG_MISSING_DEDICATED_ORBIT_STREAM"
            assert set(POSITION_FIELDS) <= set(position_streams[0]["fields"])
            assert set(ATTITUDE_FIELDS) <= set(position_streams[0]["fields"])
            report["telemetry_catalog"] = catalog_streams

            if mode == "bounded":
                task_spec = creation_requests[-1]["task_spec"]
                requested_streams = task_spec["outputs"]["telemetry_streams"]
                assert requested_streams
                scene_streams = [row for row in requested_streams if row["stream_id"] == "whole.orbit"]
                assert len(scene_streams) == 1, "BOUNDED_UI_MISSING_DEDICATED_ORBIT_STREAM"
                assert set(POSITION_FIELDS) <= set(scene_streams[0]["fields"])
                assert set(ATTITUDE_FIELDS) <= set(scene_streams[0]["fields"])
                assert all(
                    not (set(POSITION_FIELDS) & set(row["fields"]))
                    for row in requested_streams if row["stream_id"] != "whole.orbit"
                ), "BOUNDED_UI_MIXED_SCENE_SUPPORT_WITH_USER_CHART_STREAMS"

            page.locator(start_selector).wait_for(state="visible")
            page.wait_for_function(
                "selector => { const button = document.querySelector(selector); return !!button && !button.disabled; }",
                arg=start_selector,
                timeout=30000,
            )
            page.locator(start_selector).click()
            running_snapshot = _wait_for_running(base, mode, session_id)
            report["running_snapshot"] = running_snapshot

            # This is the product's actual scene navigation action. The active
            # real session and its existing telemetry transport must survive.
            page.locator("#wizardSceneShortcutBtn").click()
            page.locator("#satelliteSceneCanvas canvas").wait_for(state="visible", timeout=30000)
            page.wait_for_function("() => !!window.__p9fObservedGlobe", timeout=30000)
            try:
                page.wait_for_function(
                    """() => {
                      const node = document.querySelector('#satelliteSceneStatus');
                      return !!node && !node.textContent.includes('正在加载离线地球影像');
                    }""",
                    timeout=20000,
                )
            except Exception as error:
                report["scene_status_at_readiness_timeout"] = page.locator("#satelliteSceneStatus").inner_text()
                report["page_errors"] = list(report["page_errors"])
                report["console_errors"] = list(report["console_errors"])
                try:
                    page.screenshot(path=str(evidence / "scene-readiness-failure.png"), full_page=True)
                except Exception:
                    pass
                raise AssertionError(
                    "REAL_GLOBE_READINESS_NOT_REACHED: "
                    + json.dumps({
                        "status": report["scene_status_at_readiness_timeout"],
                        "page_errors": report["page_errors"],
                        "console_errors": report["console_errors"],
                        "request_failures": report["request_failures"],
                        "globe_asset_responses": report["globe_asset_responses"],
                    }, ensure_ascii=False)
                ) from error
            report["scene_ready_status"] = page.locator("#satelliteSceneStatus").inner_text()
            assert "加载超时" not in report["scene_ready_status"], report["scene_ready_status"]
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
                objectRotationAccessor: observed?.objectRotationAccessor,
                pathPointAltitudeAccessor: observed?.pathPointAltitudeAccessor,
              };
            }""")
            assert rendered["object"]["session_id"] == session_id
            assert rendered["object"]["stream"] == "whole.orbit"
            assert rendered["object"]["sequence"] >= 0
            assert rendered["object"]["sim_time_s"] > 0
            assert rendered["object"]["attitude_valid"] is True
            assert "示意模型轴系" in page.locator("#satelliteSceneStatus").inner_text()
            assert len(rendered["object"]["attitude_truth"]["mrp_BN"]) == 3
            assert len(rendered["object"]["attitude_truth"]["quaternion_BN"]) == 4
            assert sum(value * value for value in rendered["object"]["attitude_truth"]["quaternion_BN"]) == pytest.approx(1.0, abs=1e-6)
            assert set(rendered["object"]["rotation"]) == {"x", "y", "z"}
            assert all(math.isfinite(value) for value in rendered["object"]["rotation"].values())
            expected_rotation = page.evaluate("""({quaternion, epoch, simTime}) =>
              window.SatScene.attitudeToGlobeRotation(quaternion, epoch, simTime)
            """, {
                "quaternion": rendered["object"]["attitude_truth"]["quaternion_BN"],
                "epoch": detail["orbit_epoch_utc"],
                "simTime": rendered["object"]["sim_time_s"],
            })
            for axis in ("x", "y", "z"):
                assert rendered["object"]["rotation"][axis] == pytest.approx(expected_rotation[axis], abs=1e-6)
            # The browser transform must preserve a unit quaternion and react
            # to an independent body-axis rotation, not just a fixed model pose.
            rotations = page.evaluate("""(epoch) => {
              const convert = window.SatScene.attitudeToGlobeQuaternion;
              const a = convert([1, 0, 0, 0], epoch, 0);
              const b = convert([Math.SQRT1_2, Math.SQRT1_2, 0, 0], epoch, 0);
              const badNorm = convert([2, 0, 0, 0], epoch, 0);
              const badEpoch = convert([1, 0, 0, 0], 'not-an-epoch', 0);
              const nonfinite = convert([NaN, 0, 0, 0], epoch, 0);
              return {a, b, normA: Math.hypot(...a), normB: Math.hypot(...b), badNorm, badEpoch, nonfinite};
            }""", detail["orbit_epoch_utc"])
            assert rotations["normA"] == pytest.approx(1.0, abs=1e-6)
            assert rotations["normB"] == pytest.approx(1.0, abs=1e-6)
            assert rotations["a"] != pytest.approx(rotations["b"], abs=1e-5)
            assert rotations["badNorm"] is None
            assert rotations["badEpoch"] is None
            assert rotations["nonfinite"] is None
            assert rendered["objectAltitudeAccessor"] == "alt"
            assert rendered["objectRotationAccessor"] == "rotation"
            assert rendered["pathPointAltitudeAccessor"] == "alt"

            # The live scene exposes the same telemetry in a user-readable
            # inspector and keeps estimator output explicitly separate.
            assert page.locator("#sceneDataSource").get_attribute("data-state") == "live"
            assert page.locator("#sceneTruthStatus").inner_text().startswith("有效")
            assert page.locator("#sceneMrpValue").inner_text() != "—"
            assert page.locator("#sceneQuaternionValue").inner_text() != "—"
            assert page.locator("#sceneFrameSource").inner_text() == f"{session_id} / whole.orbit"
            assert page.locator("#sceneFrameSequence").inner_text() == str(rendered["object"]["sequence"])
            assert page.locator("#sceneFrameQuality").inner_text() == "GOOD"
            assert page.locator("#sceneEstimatorStatus").inner_text() == "尚未接入"

            axes_button = page.locator('[data-scene-action="axes"]')
            assert axes_button.get_attribute("aria-pressed") == "false"
            assert page.evaluate("window.SatScene.hasBodyAxesModel()") is True
            axes_button.click()
            assert axes_button.get_attribute("aria-pressed") == "true"
            assert page.evaluate("window.SatScene.isBodyAxesVisible()") is True
            axes_button.click()
            assert axes_button.get_attribute("aria-pressed") == "false"
            assert page.evaluate("window.SatScene.isBodyAxesVisible()") is False
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
            assert set(ATTITUDE_FIELDS) <= set(matching["values"])
            assert matching["values"]["attitude.truth.valid"] == 1
            assert matching["values"]["attitude.truth.valid"] == 1
            report["samples"].append({"rendered": rendered["object"], "native_frame": matching})
            report["page_errors"] = list(report["page_errors"])
            assert not report["page_errors"], report["page_errors"]
            if mode == "bounded":
                socket_messages = [json.loads(item) for item in report["socket_messages"] if isinstance(item, str)]
                assert any(
                    item.get("type") == "telemetry"
                    and item.get("payload", {}).get("stream") == "whole.orbit"
                    and item.get("payload", {}).get("quality") == "GOOD"
                    for item in socket_messages
                ), "BOUNDED_BROWSER_WEBSOCKET_DID_NOT_DELIVER_GOOD_ORBIT_FRAME"
            report["status"] = "PASS"
            page.screenshot(path=str(evidence / "native-scene.png"), full_page=True)
            browser.close()
            browser = None
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
            try:
                browser.close()
            except Exception:
                # sync_playwright owns the browser and may already have closed
                # its event loop while unwinding the context manager. Preserve
                # the original browser assertion rather than masking it here.
                pass
        if server.poll() is None:
            import psutil
            try:
                descendants = psutil.Process(server.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                descendants = []
            for child in descendants:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
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
