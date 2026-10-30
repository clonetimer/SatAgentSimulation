"""R7h forensic cross-check of ORIGINAL continuous session and TC sources.

Never mutates a session, a Basilisk runtime, a command-writer epoch or a
SQLite transaction. Intended for a *stopped, finalized* evidence snapshot:
while a writer is alive, command JSONL and SQLite may legitimately reflect
different transaction instants; do not mislabel that instant as durable truth.

Proof covers integrity/coherence of durable logs, NOT authentication,
in-flight physical effect or restartability of native Basilisk state.
"""
from __future__ import annotations

import math
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workspace import SessionWorkspace
from .command_outbox_mirror import verified_ack_projection

_ACCEPTED_STATES = frozenset({"QUEUED", "EXECUTING", "ACKED", "FAILED", "EXPIRED"})
_FINAL_COMMAND = frozenset({"ACKED", "FAILED", "EXPIRED"})
_TERMINAL_SESSION = frozenset({"COMPLETED", "FAILED", "ABORTED", "INTERRUPTED"})
_MAX_COMMAND_IDS = 100_000


def _time_utc(value: str) -> datetime:
    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("SOURCE_EVENT_WALL_TIME_UNZONED")
    return value.astimezone(timezone.utc)


def _state_trace(root: Path, session_id: str, first_at: datetime, last_at: datetime) -> dict[str, Any]:
    events = root / "state_events.jsonl"
    if not events.is_file():
        raise ValueError("SOURCE_STATE_EVENTS_MISSING")
    count = 0
    latest_revision = -1
    latest_state = None
    last_sim = -1.
    last_wall = None
    transitions_during_window: list[str] = []
    interruption_observed = False
    for row in SessionWorkspace.iter_hash_chain(events):
        state = row["state"]
        wall = _time_utc(row["wall_time"])
        sim = row["sim_time_s"]
        rev = row["revision"]
        if (row.get("session_id") != session_id
                or row.get("schema_version") != "interactive-state-event.v1"
                or type(row.get("sequence")) is not int
                or row["sequence"] != count
                or type(rev) is not int or rev < 0
                or type(sim) not in (int, float) or not math.isfinite(sim)
                or sim < 0 or sim < last_sim
                or (last_wall is not None and wall < last_wall)):
            raise ValueError("SOURCE_STATE_TIMELINE_INCONSISTENT")
        if count == 0:
            if state != "CREATED" or rev != 0:
                raise ValueError("SOURCE_STATE_INITIAL_EVENT_INVALID")
        elif rev == latest_revision:
            # Same-state heartbeat or safe duplicate snapshot, not transition.
            if state != latest_state:
                raise ValueError("SOURCE_STATE_REVISION_REUSED")
        elif rev == latest_revision + 1:
            if state == latest_state:
                raise ValueError("SOURCE_STATE_REVISION_CHANGED_NO_TRANSITION")
        else:
            raise ValueError("SOURCE_STATE_REVISION_GAP")
        if first_at <= wall <= last_at:
            if rev > latest_revision or state in ("PAUSED", "FAILED", "ABORTED", "INTERRUPTED"):
                transitions_during_window.append(state)
            if state in ("PAUSED", "FAILED", "ABORTED", "INTERRUPTED"):
                interruption_observed = True
        latest_revision, latest_state = rev, state
        last_sim, last_wall = sim, wall
        count += 1
    if count == 0:
        raise ValueError("SOURCE_STATE_TRACE_EMPTY")
    # A stationary READY/PAUSED session may have no transitions throughout
    # the evidence window. Its last original state event can predate the
    # first monitoring sample without implying missing source evidence.
    return {
        "events": count, "final_state": latest_state,
        "last_sim_time_s": last_sim, "final_revision": latest_revision,
        "observed_window_interruption": interruption_observed,
        "window_transition_states": transitions_during_window[:16],
    }


def _command_ledger(root: Path, session_id: str) -> dict[str, Any]:
    ledger_path = root / "command_ids.sqlite"
    if not ledger_path.is_file():
        raise ValueError("SOURCE_SQLITE_COMMAND_LEDGER_MISSING")
    # SQLite connection in URI ro/query_only mode. PRAGMA quick_check is a
    # read-only integrity check, not a journal checkpoint or repair operation.
    conn = sqlite3.connect(ledger_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("SOURCE_SQLITE_INTEGRITY_FAILED")
        owner = conn.execute(
            "SELECT epoch,owner_state FROM command_writer_epoch WHERE singleton=1"
        ).fetchone()
        if (owner is None or type(owner[0]) is not int or owner[0] <= 0
                or owner[1] not in ("ACTIVE", "RELEASED")):
            raise ValueError("SOURCE_COMMAND_WRITER_UNVERIFIED")

        accepted: dict[str, str] = {}
        for ident, fingerprint in conn.execute(
            "SELECT command_id,fingerprint FROM command_ids ORDER BY command_id"
        ):
            if (len(accepted) >= _MAX_COMMAND_IDS or not isinstance(ident, str)
                    or not isinstance(fingerprint, str) or len(fingerprint) != 64):
                raise ValueError("SOURCE_ACCEPTED_ID_INDEX_INVALID")
            accepted[ident] = fingerprint

        projection = verified_ack_projection(root)
        states: dict[str, str] = {}
        first_stage: set[str] = set()
        for (ident, stage), (seq, entry_hash, fp, at) in projection.items():
            if ident not in accepted or fp != accepted[ident]:
                raise ValueError("SOURCE_TC_JSONL_ACCEPTED_INDEX_MISMATCH")
            if stage == "QUEUED":
                first_stage.add(ident)
            previous = states.get(ident)
            if previous is None or seq > previous[1]:
                states[ident] = (stage, seq)

        if first_stage != set(accepted):
            raise ValueError("SOURCE_TC_ACCEPTED_EVENT_MISSING")

        lifecycle: dict[str, str] = {}
        for ident, fp, owner_session, state in conn.execute(
            "SELECT command_id,fingerprint,session_id,state "
            "FROM command_lifecycle ORDER BY receive_sequence"
        ):
            if (ident not in accepted or accepted[ident] != fp
                    or owner_session != session_id or state not in _ACCEPTED_STATES
                    or ident in lifecycle):
                raise ValueError("SOURCE_TC_LIFECYCLE_INCONSISTENT")
            lifecycle[ident] = state
        if set(lifecycle) != set(accepted):
            raise ValueError("SOURCE_TC_LIFECYCLE_IDS_MISMATCH")

        outbox_keys: set[tuple[str, str]] = set()
        outbox_count = 0
        for ident, fp, stage, sim, mirrored_seq, mirrored_hash in conn.execute(
            "SELECT command_id,fingerprint,state,sim_time_s,"
            "mirrored_sequence,mirrored_entry_hash "
            "FROM command_event_outbox ORDER BY event_id"
        ):
            key = ident, stage
            if (key in outbox_keys or ident not in accepted or
                    accepted[ident] != fp or key not in projection):
                raise ValueError("SOURCE_TC_OUTBOX_UNPROVEN_EVENT")
            ack_seq, ack_hash, ack_fp, ack_at = projection[key]
            if (fp != ack_fp or sim != ack_at
                    or mirrored_seq != ack_seq or mirrored_hash != ack_hash):
                raise ValueError("SOURCE_TC_OUTBOX_MIRROR_MISMATCH")
            outbox_keys.add(key)
            outbox_count += 1

        if outbox_keys != set(projection):
            raise ValueError("SOURCE_TC_OUTBOX_EVENT_SET_MISMATCH")
        for ident, sql_state in lifecycle.items():
            if ident not in states or sql_state != states[ident][0]:
                raise ValueError("SOURCE_TC_FINAL_STATE_MISMATCH")
        unresolved = sorted(ident for ident, stage in lifecycle.items() if stage not in _FINAL_COMMAND)
        non_ack = sorted(ident for ident, stage in lifecycle.items() if stage != "ACKED")
        return {
            "sqlite_writer_epoch": owner[0],
            "sqlite_writer_state": owner[1],
            "accepted_commands": len(accepted),
            "outbox_events": outbox_count,
            "jsonl_lifecycle_events": len(projection),
            "unresolved_tc_count": len(unresolved),
            "non_acked_tc_count": len(non_ack),
            "tc_all_acked": not non_ack,
            # Only counts, never leak IDs, original TC contents, owner secret.
        }
    finally:
        conn.close()


def audit_telemetry_sampling(root: Path, period_s: float, stream_id: str = 'whole.continuous') -> dict[str, Any]:
    """Check every original frame, including rotated/compressed segments."""
    if not math.isfinite(period_s) or period_s <= 0:
        raise ValueError('SOURCE_TELEMETRY_PERIOD_INVALID')
    period_ns = int(period_s * 1e9 + .5)
    if period_ns <= 0:
        raise ValueError('SOURCE_TELEMETRY_PERIOD_INVALID')
    count = off_grid = unexpected_intervals = 0
    previous_ns = None
    min_interval_ns = max_interval_ns = None
    for entry in SessionWorkspace.iter_telemetry(root):
        frame = entry['frame']
        if frame['stream'] != stream_id:
            continue
        time_s = float(frame['sim_time_s'])
        if not math.isfinite(time_s) or time_s < 0:
            raise ValueError('SOURCE_TELEMETRY_TIME_INVALID')
        time_ns = int(time_s * 1e9 + .5)
        count += 1
        off_grid += time_ns % period_ns != 0
        if previous_ns is not None:
            interval_ns = time_ns - previous_ns
            unexpected_intervals += interval_ns != period_ns
            min_interval_ns = interval_ns if min_interval_ns is None else min(min_interval_ns, interval_ns)
            max_interval_ns = interval_ns if max_interval_ns is None else max(max_interval_ns, interval_ns)
        previous_ns = time_ns
    return {
        'status': 'PASS' if count > 1 and not off_grid and not unexpected_intervals else 'NOT_SATISFIED',
        'stream_id': stream_id, 'declared_period_s': period_s, 'frames_checked': count,
        'off_grid_frame_count': off_grid, 'unexpected_interval_count': unexpected_intervals,
        'min_interval_s': min_interval_ns * 1e-9 if min_interval_ns is not None else None,
        'max_interval_s': max_interval_ns * 1e-9 if max_interval_ns is not None else None,
    }


def audit_original_sources(
    workspace: Path, *, first_sample_utc: datetime, last_sample_utc: datetime,
    evidence_last_sim_time_s: float,
) -> dict[str, Any]:
    """Cross-check stable final evidence against ORIGINAL persisted sources."""
    root = Path(workspace).resolve()
    spec_path = root / "session_spec.json"
    if not spec_path.is_file():
        raise ValueError("SOURCE_SESSION_SPEC_MISSING")
    with spec_path.open("r", encoding="utf-8") as handle:
        spec = json.load(handle)
    original = spec["session_spec"]
    if (original["session_id"] != root.name or
            original.get("continuous") is not True or
            spec.get("schema_version") != "interactive-workspace.v1"):
        raise ValueError("SOURCE_SESSION_ID_OR_MODE_MISMATCH")
    state = _state_trace(root, root.name, first_sample_utc, last_sample_utc)
    # State-event sim time records only *transitions/heartbeats*, not every
    # native quantum. During a live RUNNING session it may legitimately lag
    # behind the latest telemetry sample. Only a terminal state commits the
    # final spacecraft time and can be compared against the evidence tail.
    if (state["final_state"] in _TERMINAL_SESSION
            and state["last_sim_time_s"] + 1e-7 < evidence_last_sim_time_s):
        raise ValueError("SOURCE_TERMINAL_SIM_BEHIND_EVIDENCE")
    command = _command_ledger(root, root.name)
    period_s = float(original['quantum_s'])
    for stream in original.get('task_spec', {}).get('outputs', {}).get('telemetry_streams', ()):
        if stream.get('stream_id') == 'whole.continuous':
            period_s = float(stream['sample_s'])
    sampling = audit_telemetry_sampling(root, period_s)
    warnings = []
    if sampling['status'] != 'PASS':
        warnings.append('ORIGINAL_TELEMETRY_SAMPLING_NOT_SATISFIED')
    if state["observed_window_interruption"]:
        warnings.append("ORIGINAL_STATE_PAUSED_OR_INTERRUPTED_IN_WINDOW")
    if command["sqlite_writer_state"] != "RELEASED":
        warnings.append("ORIGINAL_WRITER_NOT_CLEANLY_RELEASED")
    if state["final_state"] != "COMPLETED":
        warnings.append("ORIGINAL_SESSION_NOT_COMPLETED")
    if not command["tc_all_acked"]:
        warnings.append("ORIGINAL_TC_NOT_ALL_ACKED")
    return {
        "schema_version": "sat-continuous-original-source-audit.v2",
        "session_id": root.name,
        "status": "CONSISTENT" if not warnings else "WARNING",
        "reason_codes": warnings,
        "state": state, "tc_ledger": command, 'telemetry_sampling': sampling,
        "native_resume_supported": False,
        "automatic_tc_replay_allowed": False,
    }
