from __future__ import annotations

import json
import time
import base64

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sat_sim.api import create_app
from sat_sim.security import token_sha256


def _session_spec(session_id: str = "eps-api-session") -> dict:
    return {
        "session_id": session_id,
        "capability_id": "subsystem.eps.unified_native.v1",
        "task_spec": {
            "simulation": {"duration_s": 1.0, "solver": {"step_s": 0.5}},
            "parameters": {"values": {"initial_soc": 0.8}},
            "outputs": {
                "telemetry_streams": [{
                    "stream_id": "eps.fast",
                    "sample_s": 0.5,
                    "fields": ["eps.battery_soc", "eps.net_power_w"],
                }],
            },
        },
        "quantum_s": 0.5,
        "paced": False,
        "max_sim_time_s": 1.0,
    }


def _wait_terminal(client: TestClient, session_id: str) -> dict:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        payload = client.get(f"/interactive/sessions/{session_id}").json()
        if payload["state"] in {"COMPLETED", "FAILED", "ABORTED"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("interactive session did not become terminal")


def test_feature_flag_off_preserves_openapi_and_creates_no_manager(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("SAT_SIM_INTERACTIVE_ENABLED", raising=False)
    app = create_app(artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs", embedded_worker=False)
    paths = set(app.openapi()["paths"])
    assert not any(path.startswith("/interactive/") for path in paths)
    assert "/runs/{run_id}/dataset" in paths
    assert "/runs/{run_id}/dataset/download" in paths
    assert not hasattr(app.state, "interactive_manager")


def test_rest_command_telemetry_and_websocket_use_real_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    app = create_app(artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs", embedded_worker=False)
    with TestClient(app) as client:
        created = client.post("/interactive/sessions", json=_session_spec())
        assert created.status_code == 201
        assert created.json()["state"] == "READY"

        command = {
            "command_id": "eps-load-off",
            "session_id": "eps-api-session",
            "session_revision": created.json()["revision"],
            "operation": "eps.load.set",
            "target": "subsystem.eps",
            "parameters": {"load_id": "payload", "enabled": False},
            "actor_id": "untrusted-client-value",
            "actor_role": "operator",
            "execute_at_sim_time_s": 0.5,
        }
        queued = client.post("/interactive/sessions/eps-api-session/commands", json=command)
        assert queued.status_code == 202
        assert queued.json()["state"] == "QUEUED"
        assert client.post(
            "/interactive/sessions/eps-api-session/control", json={"action": "start"},
        ).status_code == 200
        assert _wait_terminal(client, "eps-api-session")["state"] == "COMPLETED"

        replay = client.get("/interactive/sessions/eps-api-session/telemetry/eps.fast?after_sequence=-1")
        assert replay.status_code == 200
        assert replay.json()["gap"] is None
        assert [row["sequence"] for row in replay.json()["frames"]] == [0, 1, 2]
        assert all(set(row["values"]) == {"eps.battery_soc", "eps.net_power_w"} for row in replay.json()["frames"])
        assert client.get("/interactive/sessions/eps-api-session/telemetry/internal.secret").status_code == 404
        assert client.get("/interactive/sessions/eps-api-session/telemetry/runtime.trace").status_code == 404

        with client.websocket_connect("/interactive/sessions/eps-api-session/telemetry") as websocket:
            websocket.send_json({
                "action": "subscribe",
                "streams": ["eps.fast"],
                "after_sequences": {"eps.fast": 0},
            })
            assert websocket.receive_json()["type"] == "subscription_ack"
            first_frame = websocket.receive_json()
            while first_frame["type"] != "telemetry":
                first_frame = websocket.receive_json()
            assert first_frame["type"] == "telemetry"
            assert first_frame["payload"]["sequence"] == 1
            websocket.send_json({"action": "unsubscribe"})
            while websocket.receive_json()["type"] != "unsubscribe_ack":
                pass
        acks = client.get("/interactive/sessions/eps-api-session/commands?command_id=eps-load-off").json()["acks"]
        assert acks[-1]["state"] == "ACKED"
        events = client.get("/interactive/sessions/eps-api-session/events").json()["events"]
        assert [event["sequence"] for event in events] == list(range(len(events)))
        assert events[-1]["state"] == "COMPLETED"


def test_websocket_auth_and_http_roles_are_enforced(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    viewer_token = "viewer-secret"
    operator_token = "operator-secret"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "schema_version": "auth-config.v1",
        "tokens": [
            {
                "token_id": "viewer",
                "principal_id": "viewer-1",
                "roles": ["viewer"],
                "token_sha256": token_sha256(viewer_token),
            },
            {
                "token_id": "operator",
                "principal_id": "operator-1",
                "roles": ["operator"],
                "token_sha256": token_sha256(operator_token),
            },
        ],
    }), encoding="utf-8")
    app = create_app(
        artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs",
        embedded_worker=False, auth_mode="token", auth_config=auth_path,
    )
    with TestClient(app) as client:
        assert client.post("/interactive/sessions", json=_session_spec()).status_code == 401
        try:
            with client.websocket_connect("/interactive/sessions/missing/telemetry"):
                pass
        except WebSocketDisconnect as exc:
            assert exc.code == 4401
        else:
            raise AssertionError("unauthenticated WebSocket was accepted")

        operator_headers = {"Authorization": f"Bearer {operator_token}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}
        assert client.post(
            "/interactive/sessions", json=_session_spec("rbac-session"), headers=operator_headers,
        ).status_code == 201
        assert client.post(
            "/interactive/sessions/rbac-session/control", json={"action": "start"}, headers=viewer_headers,
        ).status_code == 403

        with client.websocket_connect(
            "/interactive/sessions/rbac-session/telemetry", headers=viewer_headers,
        ) as websocket:
            websocket.send_json({"action": "subscribe", "streams": ["eps.fast"]})
            assert websocket.receive_json()["type"] == "subscription_ack"
            websocket.send_json({"action": "heartbeat"})
            while websocket.receive_json()["type"] != "heartbeat_ack":
                pass
            websocket.send_json({"action": "unsubscribe"})
            while websocket.receive_json()["type"] != "unsubscribe_ack":
                pass
        assert client.get(
            "/interactive/sessions/rbac-session", headers=viewer_headers,
        ).json()["state"] == "READY"

        monkeypatch.setenv("SAT_SIM_INTERACTIVE_ALLOWED_ORIGINS", "https://trusted.example")
        try:
            with client.websocket_connect(
                "/interactive/sessions/rbac-session/telemetry",
                headers={**viewer_headers, "Origin": "https://untrusted.example"},
            ):
                pass
        except WebSocketDisconnect as exc:
            assert exc.code == 4401
        else:
            raise AssertionError("untrusted WebSocket origin was accepted")
        monkeypatch.delenv("SAT_SIM_INTERACTIVE_ALLOWED_ORIGINS")

        with client.websocket_connect(
            "/interactive/sessions/rbac-session/telemetry", headers=viewer_headers,
        ) as websocket:
            websocket.send_json({"action": "subscribe", "streams": ["eps.fast"]})
            assert websocket.receive_json()["type"] == "subscription_ack"
            for _ in range(21):
                websocket.send_json({"action": "heartbeat"})
            try:
                while True:
                    websocket.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 1008
                assert exc.reason == "MESSAGE_RATE_EXCEEDED"

        encoded_token = base64.urlsafe_b64encode(viewer_token.encode()).decode().rstrip("=")
        with client.websocket_connect(
            "/interactive/sessions/rbac-session/telemetry",
            subprotocols=["sat-sim-v1", f"sat-auth.{encoded_token}"],
        ) as websocket:
            assert websocket.accepted_subprotocol == "sat-sim-v1"
            websocket.send_json({"action": "subscribe", "streams": ["eps.fast"]})
            assert websocket.receive_json()["type"] == "subscription_ack"


def test_websocket_replay_gap_and_message_limit_are_explicit(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    app = create_app(artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs", embedded_worker=False)
    spec = _session_spec("gap-session")
    spec["telemetry_buffer_frames"] = 2
    with TestClient(app) as client:
        assert client.post("/interactive/sessions", json=spec).status_code == 201
        assert client.post(
            "/interactive/sessions/gap-session/control", json={"action": "start"},
        ).status_code == 200
        assert _wait_terminal(client, "gap-session")["state"] == "COMPLETED"

        replay = client.get("/interactive/sessions/gap-session/telemetry/eps.fast?after_sequence=-1").json()
        assert replay["gap"]["reason_code"] == "BUFFER_EVICTED"
        assert replay["gap"]["next_sequence"] == 1

        with client.websocket_connect("/interactive/sessions/gap-session/telemetry") as websocket:
            websocket.send_json({
                "action": "subscribe", "streams": ["eps.fast"],
                "after_sequences": {"eps.fast": -1},
            })
            assert websocket.receive_json()["type"] == "subscription_ack"
            message = websocket.receive_json()
            while message["type"] != "gap":
                message = websocket.receive_json()
            assert message["payload"]["reason_code"] == "BUFFER_EVICTED"

        with client.websocket_connect("/interactive/sessions/gap-session/telemetry") as websocket:
            websocket.send_text("x" * 65_537)
            try:
                websocket.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 1009
            else:
                raise AssertionError("oversized WebSocket message was accepted")


def test_http_command_cross_session_and_flood_are_fail_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SAT_SIM_INTERACTIVE_ENABLED", "1")
    app = create_app(artifacts_root=tmp_path / "artifacts", runs_root=tmp_path / "runs", embedded_worker=False)
    with TestClient(app) as client:
        created = client.post("/interactive/sessions", json=_session_spec("flood-session"))
        assert created.status_code == 201
        command = {
            "command_id": "cross-session",
            "session_id": "another-session",
            "session_revision": created.json()["revision"],
            "operation": "eps.load.set",
            "target": "subsystem.eps",
            "parameters": {"load_id": "payload", "enabled": False},
            "actor_id": "client-value",
            "actor_role": "operator",
            "execute_at_sim_time_s": 0.5,
        }
        mismatch = client.post("/interactive/sessions/flood-session/commands", json=command)
        assert mismatch.status_code == 422
        assert mismatch.json()["detail"]["reason_code"] == "SESSION_MISMATCH"

        command["session_id"] = "flood-session"
        # The rejected cross-session attempt also consumes rate budget, so 19
        # valid attempts remain in the same rolling window.
        responses = []
        for index in range(20):
            command["command_id"] = f"flood-{index}"
            responses.append(client.post("/interactive/sessions/flood-session/commands", json=command))
        assert all(item.status_code == 202 for item in responses[:19])
        assert responses[19].status_code == 429
        assert responses[19].json()["detail"]["reason_code"] == "COMMAND_RATE_EXCEEDED"
        assert client.post(
            "/interactive/sessions/flood-session/control", json={"action": "abort"},
        ).status_code == 200
