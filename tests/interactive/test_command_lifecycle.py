from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import CommandState, InteractiveSessionSpec, Telecommand
from sat_sim.interactive.workspace import SessionWorkspace

from test_session_manager import RecordingRuntime


def _session(*, queue_capacity: int = 8) -> tuple[InteractiveSession, RecordingRuntime]:
    runtime = RecordingRuntime()
    spec = InteractiveSessionSpec(
        session_id="command-session", capability_id="whole_spacecraft.unified_native.v1",
        task_spec={}, quantum_s=0.1, paced=False, max_sim_time_s=0.2,
        command_queue_capacity=queue_capacity,
    )
    session = InteractiveSession(spec, runtime, end_time_s=0.2)
    session.prepare()
    return session, runtime


def _command(
    session: InteractiveSession,
    command_id: str,
    *,
    sigma: float = 0.1,
    execute_at: float = 0.1,
    expires_at=None,
) -> Telecommand:
    return Telecommand(
        command_id=command_id, session_id=session.spec.session_id, session_revision=session.revision,
        operation="adcs.target.set", target="subsystem.adcs", parameters={"sigma_rn": [sigma, 0.0, 0.0]},
        actor_id="operator-1", actor_role="operator", execute_at_sim_time_s=execute_at,
        expires_at=expires_at,
    )


def test_command_runs_full_ack_lifecycle_at_quantum_boundary() -> None:
    session, runtime = _session()
    command = _command(session, "nominal")
    assert session.submit_command(command).state is CommandState.QUEUED
    session.start()
    assert session.wait(1.0)
    states = [ack.state for ack in session.command_acks(command.command_id)]
    assert states == [
        CommandState.RECEIVED, CommandState.VALIDATED, CommandState.QUEUED,
        CommandState.EXECUTING, CommandState.ACKED,
    ]
    assert session.command_acks(command.command_id)[-1].sim_time_s == 0.1
    assert runtime.owner_threads == {session.worker_ident}


def test_duplicate_conflict_revision_unknown_and_queue_full_are_explicit() -> None:
    session, _ = _session(queue_capacity=1)
    first = _command(session, "same-id")
    assert session.submit_command(first).state is CommandState.QUEUED
    assert session.submit_command(first.model_copy(update={"received_at": datetime.now(timezone.utc)})).reason_code == "DUPLICATE_IDEMPOTENT"
    assert session.submit_command(_command(session, "same-id", sigma=0.2)).reason_code == "COMMAND_ID_CONFLICT"
    assert session.submit_command(_command(session, "full")).reason_code == "COMMAND_QUEUE_FULL"
    stale = _command(session, "stale").model_copy(update={"session_revision": session.revision + 1})
    assert session.submit_command(stale).reason_code == "SESSION_REVISION_MISMATCH"
    unknown = _command(session, "unknown").model_copy(update={"operation": "python.exec"})
    assert session.submit_command(unknown).reason_code == "UNKNOWN_OPERATION"
    session.abort()


def test_queued_command_can_expire_before_execution() -> None:
    session, _ = _session()
    command = _command(
        session, "expires-later",
        expires_at=datetime.now(timezone.utc) + timedelta(milliseconds=30),
    )
    assert session.submit_command(command).state is CommandState.QUEUED
    time.sleep(0.04)
    session.start()
    assert session.wait(1.0)
    assert session.command_acks(command.command_id)[-1].state is CommandState.EXPIRED


def test_every_ack_is_hash_chained_with_actor_audit(tmp_path: Path) -> None:
    runtime = RecordingRuntime()
    spec = InteractiveSessionSpec(
        session_id="audited-command-session", capability_id="whole_spacecraft.unified_native.v1",
        task_spec={}, quantum_s=0.1, paced=False, max_sim_time_s=0.2,
    )
    workspace = SessionWorkspace(tmp_path, spec)
    session = InteractiveSession(spec, runtime, end_time_s=0.2, workspace=workspace)
    session.prepare()
    command = Telecommand(
        command_id="audited", session_id=spec.session_id, session_revision=session.revision,
        operation="adcs.target.set", target="subsystem.adcs", parameters={"sigma_rn": [0.1, 0.0, 0.0]},
        actor_id="named-operator", actor_role="operator", execute_at_sim_time_s=0.1,
    )
    session.submit_command(command)
    session.start()
    assert session.wait(1.0)
    rows = SessionWorkspace.inspect_hash_chain(workspace.commands_path)
    assert [row["ack"]["state"] for row in rows] == ["RECEIVED", "VALIDATED", "QUEUED", "EXECUTING", "ACKED"]
    assert all(row["actor_id"] == "named-operator" for row in rows)

    text = workspace.commands_path.read_text(encoding="utf-8")
    workspace.commands_path.write_text(text.replace("named-operator", "other-operator", 1), encoding="utf-8")
    inspection = SessionWorkspace.inspect(workspace.path)
    assert inspection.valid is False
    assert "COMMAND_HASH_CHAIN_INVALID" in inspection.reason_code
