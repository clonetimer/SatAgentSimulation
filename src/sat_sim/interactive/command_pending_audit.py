"""Read-only R6d prospective pending queue reconstruction report.

Never restores a Basilisk runtime, creates a new command worker, claims a
writer epoch, or puts a command into an executable heap. A fully verified
QUEUED record is a candidate, not authorization to apply a TC in a new
physics epoch; original session must remain INTERRUPTED.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from .models import Telecommand
from .recovery_command_preflight import verify_interrupted_command_preflight
from .recovery_handoff import verify_interrupted_handoff

SCHEMA = "interactive-tc-pending-audit.v1"
MAX_CANDIDATES = 4096


def _fingerprint(command: Telecommand) -> str:
    payload = command.model_dump(mode="json", exclude={"received_at"})
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def inspect_pending_queue(root: Path) -> dict[str, Any]:
    """Return only grounded, sorted *non-executable* candidate metadata."""
    root = Path(root).resolve()
    preflight = verify_interrupted_command_preflight(root)
    handoff = verify_interrupted_handoff(root)
    report: dict[str, Any] = {
        "schema_version": SCHEMA,
        "session_id": handoff["session_id"],
        "source_preflight_sha256": preflight["record_sha256"],
        "physical_resume_supported": False,
        "activate_native_queue_allowed": False,
        "automatic_tc_replay_allowed": False,
        "required_action": "NEW_EPOCH_WITH_NEW_SESSION_ID",
        "candidates": [],
    }
    if preflight["status"] not in {
        "BLOCKED_UNRESOLVED_PENDING_TC",
        "EVIDENCE_CONSISTENT_NO_NATIVE_RESTART",
    }:
        report.update(
            status="BLOCKED_SOURCE_NOT_FULLY_VERIFIED",
            reason_code=preflight["status"],
        )
        return report
    evidence = preflight["command_evidence"]
    if evidence is None or evidence["executing_ids"] or evidence["failed_ids"]:
        report.update(
            status="BLOCKED_AMBIGUOUS_EXECUTION",
            reason_code="INFLIGHT_OR_FAILED_NATIVE_EFFECT_UNKNOWN",
        )
        return report
    if evidence["preacceptance_uncertain_ids"] or evidence["pending_accepted_ids"]:
        report.update(
            status="BLOCKED_INCOMPLETE_ACCEPTANCE",
            reason_code="RECEIPT_NOT_TRANSACTIONALLY_ACCEPTED",
        )
        return report
    try:
        uri = (root / "command_ids.sqlite").resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            # A suspended/crashed ACTIVE owner cannot be replaced
            # automatically; even a RELEASED owner is not a native restart
            # permit. No token is emitted in an operator report.
            row = con.execute(
                "SELECT epoch,owner_state FROM command_writer_epoch WHERE singleton=1"
            ).fetchone()
            if row is None or row[1] not in ("ACTIVE", "RELEASED"):
                raise ValueError("TC_PENDING_WRITER_EPOCH_UNKNOWN")
            report["source_writer_epoch"] = int(row[0])
            report["source_writer_state"] = row[1]
            rows = con.execute(
                "SELECT command_id, fingerprint, session_id, "
                "execute_at_sim_time_s, receive_sequence, command_json "
                "FROM command_lifecycle WHERE state='QUEUED' "
                "ORDER BY execute_at_sim_time_s, receive_sequence"
            )
            prior: tuple[float, int] | None = None
            seen: set[str] = set()
            for ident, fp, session, due, sequence, raw in rows:
                if len(seen) >= MAX_CANDIDATES:
                    raise ValueError("TC_PENDING_RECONSTRUCTION_CAPACITY")
                if not isinstance(raw, str) or not raw:
                    raise ValueError("TC_PENDING_MISSING_OR_LEGACY_COMMAND_PAYLOAD")
                if (type(sequence) is not int or sequence < 0
                        or type(due) not in (float, int)
                        or not math.isfinite(due)
                        or ident in seen or session != root.name):
                    raise ValueError("TC_PENDING_ID_ORDER_OR_SESSION_INVALID")
                if prior is not None and (due, sequence) < prior:
                    raise ValueError("TC_PENDING_ORDER_INVALID")
                prior = (due, sequence)
                seen.add(ident)
                cmd = Telecommand.model_validate(json.loads(raw))
                if (cmd.command_id != ident or cmd.session_id != session
                        or cmd.execute_at_sim_time_s != due
                        or _fingerprint(cmd) != fp):
                    raise ValueError("TC_PENDING_FINGERPRINT_OR_PAYLOAD_MISMATCH")
                report["candidates"].append({
                    "command_id": ident,
                    "fingerprint": fp,
                    "execute_at_sim_time_s": due,
                    "receive_sequence": sequence,
                })
        finally:
            con.close()
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError) as exc:
        report["candidates"] = []
        report.update(
            status="BLOCKED_PENDING_PAYLOAD_UNVERIFIABLE",
            reason_code=str(exc),
        )
        return report
    expected = set(evidence["queued_ids"])
    if {candidate["command_id"] for candidate in report["candidates"]} != expected:
        report["candidates"] = []
        report.update(
            status="BLOCKED_PENDING_LEDGER_SET_MISMATCH",
            reason_code="QUEUED_INDEX_NOT_EQUIVALENT_TO_PRECHECK",
        )
        return report
    # Durable telemetry is not the native integrator state, but a TC due
    # before its latest observed trace cannot be declared a future queue.
    last_trace = handoff["evidence"]["last_durable_native_trace_sim_time_s"]
    if (report["candidates"] and (
        last_trace is None or any(
            candidate["execute_at_sim_time_s"] <= last_trace
            for candidate in report["candidates"]
        )
    )):
        report.update(
            status="BLOCKED_PENDING_NOT_PROVEN_FUTURE",
            reason_code="DUE_NOT_AFTER_DURABLE_NATIVE_TRACE",
        )
        return report
    report.update(
        status="CANDIDATE_METADATA_VERIFIED_NO_NATIVE_ACTIVATION",
        reason_code="PHYSICAL_CHECKPOINT_AND_WRITER_HANDOFF_NOT_PROVEN",
    )
    return report
