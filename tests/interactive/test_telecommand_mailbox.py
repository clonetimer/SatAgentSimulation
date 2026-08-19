from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.models import Telecommand


def _command(command_id: str, execute_at: float, *, operation: str = "adcs.target.set", revision: int = 3, expires_at=None) -> Telecommand:
    return Telecommand(
        command_id=command_id, session_id="session-1", session_revision=revision,
        operation=operation, target="subsystem.adcs", parameters={"angle_deg": 1.0},
        actor_id="operator-1", actor_role="operator", execute_at_sim_time_s=execute_at,
        expires_at=expires_at,
    )


def test_mailbox_orders_by_sim_time_then_receive_sequence() -> None:
    mailbox = DeterministicCommandMailbox(max_depth=4)
    assert mailbox.submit(_command("second", 2.0), session_revision=3).accepted
    assert mailbox.submit(_command("first-a", 1.0), session_revision=3).accepted
    assert mailbox.submit(_command("first-b", 1.0), session_revision=3).accepted
    assert [item.command_id for item in mailbox.pop_due(1.0)] == ["first-a", "first-b"]
    assert [item.command_id for item in mailbox.pop_due(2.0)] == ["second"]


def test_mailbox_idempotency_conflict_revision_expiry_and_capacity_fail_closed() -> None:
    mailbox = DeterministicCommandMailbox(max_depth=1)
    command = _command("one", 1.0)
    assert mailbox.submit(command, session_revision=3).reason_code == "QUEUED"
    assert mailbox.submit(command, session_revision=3).reason_code == "DUPLICATE_IDEMPOTENT"
    assert mailbox.submit(_command("one", 1.0, operation="eps.load.set"), session_revision=3).reason_code == "COMMAND_ID_CONFLICT"
    assert mailbox.submit(_command("wrong-revision", 1.0, revision=2), session_revision=3).reason_code == "SESSION_REVISION_MISMATCH"
    assert mailbox.submit(_command("full", 1.0), session_revision=3).reason_code == "COMMAND_QUEUE_FULL"
    mailbox.pop_due(1.0)
    expired = _command("expired", 2.0, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert mailbox.submit(expired, session_revision=3).reason_code == "COMMAND_EXPIRED"

