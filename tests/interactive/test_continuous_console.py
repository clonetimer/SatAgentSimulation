"""R7 UI continuous console API and first-party web integration tests.

Unit tests use a deterministic *protocol test double* only to verify the
control plane, not as a source of spacecraft physics or research datasets.
The native test must run genuine upstream Basilisk on its own CI gate.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.interactive.models import SessionState


class _ControlProtocolStub:
    def __init__(self):
        self.current_time_s = 0.0
        self.calls = []

    def prepare(self):
        self.calls.append("prepare")

    def apply_command(self, command):
        self.calls.append(command.operation)

    def advance_to(self, stop_time_s):
        self.current_time_s = stop_time_s
        return ({
            "time_s": stop_time_s, "orbit.radius_m": 7000000.,
            "adcs.pointing_error_deg": 0.4, "eps.battery_soc": 0.65,
        },)

    def read_delta(self):
        return ()

    def finalize(self):
        self.calls.append("finalize")
        return {"summary": {"overall_status": "PASS"}}

    def abort(self):
        self.calls.append("abort")


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    monkeypatch.delenv("SAT_SIM_SUPERVISED_NATIVE_RUNTIME", raising=False)
    app = create_app(
        artifacts_root=tmp_path / "artifacts",
        runs_root=tmp_path / "runs",
        embedded_worker=False,
    )
    return app


def _body(name="continuous-web"):
    return {
        "session_id": name, "quantum_s": .2,
        "rate": 1.0, "paced": False, "initial_horizon_s": 60.0,
    }


def test_continuous_console_frontend_and_feature_flag_visibility(tmp_path, monkeypatch):
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        html = client.get("/").text
        js = client.get("/assets/continuous.js")
        css = client.get("/assets/continuous.css")
        original = client.get("/assets/app.js").text
        assert js.status_code == 200 and css.status_code == 200
        for marker in (
            'id="continuousNavButton"',
            'id="continuousView"',
            'id="continuousCreateForm"',
            'id="continuousSessionList"',
            'id="continuousChart"',
            'id="continuousCommandForm"',
            'id="continuousAckList"',
            'id="continuousInterruptedList"',
            'id="continuousChildPid"',
        ):
            assert marker in html
        assert "SatContinuousConsole" in original
        assert "continuous: 'continuousView'" in original
        assert "window.SatContinuousConsole?.enter()" in original
        assert "/interactive/continuous/sessions" in js.text
        assert "/interactive/sessions/" in js.text
        assert "state.telemetry.push(frame)" in js.text
        assert "continuous-grid" in css.text
        assert "@media (max-width: 850px)" in css.text
        assert 'id="continuousNavButton" class="nav-button hidden"' in html

    monkeypatch.delenv("SAT_SIM_INTERACTIVE_ENABLED", raising=False)
    app_off = create_app(
        artifacts_root=tmp_path / "off_artifacts",
        runs_root=tmp_path / "off_runs", embedded_worker=False,
    )
    with TestClient(app_off) as client:
        assert client.get("/interactive/continuous/overview").status_code == 404
        assert 'id="continuousNavButton" class="nav-button hidden"' in client.get("/").text


def test_continuous_api_denies_arbitrary_task_specs_and_bad_values(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        overview = client.get("/interactive/continuous/overview")
        assert overview.status_code == 200
        data = overview.json()
        assert data["native_resume_supported"] is False
        assert data["automatic_tc_replay_allowed"] is False
        assert data["sessions"] == []
        assert data["mode"] == "IN_PROCESS_NATIVE"
        assert data["capability_id"] == "whole_spacecraft.unified_native.v1"
        assert client.post(
            "/interactive/continuous/sessions",
            json={**_body(), "task_spec": {"python": "print('unsafe')"}},
        ).status_code == 422
        assert client.post(
            "/interactive/continuous/sessions",
            json={**_body(), "initial_horizon_s": 0},
        ).status_code == 422
        assert client.post(
            "/interactive/continuous/sessions",
            json={**_body(), "quantum_s": 999},
        ).status_code == 422


def test_continuous_new_page_creates_actual_session_control_and_tm_api(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    instances = []
    def factory(spec):
        assert spec.continuous is True
        assert spec.capability_id == "whole_spacecraft.unified_native.v1"
        assert spec.task_spec["outputs"]["telemetry_streams"][0]["stream_id"] == "whole.continuous"
        runtime = _ControlProtocolStub()
        instances.append(runtime)
        return runtime, None

    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime", factory)
    with TestClient(app) as client:
        created = client.post("/interactive/continuous/sessions", json=_body())
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "READY"
        assert len(instances) == 1
        duplicate = client.post("/interactive/continuous/sessions", json=_body())
        assert duplicate.status_code in (409, 422)
        status = client.get("/interactive/continuous/sessions/continuous-web/status")
        assert status.status_code == 200
        p = status.json()
        assert p["native_resume_supported"] is False
        assert p["automatic_tc_replay_allowed"] is False
        assert p["activate_native_queue_allowed"] is False
        assert p["runtime_mode"] == "IN_PROCESS_NATIVE"
        assert p["writer_epoch"] == 1
        assert p["telemetry_streams"] == ["whole.continuous"]

        commands = {
            "command_id": "ui-set-target", "session_id": "continuous-web",
            "session_revision": created.json()["revision"],
            "operation": "adcs.target.set", "target": "subsystem.adcs",
            "parameters": {"sigma_rn": [.1, 0., 0.]},
            "actor_id": "ui-input-ignored", "actor_role": "operator",
            "execute_at_sim_time_s": .2,
        }
        queue = client.post("/interactive/sessions/continuous-web/commands", json=commands)
        assert queue.status_code == 202, queue.text
        assert queue.json()["state"] == "QUEUED"
        assert client.post(
            "/interactive/sessions/continuous-web/control",
            json={"action": "start"},
        ).status_code == 200

        deadline = time.monotonic() + 6.
        frame = {}
        while time.monotonic() < deadline:
            result = client.get(
                "/interactive/sessions/continuous-web/telemetry/whole.continuous"
            )
            assert result.status_code == 200
            frames = result.json()["frames"]
            if frames:
                frame = frames[-1]
                break
            time.sleep(.02)
        assert frame["values"]["orbit.radius_m"] == 7000000.
        assert frame["values"]["eps.battery_soc"] == .65
        ack = client.get("/interactive/sessions/continuous-web/commands",
                         params={"command_id": "ui-set-target"}).json()["acks"]
        assert any(row["state"] == "ACKED" for row in ack)
        assert "adcs.target.set" in instances[0].calls
        stop = client.post(
            "/interactive/sessions/continuous-web/control",
            json={"action": "stop"},
        )
        assert stop.status_code == 200
        overview = client.get("/interactive/continuous/overview").json()
        assert overview["sessions"][0]["session_id"] == "continuous-web"
        assert overview["sessions"][0]["state"] == "COMPLETED"


def test_continuous_status_rejects_unknown_session(tmp_path, monkeypatch):
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get(
            "/interactive/continuous/sessions/not-found/status"
        ).status_code == 404


def test_native_basilisk_continuous_new_page_rest_creates_real_graph(tmp_path, monkeypatch):
    """Native CI only: REST create & start and real Basilisk telemetry, no proxy."""
    pytest.importorskip("Basilisk")
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        created = client.post(
            "/interactive/continuous/sessions",
            json=_body("real-basilisk-console"),
        )
        assert created.status_code == 201, created.text
        assert created.json()["state"] == "READY"
        assert client.post(
            "/interactive/sessions/real-basilisk-console/control",
            json={"action": "start"},
        ).status_code == 200
        deadline = time.monotonic() + 25.
        while True:
            telemetry = client.get(
                "/interactive/sessions/real-basilisk-console/telemetry/whole.continuous"
            ).json()
            if len(telemetry["frames"]) >= 3:
                break
            assert time.monotonic() < deadline, "native continuous console timed out"
            time.sleep(.04)
        frames = telemetry["frames"]
        assert frames[-1]["values"]["orbit.radius_m"] > 6_000_000
        assert frames[-1]["values"]["eps.battery_soc"] >= 0.
        response = client.post(
            "/interactive/sessions/real-basilisk-console/control",
            json={"action": "stop"},
        )
        assert response.status_code == 200, response.text
