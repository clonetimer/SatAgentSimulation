"""Bounded deterministic telecommand scheduling with idempotency checks."""
from __future__ import annotations

import hashlib
import heapq
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Telecommand


@dataclass(frozen=True)
class MailboxReceipt:
    accepted: bool
    duplicate: bool
    reason_code: str
    receive_sequence: int | None


class DeterministicCommandMailbox:
    def __init__(
        self, max_depth: int = 1024, *, persistent_ledger: bool = False,
        ledger_path: Path | None = None,
    ) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        self._max_depth = max_depth
        self._heap: list[tuple[float, int, Telecommand]] = []
        # Never evict an accepted command ID: doing so would allow duplicate execution.
        # Continuous sessions store that UNIQUE index on disk instead of in Python memory.
        self._fingerprints: dict[str, str] = {}
        self._temporary_directory = (
            tempfile.TemporaryDirectory(prefix="sat-tc-ledger-")
            if persistent_ledger and ledger_path is None else None
        )
        self._database = None
        if persistent_ledger:
            path = Path(ledger_path) if ledger_path is not None else Path(self._temporary_directory.name) / "command_ids.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._database = sqlite3.connect(str(path), check_same_thread=False)
            self._database.execute(
                "CREATE TABLE IF NOT EXISTS command_ids ("
                "command_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)"
            )
            self._database.commit()
        self._sequence = 0
        self._lock = threading.Lock()

    @staticmethod
    def _fingerprint(command: Telecommand) -> str:
        payload = command.model_dump(mode="json", exclude={"received_at"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def submit(self, command: Telecommand, *, session_revision: int) -> MailboxReceipt:
        fingerprint = self._fingerprint(command)
        with self._lock:
            if self._database is not None:
                stored = self._database.execute(
                    "SELECT fingerprint FROM command_ids WHERE command_id=?", (command.command_id,)
                ).fetchone()
                existing = stored[0] if stored is not None else None
            else:
                existing = self._fingerprints.get(command.command_id)
            if existing is not None:
                if existing == fingerprint:
                    return MailboxReceipt(True, True, "DUPLICATE_IDEMPOTENT", None)
                return MailboxReceipt(False, False, "COMMAND_ID_CONFLICT", None)
            if command.session_revision != session_revision:
                return MailboxReceipt(False, False, "SESSION_REVISION_MISMATCH", None)
            if len(self._heap) >= self._max_depth:
                return MailboxReceipt(False, False, "COMMAND_QUEUE_FULL", None)
            if command.expires_at is not None and command.expires_at <= datetime.now(timezone.utc):
                return MailboxReceipt(False, False, "COMMAND_EXPIRED", None)
            sequence = self._sequence
            self._sequence += 1
            if self._database is not None:
                self._database.execute(
                    "INSERT INTO command_ids (command_id, fingerprint) VALUES (?, ?)",
                    (command.command_id, fingerprint),
                )
                self._database.commit()
            else:
                self._fingerprints[command.command_id] = fingerprint
            heapq.heappush(self._heap, (command.execute_at_sim_time_s, sequence, command))
            return MailboxReceipt(True, False, "QUEUED", sequence)

    def pop_due(self, sim_time_s: float) -> tuple[Telecommand, ...]:
        due, _ = self.pop_due_partitioned(sim_time_s)
        return due

    def pop_due_partitioned(self, sim_time_s: float) -> tuple[tuple[Telecommand, ...], tuple[Telecommand, ...]]:
        due: list[Telecommand] = []
        expired: list[Telecommand] = []
        now = datetime.now(timezone.utc)
        with self._lock:
            while self._heap and self._heap[0][0] <= sim_time_s:
                _, _, command = heapq.heappop(self._heap)
                if command.expires_at is None or command.expires_at > now:
                    due.append(command)
                else:
                    expired.append(command)
        return tuple(due), tuple(expired)

    def close(self) -> None:
        with self._lock:
            if self._database is not None:
                self._database.close()
                self._database = None
            if self._temporary_directory is not None:
                self._temporary_directory.cleanup()
                self._temporary_directory = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)
