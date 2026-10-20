"""Long-running session persistence regressions, without a Basilisk dependency."""
from __future__ import annotations

import hashlib
import json
from collections import deque

import pytest

from sat_sim.interactive.command_mailbox import DeterministicCommandMailbox
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import (
    ActorRole, CommandState, InteractiveSessionSpec, SessionState, Telecommand, TelemetryFrame,
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



def test_continuous_session_reports_storage_budget_exhaustion_to_client(tmp_path) -> None:
    spec = _spec("budget-failed-session").model_copy(update={
        "telemetry_log_max_bytes": 1,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    session = InteractiveSession(spec, _StubRuntime(), end_time_s=None, workspace=workspace)
    session.prepare()
    session.start()
    assert session.wait(3.0)
    snapshot = session.snapshot()
    assert snapshot.state.value == "FAILED"
    assert "TELEMETRY_LOG_BUDGET_EXCEEDED" in (snapshot.terminal_error or "")
    assert SessionWorkspace.inspect(workspace.path).valid is True
    assert not (workspace.path / "run_bundle" / "SEALED.json").exists()



def test_rotated_telemetry_retains_global_hash_chain_and_reopens(tmp_path) -> None:
    import hashlib
    from sat_sim.interactive import telemetry_segments

    spec = _spec("segmented-reopen").model_copy(update={
        "telemetry_segment_max_bytes": 1024,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)

    def frame(number: int) -> TelemetryFrame:
        return TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=number,
            sim_time_s=float(number), values={"value": number},
        )

    for index in range(30):
        workspace.append_telemetry(frame(index))
    closed = telemetry_segments.segments_metadata(workspace.path)
    assert len(closed) > 1
    assert all(item["bytes"] <= 1024 for item in closed)
    assert len(list(SessionWorkspace.iter_telemetry(workspace.path))) == 30
    assert SessionWorkspace.telemetry_chain_tail(workspace.path)[0] == 30
    assert SessionWorkspace.inspect(workspace.path).valid is True

    restarted = SessionWorkspace(tmp_path, spec, create=False)
    restarted.append_telemetry(frame(30))
    assert SessionWorkspace.telemetry_chain_tail(workspace.path)[0] == 31
    manifest = restarted.finalize_telemetry()
    assert manifest["frame_count"] == 31
    assert len(manifest["source_segments"]) >= 2
    logical = b"".join(path.read_bytes() for path in telemetry_segments.files(workspace.path))
    assert manifest["telemetry_sha256"] == hashlib.sha256(logical).hexdigest()
    assert SessionWorkspace.inspect(workspace.path).valid


def test_rotated_telemetry_detects_missing_or_corrupted_segments(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments

    spec = _spec("tampered-segments").model_copy(update={
        "telemetry_segment_max_bytes": 1024,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    for number in range(12):
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=number,
            sim_time_s=float(number), values={"value": number},
        ))
    closed = telemetry_segments.segments_metadata(workspace.path)
    assert len(closed) >= 2
    first = workspace.path / "telemetry_segments" / closed[0]["filename"]
    original = first.read_bytes()
    first.write_bytes(original.replace(b'"value": 0', b'"value": 9', 1))
    inspection = SessionWorkspace.inspect(workspace.path)
    assert not inspection.valid
    assert "TELEMETRY_HASH_CHAIN_INVALID" in inspection.reason_code
    first.write_bytes(original)
    assert SessionWorkspace.inspect(workspace.path).valid
    first.unlink()
    inspection = SessionWorkspace.inspect(workspace.path)
    assert not inspection.valid
    # Public workspace API intentionally wraps the underlying segment error.
    assert "TELEMETRY_HASH_CHAIN_INVALID" in inspection.reason_code
    with pytest.raises(ValueError, match="TELEMETRY_SEGMENT_SET_MISMATCH"):
        telemetry_segments.files(workspace.path)


def test_total_telemetry_budget_counts_closed_segments_and_active_file(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments

    spec = _spec("total-budget").model_copy(update={
        "telemetry_segment_max_bytes": 1024,
        "telemetry_log_max_bytes": 0,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    for number in range(8):
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=number,
            sim_time_s=float(number), values={"value": number},
        ))
    assert telemetry_segments.segments_metadata(workspace.path)
    before = telemetry_segments.total_bytes(workspace.path)
    workspace._telemetry_log_max_bytes = before + 1
    with pytest.raises(RuntimeError, match="TELEMETRY_LOG_BUDGET_EXCEEDED"):
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=8,
            sim_time_s=8.0, values={"value": 8},
        ))
    assert telemetry_segments.total_bytes(workspace.path) == before
    assert SessionWorkspace.inspect(workspace.path).valid



def _compressed_fixture(tmp_path, name="gzip-evidence"):
    from sat_sim.interactive import telemetry_segments

    spec = _spec(name).model_copy(update={
        "telemetry_segment_max_bytes": 1024,
        "telemetry_min_free_disk_bytes": 0,
    })
    workspace = SessionWorkspace(tmp_path, spec)
    for index in range(35):
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=index,
            sim_time_s=float(index) / 2,
            values={"value": index, "payload": "x" * 40},
        ))
    assert len(telemetry_segments.segments_metadata(workspace.path)) >= 2
    return spec, workspace


def test_offline_compression_preserves_global_hash_and_legacy_manifest(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments

    spec, workspace = _compressed_fixture(tmp_path)
    initial = workspace.finalize_telemetry()
    initial_hash = telemetry_segments.logical_sha256(workspace.path)
    physical_before = telemetry_segments.total_bytes(workspace.path)
    logical_before = telemetry_segments.total_logical_bytes(workspace.path)
    outcome = telemetry_segments.compress_closed(workspace.path)
    assert outcome["compressed"] >= 2
    assert outcome["bytes_after"] < physical_before
    assert telemetry_segments.total_logical_bytes(workspace.path) == logical_before
    assert telemetry_segments.logical_sha256(workspace.path) == initial_hash
    assert len(list(SessionWorkspace.iter_telemetry(workspace.path))) == 35
    assert SessionWorkspace.inspect(workspace.path).valid
    updated = workspace.finalize_telemetry()
    assert updated["telemetry_sha256"] == initial["telemetry_sha256"]
    assert all(entry["filename"].endswith(".gz") for entry in updated["source_segments"])
    reopened = SessionWorkspace(tmp_path, spec, create=False)
    reopened.append_telemetry(TelemetryFrame(
        session_id=spec.session_id, stream="fast", sequence=35,
        sim_time_s=18.0, values={"value": 35, "payload": "x" * 40},
    ))
    assert len(list(SessionWorkspace.iter_telemetry(workspace.path))) == 36


def test_compression_does_not_reset_total_logical_budget(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments

    spec, workspace = _compressed_fixture(tmp_path, name="gzip-quota")
    before = telemetry_segments.total_logical_bytes(workspace.path)
    telemetry_segments.compress_closed(workspace.path)
    workspace = SessionWorkspace(tmp_path, spec, create=False)
    workspace._telemetry_log_max_bytes = before + 1
    with pytest.raises(RuntimeError, match="TELEMETRY_LOG_BUDGET_EXCEEDED"):
        workspace.append_telemetry(TelemetryFrame(
            session_id=spec.session_id, stream="fast", sequence=35,
            sim_time_s=18, values={"value": 35},
        ))
    assert telemetry_segments.total_logical_bytes(workspace.path) == before
    assert SessionWorkspace.inspect(workspace.path).valid


def test_compression_detects_corrupt_gzip_and_missing_archive(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments

    _, workspace = _compressed_fixture(tmp_path, name="gzip-corrupt")
    telemetry_segments.compress_closed(workspace.path, max_segments=1)
    info = telemetry_segments.segments_metadata(workspace.path)[0]
    path = workspace.path / "telemetry_segments" / info["filename"]
    original = path.read_bytes()
    path.write_bytes(original[:-5] + b"xxxxx")
    assert not SessionWorkspace.inspect(workspace.path).valid
    path.write_bytes(original)
    assert SessionWorkspace.inspect(workspace.path).valid
    path.unlink()
    assert not SessionWorkspace.inspect(workspace.path).valid


def test_compression_interruption_requires_verified_explicit_repair(tmp_path) -> None:
    import gzip
    from sat_sim.interactive import telemetry_segments

    _, workspace = _compressed_fixture(tmp_path, name="gzip-repair")
    folder = workspace.path / "telemetry_segments"
    record = telemetry_segments.segments_metadata(workspace.path)[0]
    source = folder / record["filename"]
    duplicate = folder / (record["filename"] + ".gz")

    # Crash before index publication: verified compressed duplicate is discarded.
    duplicate.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
    with pytest.raises(ValueError, match="TELEMETRY_SEGMENT_SET_MISMATCH"):
        telemetry_segments.segments_metadata(workspace.path)
    assert telemetry_segments.repair_interrupted_compression(workspace.path) == 1
    assert source.exists() and not duplicate.exists()
    assert SessionWorkspace.inspect(workspace.path).valid

    # Crash after index publication but before removing old plaintext.
    telemetry_segments.compress_closed(workspace.path, max_segments=1)
    compressed_record = telemetry_segments.segments_metadata(workspace.path)[0]
    compressed = folder / compressed_record["filename"]
    source.write_bytes(gzip.decompress(compressed.read_bytes()))
    with pytest.raises(ValueError, match="TELEMETRY_SEGMENT_SET_MISMATCH"):
        telemetry_segments.segments_metadata(workspace.path)
    assert telemetry_segments.repair_interrupted_compression(workspace.path) == 1
    assert compressed.exists() and not source.exists()
    assert SessionWorkspace.inspect(workspace.path).valid



def test_explicit_repair_of_verified_incomplete_compression_stage(tmp_path) -> None:
    import gzip
    from sat_sim.interactive import telemetry_segments

    _, workspace = _compressed_fixture(tmp_path, name="gzip-temporary")
    folder = workspace.path / "telemetry_segments"
    entry = telemetry_segments.segments_metadata(workspace.path)[0]
    original = folder / entry["filename"]
    temporary = folder / (".compress-" + original.name + ".tmp")
    temporary.write_bytes(gzip.compress(original.read_bytes(), mtime=0))
    assert telemetry_segments.repair_interrupted_compression(workspace.path) == 1
    assert not temporary.exists()
    assert original.exists()
    assert SessionWorkspace.inspect(workspace.path).valid

    temporary.write_bytes(b"invalid gzip staging")
    with pytest.raises(ValueError, match="TELEMETRY_SEGMENT_DECOMPRESSION_FAILED"):
        telemetry_segments.repair_interrupted_compression(workspace.path)
    assert temporary.exists() and original.exists()
    assert SessionWorkspace.inspect(workspace.path).valid



def test_restart_handoff_attests_last_durable_data_not_physical_state(tmp_path) -> None:
    from sat_sim.interactive.manager import InteractiveSessionManager
    from sat_sim.interactive.recovery_handoff import verify_interrupted_handoff
    from sat_sim.interactive import telemetry_segments

    spec, workspace = _compressed_fixture(tmp_path, name="restart-evidence")
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_state(SessionState.RUNNING, 3, 1.0, "OK")
    # The durable sensor history has progressed well beyond the most recent
    # state event: it is still not a restorable native-state checkpoint.
    telemetry_segments.compress_closed(workspace.path)
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    handoff = verify_interrupted_handoff(workspace.path)
    assert handoff["evidence"]["last_state_event_sim_time_s"] == pytest.approx(1.0)
    assert handoff["evidence"]["last_durable_telemetry_sim_time_s"] == pytest.approx(17.0)
    assert handoff["evidence"]["telemetry_event_count"] == 35
    assert handoff["physical_recovery"]["resume_supported"] is False
    assert handoff["physical_recovery"]["state_equivalence_proven"] is False
    assert handoff["physical_recovery"]["required_action"] == "NEW_EPOCH_WITH_NEW_SESSION_ID"
    assert handoff["source_sha256"]["telemetry_logical"] == telemetry_segments.logical_sha256(workspace.path)
    # Subsequent app startup must not manufacture a second interruption.
    restarted = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert restarted.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    assert verify_interrupted_handoff(workspace.path) == handoff


def test_restart_handoff_rejects_modified_record_or_source_evidence(tmp_path) -> None:
    from sat_sim.interactive.manager import InteractiveSessionManager
    from sat_sim.interactive.recovery_handoff import verify_interrupted_handoff

    spec = _spec("restart-tampering")
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_telemetry(TelemetryFrame(
        session_id=spec.session_id, stream="runtime.trace", sequence=0,
        sim_time_s=3.0, values={"time_s": 3.0},
    ))
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    assert verify_interrupted_handoff(workspace.path)["evidence"]["last_durable_native_trace_sim_time_s"] == 3.0
    handoff_path = workspace.path / "recovery_handoff.json"
    original = handoff_path.read_text(encoding="utf-8")
    handoff_path.write_text(original.replace("NEW_EPOCH_WITH_NEW_SESSION_ID", "RESUME_IN_PLACE"), encoding="utf-8")
    with pytest.raises(ValueError, match="RECOVERY_HANDOFF_DIGEST_MISMATCH"):
        verify_interrupted_handoff(workspace.path)
    handoff_path.write_text(original, encoding="utf-8")
    assert verify_interrupted_handoff(workspace.path)["physical_recovery"]["resume_supported"] is False
    # Even if an attacker kept the report byte-identical, a modified original
    # specification must not be accepted as a valid checkpoint.
    spec_path = workspace.spec_path
    content = spec_path.read_text(encoding="utf-8")
    spec_path.write_text(content + " \n", encoding="utf-8")
    with pytest.raises(ValueError, match="RECOVERY_SOURCE_CHANGED"):
        verify_interrupted_handoff(workspace.path)


def test_restart_handoff_refuses_finite_mode_and_healthy_workspace(tmp_path) -> None:
    from sat_sim.interactive.recovery_handoff import write_interrupted_handoff

    continuous = _spec("healthy-continuous")
    healthy = SessionWorkspace(tmp_path, continuous)
    healthy.append_state(SessionState.RUNNING, 1, 2.0, "OK")
    with pytest.raises(ValueError, match="RECOVERY_REQUIRES_INTERRUPTED_SESSION"):
        write_interrupted_handoff(healthy.path)
    finite_spec = continuous.model_copy(update={"session_id": "finite-handoff", "continuous": False})
    finite = SessionWorkspace(tmp_path, finite_spec)
    finite.append_state(SessionState.INTERRUPTED, 1, 2.0, "PROCESS_RESTART")
    with pytest.raises(ValueError, match="RECOVERY_REQUIRES_CONTINUOUS_SESSION"):
        write_interrupted_handoff(finite.path)



def test_recovery_handoff_cli_is_read_only_and_reports_no_native_resume(tmp_path, monkeypatch, capsys) -> None:
    import sys
    from scripts.inspect_continuous_recovery import main
    from sat_sim.interactive.manager import InteractiveSessionManager

    spec = _spec("restart-cli")
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_telemetry(TelemetryFrame(
        session_id=spec.session_id, stream="runtime.trace", sequence=0,
        sim_time_s=5.0, values={"time_s": 5.0},
    ))
    InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    before = workspace.events_path.read_bytes()
    monkeypatch.setattr(sys, "argv", [
        "inspect_continuous_recovery.py", "--workspace", str(workspace.path),
    ])
    assert main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "VERIFIED_SOURCE_EVIDENCE"
    assert output["evidence"]["last_durable_telemetry_sim_time_s"] == 5.0
    assert output["physical_recovery"]["resume_supported"] is False
    assert workspace.events_path.read_bytes() == before



def test_manual_handoff_rebuild_requires_existing_verified_interruption(tmp_path, monkeypatch, capsys) -> None:
    import sys
    from scripts.inspect_continuous_recovery import main
    from sat_sim.interactive.recovery_handoff import HANDOFF_FILENAME

    spec = _spec("missing-handoff")
    workspace = SessionWorkspace(tmp_path, spec)
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_state(SessionState.INTERRUPTED, 1, 0.0, "PROCESS_RESTART")
    target = workspace.path / HANDOFF_FILENAME
    assert not target.exists()
    monkeypatch.setattr(sys, "argv", [
        "inspect_continuous_recovery.py", "--workspace", str(workspace.path),
    ])
    with pytest.raises(FileNotFoundError):
        main()
    monkeypatch.setattr(sys, "argv", [
        "inspect_continuous_recovery.py", "--workspace", str(workspace.path),
        "--create-missing",
    ])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["physical_recovery"]["resume_supported"] is False
    original = target.read_bytes()
    assert main() == 0
    capsys.readouterr()
    assert target.read_bytes() == original

    healthy = SessionWorkspace(tmp_path, _spec("healthy-no-handoff"))
    healthy.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    monkeypatch.setattr(sys, "argv", [
        "inspect_continuous_recovery.py", "--workspace", str(healthy.path),
        "--create-missing",
    ])
    with pytest.raises(ValueError, match="RECOVERY_REQUIRES_INTERRUPTED_SESSION"):
        main()
