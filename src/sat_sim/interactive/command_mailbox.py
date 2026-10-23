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
from .command_writer_epoch import (
    claim_fresh_or_cleanly_released, require_current_writer, release_owned_writer,
)


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
        self._owner_epoch: int | None = None
        self._owner_token: str | None = None
        self._sequence = 0
        if persistent_ledger:
            path = Path(ledger_path) if ledger_path is not None else Path(self._temporary_directory.name) / "command_ids.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._database = sqlite3.connect(str(path), check_same_thread=False)
            # The accepted-ID UNIQUE index and command lifecycle share one
            # SQLite transaction. No split commit / silent replay on crash.
            self._database.execute("PRAGMA foreign_keys=ON")
            self._database.execute("PRAGMA synchronous=FULL")
            self._database.execute(
                "CREATE TABLE IF NOT EXISTS command_ids ("
                "command_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)"
            )
            self._database.execute(
                "CREATE TABLE IF NOT EXISTS command_lifecycle ("
                "command_id TEXT PRIMARY KEY REFERENCES command_ids(command_id), "
                "fingerprint TEXT NOT NULL, "
                "session_id TEXT NOT NULL, "
                "execute_at_sim_time_s REAL NOT NULL, "
                "receive_sequence INTEGER NOT NULL UNIQUE, "
                "state TEXT NOT NULL CHECK (state IN "
                "('QUEUED','EXECUTING','ACKED','FAILED','EXPIRED')), "
                "transition_sim_time_s REAL, "
                "command_json TEXT)"
            )
            self._database.execute(
                "CREATE TABLE IF NOT EXISTS command_event_outbox ("
                "event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "command_id TEXT NOT NULL REFERENCES command_ids(command_id), "
                "fingerprint TEXT NOT NULL, "
                "state TEXT NOT NULL CHECK (state IN "
                "('QUEUED','EXECUTING','ACKED','FAILED','EXPIRED')), "
                "sim_time_s REAL, "
                "mirrored_sequence INTEGER UNIQUE, "
                "mirrored_entry_hash TEXT, "
                "UNIQUE(command_id, state), "
                "CHECK ((mirrored_sequence IS NULL AND mirrored_entry_hash IS NULL) "
                "OR (mirrored_sequence IS NOT NULL AND mirrored_entry_hash IS NOT NULL)))"
            )
            self._database.commit()
            # R6d upgrade path: existing accepted rows cannot magically
            # acquire a historical full command payload. Their NULL
            # command_json remains non-rehydratable and fail-closed.
            columns = {
                row[1] for row in self._database.execute(
                    "PRAGMA table_info(command_lifecycle)"
                )
            }
            if "command_json" not in columns:
                self._database.execute(
                    "ALTER TABLE command_lifecycle ADD COLUMN command_json TEXT"
                )
                self._database.commit()
            try:
                self._owner_epoch, self._owner_token = claim_fresh_or_cleanly_released(
                    self._database
                )
            except BaseException:
                self._database.close()
                self._database = None
                raise
            # Reopening the *same* ledger must not reuse sequence numbers.
            row = self._database.execute(
                "SELECT COALESCE(MAX(receive_sequence), -1) FROM command_lifecycle"
            ).fetchone()
            self._sequence = int(row[0]) + 1
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
                self._require_owner()
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
            if self._database is not None:
                # Both durable rows commit or both roll back. If crash occurs
                # before the JSONL QUEUED receipt, the R6 preflight still
                # blocks restart due to the missing mirrored event.
                with self._database:
                    self._require_owner()
                    self._database.execute(
                        "INSERT INTO command_ids (command_id, fingerprint) VALUES (?, ?)",
                        (command.command_id, fingerprint),
                    )
                    self._database.execute(
                        "INSERT INTO command_lifecycle "
                        "(command_id, fingerprint, session_id, execute_at_sim_time_s, "
                        "receive_sequence, state, transition_sim_time_s, command_json) "
                        "VALUES (?, ?, ?, ?, ?, 'QUEUED', NULL, ?)",
                        (command.command_id, fingerprint, command.session_id,
                         command.execute_at_sim_time_s, sequence,
                         json.dumps(command.model_dump(mode="json"),
                                    sort_keys=True, separators=(",", ":"))),
                    )
                    self._database.execute(
                        "INSERT INTO command_event_outbox "
                        "(command_id, fingerprint, state, sim_time_s) "
                        "VALUES (?, ?, 'QUEUED', NULL)",
                        (command.command_id, fingerprint),
                    )
            else:
                self._fingerprints[command.command_id] = fingerprint
            self._sequence += 1
            heapq.heappush(self._heap, (command.execute_at_sim_time_s, sequence, command))
            return MailboxReceipt(True, False, "QUEUED", sequence)

    @property
    def writer_epoch(self) -> int | None:
        return self._owner_epoch

    def _require_owner(self) -> None:
        if self._database is not None:
            assert self._owner_epoch is not None and self._owner_token is not None
            require_current_writer(
                self._database, epoch=self._owner_epoch, token=self._owner_token,
            )

    def pop_due(self, sim_time_s: float) -> tuple[Telecommand, ...]:
        due, _ = self.pop_due_partitioned(sim_time_s)
        return due

    def pop_due_partitioned(self, sim_time_s: float) -> tuple[tuple[Telecommand, ...], tuple[Telecommand, ...]]:
        due: list[Telecommand] = []
        expired: list[Telecommand] = []
        now = datetime.now(timezone.utc)
        with self._lock:
            self._require_owner()
            while self._heap and self._heap[0][0] <= sim_time_s:
                _, _, command = heapq.heappop(self._heap)
                if command.expires_at is None or command.expires_at > now:
                    due.append(command)
                else:
                    expired.append(command)
        return tuple(due), tuple(expired)

    def _transition_durable(
        self, command: Telecommand, *, from_state: str, to_state: str,
        sim_time_s: float,
    ) -> None:
        """Commit the idempotent replay fence before native side effects.

        For nonpersistent finite simulation sessions this is intentionally a
        no-op. In a continuous session an unexpected state or failed commit
        raises and therefore prevents further native command execution.
        """
        if self._database is None:
            return
        with self._lock:
            with self._database:
                self._require_owner()
                cursor = self._database.execute(
                    "UPDATE command_lifecycle SET state=?, transition_sim_time_s=? "
                    "WHERE command_id=? AND fingerprint=? AND session_id=? AND state=?",
                    (to_state, float(sim_time_s), command.command_id,
                     self._fingerprint(command), command.session_id, from_state),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        "TC_DURABLE_LIFECYCLE_TRANSITION_REJECTED:"
                        + from_state + "->" + to_state
                    )
                # One transaction owns both the authoritative phase change
                # and an append-only durable event intent. The JSONL event
                # is a projection and is never an authoritative TC replay log.
                self._database.execute(
                    "INSERT INTO command_event_outbox "
                    "(command_id, fingerprint, state, sim_time_s) VALUES (?, ?, ?, ?)",
                    (command.command_id, self._fingerprint(command),
                     to_state, float(sim_time_s)),
                )

    def mirror_durable_ack(
        self, command: Telecommand, *, state: str, reason_code: str,
        ack_sequence: int, entry_hash: str, sim_time_s: float | None,
    ) -> None:
        """Attest a *previously fsynced* JSONL projection of an SQLite event.

        Crash after JSONL but before this marker is safe to reconcile from
        identical verified bytes. Absence of JSONL is never repaired by
        inventing or redispatching a command.
        """
        if self._database is None or state not in (
            "QUEUED", "EXECUTING", "ACKED", "FAILED", "EXPIRED"
        ) or reason_code == "DUPLICATE_IDEMPOTENT":
            return
        fp = self._fingerprint(command)
        with self._lock:
            self._require_owner()
            row = self._database.execute(
                "SELECT fingerprint, sim_time_s, mirrored_sequence, "
                "mirrored_entry_hash FROM command_event_outbox "
                "WHERE command_id=? AND state=?", (command.command_id, state),
            ).fetchone()
            if row is None:
                # Rejected or already-expired *unaccepted* commands have
                # no authoritative lifecycle state; their JSONL history
                # remains append-only and is never replayed.
                accepted = self._database.execute(
                    "SELECT 1 FROM command_ids WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()
                if accepted:
                    raise RuntimeError("TC_OUTBOX_MISSING_ACCEPTED_EVENT")
                return
            source_fp, time_s, mirrored_seq, mirrored_hash = row
            if (source_fp != fp or type(ack_sequence) is not int
                    or ack_sequence < 0
                    or type(entry_hash) is not str or len(entry_hash) != 64
                    or time_s != sim_time_s):
                raise ValueError("TC_OUTBOX_ACK_PROJECTION_MISMATCH")
            if mirrored_seq is not None:
                if mirrored_seq == ack_sequence and mirrored_hash == entry_hash:
                    return
                raise ValueError("TC_OUTBOX_DUPLICATE_PROJECTION_CONFLICT")
            with self._database:
                self._require_owner()
                updated = self._database.execute(
                    "UPDATE command_event_outbox SET mirrored_sequence=?, "
                    "mirrored_entry_hash=? WHERE command_id=? AND state=? "
                    "AND mirrored_sequence IS NULL",
                    (ack_sequence, entry_hash, command.command_id, state),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("TC_OUTBOX_MIRROR_RACE")

    def mark_executing_before_effect(self, command: Telecommand, *, sim_time_s: float) -> None:
        self._transition_durable(
            command, from_state="QUEUED", to_state="EXECUTING",
            sim_time_s=sim_time_s,
        )

    def mark_acked_after_receipt(self, command: Telecommand, *, sim_time_s: float) -> None:
        self._transition_durable(
            command, from_state="EXECUTING", to_state="ACKED",
            sim_time_s=sim_time_s,
        )

    def mark_failed_after_receipt(self, command: Telecommand, *, sim_time_s: float) -> None:
        self._transition_durable(
            command, from_state="EXECUTING", to_state="FAILED",
            sim_time_s=sim_time_s,
        )

    def mark_expired_after_receipt(self, command: Telecommand, *, sim_time_s: float) -> None:
        self._transition_durable(
            command, from_state="QUEUED", to_state="EXPIRED",
            sim_time_s=sim_time_s,
        )

    def close(self, *, release_owner: bool = True) -> None:
        """Close DB. A failed native worker MUST leave an ACTIVE orphan fence.

        Releasing an ACTIVE owner after unknown native side effects could
        falsely signal a clean handoff. Abnormal termination closes only the
        local connection; a supervisor must later prove original process
        death and reconcile effects before any separate generation claim.
        """
        with self._lock:
            if self._database is not None:
                if (release_owner and self._owner_token is not None
                        and self._owner_epoch is not None):
                    release_owned_writer(
                        self._database,
                        epoch=self._owner_epoch,
                        token=self._owner_token,
                    )
                self._database.close()
                self._database = None
            if self._temporary_directory is not None:
                self._temporary_directory.cleanup()
                self._temporary_directory = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._heap)
