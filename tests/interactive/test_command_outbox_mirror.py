"""R6c: authoritative SQLite TC outbox and JSONL-only projection repair.

No test may replay a telecommand in recovery. All fixtures use real
SessionWorkspace / DeterministicCommandMailbox persistent files and
production InteractiveSessionManager interruption.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.command_outbox_mirror import (
    reconcile_existing_outbox_mirrors,
)
from sat_sim.interactive.manager import InteractiveSession, InteractiveSessionManager
from sat_sim.interactive.models import CommandAck, CommandState, SessionState
from sat_sim.interactive.recovery_command_preflight import (
    verify_interrupted_command_preflight,
)
from sat_sim.interactive.workspace import SessionWorkspace
from test_command_transaction_ledger import _cmd, _spec, _RecordedEffect


def _outbox_rows(path):
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT state, mirrored_sequence, mirrored_entry_hash "
            "FROM command_event_outbox ORDER BY event_id"
        ).fetchall()


def _ack(ws, mailbox, command, state, sequence, *,
         at=None, reason="OK", mirror=True):
    ack = CommandAck(
        command_id=command.command_id, session_id=command.session_id,
        state=state, sequence=sequence, reason_code=reason, sim_time_s=at,
    )
    row = ws.append_command_ack(command, ack)
    if mirror:
        mailbox.mirror_durable_ack(
            command, state=state.value, reason_code=reason,
            ack_sequence=sequence, entry_hash=row["entry_hash"],
            sim_time_s=at,
        )
    return row


def _source(tmp_path, case="acked", *, missing_marker=False):
    spec = _spec("outbox-" + case)
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.CREATED, 0, 0., "SESSION_CREATED")
    ws.append_state(SessionState.RUNNING, 2, 2., "OK")
    cmd = _cmd(spec.session_id)
    mail = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    _ack(ws, mail, cmd, CommandState.RECEIVED, 0)
    _ack(ws, mail, cmd, CommandState.VALIDATED, 1)
    assert mail.submit(cmd, session_revision=2).accepted
    if case == "accepted_no_jsonl":
        mail.close()
        return ws, cmd
    _ack(ws, mail, cmd, CommandState.QUEUED, 2,
         reason="QUEUED", mirror=not missing_marker)
    if case == "queued":
        mail.close()
        return ws, cmd
    mail.mark_executing_before_effect(cmd, sim_time_s=2.)
    if case == "claimed_no_jsonl":
        mail.close()
        return ws, cmd
    _ack(ws, mail, cmd, CommandState.EXECUTING, 3,
         at=2., mirror=not missing_marker)
    if case == "executing":
        mail.close()
        return ws, cmd
    mail.mark_acked_after_receipt(cmd, sim_time_s=2.2)
    if case == "acked_no_jsonl":
        mail.close()
        return ws, cmd
    _ack(ws, mail, cmd, CommandState.ACKED, 4,
         at=2.2, mirror=not missing_marker)
    mail.close()
    return ws, cmd


def _interrupt(tmp_path, ws):
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    return verify_interrupted_command_preflight(ws.path)


def test_real_live_session_emits_all_lifecycle_outbox_markers(tmp_path):
    spec = _spec("outbox-live")
    ws = SessionWorkspace(tmp_path, spec)
    runtime = _RecordedEffect(ws.path / "command_ids.sqlite")
    session = InteractiveSession(spec, runtime, end_time_s=None, workspace=ws)
    try:
        tc = _cmd(spec.session_id)
        session._emit_ack(tc, CommandState.RECEIVED)
        session._emit_ack(tc, CommandState.VALIDATED)
        assert session._command_mailbox.submit(tc, session_revision=2).accepted
        session._emit_ack(tc, CommandState.QUEUED, "QUEUED")
        session._advance()
        assert runtime.call_count == 1
        assert runtime.before_effect_state == "EXECUTING"
        rows = _outbox_rows(runtime.db)
        assert [r[0] for r in rows] == ["QUEUED", "EXECUTING", "ACKED"]
        assert [r[1] for r in rows] == [2, 3, 4]
        assert all(len(r[2]) == 64 for r in rows)
    finally:
        session.abort()
        session.wait(2.)


@pytest.mark.parametrize("phase,expected", [
    ("queued", "BLOCKED_UNRESOLVED_PENDING_TC"),
    ("executing", "BLOCKED_AMBIGUOUS_INFLIGHT"),
    ("acked", "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART"),
])
def test_clean_interrupt_outbox_and_ack_projection(phase, expected, tmp_path):
    ws, _ = _source(tmp_path, phase)
    report = _interrupt(tmp_path, ws)
    assert report["status"] == expected
    assert report["automatic_tc_replay_allowed"] is False
    assert report["physical_resume_supported"] is False
    assert report["command_evidence"]["outbox_events"] == {
        "queued": 1, "executing": 2, "acked": 3,
    }[phase]


def test_crash_after_jsonl_fsync_before_cursor_is_only_mirror_reconciled(tmp_path):
    ws, tc = _source(tmp_path, "acked", missing_marker=True)
    original = ws.commands_path.read_bytes()
    assert [row[1] for row in _outbox_rows(ws.path / "command_ids.sqlite")] == [None] * 3
    report = _interrupt(tmp_path, ws)
    assert report["status"] == "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART"
    assert [row[1] for row in _outbox_rows(ws.path / "command_ids.sqlite")] == [2, 3, 4]
    assert ws.commands_path.read_bytes() == original
    assert report["automatic_tc_replay_allowed"] is False
    assert reconcile_existing_outbox_mirrors(ws.path)["reconciled"] == 0


@pytest.mark.parametrize("phase", [
    "accepted_no_jsonl", "claimed_no_jsonl", "acked_no_jsonl",
])
def test_sqlite_committed_but_missing_jsonl_never_fabricated_or_replayed(tmp_path, phase):
    ws, _ = _source(tmp_path, phase)
    original = ws.commands_path.read_bytes()
    assessment = _interrupt(tmp_path, ws)
    assert assessment["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert assessment["automatic_tc_replay_allowed"] is False
    assert ws.commands_path.read_bytes() == original
    assert any(seq is None for _, seq, _ in _outbox_rows(
        ws.path / "command_ids.sqlite"
    ))


def test_sqlite_mirror_write_failure_can_recover_from_existing_jsonl_only(tmp_path):
    ws, tc = _source(tmp_path, "queued")
    db_path = ws.path / "command_ids.sqlite"
    mail = DeterministicCommandMailbox(persistent_ledger=True, ledger_path=db_path)
    try:
        mail.mark_executing_before_effect(tc, sim_time_s=2.)
        with sqlite3.connect(db_path) as db:
            db.execute(
                "CREATE TRIGGER deny_mirror BEFORE UPDATE OF mirrored_sequence "
                "ON command_event_outbox "
                "WHEN NEW.state='EXECUTING' "
                "BEGIN SELECT RAISE(ABORT, 'MIRROR_WRITE_FAILED'); END"
            )
        ack = _ack(
            ws, mail, tc, CommandState.EXECUTING, 3, at=2., mirror=False,
        )
        with pytest.raises(sqlite3.IntegrityError, match="MIRROR_WRITE_FAILED"):
            mail.mirror_durable_ack(
                tc, state="EXECUTING", reason_code="OK",
                ack_sequence=3, entry_hash=ack["entry_hash"], sim_time_s=2.,
            )
        with sqlite3.connect(db_path) as db:
            db.execute("DROP TRIGGER deny_mirror")
    finally:
        mail.close()
    before = ws.commands_path.read_bytes()
    report = _interrupt(tmp_path, ws)
    assert report["status"] == "BLOCKED_AMBIGUOUS_INFLIGHT"
    assert ws.commands_path.read_bytes() == before
    assert _outbox_rows(db_path)[1][1] == 3


def test_malicious_or_stale_mirror_cursor_fails_closed(tmp_path):
    ws, _ = _source(tmp_path, "acked")
    with sqlite3.connect(ws.path / "command_ids.sqlite") as db:
        db.execute(
            "UPDATE command_event_outbox SET mirrored_entry_hash=? "
            "WHERE state='QUEUED'", ("a" * 64,),
        )
    with pytest.raises(ValueError, match="TC_OUTBOX_COMMITTED_MIRROR_MISMATCH"):
        reconcile_existing_outbox_mirrors(ws.path)


def test_duplicate_idempotent_ack_does_not_mutate_original_outbox(tmp_path):
    ws, tc = _source(tmp_path, "queued")
    mail = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    try:
        assert mail.submit(tc, session_revision=2).reason_code == "DUPLICATE_IDEMPOTENT"
        before = _outbox_rows(ws.path / "command_ids.sqlite")
        _ack(ws, mail, tc, CommandState.QUEUED, 3, reason="DUPLICATE_IDEMPOTENT")
        assert _outbox_rows(ws.path / "command_ids.sqlite") == before
    finally:
        mail.close()
    # Duplicate ACK is not a second accepted event or command execution.
    report = _interrupt(tmp_path, ws)
    assert report["automatic_tc_replay_allowed"] is False


def test_failed_mirror_commit_does_not_reuse_fsynced_ack_sequence(tmp_path):
    spec = _spec("outbox-sequence-safety")
    ws = SessionWorkspace(tmp_path, spec)
    runtime = _RecordedEffect(ws.path / "command_ids.sqlite")
    session = InteractiveSession(spec, runtime, end_time_s=None, workspace=ws)
    try:
        tc = _cmd(spec.session_id)
        session._emit_ack(tc, CommandState.RECEIVED)
        assert session._command_mailbox.submit(tc, session_revision=2).accepted
        with sqlite3.connect(runtime.db) as db:
            db.execute(
                "CREATE TRIGGER deny_cursor BEFORE UPDATE OF mirrored_sequence "
                "ON command_event_outbox "
                "BEGIN SELECT RAISE(ABORT, 'CURSOR_IO_ERROR'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="CURSOR_IO_ERROR"):
            session._emit_ack(tc, CommandState.QUEUED, "QUEUED")
        # The JSONL sequence was already fsynced, despite the exception.
        assert session._ack_sequence == 2
        assert [r["sequence"] for r in SessionWorkspace.iter_hash_chain(
            ws.commands_path
        )] == [0, 1]
        with sqlite3.connect(runtime.db) as db:
            db.execute("DROP TRIGGER deny_cursor")
        assert reconcile_existing_outbox_mirrors(ws.path)["reconciled"] == 1
        assert _outbox_rows(runtime.db)[0][1] == 1
        # The recovered source is *only* a mirror cursor, not a replayable TC.
        assert runtime.call_count == 0
    finally:
        session.abort()
        session.wait(2.)


def test_corrupt_outbox_blocks_only_its_own_interrupted_workspace(tmp_path):
    corrupted, _ = _source(tmp_path, "acked")
    healthy, _ = _source(tmp_path, "queued")
    with sqlite3.connect(corrupted.path / "command_ids.sqlite") as db:
        db.execute(
            "UPDATE command_event_outbox SET mirrored_entry_hash=? "
            "WHERE state='ACKED'", ("f" * 64,),
        )
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert len(manager.interrupted_workspaces) == 2
    assert all(row.state is SessionState.INTERRUPTED
               for row in manager.interrupted_workspaces)
    blocked = verify_interrupted_command_preflight(corrupted.path)
    good = verify_interrupted_command_preflight(healthy.path)
    assert blocked["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "OUTBOX_PROJECTION_MISMATCH" in blocked["reason_code"]
    assert good["status"] == "BLOCKED_UNRESOLVED_PENDING_TC"
    assert blocked["automatic_tc_replay_allowed"] is False
    assert good["automatic_tc_replay_allowed"] is False
