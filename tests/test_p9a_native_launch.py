"""Real native first-frame proof; never replaced by a UI transport fixture."""
from __future__ import annotations
import importlib.util
import math
import time
import pytest
from fastapi.testclient import TestClient
from sat_sim.api import create_app
from sat_sim.local_workbench import configure_local_environment

pytestmark = pytest.mark.skipif(importlib.util.find_spec("Basilisk") is None, reason="Genuine Basilisk is required")

@pytest.mark.parametrize("capability_id,continuous", [
    ("subsystem.adcs_unified_native.v1", False),
    ("whole_spacecraft.unified_native.v1", False),
    ("whole_spacecraft.unified_native.v1", True),
])
def test_local_defaults_produce_real_native_telemetry(capability_id, continuous, tmp_path, monkeypatch):
    monkeypatch.delenv("SAT_SIM_INTERACTIVE_ENABLED", raising=False)
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    configure_local_environment("127.0.0.1")
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
    fields = ["adcs.pointing_error_deg", "adcs.rw.speed_rad_s_0"]
    spec = {
        "session_id": "p9a-native", "capability_id": capability_id,
        "continuous": continuous, "quantum_s": 0.5, "rate": 1.0, "paced": True,
        "max_sim_time_s": 2.0, "max_wall_time_s": 30.0,
        "task_spec": {
            "simulation": {"duration_s": 2.0, "sample_s": 0.5, "solver": {"step_s": 0.1}},
            "parameters": {"values": {}},
            "outputs": {"telemetry_streams": [{"stream_id": "p9a.state", "sample_s": 0.5, "fields": fields}]},
        },
    }
    with TestClient(app) as client:
        assert client.get("/health").json()["features"]["interactive"] is True
        created = client.post("/interactive/sessions", json=spec)
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "READY"
        try:
            started = client.post("/interactive/sessions/p9a-native/control", json={"action": "start"})
            assert started.status_code == 200, started.text
            deadline = time.monotonic() + 15.0
            frames = []
            while time.monotonic() < deadline:
                response = client.get("/interactive/sessions/p9a-native/telemetry/p9a.state")
                assert response.status_code == 200, response.text
                frames = response.json()["frames"]
                if any(frame["sim_time_s"] > 0 for frame in frames):
                    break
                time.sleep(0.05)
            assert any(frame["sim_time_s"] > 0 for frame in frames), "No advanced native telemetry frame"
            assert all(set(frame["values"]) == set(fields) for frame in frames)
            assert all(math.isfinite(value) for frame in frames for value in frame["values"].values())
        finally:
            status = client.get("/interactive/sessions/p9a-native").json()
            if status["state"] not in {"COMPLETED", "FAILED", "ABORTED", "INTERRUPTED"}:
                stopped = client.post("/interactive/sessions/p9a-native/control", json={"action": "stop"})
                assert stopped.status_code == 200, stopped.text
