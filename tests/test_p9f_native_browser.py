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


def _open_orbit_from_physical_default(page) -> None:
    """Use real mode controls; never inject telemetry or click a hidden canvas."""
    from playwright.sync_api import expect

    expect(page.locator('[data-scene-mode="simulation"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#simulationGeometryCanvas")).to_be_visible()
    expect(page.locator("#satelliteOrbitMode")).to_be_hidden()
    page.locator('[data-scene-mode="orbit"]').click()
    expect(page.locator('[data-scene-mode="orbit"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#satelliteSceneCanvas canvas")).to_be_visible(timeout=30000)
    expect(page.locator('[data-scene-action="track"]')).to_be_enabled(timeout=30000)


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
@pytest.mark.parametrize("entry", ["sidebar", "scene", "form", "json", "graph", "scene_custom", "natural_language"])
def test_native_user_created_session_drives_actual_globe_renderer(mode, entry, tmp_path):
    if os.environ.get("SAT_SIM_P9F_NATIVE_BROWSER_TEST") != "1":
        pytest.skip("Dedicated genuine Basilisk/Chromium acceptance job only")
    if entry == "natural_language" and not os.environ.get("SAT_SIM_DT_MODEL_URL"):
        pytest.skip("Requires an actual local model service")
    import Basilisk
    from playwright.sync_api import sync_playwright

    custom_case = entry in {"form", "json", "graph", "natural_language", "scene_custom"}
    evidence_name = mode if entry == "sidebar" else f"{entry}-{mode}"
    evidence = Path(os.environ.get("SAT_SIM_P9F_EVIDENCE", str(tmp_path))) / evidence_name
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
        "creation_entry": entry,
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
                executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"),
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
                websocket.on("framesent", lambda data: report.setdefault("socket_sent", []).append(data))
                websocket.on("framereceived", lambda data: report["socket_messages"].append(data))

            page.on("websocket", observe_socket)
            if entry == "natural_language":
                from dt_creation_browser import register_local_model
                register_local_model(page, base)
            page.goto(base, wait_until="domcontentloaded")
            page.evaluate(GLOBE_OBSERVER)
            assert page.evaluate("typeof window.Globe") == "function"

            rates = dict(zip("xyz", [-0.003, 0.0, 0.006]))
            if entry in {"form", "json", "graph", "natural_language"}:
                if entry == "json":
                    from dt_creation_browser import apply_json_case
                    apply_json_case(page)
                elif entry == "natural_language":
                    from dt_creation_browser import apply_natural_language_case
                    apply_natural_language_case(page, evidence)
                elif entry == "graph":
                    from dt_creation_browser import configure_graph_case
                    configure_graph_case(page)
                else:
                    page.locator('[data-wizard-goal="normal_run"]').click()
                    page.locator('[data-wizard-level="whole_spacecraft"]').click()
                    page.locator('#wizardOptions [data-wizard-object]').first.click()
                    page.locator('[data-wizard-mode="offline"]').click()
                    page.locator("#wizardParametersPanel > summary").click()
                    page.locator('[data-wizard-parameter-mode="all"]').click()
                    for axis, value in rates.items():
                        path = f"parameters.values.initial_body_rate_{axis}_rad_s"
                        page.locator("#wizardParameterSearch").fill(path)
                        page.locator(f'[data-path="{path}"]').fill(str(value))
                        page.locator("#wizardParameterSearch").focus()
                if entry != "graph":
                    page.locator("#wizardAdvancedBtn").click()
                button = "#modeBoundedRealtimeBtn" if mode == "bounded" else "#modeContinuousRealtimeBtn"
                page.locator(button).click()
            elif entry in {"scene", "scene_custom"}:
                page.locator("#wizardSceneShortcutBtn").click()
                if entry == "scene_custom":
                    root_object = page.locator("#p9dObjectTree [role=treeitem]").first
                    root_object.click()
                    report["selected_object"] = root_object.inner_text()
                    for axis, value in rates.items():
                        field = page.locator("#p9dParameterHost label").filter(has_text=f"机体系 {axis.upper()} 分量")
                        field.locator("input").fill(str(value))
                        field.locator("input").press("Tab")
                page.locator("#sceneCreateTaskBtn").click()
                option = "continuous" if mode == "continuous" else "interactive"
                page.locator(f'#sceneTaskCreateModal [data-scene-create="{option}"]').click()
            else:
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

            if entry == "scene_custom":
                if mode == "bounded":
                    page.locator("#interactiveCapability").select_option("whole_spacecraft.unified_native.v1")
                prefix = "interactive" if mode == "bounded" else "continuous"
                page.locator(f"#{prefix}ImportOfflineConfigBtn").click()
                from playwright.sync_api import expect
                expect(page.locator(f"#{prefix}OfflineConfigStatus")).to_contain_text("已导入")
            if mode == "continuous":
                session_id = "p9f-native-browser-continuous"
                _set_control(page, "#continuousSessionId", session_id)
                identifier = page.locator("#continuousSessionId")
                assert identifier.evaluate("(node) => node.checkValidity()") is True
                identifier.fill("probe:valid-1")
                assert identifier.evaluate("(node) => node.checkValidity()") is True
                identifier.fill(session_id)
                _set_control(page, "#continuousQuantum", "0.5" if custom_case else "0.2")
                _set_control(page, "#continuousHorizon", "120")
                create = page.locator('#continuousCreateForm button[type="submit"]')
                create_path = "/interactive/continuous/sessions"
                start_selector = '[data-continuous-action="start"]'
            else:
                capability = page.locator("#interactiveCapability")
                if custom_case:
                    assert capability.input_value() == "whole_spacecraft.unified_native.v1"
                else:
                    capability.select_option("whole_spacecraft.unified_native.v1")
                _set_control(page, "#interactiveDuration", "20" if custom_case else "120")
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
            if custom_case:
                payload = creation_requests[-1]
                report["creation_payload"] = payload
                report["runtime_capabilities"] = _json_request(base, "/interactive/capabilities")
                config = (payload["task_spec"]["parameters"]["values"] if mode == "bounded"
                          else payload["initial_configuration"]["adcs"])
                assert [config[f"initial_body_rate_{a}_rad_s"] for a in rates] == list(rates.values())
                report["requested_rates_body_rad_s"] = rates

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
            adcs_streams = [stream for stream in catalog_streams if stream["stream_id"] == "whole.adcs"]
            assert len(adcs_streams) == 1, "NATIVE_SESSION_CATALOG_MISSING_DEDICATED_ADCS_STREAM"
            assert adcs_streams[0]["sample_s"] == position_streams[0]["sample_s"]
            required_adcs_fields = {
                *[f"adcs.rw.speed_rad_s_{index}" for index in range(3)],
                *[f"adcs.sensor.gyro_measured_rad_s_{axis}" for axis in "xyz"],
                *[f"adcs.sensor.accel_measured_m_s2_{axis}" for axis in "xyz"],
                *[f"adcs.star_tracker.q{index}" for index in range(4)],
                "adcs.star_tracker.valid",
                *[f"adcs.magnetometer.tesla_{axis}" for axis in "xyz"],
                "adcs.sun_sensor.cosine_output", "adcs.sun_sensor.valid",
                *[f"adcs.sun_sensor.direction_b_{axis}" for axis in "xyz"],
            }
            assert required_adcs_fields <= set(adcs_streams[0]["fields"])
            assert set(POSITION_FIELDS) <= set(position_streams[0]["fields"])
            assert set(ATTITUDE_FIELDS) <= set(position_streams[0]["fields"])
            report["telemetry_catalog"] = catalog_streams

            if mode == "bounded":
                task_spec = creation_requests[-1]["task_spec"]
                requested_streams = task_spec["outputs"]["telemetry_streams"]
                assert requested_streams
                scene_streams = [row for row in requested_streams if row["stream_id"] == "whole.orbit"]
                assert len(scene_streams) == 1, "BOUNDED_UI_MISSING_DEDICATED_ORBIT_STREAM"
                adcs_task_streams = [row for row in requested_streams if row["stream_id"] == "whole.adcs"]
                assert len(adcs_task_streams) == 1, "BOUNDED_UI_MISSING_DEDICATED_ADCS_STREAM"
                assert adcs_task_streams[0]["sample_s"] == scene_streams[0]["sample_s"]
                assert required_adcs_fields <= set(adcs_task_streams[0]["fields"])
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
            if custom_case:
                trace = tmp_path / "api" / "interactive_sessions" / session_id / "telemetry.jsonl"
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if trace.exists():
                        with trace.open("rb") as source:
                            first = source.readline()
                        if first.endswith(b"\n"):
                            break
                    page.wait_for_timeout(100)
                else:
                    pytest.fail(f"No native frame recorded for {session_id}")
                frame = json.loads(first)["frame"]
                assert frame["session_id"] == session_id and frame["sim_time_s"] == 0
                truth = {a: frame["values"][f"attitude.truth.omega_BN_B_{a}"] for a in rates}
                assert truth == pytest.approx(rates, abs=1e-14, rel=0)
                report["first_frame_rates_body_rad_s"] = truth
                if mode == "bounded":
                    detail_path = f"/interactive/sessions/{quote(session_id)}"
                    for action in ("Pause", "Step", "Resume"):
                        with page.expect_response(lambda r: r.request.method == "POST" and
                                                  r.url.endswith(detail_path + "/control")) as response:
                            page.locator(f"#interactive{action}Btn").click()
                        assert response.value.ok
                        snap = _json_request(base, detail_path)
                        report[action.lower()] = {"state": snap["state"], "time_s": snap["sim_time_s"]}
                        if action == "Pause":
                            assert snap["state"] == "PAUSED"
                            time.sleep(0.65)
                            assert _json_request(base, detail_path)["sim_time_s"] == snap["sim_time_s"]
                            page.locator("#interactiveCommandTarget").select_option("whole_spacecraft")
                            page.locator("#interactiveCommandOperation").select_option("session.mode.set")
                            page.locator('#interactiveCommandForm [data-parameter="mode"]').select_option("safe")
                            page.locator("#interactiveCommandTime").fill(str(snap["sim_time_s"] + 0.5))
                            with page.expect_response(lambda r: r.request.method == "POST" and
                                                      r.url.endswith(detail_path + "/commands")) as receipt:
                                page.locator("#interactiveSendCommandBtn").click()
                            assert receipt.value.status == 202
                            command_id = receipt.value.json()["command_id"]
                        if action == "Step":
                            assert snap["state"] == "PAUSED"
                            assert snap["sim_time_s"] == report["pause"]["time_s"] + 0.5
                        if action == "Resume":
                            assert snap["state"] == "RUNNING"

            # This is the product's actual scene navigation action. The active
            # real session and its existing telemetry transport must survive.
            page.locator("#wizardSceneShortcutBtn").click()
            _open_orbit_from_physical_default(page)
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
            page.wait_for_function("""() => {
              const inspector = document.querySelector("#sceneFrameSequence")?.textContent;
              const row = window.__p9fObservedGlobe?.objectRows?.find(item => item.session_id);
              return row && inspector === String(row.sequence);
            }""", timeout=10000)
            displayed_frame = page.evaluate("""() => ({
              inspectorSequence: document.querySelector("#sceneFrameSequence")?.textContent,
              globeSequence: window.__p9fObservedGlobe?.objectRows?.find(row => row.session_id)?.sequence,
            })""")
            assert displayed_frame["inspectorSequence"] == str(displayed_frame["globeSequence"])
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
            try:
                # Snapshot one rendered pair before another telemetry tick can change the DOM.
                observation = page.wait_for_function(
                    """() => {
                        const source = document.querySelector('#sceneAdcsFrameSource')?.innerText;
                        const sensors = document.querySelector('#simulationSensorTelemetryRows')?.innerText;
                        return source?.includes('whole.adcs') && sensors && {source, sensors};
                    }""",
                    timeout=15000,
                ).json_value()
            except Exception as error:
                adcs_payload = _json_request(
                    base,
                    f"/interactive/sessions/{quote(session_id)}/telemetry/whole.adcs?after_sequence=0",
                )
                orbit_payload = _json_request(
                    base,
                    f"/interactive/sessions/{quote(session_id)}/telemetry/whole.orbit?after_sequence=0",
                )
                report["p9k_join_diagnostic"] = {
                    "adcs_frames": [
                        {"sequence": row.get("sequence"), "sim_time_s": row.get("sim_time_s"),
                         "quality": row.get("quality"), "stream": row.get("stream")}
                        for row in adcs_payload.get("frames", [])[-12:]
                    ],
                    "orbit_frames": [
                        {"sequence": row.get("sequence"), "sim_time_s": row.get("sim_time_s"),
                         "quality": row.get("quality"), "stream": row.get("stream")}
                        for row in orbit_payload.get("frames", [])[-12:]
                    ],
                    "scene_status": page.locator("#sceneAdcsFrameSource").inner_text(),
                    "socket_sent": report.get("socket_sent", [])[-12:],
                    "socket_received": [
                        json.loads(item) for item in report.get("socket_messages", [])[-80:]
                        if isinstance(item, str) and item.startswith("{")
                    ],
                }
                raise AssertionError(
                    "P9K_STRICT_ADCS_ASSOCIATION_FAILED: "
                    + json.dumps(report["p9k_join_diagnostic"], ensure_ascii=False)
                ) from error
            sensor_rows = observation["sensors"]
            assert "RW1" in sensor_rows
            assert "IMU 陀螺" in sensor_rows
            assert "IMU 加速度计" in sensor_rows
            assert "磁强计" in sensor_rows
            assert "星敏感器" in sensor_rows
            assert "太阳敏感器" in sensor_rows
            assert "whole.adcs" in observation["source"]
            assert matching["values"]["attitude.truth.valid"] == 1
            report["samples"].append({"rendered": rendered["object"], "native_frame": matching})
            report["page_errors"] = list(report["page_errors"])
            assert not report["page_errors"], report["page_errors"]
            assert not report["console_errors"], report["console_errors"]
            if mode == "bounded":
                socket_messages = [json.loads(item) for item in report["socket_messages"] if isinstance(item, str)]
                assert any(
                    item.get("type") == "telemetry"
                    and item.get("payload", {}).get("stream") == "whole.orbit"
                    and item.get("payload", {}).get("quality") == "GOOD"
                    for item in socket_messages
                ), "BOUNDED_BROWSER_WEBSOCKET_DID_NOT_DELIVER_GOOD_ORBIT_FRAME"
                adcs_messages = [
                    item.get("payload", {}) for item in socket_messages
                    if item.get("type") == "telemetry"
                    and item.get("payload", {}).get("stream") == "whole.adcs"
                    and item.get("payload", {}).get("quality") == "GOOD"
                ]
                orbit_messages = [
                    item.get("payload", {}) for item in socket_messages
                    if item.get("type") == "telemetry"
                    and item.get("payload", {}).get("stream") == "whole.orbit"
                    and item.get("payload", {}).get("quality") == "GOOD"
                ]
                assert adcs_messages, "BOUNDED_BROWSER_WEBSOCKET_DID_NOT_DELIVER_GOOD_ADCS_FRAME"
                assert required_adcs_fields <= set(adcs_messages[-1].get("values", {})), \
                    "BOUNDED_BROWSER_ADCS_SENSOR_FIELDS_NOT_PUBLISHED"
                assert any(
                    orbit["session_id"] == adcs["session_id"] == session_id
                    and orbit["sim_time_s"] == adcs["sim_time_s"]
                    for orbit in orbit_messages for adcs in adcs_messages
                ), "BOUNDED_BROWSER_NO_EXACT_ORBIT_ADCS_TIME_PAIR"
            if custom_case:
                if mode == "continuous":
                    previous_time = _json_request(base, f"/interactive/sessions/{quote(session_id)}")["sim_time_s"]
                    page.goto(base, wait_until="domcontentloaded")
                    page.locator("#wizardAdvancedBtn").click()
                    nav = page.locator("#continuousNavButton")
                    for ancestor in ("#workbenchMoreNavigation", ".sidebar-nav-group:has(#continuousNavButton)"):
                        group = page.locator(ancestor)
                        if group.count() == 1 and group.get_attribute("open") is None:
                            group.locator(":scope > summary").click()
                    nav.click()
                    session_button = page.locator("#continuousSessionList button").filter(has_text=session_id)
                    session_button.click()
                    resumed = _wait_for_running(base, mode, session_id)
                    assert resumed["session_id"] == session_id and resumed["sim_time_s"] >= previous_time
                    assert len(creation_requests) == 1
                    report["reconnected_session_id"] = session_id
                    page.locator('[data-continuous-action="stop"]').click()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    snap = _json_request(base, f"/interactive/sessions/{quote(session_id)}")
                    if snap["state"] == "COMPLETED" and snap["archive_status"] == "SEALED":
                        break
                    page.wait_for_timeout(200)
                assert (snap["state"], snap["archive_status"]) == ("COMPLETED", "SEALED"), snap
                if mode == "bounded":
                    assert snap["sim_time_s"] == 20
                    acks = _json_request(base, detail_path + "/commands?command_id=" + command_id)["acks"]
                    assert any(a["state"] == "ACKED" for a in acks), acks
                    recorded = (json.loads(line)["frame"] for line in trace.read_bytes().splitlines())
                    frames = [frame for frame in recorded if frame["stream"] == "runtime.trace"]
                    assert frames[0]["values"]["eps.pdu.payload_enabled"] == 1
                    assert frames[-1]["values"]["eps.pdu.payload_enabled"] == 0
                    report["safe_mode_command"] = {"id": command_id, "acks": acks,
                                                    "payload_enabled_first": 1, "payload_enabled_last": 0}
                import hashlib
                manifest = json.loads((trace.parent / "telemetry_manifest.json").read_bytes())
                assert manifest["telemetry_sha256"] == hashlib.sha256(trace.read_bytes()).hexdigest()
                report["completion"] = {key: snap[key] for key in ("session_id", "state", "sim_time_s", "archive_status")}
                report["telemetry_sha256"] = manifest["telemetry_sha256"]
                result = json.loads((trace.parent / "runtime_result.json").read_bytes())["result"]
                report["native_epoch_utc"] = result["summary"]["orbital_environment"]["epoch_utc"]
                assert report["native_epoch_utc"].replace("+00:00", "Z") == "2026-01-01T00:00:00Z"
                with trace.open("rb") as source:
                    native_times = [frame["sim_time_s"] for frame in
                                    (json.loads(line)["frame"] for line in source)
                                    if frame["stream"] == "runtime.trace"]
                assert len(native_times) > 1 and native_times[0] == 0
                assert native_times[-1] == snap["sim_time_s"]
                report["native_first_last_s"] = [native_times[0], native_times[-1]]
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


@pytest.mark.parametrize("viewport", [{"width": 1600, "height": 1000}, {"width": 390, "height": 844}])
def test_sealed_finite_run_attitude_is_restored_in_real_scene(viewport, tmp_path):
    """A genuine sealed Basilisk Run must restore attitude in the user's history scene."""
    if os.environ.get("SAT_SIM_P9F_NATIVE_BROWSER_TEST") != "1":
        pytest.skip("Dedicated genuine Basilisk/Chromium acceptance job only")
    import Basilisk
    from playwright.sync_api import sync_playwright
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
    from sat_sim.scenario_templates import instantiate_scenario_template

    runs_root = tmp_path / "runs"
    spec = instantiate_scenario_template(
        "whole_spacecraft_unified_native", task_id="p9h_sealed_scene_browser"
    )
    spec["simulation"].update({"duration_s": 2.0, "step_s": 0.1, "sample_s": 0.5})
    spec.setdefault("parameters", {}).setdefault("values", {})["initial_pointing_error_deg"] = 17.0
    # Deliberately select only an EPS curve. Scene-support channels are
    # internal evidence and must not depend on the user's plot selection.
    spec.setdefault("outputs", {})["plots"] = ["eps.battery.soc"]
    prepared = prepare_run(spec, output_root=runs_root, run_id="p9h_sealed_scene_run")
    execution = execute_prepared_run(
        prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
    )
    assert execution.run_record.status.value == "SUCCEEDED"
    assert execution.run_record.sealed is True
    assert verify_run_bundle(prepared.bundle_root)["ok"] is True

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "SAT_SIM_INTERACTIVE_ENABLED": "1",
        "SAT_SIM_SUPERVISED_NATIVE_RUNTIME": "0",
        "SAT_SIM_AUTH_MODE": "disabled",
        "SAT_SIM_EMBEDDED_WORKER": "0",
        "SAT_SIM_RUNS_ROOT": str(runs_root),
        "SAT_SIM_API_ARTIFACTS_ROOT": str(tmp_path / "api"),
    }
    for key in (
        "SAT_SIM_QUEUE_DATABASE", "SAT_SIM_TASK_CENTER_DATABASE",
        "SAT_SIM_EXPERIMENT_DATABASE", "SAT_SIM_AUDIT_LOG",
        "SAT_SIM_WORKBENCH_LOG", "SAT_SIM_MODEL_PROVIDERS_FILE",
    ):
        env.pop(key, None)
    evidence = Path(os.environ.get("SAT_SIM_P9F_EVIDENCE", str(tmp_path))) / "sealed-run"
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "RUNNING", "run_id": prepared.run_id,
        "basilisk_version": Basilisk.__version__, "sealed": True,
        "integrity": "PASS", "selected_plot_fields": spec["outputs"]["plots"],
        "page_errors": [], "console_errors": [], "request_failures": [],
    }
    server_log = (evidence / "server.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sat_sim.api:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
        cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT,
    )
    browser = None
    page = None
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "SEALED_RUN_API_EXITED_BEFORE_READY"
            try:
                _json_request(base, "/health")
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("SEALED_RUN_API_STARTUP_TIMEOUT")
                time.sleep(0.2)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ.get("SAT_SIM_BROWSER_EXECUTABLE"),
                headless=True,
                args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"],
            )
            page = browser.new_page(viewport=viewport, locale="zh-CN")
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("console", lambda message: report["console_errors"].append(message.text)
                    if message.type == "error" else None)
            page.on("requestfailed", lambda request: report["request_failures"].append({
                "url": request.url, "failure": request.failure,
            }))
            page.goto(base, wait_until="domcontentloaded")
            page.evaluate(GLOBE_OBSERVER)
            page.locator("#wizardAdvancedBtn").click()
            page.locator("#runList [data-run-id='p9h_sealed_scene_run']").wait_for(
                state="visible", timeout=30000
            )
            page.locator("#runList [data-run-id='p9h_sealed_scene_run']").click()
            page.locator("#activeRunCard .run-id").wait_for(state="visible", timeout=30000)
            # Use the primary navigation scene entry from the selected Run;
            # the loaded Run remains the scene's authoritative data source.
            page.locator('.sidebar-nav button[data-view="scene"]').click()
            _open_orbit_from_physical_default(page)
            page.wait_for_function("() => !!window.__p9fObservedGlobe", timeout=30000)
            page.wait_for_function(
                "() => window.__p9fObservedGlobe?.objectRows?.some(row => row.attitude_valid === true)",
                timeout=30000,
            )
            rendered = page.evaluate("""() => ({
              object: window.__p9fObservedGlobe?.objectRows?.[0] || null,
              paths: window.__p9fObservedGlobe?.pathRows || [],
              source: document.querySelector('#sceneDataSource')?.dataset.state,
              truth: document.querySelector('#sceneTruthStatus')?.textContent,
              mrp: document.querySelector('#sceneMrpValue')?.textContent,
              quaternion: document.querySelector('#sceneQuaternionValue')?.textContent,
              frameSource: document.querySelector('#sceneFrameSource')?.textContent,
              frameSequence: document.querySelector('#sceneFrameSequence')?.textContent,
              quality: document.querySelector('#sceneFrameQuality')?.textContent,
              estimator: document.querySelector('#sceneEstimatorStatus')?.textContent,
              status: document.querySelector('#satelliteSceneStatus')?.textContent
            })""")
            assert rendered["object"] is not None
            assert rendered["object"]["attitude_valid"] is True
            assert rendered["object"]["attitude_truth"] is not None
            assert len(rendered["object"]["attitude_truth"]["quaternion_BN"]) == 4
            assert sum(v*v for v in rendered["object"]["attitude_truth"]["quaternion_BN"]) == pytest.approx(1.0, abs=1e-6)
            assert rendered["source"] == "sealed"
            assert rendered["truth"].startswith("有效")
            assert rendered["mrp"] != "—" and rendered["quaternion"] != "—"
            assert rendered["frameSource"] == "run:p9h_sealed_scene_run"
            assert rendered["frameSequence"] == "封存样本"
            assert rendered["quality"] == "SEALED"
            assert rendered["estimator"] == "尚未接入"
            assert "已封存有限 Run 遥测" in rendered["status"]
            assert "示意模型轴系" in rendered["status"]
            assert len(rendered["paths"]) >= 1
            assert not report["page_errors"], report["page_errors"]
            report["rendered"] = rendered
            report["status"] = "PASS"
            page.screenshot(path=str(evidence / "sealed-run-scene.png"), full_page=True)
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
        if browser is not None:
            try:
                browser.close()
            except Exception:
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
