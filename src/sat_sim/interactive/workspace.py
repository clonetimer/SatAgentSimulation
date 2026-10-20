"""Append-only, hash-chained working state for interactive sessions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CommandAck, InteractiveSessionSpec, SessionState, Telecommand, TelemetryFrame
from . import telemetry_segments


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
        self._continuous_storage_guard = bool(spec.continuous)
        self._telemetry_log_max_bytes = int(spec.telemetry_log_max_bytes)
        self._telemetry_segment_max_bytes = int(spec.telemetry_segment_max_bytes) if spec.continuous else 0
        self._telemetry_min_free_disk_bytes = int(spec.telemetry_min_free_disk_bytes)
        # Amortize disk usage checks; the safety reserve greatly exceeds the
        # maximum size of a normal 128-frame batch.
        self._telemetry_disk_check_interval = 128
        self._telemetry_disk_check_count = 0
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
            self._telemetry_total_bytes = 0
        else:
            inspection, rows = self.inspect(self.path, include_rows=True)
            if not inspection.valid:
                raise ValueError(f"invalid session workspace: {inspection.reason_code}")
            self._sequence = inspection.sequence + 1
            self._last_hash = str(rows[-1]["entry_hash"]) if rows else "0" * 64
            self._command_sequence, self._command_last_hash = self.hash_chain_tail(self.commands_path)
            self._telemetry_sequence, self._telemetry_last_hash = self.telemetry_chain_tail(self.path)
            self._telemetry_total_bytes = telemetry_segments.total_bytes(self.path)

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
            encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
            if self._continuous_storage_guard:
                # Reject before appending: never truncate, silently drop data,
                # or create a broken hash-chain on capacity exhaustion.
                if self._telemetry_log_max_bytes:
                    if self._telemetry_total_bytes + len(encoded) > self._telemetry_log_max_bytes:
                        raise RuntimeError("TELEMETRY_LOG_BUDGET_EXCEEDED")
                if self._telemetry_segment_max_bytes and len(encoded) > self._telemetry_segment_max_bytes:
                    raise RuntimeError("TELEMETRY_FRAME_EXCEEDS_SEGMENT_CAP")
                if self._telemetry_min_free_disk_bytes and self._telemetry_disk_check_count == 0:
                    if shutil.disk_usage(self.path).free <= (
                        self._telemetry_min_free_disk_bytes + len(encoded)
                    ):
                        raise RuntimeError("TELEMETRY_DISK_RESERVE_EXHAUSTED")
            if self._telemetry_segment_max_bytes:
                used = self.telemetry_path.stat().st_size if self.telemetry_path.exists() else 0
                if used and used + len(encoded) > self._telemetry_segment_max_bytes:
                    telemetry_segments.roll_active(self.path)
            with self.telemetry_path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._telemetry_total_bytes += len(encoded)
            self._telemetry_last_hash = payload["entry_hash"]
            self._telemetry_sequence += 1
            if self._continuous_storage_guard:
                self._telemetry_disk_check_count = (
                    self._telemetry_disk_check_count + 1
                ) % self._telemetry_disk_check_interval
            return payload

    def finalize_telemetry(self) -> dict[str, Any]:
        """Archive verified telemetry with memory O(number of streams)."""
        with self._lock:
            counts: dict[str, int] = {}
            latest_sequences: dict[str, int] = {}
            hashes: dict[str, Any] = {}
            artifact_root = self.path / "telemetry_streams"
            artifact_root.mkdir(parents=True, exist_ok=True)
            artifacts: dict[str, dict[str, Any]] = {}
            last_hash = "0" * 64
            frame_count = 0
            with ExitStack() as stack:
                writers: dict[str, Any] = {}
                for row in self.iter_telemetry(self.path):
                    frame = row["frame"]
                    stream = str(frame["stream"])
                    if stream not in writers:
                        filename = f"stream-{hashlib.sha256(stream.encode('utf-8')).hexdigest()[:16]}.jsonl"
                        writers[stream] = stack.enter_context((artifact_root / filename).open("wb"))
                        hashes[stream] = hashlib.sha256()
                        artifacts[stream] = {"path": f"telemetry_streams/{filename}"}
                    data = (json.dumps(frame, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                    writers[stream].write(data)
                    hashes[stream].update(data)
                    counts[stream] = counts.get(stream, 0) + 1
                    latest_sequences[stream] = int(frame["sequence"])
                    last_hash = str(row["entry_hash"])
                    frame_count += 1
            for stream, artifact in artifacts.items():
                artifact["row_count"] = counts[stream]
                artifact["sha256"] = hashes[stream].hexdigest()
            result = {
                "schema_version": "interactive-telemetry-manifest.v1",
                "session_id": self.path.name,
                "frame_count": frame_count,
                "streams": counts,
                "latest_sequences": latest_sequences,
                "artifacts": dict(sorted(artifacts.items())),
                "telemetry_sha256": telemetry_segments.logical_sha256(self.path),
                "final_entry_hash": last_hash,
            }
            segments = telemetry_segments.segments_metadata(self.path)
            if segments:
                result["source_segments"] = segments
            self.telemetry_manifest_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return result

    @staticmethod
    def iter_telemetry(root: Path):
        """Walk rotated and legacy telemetry with a shared global hash chain."""
        yield from telemetry_segments.iter_entries(root)

    @classmethod
    def telemetry_chain_tail(cls, root: Path) -> tuple[int, str]:
        count, last = 0, "0" * 64
        for row in cls.iter_telemetry(root):
            count += 1
            last = row["entry_hash"]
        return count, last

    @staticmethod
    def iter_hash_chain(path: Path):
        """Verify each hash-chained JSONL entry without retaining prior rows."""
        previous_hash = "0" * 64
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                row = json.loads(raw_line)
                entry_hash = str(row.pop("entry_hash"))
                if row.get("previous_hash") != previous_hash or _sha256_bytes(_canonical(row)) != entry_hash:
                    raise ValueError("HASH_CHAIN_INVALID")
                row["entry_hash"] = entry_hash
                previous_hash = entry_hash
                yield row

    @classmethod
    def hash_chain_tail(cls, path: Path) -> tuple[int, str]:
        count, last_hash = 0, "0" * 64
        for row in cls.iter_hash_chain(path):
            count += 1
            last_hash = str(row["entry_hash"])
        return count, last_hash

    @classmethod
    def inspect_hash_chain(cls, path: Path) -> list[dict[str, Any]]:
        """Legacy full-materialization helper for finite session diagnostics."""
        return list(cls.iter_hash_chain(path))

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
                cls.hash_chain_tail(path / "command_events.jsonl")
            except ValueError as exc:
                raise ValueError("COMMAND_HASH_CHAIN_INVALID") from exc
            try:
                cls.telemetry_chain_tail(path)
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
