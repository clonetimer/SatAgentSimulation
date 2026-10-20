"""Fail-closed crash handoff for continuous Basilisk interactive sessions.

This is a durable evidence/recovery assessment, NOT a physical-state checkpoint.
A fresh process may start a separately identified epoch from initial conditions;
it must never claim to resume the old Basilisk graph at the telemetry timestamp.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import InteractiveSessionSpec, SessionState
from .telemetry_segments import iter_entries, logical_sha256
from .workspace import SessionWorkspace

HANDOFF_SCHEMA = "interactive-recovery-handoff.v1"
HANDOFF_FILENAME = "recovery_handoff.json"
_UNAVAILABLE = "NATIVE_FULL_PHYSICAL_STATE_SNAPSHOT_NOT_AVAILABLE"


def _canonical(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    if path.exists():
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _handoff_payload(root: Path) -> dict[str, Any]:
    root = Path(root)
    inspection = SessionWorkspace.inspect(root)
    if not inspection.valid:
        raise ValueError(f"RECOVERY_SOURCE_INVALID:{inspection.reason_code}")
    if inspection.state is not SessionState.INTERRUPTED:
        raise ValueError("RECOVERY_REQUIRES_INTERRUPTED_SESSION")
    spec_file = root / "session_spec.json"
    envelope = json.loads(spec_file.read_text(encoding="utf-8"))
    spec = InteractiveSessionSpec.model_validate(envelope["session_spec"])
    if not spec.continuous:
        raise ValueError("RECOVERY_REQUIRES_CONTINUOUS_SESSION")
    if spec.session_id != root.name:
        raise ValueError("RECOVERY_SOURCE_SESSION_ID_MISMATCH")

    state_count = 0
    last_state_time_s = 0.0
    last_state_reason = ""
    for row in SessionWorkspace.iter_hash_chain(root / "state_events.jsonl"):
        state_count += 1
        last_state_time_s = float(row["sim_time_s"])
        last_state_reason = str(row["reason_code"])
    if last_state_reason != "PROCESS_RESTART":
        raise ValueError("RECOVERY_NOT_PROCESS_RESTART")

    command_count = 0
    for _ in SessionWorkspace.iter_hash_chain(root / "command_events.jsonl"):
        command_count += 1

    telemetry_count = 0
    last_tm_s: float | None = None
    last_trace_s: float | None = None
    for row in iter_entries(root):
        frame = row["frame"]
        time_s = float(frame["sim_time_s"])
        if last_tm_s is not None and time_s < last_tm_s:
            raise ValueError("RECOVERY_TELEMETRY_TIME_REGRESSION")
        last_tm_s = time_s
        if frame["stream"] == "runtime.trace":
            last_trace_s = time_s
        telemetry_count += 1

    return {
        "schema_version": HANDOFF_SCHEMA,
        "session_id": spec.session_id,
        "capability_id": spec.capability_id,
        "original_task_spec_sha256": envelope["task_spec_sha256"],
        "source_sha256": {
            "session_spec": _file_sha256(spec_file),
            "state_events": _file_sha256(root / "state_events.jsonl"),
            "command_events": _file_sha256(root / "command_events.jsonl"),
            "telemetry_logical": logical_sha256(root),
        },
        "evidence": {
            "state_event_count": state_count,
            "command_event_count": command_count,
            "telemetry_event_count": telemetry_count,
            "last_state_event_sim_time_s": last_state_time_s,
            "last_durable_telemetry_sim_time_s": last_tm_s,
            "last_durable_native_trace_sim_time_s": last_trace_s,
        },
        "physical_recovery": {
            "resume_supported": False,
            "reason_code": _UNAVAILABLE,
            "required_action": "NEW_EPOCH_WITH_NEW_SESSION_ID",
            "state_equivalence_proven": False,
            "queued_command_reconciliation_proven": False,
            "note": (
                "Telemetry is observation-only; Basilisk integrators, internal "
                "subsystem state and command queues are not fully serialized."
            ),
        },
    }


def write_interrupted_handoff(root: Path) -> Path:
    root = Path(root).resolve()
    target = root / HANDOFF_FILENAME
    staged = root / (HANDOFF_FILENAME + ".pending")
    if target.exists() or staged.exists():
        raise ValueError("RECOVERY_HANDOFF_ALREADY_EXISTS")
    result = _handoff_payload(root)
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    result["record_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    try:
        with staged.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(target)
    except BaseException:
        # Keep a failed staged record for explicit forensic inspection.
        raise
    return target


def verify_interrupted_handoff(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    target = root / HANDOFF_FILENAME
    payload = json.loads(target.read_text(encoding="utf-8"))
    proof = payload.pop("record_sha256", None)
    if proof != hashlib.sha256(_canonical(payload)).hexdigest():
        raise ValueError("RECOVERY_HANDOFF_DIGEST_MISMATCH")
    expected = _handoff_payload(root)
    for name, value in expected.items():
        if payload.get(name) != value:
            raise ValueError("RECOVERY_SOURCE_CHANGED")
    payload["record_sha256"] = proof
    return payload
