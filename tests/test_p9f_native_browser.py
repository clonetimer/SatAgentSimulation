"""Native UI-to-renderer acceptance. No route interception or fabricated telemetry."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Observe the actual Globe instance without replacing its implementation or data.
OBSERVE_GLOBE = """() => {
  let wrapped;
  Object.defineProperty(window, 'Globe', {
    configurable: true,
    get() { return wrapped; },
    set(value) {
      wrapped = new Proxy(value, {construct(Target,args,newTarget) {
        const globe = Reflect.construct(Target,args,newTarget);
        window.__p9fObservedGlobe = globe;
        return globe;
      }});
    }
  });
}"""
READ_RENDERER = """() => {
  const g=window.__p9fObservedGlobe;
  if (!g) return null;
  const o=g.objectsData()[0];
  if (!o || !o.session_id) return null;
  g.scene().updateMatrixWorld(true);
  const radii=[];
  g.scene().traverse(node=>{
    if(node.name==='Original schematic satellite model') {
      const point=node.getWorldPosition(node.position.clone());
      radii.push(point.length());
    }
  });
  const trail=g.pathsData()[0]?.points || [];
  return {session_id:o.session_id,stream:o.stream,sequence:o.sequence,
    sim_time_s:o.sim_time_s,lat:o.lat,lng:o.lng,alt:o[g.objectAltitude()],
    object_altitude_accessor:g.objectAltitude(),path_altitude_accessor:g.pathPointAlt(),
    trail_count:trail.length,trail_last:trail.at(-1),mesh_radius_units:radii,
    globe_radius_units:g.getGlobeRadius()};
}"""


def set_control(page, selector, value):
    control = page.locator(selector)
    if control.evaluate("node=>node.tagName") == "SELECT":
        control.select_option(str(value))
    else:
        control.fill(str(value))


def check_projection(sample, frame, metadata):
    values = frame["values"]
    x, y, z = [values[f"orbit.position_m_{axis}"] for axis in "xyz"]
    assert all(math.isfinite(v) for v in (x, y, z))
    radius = math.sqrt(x*x+y*y+z*z)
    assert radius > 6378137
    epoch = datetime.fromisoformat(metadata["epoch_utc"].replace("Z", "+00:00"))
    jd = epoch.timestamp()/86400 + 2440587.5 + frame["sim_time_s"]/86400
    t = (jd-2451545.0)/36525
    theta = math.radians((280.46061837+360.98564736629*(jd-2451545.0)+0.000387933*t*t-t*t*t/38710000)%360)
    xe, ye = math.cos(theta)*x+math.sin(theta)*y, -math.sin(theta)*x+math.cos(theta)*y
    lat, lon = math.degrees(math.atan2(z,math.hypot(xe,ye))), math.degrees(math.atan2(ye,xe))
    assert abs(sample["lat"]-lat) < 1e-6
    assert abs((sample["lng"]-lon+180)%360-180) < 1e-6
    expected_alt = (radius-6378137)/6378137
    assert abs(sample["alt"]-expected_alt) < 1e-10
    assert abs(sample["trail_last"][sample["path_altitude_accessor"]]-expected_alt) < 1e-10
    assert sample["mesh_radius_units"], "actual Three.js satellite model was not rendered"
    expected_radius = sample["globe_radius_units"]*(1+expected_alt)
    assert any(abs(r-expected_radius)<1e-3 for r in sample["mesh_radius_units"])


@pytest.mark.parametrize("mode", ["bounded", "continuous"])
def test_native_user_created_session_drives_the_actual_renderer(mode, tmp_path):
    if os.environ.get("SAT_SIM_P9F_NATIVE_BROWSER_TEST") != "1":
        pytest.skip("Dedicated real Basilisk/Chromium acceptance job only")
    import Basilisk
    import psutil
    from playwright.sync_api import sync_playwright

    evidence = Path(os.environ.get("SAT_SIM_P9F_EVIDENCE", str(tmp_path))) / mode
    evidence.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "SAT_SIM_INTERACTIVE_ENABLED":"1",
           "SAT_SIM_SUPERVISED_NATIVE_RUNTIME":"1" if mode == "continuous" else "0",
           "SAT_SIM_AUTH_MODE":"disabled", "SAT_SIM_EMBEDDED_WORKER":"0",
           "SAT_SIM_RUNS_ROOT":str(tmp_path/"runs"), "SAT_SIM_API_ARTIFACTS_ROOT":str(tmp_path/"api")}
    for key in ("SAT_SIM_QUEUE_DATABASE", "SAT_SIM_TASK_CENTER_DATABASE", "SAT_SIM_EXPERIMENT_DATABASE",
                "SAT_SIM_AUDIT_LOG", "SAT_SIM_WORKBENCH_LOG", "SAT_SIM_MODEL_PROVIDERS_FILE"):
        env.pop(key, None)
    report = {"status":"RUNNING", "mode":mode, "basilisk_version":Basilisk.__version__,
              "transport":"real HTTP/WebSocket", "telemetry_injection":False, "page_errors":[], "samples":[]}
    server_log = (evidence/"server.log").open("w", encoding="utf-8")
    server = subprocess.Popen([sys.executable,"-m","uvicorn","sat_sim.api:create_app","--factory",
        "--host","127.0.0.1","--port",str(port),"--no-access-log"],cwd=ROOT,env=env,
        stdout=server_log,stderr=subprocess.STDOUT)
    def get(path):
        with urlopen(base+path, timeout=20) as response:
            return json.load(response)
    try:
        deadline = time.monotonic()+60
        while True:
            assert server.poll() is None, "native API exited before becoming ready"
            try:
                get("/health")
                break
            except OSError:
                if time.monotonic()>deadline:
                    raise TimeoutError("NATIVE_API_STARTUP_TIMEOUT")
                time.sleep(.2)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=["--enable-webgl", "--use-gl=swiftshader", "--ignore-gpu-blocklist"])
            page = browser.new_page(viewport={"width":1600,"height":1000},locale="zh-CN")
            page.add_init_script("("+OBSERVE_GLOBE+")()")
            page.on("pageerror", lambda error:report["page_errors"].append(str(error)))
            page.on("dialog", lambda dialog:dialog.accept())
            creates = []
            socket_messages = []
            def record_request(request):
                if request.method == "POST" and urlsplit(request.url).path in ("/interactive/sessions", "/interactive/continuous/sessions"):
                    creates.append(json.loads(request.post_data))
            def observe_socket(ws):
                ws.on("framereceived", lambda data:socket_messages.append(data))
            page.on("request", record_request)
            page.on("websocket", observe_socket)
            try:
                page.goto(base, wait_until="domcontentloaded")
                page.locator("#wizardAdvancedBtn").click()
                nav = "#continuousNavButton" if mode == "continuous" else "#interactiveNavButton"
                page.locator(nav).click()
                creation_path = "/interactive/continuous/sessions" if mode == "continuous" else "/interactive/sessions"
                if mode == "continuous":
                    page.locator("#continuousSessionId").fill("p9f-native-browser-continuous")
                    set_control(page,"#continuousQuantum","0.2")
                    set_control(page,"#continuousHorizon","120")
                    create = page.locator('#continuousCreateForm button[type="submit"]')
                    state_selector, start, pause, stop = "#continuousState", '[data-continuous-action="start"]', '[data-continuous-action="pause"]', '[data-continuous-action="stop"]'
                else:
                    page.locator("#interactiveCapability").select_option("whole_spacecraft.unified_native.v1")
                    set_control(page,"#interactiveDuration","120")
                    set_control(page,"#interactiveQuantum","0.2")
                    page.locator("#interactivePaced").check()
                    create = page.locator("#createInteractiveSessionBtn")
                    state_selector, start, pause, stop = "#interactiveState", "#interactiveStartBtn", "#interactivePauseBtn", "#interactiveStopBtn"
                with page.expect_response(lambda r:urlsplit(r.url).path==creation_path and r.request.method=="POST",timeout=60000) as response:
                    create.click()
                created = response.value.json()
                assert response.value.status == 201, created
                sid = created["session_id"]
                report["session_id"] = sid
                root = "/interactive/sessions/"+quote(sid)
                page.locator(start).wait_for(state="visible")
                page.wait_for_function("selector=>!document.querySelector(selector).disabled",arg=start)
                page.locator(start).click()
                page.locator("#wizardSceneShortcutBtn").click()
                page.wait_for_function("sid=>window.__p9fObservedGlobe?.objectsData()?.[0]?.session_id===sid",arg=sid,timeout=60000)
                first = page.evaluate(READ_RENDERER)
                page.wait_for_function("seq=>window.__p9fObservedGlobe.objectsData()[0].sequence>seq",arg=first["sequence"],timeout=20000)
                for _ in range(2):
                    page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
                    sample = page.evaluate(READ_RENDERER)
                    metadata = get(root)
                    if mode == "continuous":
                        continuous = get("/interactive/continuous/sessions/"+quote(sid)+"/status")["session"]
                        for key in ("epoch_utc","time_system","orbit_position_frame"):
                            assert continuous[key] == metadata[key]
                    frames = get(root+"/telemetry/"+quote(sample["stream"])+"?after_sequence="+str(sample["sequence"]-1))["frames"]
                    frame = next(row for row in frames if row["sequence"] == sample["sequence"])
                    assert frame["session_id"] == sid and frame["quality"] == "GOOD"
                    assert frame["sim_time_s"] == sample["sim_time_s"]
                    check_projection(sample,frame,metadata)
                    report["samples"].append({"rendered":sample,"native_frame":frame,"runtime_mode":metadata["runtime_mode"]})
                    page.screenshot(path=str(evidence/"native-scene.png"),full_page=True)
                assert len(creates) == 1, "view changes created a duplicate native session"
                if mode == "bounded":
                    assert any('telemetry' in str(message) for message in socket_messages)
                page.locator(nav).click()
                page.locator(pause).click()
                page.wait_for_function("selector=>{const n=document.querySelector(selector);return (n.dataset.state||n.textContent)==='PAUSED';}",arg=state_selector,timeout=20000)
                before = get(root)
                assert before["state"] == "PAUSED"
                page.locator("#wizardSceneShortcutBtn").click()
                page.wait_for_function("sid=>window.__p9fObservedGlobe?.objectsData()?.[0]?.session_id===sid",arg=sid,timeout=30000)
                assert get(root)["sim_time_s"] == before["sim_time_s"], "opening scene advanced a paused native session"
                page.locator(nav).click()
                page.locator(stop).click()
                report["creation_requests"] = creates
                report["paused_time_s"] = before["sim_time_s"]
                assert not report["page_errors"], report["page_errors"]
                assert len(creates)==1
                report["status"] = "PASS"
            except BaseException as exc:
                report["error"] = repr(exc)
                report["status"] = "FAIL"
                try:
                    page.screenshot(path=str(evidence/"failure.png"),full_page=True)
                except Exception:
                    pass
                raise
            finally:
                browser.close()
    finally:
        if server.poll() is None:
            try:
                children = psutil.Process(server.pid).children(recursive=True)
            except psutil.NoSuchProcess:
                children=[]
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        server_log.close()
        (evidence/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
