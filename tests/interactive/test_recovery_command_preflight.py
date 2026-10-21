"""Real SessionWorkspace ↔ SQLite accepted-ID ↔ command ACK preflight.

No Basilisk is needed here: this tests the production persistence boundary
and the conservative lack of native-resume/replay entitlement.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.manager import InteractiveSessionManager
from sat_sim.interactive.models import (
    CommandAck, CommandState, InteractiveSessionSpec, SessionState, Telecommand,
)
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.interactive.recovery_command_preflight import (
    FILENAME, assess_interrupted_command_recovery,
    verify_interrupted_command_preflight,
)


def _spec(session_id: str) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id=session_id, capability_id="whole_spacecraft.unified_native.v1",
        task_spec={"task": {"id": session_id}},
        continuous=True, paced=False, quantum_s=0.2,
    )


def _command(session_id: str, ident: str, *, sim_time_s: float = 2.2) -> Telecommand:
    return Telecommand(
        command_id=ident, session_id=session_id, session_revision=2,
        operation="adcs.target.set", target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]},
        actor_id="test-operator", actor_role="operator",
        execute_at_sim_time_s=sim_time_s,
    )


def _prepare(tmp_path: Path, kind: str, *, ident: str = "tc-1"):
    spec = _spec("preflight-" + kind)
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    ws.append_state(SessionState.RUNNING, 2, 2.0, "OK")
    tc = _command(spec.session_id, ident)
    mailbox = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    # Real mailbox persists identity *before* the workspace's QUEUED ACK.
    assert mailbox.submit(tc, session_revision=2).accepted
    mailbox.close()
    sequence = 0
    states = [CommandState.RECEIVED, CommandState.VALIDATED, CommandState.QUEUED]
    if kind in ("executing", "acked"):
        states.append(CommandState.EXECUTING)
    if kind == "acked":
        states.append(CommandState.ACKED)
    for state in states:
        ws.append_command_ack(tc, CommandAck(
            command_id=ident, session_id=spec.session_id,
            state=state, sequence=sequence,
            sim_time_s=2.0 if state is CommandState.EXECUTING else
                       2.2 if state is CommandState.ACKED else None,
            reason_code="QUEUED" if state is CommandState.QUEUED else "OK",
        ))
        sequence += 1
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    return ws, tc


@pytest.mark.parametrize("kind,expected", [
    ("queued", "BLOCKED_UNRESOLVED_PENDING_TC"),
    ("executing", "BLOCKED_AMBIGUOUS_INFLIGHT"),
    ("acked", "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART"),
])
def test_real_workspace_ack_and_sqlite_recovery_fence(tmp_path, kind, expected):
    ws, tc = _prepare(tmp_path, kind)
    doc = verify_interrupted_command_preflight(ws.path)
    assert doc["status"] == expected
    assert doc["physical_resume_supported"] is False
    assert doc["automatic_tc_replay_allowed"] is False
    assert doc["required_action"] == "NEW_EPOCH_WITH_NEW_SESSION_ID"
    assert doc["command_evidence"]["accepted_count"] == 1
    assert doc["command_evidence"]["command_event_count"] == {
        "queued": 3, "executing": 4, "acked": 5,
    }[kind]
    assert (ws.path / FILENAME).exists()

    # The second manager startup must neither rewrite the manifest nor
    # reinterpret the interrupted source as a fresh resumable native graph.
    before = (ws.path / FILENAME).read_bytes()
    again = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert again.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    assert (ws.path / FILENAME).read_bytes() == before


def test_sqlite_mutation_after_handoff_invalidates_preflight(tmp_path):
    ws, _ = _prepare(tmp_path, "acked")
    db = sqlite3.connect(ws.path / "command_ids.sqlite")
    db.execute(
        "UPDATE command_ids SET fingerprint=? WHERE command_id=?",
        ("0" * 64, "tc-1"),
    )
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="PREFLIGHT_SOURCE_CHANGED"):
        verify_interrupted_command_preflight(ws.path)


def test_partial_or_lost_sqlite_index_fails_closed(tmp_path):
    ws, _ = _prepare(tmp_path, "queued")
    (ws.path / "command_ids.sqlite").unlink()
    # The sealed preflight binds the initial SQLite view. Losing that index
    # makes replay unsafe even though the ACK chain itself still verifies.
    with pytest.raises(ValueError, match="PREFLIGHT_SOURCE_CHANGED"):
        verify_interrupted_command_preflight(ws.path)
    assessment = assess_interrupted_command_recovery(ws.path)
    assert assessment["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert assessment["automatic_tc_replay_allowed"] is False


def test_accepted_id_without_durable_queued_ack_is_blocked(tmp_path):
    spec = _spec("crash-between-index-and-ack")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.RUNNING, 1, 2.0, "OK")
    tc = _command(spec.session_id, "unknown-receipt")
    mailbox = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    assert mailbox.submit(tc, session_revision=2).accepted
    mailbox.close()
    ws.append_command_ack(tc, CommandAck(
        command_id=tc.command_id, session_id=spec.session_id,
        state=CommandState.RECEIVED, sequence=0,
    ))
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assessment = verify_interrupted_command_preflight(ws.path)
    assert assessment["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "LEDGER_EVENT_SET_MISMATCH" in assessment["reason_code"]


def test_missing_sqlite_for_legacy_workspace_records_explicit_block(tmp_path):
    spec = _spec("legacy-missing-index")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assessment = verify_interrupted_command_preflight(ws.path)
    assert assessment["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "LEDGER_MISSING" in assessment["reason_code"]


def test_preflight_manifest_tampering_or_staging_is_rejected(tmp_path):
    ws, _ = _prepare(tmp_path, "acked")
    path = ws.path / FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["automatic_tc_replay_allowed"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="PREFLIGHT_DIGEST_INVALID"):
        verify_interrupted_command_preflight(ws.path)
    (ws.path / (FILENAME + ".pending")).write_text("partial")
    with pytest.raises(ValueError, match="PREFLIGHT_STAGING_PRESENT"):
        verify_interrupted_command_preflight(ws.path)


def test_recovery_inspection_cli_can_include_command_preflight(tmp_path, monkeypatch, capsys):
    import sys
    from scripts.inspect_continuous_recovery import main
    ws, _ = _prepare(tmp_path, "acked")
    before = (ws.path / FILENAME).read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "inspect_continuous_recovery.py", "--workspace", str(ws.path),
        "--command-preflight",
    ])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VERIFIED_SOURCE_EVIDENCE"
    assert result["command_recovery"]["status"] == "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART"
    assert result["command_recovery"]["automatic_tc_replay_allowed"] is False
    assert (ws.path / FILENAME).read_bytes() == before


def test_acked_without_prior_executing_is_not_accepted_as_proven(tmp_path):
    spec = _spec("forged-ack-state")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.RUNNING, 2, 2.0, "OK")
    tc = _command(spec.session_id, "tc-ack-before-execution")
    mailbox = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    assert mailbox.submit(tc, session_revision=2).accepted
    mailbox.close()
    for seq, state in enumerate((CommandState.RECEIVED, CommandState.QUEUED, CommandState.ACKED)):
        ws.append_command_ack(tc, CommandAck(
            command_id=tc.command_id, session_id=spec.session_id,
            state=state, sequence=seq,
            reason_code="QUEUED" if state is CommandState.QUEUED else "OK",
        ))
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    report = verify_interrupted_command_preflight(ws.path)
    assert report["status"] == "BLOCKED_EVIDENCE_UNVERIFIABLE"
    assert "ACK_WITHOUT_EXECUTION_PROOF" in report["reason_code"]


def test_unfinished_received_not_mistaken_for_replayable_command(tmp_path):
    spec = _spec("incomplete-received")
    ws = SessionWorkspace(tmp_path, spec)
    ws.append_state(SessionState.RUNNING, 1, 2.0, "OK")
    tc = _command(spec.session_id, "tc-received-not-validated")
    mailbox = DeterministicCommandMailbox(
        persistent_ledger=True, ledger_path=ws.path / "command_ids.sqlite",
    )
    mailbox.close()
    ws.append_command_ack(tc, CommandAck(
        command_id=tc.command_id, session_id=spec.session_id,
        state=CommandState.RECEIVED, sequence=0,
    ))
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    report = verify_interrupted_command_preflight(ws.path)
    assert report["status"] == "BLOCKED_UNRESOLVED_PENDING_TC"
    assert report["command_evidence"]["preacceptance_uncertain_ids"] == [tc.command_id]
    assert report["automatic_tc_replay_allowed"] is False
