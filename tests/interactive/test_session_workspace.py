from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sat_sim.interactive.manager import InteractiveSessionManager
from sat_sim.interactive.models import InteractiveSessionSpec, SessionState
from sat_sim.interactive.workspace import SessionWorkspace, WorkspaceInspection



class RecordingRuntime:
    def __init__(self) -> None:
        self._time = 0.0
        self.closed = False

    @property
    def current_time_s(self) -> float:
        return self._time

    def prepare(self) -> None:
        pass

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        self._time = stop_time_s
        return ({"time_s": stop_time_s},)

    def apply_command(self, command) -> None:
        pass

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        return ()

    def finalize(self) -> dict[str, Any]:
        self.closed = True
        return {"final_sim_time_s": self._time}

    def abort(self) -> None:
        self.closed = True


def _spec(session_id: str, **updates) -> InteractiveSessionSpec:
    values = {
        "session_id": session_id,
        "capability_id": "whole_spacecraft.bsksim_foundation.v1",
        "task_spec": {"task": {"id": session_id}},
        "quantum_s": 0.1,
        "paced": False,
        "max_sim_time_s": 10.0,
        "heartbeat_interval_s": 0.01,
        "idle_timeout_s": 1.0,
    }
    values.update(updates)
    return InteractiveSessionSpec(**values)


def test_workspace_persists_hash_chained_state_and_detects_tampering(tmp_path: Path) -> None:
    workspace = SessionWorkspace(tmp_path, _spec("hash-chain"))
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_state(SessionState.PREPARING, 1, 0.0, "OK")
    workspace.append_state(SessionState.READY, 2, 0.0, "OK")
    inspection = SessionWorkspace.inspect(workspace.path)
    assert isinstance(inspection, WorkspaceInspection)
    assert inspection.valid is True
    assert inspection.state is SessionState.READY

    lines = workspace.events_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["reason_code"] = "TAMPERED"
    lines[1] = json.dumps(row)
    workspace.events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tampered = SessionWorkspace.inspect(workspace.path)
    assert isinstance(tampered, WorkspaceInspection)
    assert tampered.valid is False
    assert "STATE_HASH_CHAIN_INVALID" in tampered.reason_code


def test_manager_marks_unfinished_workspace_interrupted_on_restart(tmp_path: Path) -> None:
    spec = _spec("unfinished")
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_state(SessionState.READY, 2, 0.0, "OK")
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert len(manager.interrupted_workspaces) == 1
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    _, rows = SessionWorkspace.inspect(workspace.path, include_rows=True)
    assert rows[-1]["reason_code"] == "PROCESS_RESTART"


def test_session_lifecycle_is_written_and_terminal_workspace_is_not_reclassified(tmp_path: Path) -> None:
    spec = _spec("completed")
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(spec, RecordingRuntime(), end_time_s=1.0)
    session.prepare()
    session.abort()
    assert session.wait(1.0)
    inspection = SessionWorkspace.inspect(tmp_path / spec.session_id)
    assert isinstance(inspection, WorkspaceInspection)
    assert inspection.state is SessionState.ABORTED
    restarted = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert restarted.interrupted_workspaces[0].state is SessionState.ABORTED


def test_idle_timeout_reclaims_unprepared_session_and_heartbeat_extends_lease(tmp_path: Path) -> None:
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    idle = manager.create(_spec("idle", idle_timeout_s=0.05), RecordingRuntime(), end_time_s=1.0)
    assert idle.wait(0.5)
    assert idle.state is SessionState.ABORTED
    _, idle_rows = SessionWorkspace.inspect(tmp_path / "idle", include_rows=True)
    assert idle_rows[-1]["reason_code"] == "IDLE_TIMEOUT"

    live = manager.create(_spec("heartbeat", idle_timeout_s=0.06), RecordingRuntime(), end_time_s=1.0)
    for _ in range(3):
        time.sleep(0.03)
        assert live.heartbeat().state is SessionState.CREATED
    assert live.state is SessionState.CREATED
    live.abort()


def test_max_wall_time_reclaims_paused_session(tmp_path: Path) -> None:
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(
        _spec("wall-limit", max_wall_time_s=0.08, idle_timeout_s=1.0),
        RecordingRuntime(),
        end_time_s=10.0,
    )
    session.prepare()
    assert session.wait(0.5)
    assert session.state is SessionState.ABORTED
    _, rows = SessionWorkspace.inspect(tmp_path / "wall-limit", include_rows=True)
    assert rows[-1]["reason_code"] == "MAX_WALL_TIME_EXCEEDED"


def test_telemetry_archive_matches_live_bus_and_detects_tampering(tmp_path: Path) -> None:
    spec = _spec("telemetry-archive", quantum_s=0.5, max_sim_time_s=1.0)
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(spec, RecordingRuntime(), end_time_s=1.0)
    session.prepare()
    session.start()
    assert session.wait(1.0)

    workspace_path = tmp_path / spec.session_id
    archived = SessionWorkspace.inspect_hash_chain(workspace_path / "telemetry.jsonl")
    live = session.telemetry.read_after("runtime.trace").frames
    assert [row["frame"] for row in archived] == [frame.model_dump(mode="json") for frame in live]
    manifest = json.loads((workspace_path / "telemetry_manifest.json").read_text(encoding="utf-8"))
    assert manifest["frame_count"] == len(live) == 2
    assert manifest["streams"] == {"runtime.trace": 2}
    artifact = manifest["artifacts"]["runtime.trace"]
    artifact_rows = [
        json.loads(line)
        for line in (workspace_path / artifact["path"]).read_text(encoding="utf-8").splitlines()
    ]
    assert artifact["row_count"] == 2
    assert artifact_rows == [frame.model_dump(mode="json") for frame in live]
    assert SessionWorkspace.inspect(workspace_path).valid is True

    lines = (workspace_path / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["frame"]["values"]["time_s"] = 999.0
    lines[0] = json.dumps(row)
    (workspace_path / "telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    inspection = SessionWorkspace.inspect(workspace_path)
    assert inspection.valid is False
    assert "TELEMETRY_HASH_CHAIN_INVALID" in inspection.reason_code
