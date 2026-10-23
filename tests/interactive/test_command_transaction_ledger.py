"""R6b production SQLite accepted-ID + command-state transaction and replay fencing.

These tests use the real DeterministicCommandMailbox and InteractiveSession
persistent ledger, not the R4 test-only TC journal. All graph resume remains
disabled independently of these tests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.manager import InteractiveSession, InteractiveSessionManager
from sat_sim.interactive.models import (
    CommandAck, CommandState, InteractiveSessionSpec,
    SessionState, Telecommand,
)
from sat_sim.interactive.recovery_command_preflight import (
    verify_interrupted_command_preflight,
)
from sat_sim.interactive.workspace import SessionWorkspace


def _cmd(session: str, command_id: str = "tc-1") -> Telecommand:
    return Telecommand(
        command_id=command_id, session_id=session, session_revision=2,
        operation="adcs.target.set", target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0., 0.]},
        actor_id="operator", actor_role="operator",
        execute_at_sim_time_s=0.1,
    )


def _state(db: Path, ident: str = "tc-1") -> str | None:
    with sqlite3.connect(db) as con:
        row = con.execute(
            "SELECT state FROM command_lifecycle WHERE command_id=?", (ident,),
        ).fetchone()
        return None if row is None else str(row[0])


def test_atomic_accept_rolls_back_id_and_phase_after_fault_injection(tmp_path):
    db = tmp_path / "tc.sqlite"
    tc = _cmd("txn")
    queue = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=db)
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TRIGGER deny_state BEFORE INSERT ON command_lifecycle "
            "BEGIN SELECT RAISE(ABORT, 'CRASH_BEFORE_STATE_INSERT'); END"
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CRASH_BEFORE_STATE_INSERT"):
            queue.submit(tc, session_revision=2)
        with sqlite3.connect(db) as con:
            assert con.execute("SELECT COUNT(*) FROM command_ids").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM command_lifecycle").fetchone()[0] == 0
        assert len(queue) == 0
        with sqlite3.connect(db) as con:
            con.execute("DROP TRIGGER deny_state")
        receipt = queue.submit(tc, session_revision=2)
        assert receipt.accepted and receipt.receive_sequence == 0
        assert _state(db) == "QUEUED"
    finally:
        queue.close()


def test_pre_effect_execution_claim_and_ack_are_one_way(tmp_path):
    db = tmp_path / "tc.sqlite"
    tc = _cmd("txn")
    queue = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=db)
    try:
        assert queue.submit(tc, session_revision=2).accepted
        queue.mark_executing_before_effect(tc, sim_time_s=0.)
        assert _state(db) == "EXECUTING"
        with pytest.raises(RuntimeError, match="LIFECYCLE_TRANSITION_REJECTED"):
            queue.mark_executing_before_effect(tc, sim_time_s=0.)
        with pytest.raises(RuntimeError, match="LIFECYCLE_TRANSITION_REJECTED"):
            queue.mark_expired_after_receipt(tc, sim_time_s=0.)
        queue.close()
        reopened = DeterministicCommandMailbox(
            persistent_ledger=True, ledger_path=db,
        )
        try:
            assert reopened.submit(tc, session_revision=2).reason_code == "DUPLICATE_IDEMPOTENT"
            assert _state(db) == "EXECUTING"  # no requeue
            with pytest.raises(RuntimeError, match="LIFECYCLE_TRANSITION_REJECTED"):
                reopened.mark_executing_before_effect(tc, sim_time_s=0.1)
            reopened.mark_acked_after_receipt(tc, sim_time_s=0.2)
            assert _state(db) == "ACKED"
            with pytest.raises(RuntimeError, match="LIFECYCLE_TRANSITION_REJECTED"):
                reopened.mark_executing_before_effect(tc, sim_time_s=0.3)
        finally:
            reopened.close()
    finally:
        queue.close()


def _spec(session: str) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id=session, capability_id="whole_spacecraft.unified_native.v1",
        task_spec={}, continuous=True, paced=False, quantum_s=0.2,
        heartbeat_interval_s=1., idle_timeout_s=60.,
    )


class _RecordedEffect:
    def __init__(self, database: Path):
        self.current_time_s = 0.
        self.db = database
        self.before_effect_state = None
        self.call_count = 0

    def prepare(self): pass
    def advance_to(self, end):
        self.current_time_s = end
        return ({"time_s": end},)
    def apply_command(self, cmd):
        self.before_effect_state = _state(self.db, cmd.command_id)
        self.call_count += 1
        assert self.before_effect_state == "EXECUTING"
    def read_delta(self): return ()
    def abort(self): pass
    def finalize(self): return {}


def test_real_session_applies_only_after_durable_sqlite_claim(tmp_path):
    spec = _spec("real-effect")
    workspace = SessionWorkspace(tmp_path, spec)
    runtime = _RecordedEffect(workspace.path / "command_ids.sqlite")
    session = InteractiveSession(spec, runtime, end_time_s=None, workspace=workspace)
    try:
        command = _cmd(spec.session_id)
        for state in (CommandState.RECEIVED, CommandState.VALIDATED):
            session._emit_ack(command, state)
        assert session._command_mailbox.submit(command, session_revision=2).accepted
        session._emit_ack(command, CommandState.QUEUED, "QUEUED")
        session._advance()
        assert runtime.call_count == 1
        assert runtime.before_effect_state == "EXECUTING"
        assert _state(runtime.db) == "ACKED"
        logged = list(SessionWorkspace.iter_hash_chain(workspace.commands_path))
        assert [event["ack"]["state"] for event in logged] == [
            "RECEIVED", "VALIDATED", "QUEUED", "EXECUTING", "ACKED",
        ]
    finally:
        session.abort()
        session.wait(2.)


def test_sqlite_executing_with_missing_ack_mirror_blocks_recovery(tmp_path):
    spec = _spec("uncertain-claim")
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0., "SESSION_CREATED")
    workspace.append_state(SessionState.RUNNING, 2, 0., "OK")
    tc = _cmd(spec.session_id)
    ledger = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=workspace.path / "command_ids.sqlite",
    )
    try:
        assert ledger.submit(tc, session_revision=2).accepted
        for i, state in enumerate((CommandState.RECEIVED, CommandState.QUEUED)):
            workspace.append_command_ack(tc, CommandAck(
                command_id=tc.command_id, session_id=spec.session_id,
                state=state, sequence=i,
                reason_code="QUEUED" if i else "OK",
            ))
        ledger.mark_executing_before_effect(tc, sim_time_s=0.)
        # Crash just after SQLite EXECUTING but before JSONL EXECUTING.
    finally:
        ledger.close()
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    report = verify_interrupted_command_preflight(workspace.path)
    assert report["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "LIFECYCLE_ACK_MIRROR_MISMATCH" in report["reason_code"]
    assert report["automatic_tc_replay_allowed"] is False


def test_acked_jsonl_before_sqlite_commit_is_still_unsafe(tmp_path):
    spec = _spec("uncertain-ack")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.RUNNING, 2, 0., "OK")
    tc = _cmd(spec.session_id)
    ledger = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    try:
        assert ledger.submit(tc, session_revision=2).accepted
        for i, state in enumerate((
            CommandState.RECEIVED, CommandState.QUEUED,
            CommandState.EXECUTING, CommandState.ACKED,
        )):
            if state is CommandState.EXECUTING:
                ledger.mark_executing_before_effect(tc, sim_time_s=0.)
            ws.append_command_ack(tc, CommandAck(
                command_id=tc.command_id, session_id=spec.session_id,
                state=state, sequence=i,
                reason_code="QUEUED" if state is CommandState.QUEUED else "OK",
                # Real EXECUTING JSONL includes the original native clock.
                # The deliberately early ACKED record is not authoritative.
                sim_time_s=0. if state is CommandState.EXECUTING else None,
            ))
        # Crash after the ACK event but before the SQLite ACKED transition.
        assert _state(ws.path / "command_ids.sqlite") == "EXECUTING"
    finally:
        ledger.close()
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    report = verify_interrupted_command_preflight(ws.path)
    assert report["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "LIFECYCLE_ACK_MIRROR_MISMATCH" in report["reason_code"]
    assert report["physical_resume_supported"] is False


def test_sqlite_claim_commit_failure_prevents_runtime_side_effect(tmp_path):
    spec = _spec("claim-commit-failed")
    ws = SessionWorkspace(tmp_path, spec)
    rt = _RecordedEffect(ws.path / "command_ids.sqlite")
    session = InteractiveSession(spec, rt, end_time_s=None, workspace=ws)
    try:
        tc = _cmd(spec.session_id)
        session._emit_ack(tc, CommandState.RECEIVED)
        assert session._command_mailbox.submit(tc, session_revision=2).accepted
        session._emit_ack(tc, CommandState.QUEUED, "QUEUED")
        with sqlite3.connect(rt.db) as con:
            con.execute(
                "CREATE TRIGGER deny_claim BEFORE UPDATE OF state ON command_lifecycle "
                "BEGIN SELECT RAISE(ABORT, 'CLAIM_COMMIT_DENIED'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="CLAIM_COMMIT_DENIED"):
            session._advance()
        assert rt.call_count == 0
        assert _state(rt.db) == "QUEUED"
        # pop_due removed the in-memory queue item, but the session must be
        # terminated/reconciled, NOT silently retry the native command.
    finally:
        session.abort()
        session.wait(2.)


def test_interrupted_preflight_rejects_sqlite_due_time_corruption(tmp_path):
    from test_recovery_command_preflight import _prepare
    from sat_sim.interactive.recovery_command_preflight import assess_interrupted_command_recovery
    ws, _ = _prepare(tmp_path, "acked")
    db = sqlite3.connect(ws.path / "command_ids.sqlite")
    db.execute(
        "UPDATE command_lifecycle SET execute_at_sim_time_s=999 WHERE command_id=?",
        ("tc-1",),
    )
    db.commit()
    db.close()
    assessment = assess_interrupted_command_recovery(ws.path)
    assert assessment["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "LIFECYCLE_TIME_MISMATCH" in assessment["reason_code"]
    with pytest.raises(ValueError, match="PREFLIGHT_SOURCE_CHANGED"):
        verify_interrupted_command_preflight(ws.path)
