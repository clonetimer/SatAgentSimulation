"""Append-only, hash-chained working state for interactive sessions."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CommandAck, InteractiveSessionSpec, SessionState, Telecommand, TelemetryFrame


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class WorkspaceInspection:
    session_id: str
    state: SessionState
    sequence: int
    valid: bool
    reason_code: str


class SessionWorkspace:
    """Persist a session specification and state events without rewriting history."""

    def __init__(self, root: Path, spec: InteractiveSessionSpec, *, create: bool = True) -> None:
        self.root = root.resolve()
        self.path = self.root / spec.session_id
        self.spec_path = self.path / "session_spec.json"
        self.events_path = self.path / "state_events.jsonl"
        self.commands_path = self.path / "command_events.jsonl"
        self.telemetry_path = self.path / "telemetry.jsonl"
        self.telemetry_manifest_path = self.path / "telemetry_manifest.json"
        self._lock = threading.Lock()
        if create:
            self.path.mkdir(parents=True, exist_ok=False)
            spec_payload = spec.model_dump(mode="json")
            envelope = {
                "schema_version": "interactive-workspace.v1",
                "task_spec_sha256": _sha256_bytes(_canonical(spec_payload["task_spec"])),
                "session_spec": spec_payload,
            }
            self.spec_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._sequence = 0
            self._last_hash = "0" * 64
            self._command_sequence = 0
            self._command_last_hash = "0" * 64
            self._telemetry_sequence = 0
            self._telemetry_last_hash = "0" * 64
        else:
            inspection, rows = self.inspect(self.path, include_rows=True)
            if not inspection.valid:
                raise ValueError(f"invalid session workspace: {inspection.reason_code}")
            self._sequence = inspection.sequence + 1
            self._last_hash = str(rows[-1]["entry_hash"]) if rows else "0" * 64
            command_rows = self.inspect_hash_chain(self.commands_path)
            self._command_sequence = len(command_rows)
            self._command_last_hash = str(command_rows[-1]["entry_hash"]) if command_rows else "0" * 64
            telemetry_rows = self.inspect_hash_chain(self.telemetry_path)
            self._telemetry_sequence = len(telemetry_rows)
            self._telemetry_last_hash = str(telemetry_rows[-1]["entry_hash"]) if telemetry_rows else "0" * 64

    def append_state(self, state: SessionState, revision: int, sim_time_s: float, reason_code: str) -> dict[str, Any]:
        with self._lock:
            payload = {
                "schema_version": "interactive-state-event.v1",
                "sequence": self._sequence,
                "session_id": self.path.name,
                "state": state.value,
                "revision": int(revision),
                "sim_time_s": float(sim_time_s),
                "reason_code": reason_code,
                "wall_time": datetime.now(timezone.utc).isoformat(),
                "previous_hash": self._last_hash,
            }
            payload["entry_hash"] = _sha256_bytes(_canonical(payload))
            self.path.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._last_hash = payload["entry_hash"]
            self._sequence += 1
            return payload

    def append_command_ack(self, command: Telecommand, ack: CommandAck) -> dict[str, Any]:
        with self._lock:
            command_payload = command.model_dump(mode="json")
            payload = {
                "schema_version": "interactive-command-event.v1",
                "sequence": self._command_sequence,
                "session_id": self.path.name,
                "command_id": command.command_id,
                "command_sha256": _sha256_bytes(_canonical(command_payload)),
                "command": command_payload,
                "actor_id": command.actor_id,
                "actor_role": command.actor_role.value,
                "operation": command.operation,
                "target": command.target,
                "ack": ack.model_dump(mode="json"),
                "previous_hash": self._command_last_hash,
            }
            payload["entry_hash"] = _sha256_bytes(_canonical(payload))
            with self.commands_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._command_last_hash = payload["entry_hash"]
            self._command_sequence += 1
            return payload

    def append_telemetry(self, frame: TelemetryFrame) -> dict[str, Any]:
        with self._lock:
            payload = {
                "schema_version": "interactive-telemetry-event.v1",
                "sequence": self._telemetry_sequence,
                "session_id": self.path.name,
                "frame": frame.model_dump(mode="json"),
                "previous_hash": self._telemetry_last_hash,
            }
            payload["entry_hash"] = _sha256_bytes(_canonical(payload))
            with self.telemetry_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._telemetry_last_hash = payload["entry_hash"]
            self._telemetry_sequence += 1
            return payload

    def finalize_telemetry(self) -> dict[str, Any]:
        with self._lock:
            rows = self.inspect_hash_chain(self.telemetry_path)
            counts: dict[str, int] = {}
            latest_sequences: dict[str, int] = {}
            frames_by_stream: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                frame = row["frame"]
                stream = str(frame["stream"])
                counts[stream] = counts.get(stream, 0) + 1
                latest_sequences[stream] = int(frame["sequence"])
                frames_by_stream.setdefault(stream, []).append(frame)
            artifact_root = self.path / "telemetry_streams"
            artifact_root.mkdir(parents=True, exist_ok=True)
            artifacts: dict[str, dict[str, Any]] = {}
            for stream, frames in sorted(frames_by_stream.items()):
                filename = f"stream-{hashlib.sha256(stream.encode('utf-8')).hexdigest()[:16]}.jsonl"
                artifact_path = artifact_root / filename
                content = "".join(
                    json.dumps(frame, ensure_ascii=False, sort_keys=True) + "\n"
                    for frame in frames
                ).encode("utf-8")
                artifact_path.write_bytes(content)
                artifacts[stream] = {
                    "path": f"telemetry_streams/{filename}",
                    "row_count": len(frames),
                    "sha256": _sha256_bytes(content),
                }
            payload = {
                "schema_version": "interactive-telemetry-manifest.v1",
                "session_id": self.path.name,
                "frame_count": len(rows),
                "streams": counts,
                "latest_sequences": latest_sequences,
                "artifacts": artifacts,
                "telemetry_sha256": _sha256_bytes(self.telemetry_path.read_bytes()) if self.telemetry_path.exists() else _sha256_bytes(b""),
                "final_entry_hash": rows[-1]["entry_hash"] if rows else "0" * 64,
            }
            self.telemetry_manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return payload

    @staticmethod
    def inspect_hash_chain(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        expected_previous = "0" * 64
        if not path.exists():
            return rows
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(raw_line)
            entry_hash = str(row.pop("entry_hash"))
            if row.get("previous_hash") != expected_previous or _sha256_bytes(_canonical(row)) != entry_hash:
                raise ValueError("HASH_CHAIN_INVALID")
            row["entry_hash"] = entry_hash
            rows.append(row)
            expected_previous = entry_hash
        return rows

    @classmethod
    def inspect(cls, path: Path, *, include_rows: bool = False) -> WorkspaceInspection | tuple[WorkspaceInspection, list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        events_path = path / "state_events.jsonl"
        try:
            spec_payload = json.loads((path / "session_spec.json").read_text(encoding="utf-8"))
            session_id = str(spec_payload["session_spec"]["session_id"])
            if session_id != path.name:
                raise ValueError("SESSION_ID_PATH_MISMATCH")
            try:
                rows = cls.inspect_hash_chain(events_path)
            except ValueError as exc:
                raise ValueError("STATE_HASH_CHAIN_INVALID") from exc
            try:
                cls.inspect_hash_chain(path / "command_events.jsonl")
            except ValueError as exc:
                raise ValueError("COMMAND_HASH_CHAIN_INVALID") from exc
            try:
                cls.inspect_hash_chain(path / "telemetry.jsonl")
            except ValueError as exc:
                raise ValueError("TELEMETRY_HASH_CHAIN_INVALID") from exc
            state = SessionState(rows[-1]["state"]) if rows else SessionState.CREATED
            sequence = int(rows[-1]["sequence"]) if rows else -1
            inspection = WorkspaceInspection(session_id, state, sequence, True, "OK")
        except Exception as exc:
            inspection = WorkspaceInspection(path.name, SessionState.INTERRUPTED, -1, False, str(exc))
        return (inspection, rows) if include_rows else inspection

    @classmethod
    def mark_unfinished_interrupted(cls, root: Path) -> tuple[WorkspaceInspection, ...]:
        terminal = {SessionState.COMPLETED, SessionState.FAILED, SessionState.ABORTED, SessionState.INTERRUPTED}
        results: list[WorkspaceInspection] = []
        if not root.exists():
            return ()
        for path in sorted(item for item in root.iterdir() if item.is_dir()):
            inspection, rows = cls.inspect(path, include_rows=True)
            assert isinstance(inspection, WorkspaceInspection)
            if not inspection.valid or inspection.state in terminal:
                results.append(inspection)
                continue
            envelope = json.loads((path / "session_spec.json").read_text(encoding="utf-8"))
            spec = InteractiveSessionSpec.model_validate(envelope["session_spec"])
            workspace = cls(root, spec, create=False)
            last_revision = int(rows[-1].get("revision", 0)) if rows else 0
            last_sim_time_s = float(rows[-1].get("sim_time_s", 0.0)) if rows else 0.0
            workspace.append_state(SessionState.INTERRUPTED, last_revision + 1, last_sim_time_s, "PROCESS_RESTART")
            updated = cls.inspect(path)
            assert isinstance(updated, WorkspaceInspection)
            results.append(updated)
        return tuple(results)
