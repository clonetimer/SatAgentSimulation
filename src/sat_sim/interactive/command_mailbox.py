"""Bounded deterministic telecommand scheduling with idempotency checks."""
from __future__ import annotations

import hashlib
import heapq
import json
import threading
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
    def __init__(self, max_depth: int = 1024) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        self._max_depth = max_depth
        self._heap: list[tuple[float, int, Telecommand]] = []
        self._fingerprints: dict[str, str] = {}
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

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)
