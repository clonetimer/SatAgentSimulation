"""SQLite-enforced single-writer epochs for continuous TC command journals.

An owner is *not* a time-limited lease. If the process crashes while ACTIVE,
reopening the same database refuses service until a separate, supervised
recovery decision. No automatic steal, PID guessing, timeout override or
production Basilisk resume is implemented.

All command mutations check the owner token and monotonic epoch INSIDE the
same SQLite transaction that writes state. This fences stale writers at the
control-plane transaction boundary, not an in-flight native simulation step.
"""
from __future__ import annotations

import sqlite3
import uuid


def claim_fresh_or_cleanly_released(db: sqlite3.Connection) -> tuple[int, str]:
    token = uuid.uuid4().hex
    db.execute(
        "CREATE TABLE IF NOT EXISTS command_writer_epoch ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
        "epoch INTEGER NOT NULL CHECK(epoch>0), "
        "owner_token TEXT NOT NULL, "
        "owner_state TEXT NOT NULL CHECK(owner_state IN ('ACTIVE','RELEASED')))"
    )
    # Acquire a write lock before inspecting state so concurrent processes
    # cannot both observe RELEASED and claim it.
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT epoch, owner_state FROM command_writer_epoch WHERE singleton=1"
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO command_writer_epoch "
                "(singleton,epoch,owner_token,owner_state) VALUES (1,1,?,'ACTIVE')",
                (token,),
            )
            return 1, token
        previous, state = int(row[0]), row[1]
        if state != "RELEASED":
            raise RuntimeError("TC_WRITER_ALREADY_ACTIVE_OR_CRASHED_NO_TAKEOVER")
        epoch = previous + 1
        db.execute(
            "UPDATE command_writer_epoch SET epoch=?,owner_token=?,owner_state='ACTIVE' "
            "WHERE singleton=1 AND epoch=? AND owner_state='RELEASED'",
            (epoch, token, previous),
        )
        return epoch, token


def require_current_writer(
    db: sqlite3.Connection, *, epoch: int, token: str,
) -> None:
    row = db.execute(
        "SELECT epoch,owner_token,owner_state "
        "FROM command_writer_epoch WHERE singleton=1"
    ).fetchone()
    if row != (epoch, token, "ACTIVE"):
        raise RuntimeError("TC_WRITER_EPOCH_FENCED")


def release_owned_writer(
    db: sqlite3.Connection, *, epoch: int, token: str,
) -> None:
    with db:
        db.execute("BEGIN IMMEDIATE")
        # A stale owner must not be able to release a new owner's epoch.
        require_current_writer(db, epoch=epoch, token=token)
        updated = db.execute(
            "UPDATE command_writer_epoch SET owner_state='RELEASED' "
            "WHERE singleton=1 AND epoch=? AND owner_token=? "
            "AND owner_state='ACTIVE'",
            (epoch, token),
        )
        if updated.rowcount != 1:
            raise RuntimeError("TC_WRITER_RELEASE_FENCED")
