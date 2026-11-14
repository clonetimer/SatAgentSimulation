"""P9-F: exercise the actual native session endpoints, without runtime doubles."""
from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app

POSITION_FIELDS = tuple(f"orbit.position_m_{axis}" for axis in "xyz")


@pytest.fixture
def native_client(tmp_path, monkeypatch):
    pytest.importorskip("Basilisk")
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "api",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client:
        response = client.post("/interactive/continuous/sessions", json={
            "session_id": "p9f-native-contract", "quantum_s": 0.2,
            "rate": 1.0, "paced": True, "initial_horizon_s": 60.0,
        })
        assert response.status_code == 201, response.text
        yield client


def test_default_continuous_creation_publishes_native_position(native_client):
    root = "/interactive/sessions/p9f-native-contract"
    detail = native_client.get(root).json()
    streams = detail["telemetry_catalog"]
    stream = next((row for row in streams if set(POSITION_FIELDS) <= set(row["fields"])), None)
    assert stream is not None, "P9F_DEFAULT_CONTINUOUS_POSITION_NOT_PUBLISHED"
    started = native_client.post(root + "/control", json={"action": "start"})
    assert started.status_code == 200, started.text
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        payload = native_client.get(root + "/telemetry/" + stream["stream_id"]).json()
        frames = payload.get("frames", [])
        if frames and frames[-1]["sim_time_s"] >= 0.4:
            break
        time.sleep(0.1)
    else:
        pytest.fail("NATIVE_POSITION_FRAME_TIMEOUT")
    frame = frames[-1]
    assert frame["session_id"] == "p9f-native-contract"
    assert frame["quality"] == "GOOD"
    values = [frame["values"][name] for name in POSITION_FIELDS]
    assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
    assert 6_378_137 < math.sqrt(sum(v * v for v in values)) < 100_000_000
    stopped = native_client.post(root + "/control", json={"action": "stop"})
    assert stopped.status_code == 200, stopped.text


def test_continuous_status_supplies_the_same_scene_metadata_as_session_detail(native_client):
    general = native_client.get("/interactive/sessions/p9f-native-contract").json()
    continuous = native_client.get("/interactive/continuous/sessions/p9f-native-contract/status").json()["session"]
    for key in ("epoch_utc", "time_system", "orbit_position_frame"):
        assert key in continuous, "P9F_CONTINUOUS_METADATA_MISSING: " + key
        assert continuous[key] == general[key]
    assert continuous["time_system"] == "UTC"
    assert continuous["orbit_position_frame"] == "J2000_INERTIAL"
