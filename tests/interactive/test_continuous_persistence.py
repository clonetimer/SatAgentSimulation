"""Long-running session persistence regressions, without a Basilisk dependency."""
from __future__ import annotations

import hashlib
import json
from collections import deque

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import (
    ActorRole, CommandState, InteractiveSessionSpec, Telecommand, TelemetryFrame,
)
from sat_sim.interactive.workspace import SessionWorkspace


def _spec(session_id: str) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id=session_id, capability_id="whole_spacecraft.unified_native.v1",
        task_spec={}, continuous=True, quantum_s=0.1, paced=False,
    )


def _command(session_id: str, command_id: str, operation: str = "adcs.target.set") -> Telecommand:
    return Telecommand(
        command_id=command_id, session_id=session_id, session_revision=2,
        operation=operation, target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]},
        actor_id="test-operator", actor_role=ActorRole.OPERATOR,
        execute_at_sim_time_s=0.5,
    )


class _StubRuntime:
    current_time_s = 0.0

    def prepare(self) -> None:
        pass

    def advance_to(self, stop_time_s: float) -> tuple[dict, ...]:
        self.current_time_s = stop_time_s
        return ({"time_s": stop_time_s},)

    def apply_command(self, command: Telecommand) -> None:
        pass

    def read_delta(self) -> tuple[dict, ...]:
        return ()

    def finalize(self) -> dict:
        return {}

    def abort(self) -> None:
        pass


def test_sqlite_ledger_retains_old_command_ids_after_reopening(tmp_path) -> None:
    path = tmp_path / "command_ids.sqlite"
    first = DeterministicCommandMailbox(max_depth=2, persistent_ledger=True, ledger_path=path)
    original = _command("ledger-session", "original")
    assert first.submit(original, session_revision=2).accepted
    assert first.pop_due(1.0)[0].command_id == original.command_id
    for index in range(150):
        command = _command("ledger-session", f"cmd-{index}")
        assert first.submit(command, session_revision=2).accepted
        first.pop_due(1.0)
    assert first._fingerprints == {}
    first.close()

    reopened = DeterministicCommandMailbox(
        max_depth=2, persistent_ledger=True, ledger_path=path,
    )
    try:
        assert reopened.submit(original, session_revision=2).reason_code == "DUPLICATE_IDEMPOTENT"
        assert reopened.submit(
            _command("ledger-session", "original", operation="eps.load.set"), session_revision=2
        ).reason_code == "COMMAND_ID_CONFLICT"
        assert reopened.submit(_command("ledger-session", "new"), session_revision=2).accepted
    finally:
        reopened.close()


def test_continuous_acks_use_bounded_cache_but_keep_targeted_disk_history(tmp_path) -> None:
    spec = _spec("ack-cache")
    workspace = SessionWorkspace(tmp_path, spec)
    session = InteractiveSession(spec, _StubRuntime(), end_time_s=None, workspace=workspace)
    try:
        assert isinstance(session._acks, deque)
        assert session._acks.maxlen == 4096
        session._acks = deque(maxlen=3)
        for i in range(10):
            session._emit_ack(_command(spec.session_id, f"cmd-{i}"), CommandState.RECEIVED)
        assert len(session.command_acks()) == 3
        assert [ack.command_id for ack in session.command_acks()] == [
            "cmd-7", "cmd-8", "cmd-9",
        ]
        historical = session.command_acks("cmd-0")
        assert len(historical) == 1
        assert historical[0].command_id == "cmd-0"
        assert len(SessionWorkspace.inspect_hash_chain(workspace.commands_path)) == 10
        assert SessionWorkspace.inspect(workspace.path).valid
    finally:
        session.abort()
        session.wait(2.0)


def test_streaming_archive_preserves_v1_manifest_and_hash_integrity(tmp_path) -> None:
    spec = _spec("archive-streaming")
    workspace = SessionWorkspace(tmp_path, spec)
    for index in range(120):
        stream = "fast" if index % 2 == 0 else "slow"
        sequence = index // 2
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream=stream,
            sequence=sequence, sim_time_s=float(index) / 10.0,
            values={"value": index},
        ))
    manifest = workspace.finalize_telemetry()
    assert manifest["frame_count"] == 120
    assert manifest["streams"] == {"fast": 60, "slow": 60}
    assert manifest["latest_sequences"] == {"fast": 59, "slow": 59}
    assert len(manifest["artifacts"]) == 2
    for stream in ("fast", "slow"):
        artifact = manifest["artifacts"][stream]
        data = (workspace.path / artifact["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
        rows = [json.loads(line) for line in data.splitlines()]
        assert len(rows) == 60
        assert all(row["stream"] == stream for row in rows)
    assert SessionWorkspace.hash_chain_tail(workspace.telemetry_path)[0] == 120
    assert SessionWorkspace.inspect(workspace.path).valid

    # A one-byte payload modification must be rejected, even in streaming mode.
    data = workspace.telemetry_path.read_text(encoding="utf-8")
    workspace.telemetry_path.write_text(data.replace('"value": 0', '"value": 999', 1), encoding="utf-8")
    assert SessionWorkspace.inspect(workspace.path).valid is False
    with pytest.raises(ValueError, match="HASH_CHAIN_INVALID"):
        workspace.finalize_telemetry()



def test_archive_failure_is_visible_after_simulation_stops(tmp_path) -> None:
    class FailingWorkspace(SessionWorkspace):
        def finalize_telemetry(self) -> dict:
            raise OSError("archive write unavailable")

    spec = _spec("failed-archive")
    workspace = FailingWorkspace(tmp_path, spec)
    session = InteractiveSession(spec, _StubRuntime(), end_time_s=None, workspace=workspace)
    session.prepare()
    session.start()
    session.stop()
    assert session.wait(2.0)
    snapshot = session.snapshot()
    # Simulation completion must not imply a validated Run Bundle.
    assert snapshot.state.value == "COMPLETED"
    assert snapshot.archive_status == "FAILED"
    assert "archive write unavailable" in (snapshot.archive_error or "")
    assert not (workspace.path / "run_bundle" / "SEALED.json").exists()



def test_continuous_telemetry_budget_fails_before_corrupting_hash_chain(tmp_path) -> None:
    spec = _spec("bounded-storage").model_copy(update={
        "telemetry_log_max_bytes": 100000,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    def frame(index: int) -> TelemetryFrame:
        return TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=index,
            sim_time_s=float(index), values={"value": index},
        )
    workspace.append_telemetry(frame(0))
    data_before = workspace.telemetry_path.read_bytes()
    workspace._telemetry_log_max_bytes = len(data_before) + 1
    with pytest.raises(RuntimeError, match="TELEMETRY_LOG_BUDGET_EXCEEDED"):
        workspace.append_telemetry(frame(1))
    assert workspace.telemetry_path.read_bytes() == data_before
    assert SessionWorkspace.inspect(workspace.path).valid is True
    assert workspace._telemetry_sequence == 1


def test_continuous_disk_reserve_fail_closed_without_dropping_previous_evidence(
    tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace
    import sat_sim.interactive.workspace as workspace_module

    spec = _spec("low-disk").model_copy(update={
        "telemetry_min_free_disk_bytes": 1000,
        "telemetry_log_max_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    monkeypatch.setattr(
        workspace_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=800),
    )
    frame = TelemetryFrame(
        session_id=spec.session_id, stream="fast", sequence=0,
        sim_time_s=0.0, values={"value": 123},
    )
    with pytest.raises(RuntimeError, match="TELEMETRY_DISK_RESERVE_EXHAUSTED"):
        workspace.append_telemetry(frame)
    assert not workspace.telemetry_path.exists()
    assert SessionWorkspace.inspect(workspace.path).valid
