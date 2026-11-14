"""P9-F: verify real Basilisk position publication in both live session modes.

These tests deliberately require the public Basilisk runtime. They do not use
runtime doubles, Python orbital approximations, or hand-authored position rows.
"""
from __future__ import annotations

import math
import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app

POSITION_FIELDS = tuple(f"orbit.position_m_{axis}" for axis in "xyz")
WHOLE_CAPABILITY = "whole_spacecraft.unified_native.v1"


@pytest.fixture
def native_client(tmp_path, monkeypatch):
    pytest.importorskip("Basilisk")
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "api",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        yield client


def _wait_for_position_frames(client: TestClient, session_id: str, stream_id: str, minimum_time_s: float):
    endpoint = f"/interactive/sessions/{session_id}/telemetry/{stream_id}?after_sequence=-1"
    deadline = time.monotonic() + 20
    last_payload = None
    while time.monotonic() < deadline:
        response = client.get(endpoint)
        assert response.status_code == 200, response.text
        last_payload = response.json()
        frames = last_payload.get("frames", [])
        if frames and frames[-1]["sim_time_s"] >= minimum_time_s:
            return frames
        time.sleep(0.05)
    pytest.fail(f"NATIVE_POSITION_FRAME_TIMEOUT: {last_payload}")


def _assert_real_position_frame(frame: dict, session_id: str, stream_id: str) -> None:
    assert frame["session_id"] == session_id
    assert frame["stream"] == stream_id
    assert frame["quality"] == "GOOD"
    assert isinstance(frame["sequence"], int)
    values = frame["values"]
    position = [values[name] for name in POSITION_FIELDS]
    assert all(isinstance(value, (int, float)) and math.isfinite(value) for value in position)
    radius = math.sqrt(sum(value * value for value in position))
    assert 6_378_137 < radius < 100_000_000


def test_default_continuous_creation_publishes_actual_basilisk_position(native_client):
    session_id = "p9f-native-continuous"
    response = native_client.post("/interactive/continuous/sessions", json={
        "session_id": session_id,
        "quantum_s": 0.2,
        "rate": 1.0,
        "paced": True,
        "initial_horizon_s": 60.0,
    })
    assert response.status_code == 201, response.text

    detail = native_client.get(f"/interactive/sessions/{session_id}").json()
    assert detail["orbit_time_system"] == "UTC"
    assert detail["orbit_frame"] == "N_INERTIAL"
    assert detail["orbit_epoch_utc"]
    stream = next(
        (row for row in detail["telemetry_catalog"] if row["stream_id"] == "whole.orbit"),
        None,
    )
    assert stream is not None, "P9F_DEFAULT_CONTINUOUS_POSITION_NOT_PUBLISHED"
    attitude_fields = {
        "attitude.truth.mrp_BN_x", "attitude.truth.mrp_BN_y", "attitude.truth.mrp_BN_z",
        "attitude.truth.quaternion_BN_q0", "attitude.truth.quaternion_BN_q1",
        "attitude.truth.quaternion_BN_q2", "attitude.truth.quaternion_BN_q3",
        "attitude.truth.valid",
    }
    assert set(POSITION_FIELDS) <= set(stream["fields"])
    assert attitude_fields <= set(stream["fields"])
    primary_stream = next(row for row in detail["telemetry_catalog"] if row["stream_id"] == "whole.continuous")
    assert not (set(POSITION_FIELDS) & set(primary_stream["fields"]))

    status = native_client.get(f"/interactive/continuous/sessions/{session_id}/status").json()
    assert status["orbit_epoch_utc"] == detail["orbit_epoch_utc"]
    assert status["orbit_time_system"] == detail["orbit_time_system"]
    assert status["orbit_frame"] == detail["orbit_frame"]

    started = native_client.post(f"/interactive/sessions/{session_id}/control", json={"action": "start"})
    assert started.status_code == 200, started.text
    try:
        frames = _wait_for_position_frames(native_client, session_id, stream["stream_id"], 0.4)
        _assert_real_position_frame(frames[-1], session_id, stream["stream_id"])
        assert frames[-1]["sim_time_s"] >= 0.4
    finally:
        native_client.post(f"/interactive/sessions/{session_id}/control", json={"action": "stop"})


def test_bounded_whole_spacecraft_creation_publishes_actual_basilisk_position(native_client):
    session_id = "p9f-native-bounded"
    stream_id = "whole.orbit"
    response = native_client.post("/interactive/sessions", json={
        "session_id": session_id,
        "capability_id": WHOLE_CAPABILITY,
        "task_spec": {
            "simulation": {
                "duration_s": 2.0,
                "sample_s": 0.2,
                "solver": {"step_s": 0.1},
            },
            "parameters": {"values": {"epoch_utc": "2026-01-01T00:00:00Z"}},
            "outputs": {"telemetry_streams": [{
                "stream_id": stream_id,
                "sample_s": 0.2,
                "fields": list(POSITION_FIELDS),
            }]},
        },
        "quantum_s": 0.2,
        "rate": 1.0,
        "paced": False,
        "continuous": False,
        "max_sim_time_s": 2.0,
    })
    assert response.status_code == 201, response.text

    detail = native_client.get(f"/interactive/sessions/{session_id}").json()
    assert detail["orbit_epoch_utc"] == "2026-01-01T00:00:00Z"
    assert detail["orbit_time_system"] == "UTC"
    assert detail["orbit_frame"] == "N_INERTIAL"
    stream = next(
        (row for row in detail["telemetry_catalog"] if row["stream_id"] == stream_id),
        None,
    )
    assert stream is not None
    assert set(stream["fields"]) == set(POSITION_FIELDS)

    started = native_client.post(f"/interactive/sessions/{session_id}/control", json={"action": "start"})
    assert started.status_code == 200, started.text
    frames = _wait_for_position_frames(native_client, session_id, stream_id, 0.4)
    _assert_real_position_frame(frames[-1], session_id, stream_id)
    assert frames[-1]["sim_time_s"] >= 0.4
