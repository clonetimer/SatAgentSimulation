"""Crash-handoff TC/ACK/SQLite *preflight*, never native spacecraft restoration.

Reads the real interrupted SessionWorkspace hash chain and the persisted
command-id SQLite ledger, and binds their exact observed evidence into an
atomic, immutable-once-written advisory manifest.  No TC is redispatched.
A valid preflight proves source evidence coherence, not an applied physical
effect or equivalence of a Basilisk integrator.

This is deliberately conservative. Rejected/incomplete activity, in-flight
or pending accepted TC always prevents automatic command replay. Full native
resume remains unsupported even when all commands are ACKed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from .models import CommandState
from .workspace import SessionWorkspace
from .recovery_handoff import verify_interrupted_handoff
from .command_outbox_mirror import verified_ack_projection

SCHEMA = "interactive-command-recovery-preflight.v1"
FILENAME = "command_recovery_preflight.json"
MAX_UNIQUE_IDS = 100_000
TERMINAL = {"ACKED", "REJECTED", "FAILED", "EXPIRED"}
ACTIVE = {"RECEIVED", "VALIDATED", "QUEUED", "EXECUTING"}


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_index(path: Path) -> tuple[
    dict[str, str], str, dict[str, dict[str, Any]], str,
    list[dict[str, Any]], str,
]:
    """Read an immutable *view* of the live SQLite unique index, never mutate it.

    SQLite read transactions snapshot a consistent committed state. Since this
    runs only after INTERRUPTION and not against a live writer, compare the
    persisted digest again on verification to detect later changes.
    """
    if not path.is_file():
        raise ValueError("RECOVERY_COMMAND_LEDGER_MISSING")
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only = ON")
            # One SQLite snapshot must cover both the accepted-ID index and
            # its lifecycle rows. Without BEGIN, separate SELECTs can see
            # distinct commits from an incorrectly still-running writer.
            con.execute("BEGIN")
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("RECOVERY_COMMAND_LEDGER_INTEGRITY_FAILED")
            rows = con.execute(
                "SELECT command_id, fingerprint FROM command_ids ORDER BY command_id"
            )
            ledger: dict[str, str] = {}
            digest = hashlib.sha256()
            for name, fingerprint in rows:
                if not isinstance(name, str) or not isinstance(fingerprint, str):
                    raise ValueError("RECOVERY_COMMAND_LEDGER_ROW_INVALID")
                if len(ledger) >= MAX_UNIQUE_IDS:
                    raise ValueError("RECOVERY_COMMAND_LEDGER_CAPACITY_LIMIT")
                if len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint):
                    raise ValueError("RECOVERY_COMMAND_LEDGER_HASH_INVALID")
                ledger[name] = fingerprint
                digest.update(_canonical({"command_id": name, "fingerprint": fingerprint}) + b"\n")
            # The R6b lifecycle and accepted-ID table are committed in
            # *one SQLite transaction*. Older index-only workspaces are not
            # silently upgraded into resumable command evidence.
            phases: dict[str, dict[str, Any]] = {}
            phase_sha = hashlib.sha256()
            for ident, fingerprint, session, due, sequence, state, at in con.execute(
                "SELECT command_id, fingerprint, session_id, "
                "execute_at_sim_time_s, receive_sequence, state, "
                "transition_sim_time_s FROM command_lifecycle "
                "ORDER BY receive_sequence"
            ):
                if not isinstance(ident, str) or ident in phases:
                    raise ValueError("RECOVERY_COMMAND_LIFECYCLE_DUPLICATE")
                record = {
                    "fingerprint": fingerprint, "session_id": session,
                    "execute_at_sim_time_s": due,
                    "receive_sequence": sequence, "state": state,
                    "transition_sim_time_s": at,
                }
                phases[ident] = record
                phase_sha.update(_canonical({"command_id": ident, **record}) + b"\n")
            # Same read transaction as command IDs and authoritative state:
            # no mixing two different SQLite commit generations.
            outbox: list[dict[str, Any]] = []
            outbox_sha = hashlib.sha256()
            for event_id, ident, fingerprint, state, sim_time, seq, entry in con.execute(
                "SELECT event_id, command_id, fingerprint, state, sim_time_s, "
                "mirrored_sequence, mirrored_entry_hash FROM command_event_outbox "
                "ORDER BY event_id"
            ):
                if len(outbox) >= MAX_UNIQUE_IDS * 5:
                    raise ValueError("RECOVERY_COMMAND_OUTBOX_CAPACITY_LIMIT")
                event = {
                    "event_id": event_id, "command_id": ident,
                    "fingerprint": fingerprint, "state": state,
                    "sim_time_s": sim_time, "mirrored_sequence": seq,
                    "mirrored_entry_hash": entry,
                }
                outbox.append(event)
                outbox_sha.update(_canonical(event) + b"\n")
            return (
                ledger, digest.hexdigest(), phases, phase_sha.hexdigest(),
                outbox, outbox_sha.hexdigest(),
            )
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("RECOVERY_COMMAND_LEDGER_SQLITE_INVALID") from exc


def _command_fingerprint(payload: dict[str, Any]) -> str:
    # Match DeterministicCommandMailbox._fingerprint's canonical format
    # exactly; the SQLite command-id index uses ensure_ascii=True.
    canonical = json.dumps(
        {key: value for key, value in payload.items() if key != "received_at"},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _analyze_commands(root: Path, *, session_id: str) -> dict[str, Any]:
    """Check ACK log identity, ordering and persisted accepted-command index."""
    accepted: dict[str, str] = {}
    accepted_due: dict[str, float] = {}
    command_state: dict[str, str] = {}
    seen_executing: set[str] = set()
    preacceptance: set[str] = set()
    last_sequences: dict[str, int] = {}
    events = 0
    last_event_sequence = -1
    for row in SessionWorkspace.iter_hash_chain(root / "command_events.jsonl"):
        seq = row["sequence"]
        if type(seq) is not int or seq != last_event_sequence + 1:
            raise ValueError("RECOVERY_COMMAND_EVENT_SEQUENCE_INVALID")
        last_event_sequence = seq
        events += 1
        payload = row["command"]
        ack = row["ack"]
        ident = row["command_id"]
        if (row["session_id"] != session_id or not isinstance(payload, dict)
                or not isinstance(ack, dict) or payload.get("session_id") != session_id
                or payload.get("command_id") != ident
                or ack.get("session_id") != session_id
                or ack.get("command_id") != ident):
            raise ValueError("RECOVERY_COMMAND_EVENT_IDENTITY_INVALID")
        if row["command_sha256"] != _sha(payload):
            raise ValueError("RECOVERY_COMMAND_PAYLOAD_DIGEST_INVALID")
        ack_seq = ack.get("sequence")
        if type(ack_seq) is not int or ack_seq != seq:
            raise ValueError("RECOVERY_ACK_SEQUENCE_INVALID")
        state = ack.get("state")
        if state not in {v.value for v in CommandState}:
            raise ValueError("RECOVERY_ACK_STATE_INVALID")
        fingerprint = _command_fingerprint(payload)
        previous_fp = accepted.get(ident)
        if previous_fp is not None and previous_fp != fingerprint:
            # Rejected ID collision is possible, but cannot be used to
            # establish an unambiguous original TC outcome for a resume.
            raise ValueError("RECOVERY_COMMAND_DUPLICATE_PAYLOAD_CONFLICT")
        if state == CommandState.QUEUED.value and ack.get("reason_code") != "DUPLICATE_IDEMPOTENT":
            accepted[ident] = fingerprint
            accepted_due[ident] = float(payload["execute_at_sim_time_s"])
            preacceptance.discard(ident)
        elif state in {CommandState.EXECUTING.value, CommandState.ACKED.value}:
            if ident not in accepted:
                raise ValueError("RECOVERY_COMMAND_UNACCEPTED_EXECUTION")
            if state == CommandState.EXECUTING.value:
                seen_executing.add(ident)
            elif ident not in seen_executing:
                raise ValueError("RECOVERY_COMMAND_ACK_WITHOUT_EXECUTION_PROOF")
        elif state in {CommandState.RECEIVED.value, CommandState.VALIDATED.value}:
            if ident not in accepted:
                preacceptance.add(ident)
        elif state in {CommandState.REJECTED.value, CommandState.EXPIRED.value}:
            preacceptance.discard(ident)
        command_state[ident] = state
        last_sequences[ident] = seq

    index, index_sha, phases, phase_sha, outbox, outbox_sha = _read_index(
        root / "command_ids.sqlite"
    )
    if accepted != index:
        raise ValueError("RECOVERY_COMMAND_LEDGER_EVENT_SET_MISMATCH")
    if set(phases) != set(index):
        raise ValueError("RECOVERY_COMMAND_LIFECYCLE_ID_SET_MISMATCH")
    for ident, fp in accepted.items():
        durable = phases[ident]
        if durable["session_id"] != session_id or durable["fingerprint"] != fp:
            raise ValueError("RECOVERY_COMMAND_LIFECYCLE_IDENTITY_MISMATCH")
        due = durable["execute_at_sim_time_s"]
        at = durable["transition_sim_time_s"]
        if (type(due) not in (int, float) or not math.isfinite(due)
                or due != accepted_due[ident]
                or (at is not None and (
                    type(at) not in (int, float) or not math.isfinite(at)
                ))):
            raise ValueError("RECOVERY_COMMAND_LIFECYCLE_TIME_MISMATCH")
        if (durable["state"] == "QUEUED" and at is not None
                or durable["state"] != "QUEUED" and at is None):
            raise ValueError("RECOVERY_COMMAND_LIFECYCLE_TRANSITION_TIME_INVALID")
        if durable["state"] != command_state[ident]:
            # In particular, SQLite EXECUTING but JSONL QUEUED/ACKED means
            # the second store was interrupted. Never infer a safe outcome.
            raise ValueError("RECOVERY_COMMAND_LIFECYCLE_ACK_MIRROR_MISMATCH")
        sequence = durable["receive_sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("RECOVERY_COMMAND_LIFECYCLE_SEQUENCE_INVALID")
    if sorted(row["receive_sequence"] for row in phases.values()) != list(
        range(len(phases))
    ):
        raise ValueError("RECOVERY_COMMAND_LIFECYCLE_SEQUENCE_GAP")

    projection = {
        key: val for key, val in verified_ack_projection(root).items()
        if key[0] in index
    }
    used: set[tuple[str, str]] = set()
    last_outbox_id = 0
    for event in outbox:
        ident = event["command_id"]
        state = event["state"]
        key = ident, state
        if (type(event["event_id"]) is not int
                or event["event_id"] <= last_outbox_id
                or ident not in index or event["fingerprint"] != index[ident]
                or key in used):
            raise ValueError("RECOVERY_COMMAND_OUTBOX_IDENTITY_INVALID")
        last_outbox_id = event["event_id"]
        used.add(key)
        matched = projection.get(key)
        if matched is None:
            raise ValueError("RECOVERY_COMMAND_OUTBOX_UNMIRRORED_EVENT")
        seq, entry_hash, fp, sample_time = matched
        if (event["mirrored_sequence"] != seq
                or event["mirrored_entry_hash"] != entry_hash
                or event["fingerprint"] != fp
                or event["sim_time_s"] != sample_time):
            raise ValueError("RECOVERY_COMMAND_OUTBOX_PROJECTION_MISMATCH")
    if used != set(projection):
        raise ValueError("RECOVERY_COMMAND_OUTBOX_EVENT_SET_MISMATCH")
    for ident, lifecycle in phases.items():
        if (ident, "QUEUED") not in used:
            raise ValueError("RECOVERY_COMMAND_OUTBOX_MISSING_ACCEPTANCE")
        if lifecycle["state"] != "QUEUED" and (ident, lifecycle["state"]) not in used:
            raise ValueError("RECOVERY_COMMAND_OUTBOX_MISSING_TERMINAL_PHASE")
    states = {"queued": [], "executing": [], "acked": [],
              "failed": [], "terminal_other": []}
    for ident in accepted:
        state = command_state[ident]
        bucket = ("queued" if state == "QUEUED" else "executing"
                  if state == "EXECUTING" else "acked" if state == "ACKED"
                  else "failed" if state == "FAILED" else "terminal_other")
        states[bucket].append(ident)
    return {
        "accepted_count": len(accepted),
        "command_event_count": events,
        "index_sha256": index_sha,
        "lifecycle_sha256": phase_sha,
        "outbox_sha256": outbox_sha,
        "outbox_events": len(outbox),
        "queued_ids": sorted(states["queued"]),
        "executing_ids": sorted(states["executing"]),
        "acked_ids": sorted(states["acked"]),
        "other_terminal_ids": sorted(states["terminal_other"]),
        "failed_ids": sorted(states["failed"]),
        "preacceptance_uncertain_ids": sorted(preacceptance),
        "pending_accepted_ids": sorted(
            ident for ident in accepted if command_state[ident] in {"RECEIVED", "VALIDATED"}
        ),
    }


def assess_interrupted_command_recovery(root: Path) -> dict[str, Any]:
    """Read-only, bounded evidence assessment. Do not attempt any resume."""
    root = Path(root).resolve()
    handoff = verify_interrupted_handoff(root)
    if handoff["physical_recovery"]["resume_supported"] is not False:
        raise ValueError("RECOVERY_COMMAND_PHYSICAL_RESUME_CLAIM")
    result = {
        "schema_version": SCHEMA,
        "session_id": handoff["session_id"],
        "handoff_record_sha256": handoff["record_sha256"],
        "source_command_events_sha256": handoff["source_sha256"]["command_events"],
        "source_spec_sha256": handoff["source_sha256"]["session_spec"],
        "physical_resume_supported": False,
        "automatic_tc_replay_allowed": False,
        "required_action": "NEW_EPOCH_WITH_NEW_SESSION_ID",
    }
    try:
        command = _analyze_commands(root, session_id=handoff["session_id"])
    except (ValueError, sqlite3.DatabaseError, KeyError, TypeError, OSError) as exc:
        result["status"] = "BLOCKED_EVIDENCE_UNVERIFIABLE"
        result["reason_code"] = str(exc)
        result["command_evidence"] = None
    else:
        result["command_evidence"] = command
        if command["executing_ids"] or command["failed_ids"]:
            result["status"] = "BLOCKED_AMBIGUOUS_INFLIGHT"
            result["reason_code"] = "EXECUTION_OUTCOME_UNKNOWN_NO_REPLAY"
        elif (command["queued_ids"] or command["pending_accepted_ids"]
              or command["preacceptance_uncertain_ids"]):
            result["status"] = "BLOCKED_UNRESOLVED_PENDING_TC"
            result["reason_code"] = "PENDING_TC_MUST_NOT_BE_LOST_OR_REPLAYED"
        else:
            result["status"] = "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART"
            result["reason_code"] = "NATIVE_PHYSICAL_CHECKPOINT_NOT_AVAILABLE"
    return result


def write_interrupted_command_preflight(root: Path) -> Path:
    """Write once, *after* the existing interrupted handoff is durably sealed."""
    root = Path(root).resolve()
    output = root / FILENAME
    stage = root / (FILENAME + ".pending")
    if output.exists() or stage.exists():
        raise ValueError("RECOVERY_COMMAND_PREFLIGHT_ALREADY_EXISTS")
    report = assess_interrupted_command_recovery(root)
    report["record_sha256"] = _sha(report)
    with stage.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(stage, output)
    if os.name == "posix":
        fd = os.open(str(root), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return output


def verify_interrupted_command_preflight(root: Path) -> dict[str, Any]:
    """Report is valid only while the original handoff, ACKs and index agree."""
    root = Path(root).resolve()
    if (root / (FILENAME + ".pending")).exists():
        raise ValueError("RECOVERY_COMMAND_PREFLIGHT_STAGING_PRESENT")
    doc = json.loads((root / FILENAME).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("RECOVERY_COMMAND_PREFLIGHT_SCHEMA_INVALID")
    content = dict(doc)
    digest = content.pop("record_sha256", None)
    if digest != _sha(content):
        raise ValueError("RECOVERY_COMMAND_PREFLIGHT_DIGEST_INVALID")
    expected = assess_interrupted_command_recovery(root)
    if content != expected:
        raise ValueError("RECOVERY_COMMAND_PREFLIGHT_SOURCE_CHANGED")
    return doc
