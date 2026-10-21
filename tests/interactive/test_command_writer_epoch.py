"""R6d: only one authoritative continuous TC owner; queued records are inert.

Reuses real session workspaces, mailboxes and SQLite. Tests explicitly
verify no automatic takeover of crashed owner, no stale-owner mutation,
no TC resurrection, and strict order when inspecting future queue entries.
"""
from __future__ import annotations

import sqlite3

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.command_pending_audit import inspect_pending_queue
from sat_sim.interactive.manager import InteractiveSessionManager
from sat_sim.interactive.models import CommandAck, CommandState, SessionState, TelemetryFrame
from sat_sim.interactive.workspace import SessionWorkspace
from test_command_transaction_ledger import _cmd, _spec


def _epoch(path):
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT epoch,owner_state FROM command_writer_epoch WHERE singleton=1"
        ).fetchone()


def test_competing_live_sqlite_writer_rejected_without_stealing(tmp_path):
    path = tmp_path / "command_ids.sqlite"
    owner = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=path)
    try:
        assert owner.writer_epoch == 1
        with pytest.raises(RuntimeError, match="TC_WRITER_ALREADY_ACTIVE"):
            DeterministicCommandMailbox(
                persistent_ledger=True, ledger_path=path,
            )
        assert _epoch(path) == (1, "ACTIVE")
        assert owner.submit(_cmd("owner-A"), session_revision=2).accepted
    finally:
        owner.close()
    assert _epoch(path) == (1, "RELEASED")
    successor = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=path,
    )
    try:
        assert successor.writer_epoch == 2
        assert successor.submit(_cmd("owner-A"), session_revision=2).duplicate
        assert len(successor) == 0
    finally:
        successor.close()
    assert _epoch(path) == (2, "RELEASED")


def test_unclean_owner_exit_blocks_automatic_reopen(tmp_path):
    path = tmp_path / "command_ids.sqlite"
    owner = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=path)
    assert owner.submit(_cmd("crashed-session"), session_revision=2).accepted
    # Model OS crash: SQLite connection closed but lifecycle release did not run.
    owner._database.close()
    owner._database = None
    with pytest.raises(RuntimeError, match="TC_WRITER_ALREADY_ACTIVE"):
        DeterministicCommandMailbox(persistent_ledger=True, ledger_path=path)
    assert _epoch(path) == (1, "ACTIVE")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state FROM command_lifecycle").fetchone()[0] == "QUEUED"


def test_stale_owner_epoch_fences_all_future_mutations(tmp_path):
    path = tmp_path / "command_ids.sqlite"
    owner = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=path)
    cmd = _cmd("fenced-session")
    assert owner.submit(cmd, session_revision=2).accepted
    # Fault-injected external administrative DB generation replacement.
    # Real takeover is intentionally NOT offered as a user API.
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE command_writer_epoch SET epoch=epoch+1,owner_token=? "
            "WHERE singleton=1", ("replacement-token",),
        )
    try:
        with pytest.raises(RuntimeError, match="TC_WRITER_EPOCH_FENCED"):
            owner.submit(_cmd("fenced-session", "tc-new"), session_revision=2)
        with pytest.raises(RuntimeError, match="TC_WRITER_EPOCH_FENCED"):
            owner.mark_executing_before_effect(cmd, sim_time_s=0.)
        with pytest.raises(RuntimeError, match="TC_WRITER_EPOCH_FENCED"):
            owner.pop_due(2.)
        assert len(owner) == 1
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM command_ids").fetchone()[0] == 1
            assert db.execute("SELECT state FROM command_lifecycle").fetchone()[0] == "QUEUED"
    finally:
        # A fenced writer can never release the replacement's token.
        with pytest.raises(RuntimeError, match="TC_WRITER_EPOCH_FENCED"):
            owner.close()
        owner._database.close()
        owner._database = None


def _interrupted_pending(tmp_path, values, *, include_trace=True):
    spec = _spec("queued-plan")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    ws.append_state(SessionState.RUNNING, 2, 0.0, "OK")
    mailbox = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    next_seq = 0
    for name, due in values:
        cmd = _cmd(spec.session_id, name).model_copy(
            update={"execute_at_sim_time_s": due},
        )
        for phase in (CommandState.RECEIVED, CommandState.VALIDATED):
            ws.append_command_ack(cmd, CommandAck(
                command_id=name, session_id=spec.session_id,
                state=phase, sequence=next_seq,
            ))
            next_seq += 1
        assert mailbox.submit(cmd, session_revision=2).accepted
        ack = CommandAck(
            command_id=name, session_id=spec.session_id,
            state=CommandState.QUEUED, sequence=next_seq,
            reason_code="QUEUED",
        )
        event = ws.append_command_ack(cmd, ack)
        mailbox.mirror_durable_ack(
            cmd, state="QUEUED", reason_code="QUEUED",
            ack_sequence=next_seq, entry_hash=event["entry_hash"],
            sim_time_s=None,
        )
        next_seq += 1
    mailbox.close()
    if include_trace:
        ws.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="runtime.trace",
            sequence=0, sim_time_s=0.0, values={"time_s": 0.0},
        ))
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    return ws


def test_read_only_pending_order_is_due_then_original_receive_sequence(tmp_path):
    ws = _interrupted_pending(tmp_path, [
        ("later", 0.5), ("first-tie", 0.2), ("second-tie", 0.2),
    ])
    before = (ws.path / "command_ids.sqlite").stat().st_size
    event_bytes = ws.commands_path.read_bytes()
    report = inspect_pending_queue(ws.path)
    assert report["status"] == "CANDIDATE_METADATA_VERIFIED_NO_NATIVE_ACTIVATION"
    assert report["source_writer_epoch"] == 1
    assert report["source_writer_state"] == "RELEASED"
    assert [v["command_id"] for v in report["candidates"]] == [
        "first-tie", "second-tie", "later",
    ]
    assert [v["receive_sequence"] for v in report["candidates"]] == [1, 2, 0]
    assert report["automatic_tc_replay_allowed"] is False
    assert report["physical_resume_supported"] is False
    assert report["activate_native_queue_allowed"] is False
    assert ws.commands_path.read_bytes() == event_bytes
    assert (ws.path / "command_ids.sqlite").stat().st_size == before
    assert inspect_pending_queue(ws.path) == report


def test_pending_audit_rejects_tampered_stored_command_without_replay(tmp_path):
    ws = _interrupted_pending(tmp_path, [("future", 0.8)])
    with sqlite3.connect(ws.path / "command_ids.sqlite") as db:
        raw = db.execute(
            "SELECT command_json FROM command_lifecycle"
        ).fetchone()[0]
        payload = __import__("json").loads(raw)
        payload["parameters"]["sigma_rn"] = [0.5, 0.0, 0.0]
        db.execute(
            "UPDATE command_lifecycle SET command_json=?",
            (__import__("json").dumps(payload),),
        )
    report = inspect_pending_queue(ws.path)
    assert report["status"] == "BLOCKED_PENDING_PAYLOAD_UNVERIFIABLE"
    assert report["candidates"] == []
    assert report["automatic_tc_replay_allowed"] is False


def test_pending_audit_never_activates_past_due_or_unknown_native_clock(tmp_path):
    ws = _interrupted_pending(tmp_path, [("past", 0.0)])
    report = inspect_pending_queue(ws.path)
    assert report["status"] == "BLOCKED_PENDING_NOT_PROVEN_FUTURE"
    assert report["activate_native_queue_allowed"] is False


def test_missing_native_trace_cannot_prove_future_command(tmp_path):
    ws = _interrupted_pending(tmp_path, [("future", 0.5)], include_trace=False)
    report = inspect_pending_queue(ws.path)
    assert report["status"] == "BLOCKED_PENDING_NOT_PROVEN_FUTURE"
    assert report["activate_native_queue_allowed"] is False
