"""Reconcile only the JSONL *projection* of authoritative SQLite TC events.

Called on already stopped/verified continuous SessionWorkspace at crash
handoff. This may fill a missed SQLite mirror cursor **only when the exact
ACK event is already present in the hash-chained JSONL**. It never creates
an ACK, infers a native effect, replays a TC or edits JSONL/history.

An event committed in SQLite but not present in JSONL remains unresolved
and blocks the recovery preflight. Outbox is per accepted command lifecycle
event; rejected/unaccepted ACKs stay in the legacy JSONL only.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .workspace import SessionWorkspace

STATES = frozenset({"QUEUED", "EXECUTING", "ACKED", "FAILED", "EXPIRED"})


def _fingerprint(command: dict[str, Any]) -> str:
    payload = {k: v for k, v in command.items() if k != "received_at"}
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def verified_ack_projection(root: Path) -> dict[tuple[str, str], tuple[int, str, str, float | None]]:
    """The existing JSONL is authoritative only for the fact it was fsynced."""
    found: dict[tuple[str, str], tuple[int, str, str, float | None]] = {}
    for event in SessionWorkspace.iter_hash_chain(root / "command_events.jsonl"):
        ack = event["ack"]
        state = ack["state"]
        if state not in STATES or ack.get("reason_code") == "DUPLICATE_IDEMPOTENT":
            continue
        ident = event["command_id"]
        if (event["session_id"] != root.name
                or event["command"]["session_id"] != root.name
                or event["command"]["command_id"] != ident
                or ack["command_id"] != ident
                or ack["session_id"] != root.name
                or ack["sequence"] != event["sequence"]):
            raise ValueError("TC_OUTBOX_JSONL_IDENTITY_OR_SEQUENCE_MISMATCH")
        key = ident, state
        if key in found:
            raise ValueError("TC_OUTBOX_DUPLICATE_LIFECYCLE_ACK")
        found[key] = (
            event["sequence"], event["entry_hash"],
            _fingerprint(event["command"]), ack.get("sim_time_s"),
        )
    return found


def reconcile_existing_outbox_mirrors(root: Path) -> dict[str, int | str]:
    """Repair only a missing cursor, never a missing command ACK itself.

    SQLite writes are restricted to the matched mirror metadata and use one
    IMMEDIATE transaction. Duplicate/mismatched markers reject rather than
    attempting to rewrite evidence.
    """
    root = Path(root).resolve()
    db = root / "command_ids.sqlite"
    if not db.exists():
        return {"status": "LEGACY_MISSING_LEDGER", "reconciled": 0, "unmirrored": 0}
    indexed = verified_ack_projection(root)
    try:
        connection = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "command_event_outbox" not in tables:
                return {"status": "LEGACY_MISSING_OUTBOX", "reconciled": 0, "unmirrored": 0}
            connection.execute("BEGIN IMMEDIATE")
            changed = 0
            pending = 0
            rows = connection.execute(
                "SELECT event_id, command_id, fingerprint, state, sim_time_s, "
                "mirrored_sequence, mirrored_entry_hash FROM command_event_outbox "
                "ORDER BY event_id"
            ).fetchall()
            for event_id, ident, fp, state, time_s, mirrored_seq, mirrored_hash in rows:
                expected = indexed.get((ident, state))
                if expected is None:
                    if mirrored_seq is not None or mirrored_hash is not None:
                        raise ValueError("TC_OUTBOX_MIRROR_REFERENCES_MISSING_JSONL")
                    pending += 1
                    continue
                seq, entry_hash, found_fp, found_time = expected
                if fp != found_fp or time_s != found_time:
                    raise ValueError("TC_OUTBOX_COMMITTED_EVENT_MISMATCH")
                if mirrored_seq is not None or mirrored_hash is not None:
                    if mirrored_seq != seq or mirrored_hash != entry_hash:
                        raise ValueError("TC_OUTBOX_COMMITTED_MIRROR_MISMATCH")
                    continue
                cursor = connection.execute(
                    "UPDATE command_event_outbox SET mirrored_sequence=?, "
                    "mirrored_entry_hash=? WHERE event_id=? "
                    "AND mirrored_sequence IS NULL",
                    (seq, entry_hash, event_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError("TC_OUTBOX_CURSOR_UPDATE_FAILED")
                changed += 1
            connection.commit()
            return {"status": "VALIDATED_EXISTING_PROJECTIONS",
                    "reconciled": changed, "unmirrored": pending}
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError("TC_OUTBOX_DATABASE_INVALID") from exc
