"""One-shot, exact-anchor candidate preparation. Never updates any remote ref."""
from pathlib import Path
import json
import subprocess

BASE = "b95bebb495e088324bc21b0665e00d3902f41f9a"
changed = []

def edit(path, changes):
    file = Path(path)
    source = file.read_text(encoding="utf-8")
    original = subprocess.check_output(["git", "show", f"{BASE}:{path}"]).decode("utf-8")
    if source != original:
        raise RuntimeError("Unexpected source revision: " + path)
    for before, after, count in changes:
        if source.count(before) != count:
            raise RuntimeError(f"Anchor count mismatch in {path}: {before[:100]!r}")
        source = source.replace(before, after)
    file.write_text(source, encoding="utf-8")
    changed.append(path)

# 1. Publish actual native positions, and use the same metadata envelope on both APIs.
edit("src/sat_sim/interactive/api.py", [
    ('        telemetry_fields = [\n            "orbit.radius_m", "adcs.pointing_error_deg",',
     '        telemetry_fields = [\n            "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",\n            "orbit.radius_m", "adcs.pointing_error_deg",', 1),
    ('"schema_version": "sat-continuous-status.v1",\n            "session": session.snapshot(),',
     '"schema_version": "sat-continuous-status.v1",\n            "session": session_metadata(session),', 1),
])
edit("src/sat_sim/web/app.js", [
    ("fields: ['orbit.radius_m', 'orbit.phase_rad', 'orbit.eclipse_factor']",
     "fields: ['orbit.radius_m', 'orbit.phase_rad', 'orbit.eclipse_factor', 'orbit.position_m_x', 'orbit.position_m_y', 'orbit.position_m_z']", 1),
])
# 2. Preserve the declared Globe accessor. Fail closed before accepting or buffering frames.
edit("src/sat_sim/web/satellite-scene.js", [
    ("altitude: earthFixed.altitudeRatio", "alt: earthFixed.altitudeRatio", 2),
    ("  if (!Number.isInteger(frame.sequence) || frame.sequence <= state.liveLastSequence) return false;",
     "  if (!Number.isSafeInteger(frame.sequence) || frame.sequence < 0 || frame.sequence <= state.liveLastSequence) return false;\n  if (!Number.isFinite(frame.sim_time_s) || frame.sim_time_s < 0 ||\n      (state.lastLiveSimTimeS !== null && frame.sim_time_s < state.lastLiveSimTimeS)) return false;\n  if (!session.streamId || session.timeSystem !== 'UTC' || session.positionFrame !== 'J2000_INERTIAL' || !session.epochUtc) {\n    status('当前会话缺少有效的轨道流、UTC 历元或惯性坐标系信息，暂不更新卫星位置。', 'warning');\n    return false;\n  }", 1),
    ("      name: `真实仿真卫星 · ${session.sessionId}`,\n      lat:",
     "      name: `真实仿真卫星 · ${session.sessionId}`,\n      session_id: session.sessionId, stream: frame.stream,\n      sequence: frame.sequence, sim_time_s: frame.sim_time_s,\n      lat:", 1),
    ("  sessionId, mode = 'bounded', epochUtc, timeSystem = 'UTC',",
     "  sessionId, mode = 'bounded', epochUtc, timeSystem,", 1),
    ("  if (!state.ready || !state.globe) {\n    state.pendingLiveFrame = payload;\n    return true;\n  }",
     "  if (!state.ready || !state.globe) {\n    const session = state.liveSession;\n    const pending = state.pendingLiveFrame?.frame;\n    if (frame.quality !== 'GOOD' || !Number.isSafeInteger(frame.sequence) || frame.sequence < 0 ||\n        frame.sequence <= state.liveLastSequence || !Number.isFinite(frame.sim_time_s) || frame.sim_time_s < 0 ||\n        (state.lastLiveSimTimeS !== null && frame.sim_time_s < state.lastLiveSimTimeS) ||\n        (pending && (frame.sequence <= pending.sequence || frame.sim_time_s < pending.sim_time_s))) return false;\n    if (!session.streamId || session.timeSystem !== 'UTC' || session.positionFrame !== 'J2000_INERTIAL' || !session.epochUtc) return false;\n    try {\n      inertialPositionToEarthFixed({\n        positionM: ['x', 'y', 'z'].map(axis => frame.values?.['orbit.position_m_' + axis]),\n        epochUtc: session.epochUtc, simTimeS: frame.sim_time_s,\n        timeSystem: session.timeSystem, positionFrame: session.positionFrame,\n      });\n    } catch (_) { return false; }\n    state.pendingLiveFrame = payload;\n    return true;\n  }", 1),
])
# Load the real projection module in the existing VM harness; do not replace its math.
edit("tests/js/test_p9c_scene_lifecycle.cjs", [
    ("  vm.runInContext(source.replace(/^import \\* as THREE from 'three';\\s*/, ''), context,",
     "  const projection = fs.readFileSync(path.join(__dirname, '../../src/sat_sim/web/realtime-orbit.mjs'), 'utf8');\n  vm.runInContext(projection.replace(/^export /gm, ''), context);\n  vm.runInContext(source.replace(/^import[^\\n]*\\n/gm, ''), context,", 1),
])
# Missing offline assets caused 404s; stage the same pinned assets as P9-C.
edit(".github/workflows/p9f-realtime-scene.yml", [
    ("      - name: Install Chromium for browser acceptance", "      - name: Stage pinned offline Globe assets\n        run: python scripts/vendor_scene_assets.py\n      - name: Install Chromium for browser acceptance", 1),
    ("          node --test tests/js/test_p9f_realtime_orbit.mjs", "          node --test tests/js/test_p9f_realtime_orbit.mjs tests/js/test_p9f_scene_contract.cjs tests/js/test_p9c_scene_lifecycle.cjs", 1),
    ('      - "tests/js/test_p9f_realtime_orbit.mjs"', '      - "tests/js/test_p9f_realtime_orbit.mjs"\n      - "tests/js/test_p9f_scene_contract.cjs"\n      - "tests/interactive/test_p9f_live_publication.py"', 1),
])
workflow = Path(".github/workflows/p9f-realtime-scene.yml")
workflow.write_text(workflow.read_text(encoding="utf-8") + '''\n  native-publication:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: '3.13'
      - name: Install pinned native runtime and API dependencies
        run: >-
          python -m pip install bsk==2.11.0 sgp4==2.25 colorama==0.4.6 numpy==2.3.5
          pydantic==2.13.4 jsonschema==4.26.0 PyYAML==6.0.3 joblib==1.5.3
          scikit-learn==1.9.0 psutil==7.2.2 numba==0.63.1 pytest==9.0.3
          fastapi==0.139.2 starlette==1.3.1 httpx==0.28.1
      - name: Require native runtime and verify actual position publication
        env:
          PYTHONPATH: 'src:.'
        run: |
          python -c "import Basilisk; print(Basilisk.__version__)"
          python -m pytest -q tests/interactive/test_p9f_live_publication.py
''', encoding="utf-8")
changed += ["tests/interactive/test_p9f_live_publication.py", "tests/js/test_p9f_scene_contract.cjs"]
Path("candidate_files.json").write_text(json.dumps(changed), encoding="utf-8")
print("Candidate files:", json.dumps(changed))
