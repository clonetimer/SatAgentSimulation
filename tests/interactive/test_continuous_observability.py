"""Live host observability of the continuous Basilisk control plane.

Resource observations must be tied to the present OS process, persistent TM
bytes must match original SessionWorkspace counters, and simulated seconds
must never become reported as real-world uptime.
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.interactive.continuous_observability import (
    observe_continuous_session, process_resource_observation,
)
from test_continuous_console import _client, _body, _ControlProtocolStub


def test_resource_sample_is_actual_os_process_or_explicit_unavailable():
    missing = process_resource_observation(None)
    assert missing["available"] is False
    assert missing["resident_bytes"] is None
    assert missing["cpu_time_s"] is None
    assert missing["reason_code"] == "NO_LIVE_PROCESS_HANDLE"
    actual = process_resource_observation(os.getpid())
    if os.name in ("posix", "nt"):
        assert actual["available"] is True, actual
        assert actual["resident_bytes"] > 0
        assert actual["cpu_time_s"] >= 0
        assert actual["pid"] == os.getpid()


def test_metrics_observe_real_wall_and_do_not_scan_full_telemetry_history(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    runtimes = []
    def factory(spec):
        runtime = _ControlProtocolStub()
        runtimes.append(runtime)
        return runtime, None
    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime", factory)

    with TestClient(app) as client:
        created = client.post("/interactive/continuous/sessions", json=_body("observed"))
        assert created.status_code == 201, created.text
        url = "/interactive/continuous/sessions/observed/observability"
        initial = client.get(url)
        assert initial.status_code == 200, initial.text
        first = initial.json()
        assert first["schema_version"] == "sat-continuous-observability.v1"
        assert first["session_state"] == "READY"
        assert first["sim_elapsed_s"] == 0.
        assert first["wall_elapsed_s"] >= 0.
        assert first["telemetry"]["status"] == "PAUSED_OR_READY"
        assert first["telemetry"]["last_sequence"] is None
        assert first["telemetry"]["lag_wall_s"] is None
        assert first["telemetry"]["durable_frame_count"] == 0
        assert first["telemetry"]["durable_bytes"] == 0
        assert first["process"]["scope"] == "API_AND_BASILISK_PARENT"
        assert first["process"]["pid"] == os.getpid()
        assert first["disk"]["free_bytes"] >= 0
        assert first["resume_supported"] is False
        assert first["automatic_tc_replay_allowed"] is False

        time.sleep(.05)
        second = client.get(url).json()
        assert second["wall_elapsed_s"] >= first["wall_elapsed_s"] + .03
        assert second["sim_elapsed_s"] == first["sim_elapsed_s"] == 0.

        started = client.post(
            "/interactive/sessions/observed/control", json={"action": "start"},
        )
        assert started.status_code == 200, started.text
        deadline = time.monotonic() + 8.
        while True:
            row = client.get(url).json()
            if row["telemetry"]["durable_frame_count"] > 0:
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert row["telemetry"]["last_sequence"] is not None
        assert row["telemetry"]["last_sim_time_s"] > 0
        assert row["telemetry"]["last_wall_time"].endswith("+00:00")
        assert row["telemetry"]["lag_wall_s"] >= 0.
        assert row["telemetry"]["durable_bytes"] > 0
        assert row["sim_elapsed_s"] > 0.
        assert row["wall_elapsed_s"] > first["wall_elapsed_s"]
        assert client.post(
            "/interactive/sessions/observed/control", json={"action": "stop"},
        ).status_code == 200
        ended = client.get(url).json()
        assert ended["telemetry"]["status"] == "TERMINAL"
        assert ended["session_state"] == "COMPLETED"
        assert ended["process"]["scope"] == "API_AND_BASILISK_PARENT"


def test_metrics_unknown_session_and_bounded_workspace_reads(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "sat_sim.interactive.api.build_persistent_runtime",
        lambda spec: (_ControlProtocolStub(), None),
    )
    with TestClient(app) as client:
        assert client.get(
            "/interactive/continuous/sessions/missing/observability"
        ).status_code == 404
        assert client.post(
            "/interactive/continuous/sessions",
            json=_body("bounded"),
        ).status_code == 201
        obs = client.get(
            "/interactive/continuous/sessions/bounded/observability"
        ).json()
        # Free disk is an observed OS value, never implied by the task spec.
        assert obs["disk"]["available"] is True
        assert obs["telemetry"]["durable_frame_count"] == 0
        assert obs["telemetry"]["durable_bytes"] == 0


def test_native_basilisk_actual_wallclock_metrics_smoke(tmp_path, monkeypatch):
    """A genuine >=8s *wall-clock* Basilisk run, not 8 simulated seconds."""
    import json
    pytest.importorskip("Basilisk")
    app = _client(tmp_path, monkeypatch)
    samples = []
    with TestClient(app) as client:
        created = client.post(
            "/interactive/continuous/sessions",
            json={**_body("native-wall-observation"), "paced": True, "quantum_s": .2},
        )
        assert created.status_code == 201, created.text
        path = "/interactive/continuous/sessions/native-wall-observation/observability"
        try:
            started = client.post(
                "/interactive/sessions/native-wall-observation/control",
                json={"action": "start"},
            )
            assert started.status_code == 200, started.text
            wall_start = time.monotonic()
            while time.monotonic() - wall_start < 8.0:
                row = client.get(path)
                assert row.status_code == 200, row.text
                observation = row.json()
                samples.append(observation)
                assert observation["schema_version"] == "sat-continuous-observability.v1"
                assert observation["session_state"] != "FAILED", observation
                assert observation["automatic_tc_replay_allowed"] is False
                assert observation["resume_supported"] is False
                time.sleep(.5)
            wall_actual = time.monotonic() - wall_start
            assert wall_actual >= 8.
            assert len(samples) >= 6
            assert samples[-1]["wall_elapsed_s"] - samples[0]["wall_elapsed_s"] >= 7.
            assert samples[-1]["sim_elapsed_s"] > samples[0]["sim_elapsed_s"]
            assert samples[-1]["telemetry"]["durable_frame_count"] > 0
            assert samples[-1]["telemetry"]["durable_bytes"] > 0
            assert samples[-1]["telemetry"]["last_sim_time_s"] is not None
            assert all(
                a["telemetry"]["durable_frame_count"] <= b["telemetry"]["durable_frame_count"]
                and a["telemetry"]["durable_bytes"] <= b["telemetry"]["durable_bytes"]
                and a["sim_elapsed_s"] <= b["sim_elapsed_s"]
                and a["wall_elapsed_s"] <= b["wall_elapsed_s"]
                for a, b in zip(samples, samples[1:])
            )
            if all(row["process"]["available"] for row in samples):
                assert all(
                    a["process"]["cpu_time_s"] <= b["process"]["cpu_time_s"] + 0.1
                    for a, b in zip(samples, samples[1:])
                )
            print("R7E_WALLCLOCK_NATIVE_SAMPLE_JSON:" + json.dumps({
                "wall_sample_window_s": round(wall_actual, 3),
                "wall_observed_delta_s": round(
                    samples[-1]["wall_elapsed_s"] - samples[0]["wall_elapsed_s"], 3
                ),
                "sim_delta_s": round(
                    samples[-1]["sim_elapsed_s"] - samples[0]["sim_elapsed_s"], 3
                ),
                "durable_frames": samples[-1]["telemetry"]["durable_frame_count"],
                "durable_bytes": samples[-1]["telemetry"]["durable_bytes"],
                "process_scope": samples[-1]["process"]["scope"],
                "sample_count": len(samples),
                "qualified_24h_wallclock": False,
            }, sort_keys=True))
        finally:
            client.post(
                "/interactive/sessions/native-wall-observation/control",
                json={"action": "stop"},
            )
