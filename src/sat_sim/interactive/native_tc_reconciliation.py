"""Test-only, fail-closed durable TC reconciliation for the ADCS native pilot.

This is deliberately NOT a general session recovery service. It persists
one bounded command journal by atomic JSON replacement and fsync, and it
will *not* reissue a command once execution is claimed but its outcome is
not durably ACKed. An ACK is an operator-side claim after a proven effect,
not cryptographic attestation of native Basilisk state.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA = "sat-native-tc-reconcile-pilot.v1"
STEP_NS = 200_000_000
ALLOWED_MODES = frozenset({"inertial3d", "nadir"})
MAX_COMMANDS = 128


def _hash(obj: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _time(value: Any, name: str) -> int:
    if type(value) is not int or value < 0 or value % STEP_NS:
        raise ValueError(f"TC_RECONCILE_CLOCK_INVALID:{name}")
    return value


def validate_tc_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError("TC_RECONCILE_DOCUMENT_INVALID")
    data = dict(doc)
    digest = data.pop("digest_sha256", None)
    if not isinstance(digest, str) or digest != _hash(data):
        raise ValueError("TC_RECONCILE_DIGEST_INVALID")
    if set(data) != {
        "schema_version", "session_id", "epoch", "checkpoint_ns",
        "next_sequence", "commands", "resume_supported",
    } or data["schema_version"] != SCHEMA:
        raise ValueError("TC_RECONCILE_SCHEMA_INVALID")
    if data["resume_supported"] is not False:
        raise ValueError("TC_RECONCILE_FALSE_RESUME_CLAIM")
    session_id = data["session_id"]
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        raise ValueError("TC_RECONCILE_SESSION_INVALID")
    if type(data["epoch"]) is not int or data["epoch"] < 1:
        raise ValueError("TC_RECONCILE_EPOCH_INVALID")
    now = _time(data["checkpoint_ns"], "checkpoint")
    commands = data["commands"]
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        raise ValueError("TC_RECONCILE_COMMANDS_INVALID")
    if type(data["next_sequence"]) is not int or data["next_sequence"] != len(commands):
        raise ValueError("TC_RECONCILE_SEQUENCE_INVALID")
    seen = set()
    for seq, command in enumerate(commands):
        if not isinstance(command, dict) or set(command) != {
            "command_id", "sequence", "operation", "target_mode",
            "execute_at_ns", "state", "claimed_ns", "acked_ns",
        }:
            raise ValueError("TC_RECONCILE_COMMAND_SCHEMA_INVALID")
        ident = command["command_id"]
        if not isinstance(ident, str) or not ident or len(ident) > 128 or ident in seen:
            raise ValueError("TC_RECONCILE_ID_INVALID")
        seen.add(ident)
        if type(command["sequence"]) is not int or command["sequence"] != seq:
            raise ValueError("TC_RECONCILE_COMMAND_ORDER_INVALID")
        if command["operation"] != "adcs.mode.set" or command["target_mode"] not in ALLOWED_MODES:
            raise ValueError("TC_RECONCILE_OPERATION_INVALID")
        due = _time(command["execute_at_ns"], "due")
        claimed, acked = command["claimed_ns"], command["acked_ns"]
        state = command["state"]
        if state == "QUEUED":
            if claimed is not None or acked is not None or due <= now:
                raise ValueError("TC_RECONCILE_UNSAFE_QUEUED_STATE")
        elif state == "EXECUTING":
            if acked is not None or _time(claimed, "claimed") < due or claimed > now:
                raise ValueError("TC_RECONCILE_EXECUTING_FIELDS_INVALID")
        elif state == "ACKED":
            if (_time(claimed, "claimed") < due
                    or _time(acked, "acked") < claimed or acked > now):
                raise ValueError("TC_RECONCILE_ACK_FIELDS_INVALID")
        else:
            raise ValueError("TC_RECONCILE_STATE_INVALID")
    return data


def _sealed(data: dict[str, Any]) -> dict[str, Any]:
    clean = dict(data)
    clean["digest_sha256"] = _hash(clean)
    validate_tc_snapshot(clean)
    return clean


def recovery_plan(doc: dict[str, Any]) -> dict[str, Any]:
    """Only QUEUED future commands are eligible. EXECUTING is ambiguous."""
    data = validate_tc_snapshot(doc)
    if any(cmd["state"] == "EXECUTING" for cmd in data["commands"]):
        raise ValueError("TC_RECONCILE_UNCERTAIN_INFLIGHT_FAIL_CLOSED")
    return {
        "session_id": data["session_id"],
        "epoch": data["epoch"],
        "checkpoint_ns": data["checkpoint_ns"],
        "safe_future": [
            cmd["command_id"] for cmd in data["commands"] if cmd["state"] == "QUEUED"
        ],
        "already_acked": [
            cmd["command_id"] for cmd in data["commands"] if cmd["state"] == "ACKED"
        ],
        "full_spacecraft_resume_supported": False,
    }


class NativeTcPilotJournal:
    """Single-writer offline pilot. Never attach to a live production session."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.stage = self.path.with_name(self.path.name + ".pending")

    def _save(self, data: dict[str, Any]) -> dict[str, Any]:
        snapshot = _sealed(data)
        if self.stage.exists():
            raise ValueError("TC_RECONCILE_PARTIAL_STAGING_EXISTS")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.stage.open("x", encoding="utf-8") as handle:
            json.dump(snapshot, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(self.stage, self.path)
        if os.name == "posix":
            fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return snapshot

    def create(self, *, session_id: str, checkpoint_ns: int, epoch: int = 1) -> dict[str, Any]:
        if self.path.exists() or self.stage.exists():
            raise ValueError("TC_RECONCILE_ALREADY_EXISTS")
        return self._save({
            "schema_version": SCHEMA, "session_id": session_id, "epoch": epoch,
            "checkpoint_ns": checkpoint_ns, "next_sequence": 0,
            "commands": [], "resume_supported": False,
        })

    def read(self) -> dict[str, Any]:
        if self.stage.exists():
            raise ValueError("TC_RECONCILE_PARTIAL_STAGING_EXISTS")
        if not self.path.is_file():
            raise ValueError("TC_RECONCILE_JOURNAL_NOT_FOUND")
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        validate_tc_snapshot(doc)
        return doc

    def enqueue(self, *, command_id: str, target_mode: str, execute_at_ns: int) -> str:
        doc = self.read()
        for item in doc["commands"]:
            if item["command_id"] == command_id:
                if (item["target_mode"], item["execute_at_ns"]) == (
                    target_mode, execute_at_ns
                ):
                    return "DUPLICATE_NO_REPLAY"
                raise ValueError("TC_RECONCILE_DUPLICATE_CONFLICT")
        if len(doc["commands"]) >= MAX_COMMANDS:
            raise ValueError("TC_RECONCILE_CAPACITY_EXCEEDED")
        doc["commands"].append({
            "command_id": command_id, "sequence": doc["next_sequence"],
            "operation": "adcs.mode.set", "target_mode": target_mode,
            "execute_at_ns": execute_at_ns, "state": "QUEUED",
            "claimed_ns": None, "acked_ns": None,
        })
        doc["next_sequence"] += 1
        self._save({k: v for k, v in doc.items() if k != "digest_sha256"})
        return "QUEUED"

    def plan_recovery(self) -> dict[str, Any]:
        return recovery_plan(self.read())

    def claim_due(self, *, command_id: str, now_ns: int) -> dict[str, Any]:
        doc = self.read()
        if any(item["state"] == "EXECUTING" for item in doc["commands"]):
            raise ValueError("TC_RECONCILE_UNCERTAIN_INFLIGHT_FAIL_CLOSED")
        now = _time(now_ns, "claim")
        if now < doc["checkpoint_ns"]:
            raise ValueError("TC_RECONCILE_TIME_REGRESSION")
        target = next((item for item in doc["commands"] if item["command_id"] == command_id), None)
        if target is None:
            raise ValueError("TC_RECONCILE_UNKNOWN_ID")
        if target["state"] == "ACKED":
            raise ValueError("TC_RECONCILE_ALREADY_ACKED_NO_REPLAY")
        if target["state"] != "QUEUED" or now < target["execute_at_ns"]:
            raise ValueError("TC_RECONCILE_COMMAND_NOT_DUE")
        # Fail closed if ANY earlier due item would be skipped.
        if any(item["state"] == "QUEUED" and item["execute_at_ns"] <= now
               and (item["execute_at_ns"], item["sequence"]) <
               (target["execute_at_ns"], target["sequence"])
               for item in doc["commands"]):
            raise ValueError("TC_RECONCILE_DUE_ORDER_VIOLATION")
        target["state"] = "EXECUTING"
        target["claimed_ns"] = now
        doc["checkpoint_ns"] = now
        self._save({k: v for k, v in doc.items() if k != "digest_sha256"})
        return dict(target)

    def acknowledge_applied(self, *, command_id: str, at_ns: int) -> None:
        doc = self.read()
        target = next((item for item in doc["commands"] if item["command_id"] == command_id), None)
        if target is None or target["state"] != "EXECUTING":
            raise ValueError("TC_RECONCILE_ACK_WITHOUT_EXECUTION_CLAIM")
        now = _time(at_ns, "ack")
        if now < target["claimed_ns"] or now < doc["checkpoint_ns"]:
            raise ValueError("TC_RECONCILE_ACK_TIME_REGRESSION")
        target["state"] = "ACKED"
        target["acked_ns"] = now
        doc["checkpoint_ns"] = now
        self._save({k: v for k, v in doc.items() if k != "digest_sha256"})
