from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.experience import ExperienceScope, ExperienceStore
from sat_sim.interactive.evidence import replay_interactive_workspace, seal_interactive_workspace
from sat_sim.interactive.manager import InteractiveSessionManager
from sat_sim.interactive.models import (
    CommandAck,
    CommandState,
    InteractiveSessionSpec,
    SessionState,
    Telecommand,
    TelemetryFrame,
)
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.interactive.runtime_factory import build_persistent_runtime
from sat_sim.run_bundle import verify_run_bundle

from test_session_manager import RecordingRuntime


def _spec(session_id: str) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id=session_id,
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={
            "task": {"id": session_id},
            "model": {"capability_id": "whole_spacecraft.unified_native.v1", "target": {"mode": "normal"}},
        },
        quantum_s=0.1,
        paced=False,
        max_sim_time_s=0.2,
    )


def _runtime_builder(spec: InteractiveSessionSpec):
    return RecordingRuntime(), spec.max_sim_time_s


def _interrupted_after_acked_command(root: Path, session_id: str = "interrupted-replay") -> Path:
    spec = _spec(session_id)
    workspace = SessionWorkspace(root, spec)
    workspace.append_state(SessionState.CREATED, 0, 0.0, "SESSION_CREATED")
    workspace.append_state(SessionState.READY, 1, 0.0, "OK")
    workspace.append_state(SessionState.RUNNING, 2, 0.0, "OK")
    command = Telecommand(
        command_id="cmd-1", session_id=session_id, session_revision=2,
        operation="adcs.target.set", target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]}, actor_id="operator-1",
        actor_role="operator", execute_at_sim_time_s=0.1,
    )
    for sequence, state, sim_time in (
        (0, CommandState.RECEIVED, None),
        (1, CommandState.VALIDATED, None),
        (2, CommandState.QUEUED, None),
        (3, CommandState.EXECUTING, 0.0),
        (4, CommandState.ACKED, 0.1),
    ):
        workspace.append_command_ack(command, CommandAck(
            command_id=command.command_id, session_id=session_id, state=state,
            sequence=sequence, sim_time_s=sim_time,
        ))
    workspace.append_telemetry(TelemetryFrame(
        session_id=session_id, stream="runtime.trace", sequence=0,
        sim_time_s=0.1, values={"time_s": 0.1, "value": 0.1},
    ))
    workspace.append_state(SessionState.RUNNING, 2, 0.1, "HEARTBEAT")
    manager = InteractiveSessionManager(enabled=True, workspace_root=root)
    assert manager.interrupted_workspaces[0].state is SessionState.INTERRUPTED
    return workspace.path


def test_completed_session_seals_compatible_bundle_and_effect_window(tmp_path: Path) -> None:
    spec = _spec("sealed-session")
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(spec, RecordingRuntime(), end_time_s=0.2)
    session.prepare()
    command = Telecommand(
        command_id="sealed-command", session_id=spec.session_id, session_revision=session.revision,
        operation="adcs.target.set", target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]}, actor_id="operator-1",
        actor_role="operator", execute_at_sim_time_s=0.1,
    )
    assert session.submit_command(command).state is CommandState.QUEUED
    session.start()
    assert session.wait(1.0)

    bundle = tmp_path / spec.session_id / "run_bundle"
    integrity = verify_run_bundle(bundle)
    assert integrity["ok"] is True
    validation = json.loads((bundle / "validation" / "validation_outcome.json").read_text(encoding="utf-8"))
    effects = json.loads((bundle / "results" / "command_effect_windows.json").read_text(encoding="utf-8"))
    assert validation["result"] == "PASS"
    assert effects["commands"][0]["command_id"] == command.command_id
    assert effects["commands"][0]["frame_count"] > 0
    assert seal_interactive_workspace(tmp_path / spec.session_id)["ok"] is True


def test_only_sealed_pass_bundle_can_enter_experience_store(tmp_path: Path) -> None:
    unfinished = SessionWorkspace(tmp_path / "workspaces", _spec("unfinished-experience"))
    store = ExperienceStore(tmp_path / "experience")
    scope = ExperienceScope(tenant_id="tenant", project_id="project")
    with pytest.raises(ValueError, match="RUN_BUNDLE_INTEGRITY_FAILED"):
        store.capture_run_bundle(unfinished.path, request_text="unfinished", scope=scope)

    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path / "workspaces")
    session = manager.create(_spec("captured-experience"), RecordingRuntime(), end_time_s=0.2)
    session.prepare()
    session.start()
    assert session.wait(1.0)
    record, created = store.capture_run_bundle(
        tmp_path / "workspaces" / "captured-experience" / "run_bundle",
        request_text="interactive engineering simulation", scope=scope,
    )
    assert created is True
    assert record.validation_result == "PASS"
    assert set(record.security_labels) == {"sealed_run_bundle", "hash_verified"}


def test_interrupted_workspace_replays_and_appends_recovery_epochs(tmp_path: Path) -> None:
    workspace = _interrupted_after_acked_command(tmp_path)
    first = replay_interactive_workspace(workspace, runtime_builder=_runtime_builder)
    second = replay_interactive_workspace(workspace, runtime_builder=_runtime_builder)
    assert first["result"] == second["result"] == "PASS"
    assert first["semantic_trace_sha256"] == second["semantic_trace_sha256"]
    assert (first["epoch"], second["epoch"]) == (1, 2)
    assert SessionWorkspace.inspect(workspace).state is SessionState.INTERRUPTED
    assert not (workspace / "run_bundle" / "SEALED.json").exists()


def test_tampering_and_semantic_replay_mismatch_fail_closed(tmp_path: Path) -> None:
    tampered = _interrupted_after_acked_command(tmp_path / "tampered", "tampered-session")
    command_log = tampered / "command_events.jsonl"
    command_log.write_text(command_log.read_text(encoding="utf-8").replace("operator-1", "operator-x", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="INTERACTIVE_WORKSPACE_INTEGRITY_FAILED"):
        replay_interactive_workspace(tampered, runtime_builder=_runtime_builder)

    mismatched = _interrupted_after_acked_command(tmp_path / "mismatch", "mismatch-session")

    class MismatchRuntime(RecordingRuntime):
        def advance_to(self, stop_time_s: float):
            self._time = stop_time_s
            return ({"time_s": stop_time_s, "value": stop_time_s + 1.0},)

    with pytest.raises(ValueError, match="INTERACTIVE_REPLAY_MISMATCH"):
        replay_interactive_workspace(mismatched, runtime_builder=lambda spec: (MismatchRuntime(), spec.max_sim_time_s))
    assert not list(mismatched.glob("recovery_epoch-*.json"))


@pytest.mark.parametrize("capability_id", [
    "whole_spacecraft.unified_native.v1",
    "subsystem.adcs_unified_native.v1",
    "subsystem.eps.unified_native.v1",
    "subsystem.comm_data.unified_native.v1",
])
def test_four_main_capabilities_have_deterministic_native_replay(tmp_path: Path, capability_id: str) -> None:
    session_id = "native-" + capability_id.replace(".", "-")
    spec = InteractiveSessionSpec(
        session_id=session_id, capability_id=capability_id,
        task_spec={
            "task": {"id": session_id},
            "model": {"capability_id": capability_id, "target": {"mode": "normal"}},
            "simulation": {"duration_s": 0.2, "solver": {"step_s": 0.1}},
            "parameters": {"values": {}},
        },
        quantum_s=0.1, paced=False, max_sim_time_s=0.2,
    )
    runtime, end_time_s = build_persistent_runtime(spec)
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(spec, runtime, end_time_s=end_time_s)
    session.prepare()
    session.start()
    assert session.wait(5.0)
    assert session.state is SessionState.COMPLETED

    replay = replay_interactive_workspace(tmp_path / session_id)
    assert replay["result"] == "PASS"
    assert replay["command_count"] == 0


def test_native_eps_replay_includes_acked_command_log(tmp_path: Path) -> None:
    spec = InteractiveSessionSpec(
        session_id="native-eps-command-replay", capability_id="subsystem.eps.unified_native.v1",
        task_spec={
            "task": {"id": "native-eps-command-replay"},
            "model": {"capability_id": "subsystem.eps.unified_native.v1", "target": {"mode": "normal"}},
            "simulation": {"duration_s": 0.5, "solver": {"step_s": 0.1}},
            "parameters": {"values": {"initial_soc": 0.8}},
        },
        quantum_s=0.1, paced=False, max_sim_time_s=0.5,
    )
    runtime, end_time_s = build_persistent_runtime(spec)
    manager = InteractiveSessionManager(enabled=True, workspace_root=tmp_path)
    session = manager.create(spec, runtime, end_time_s=end_time_s)
    session.prepare()
    command = Telecommand(
        command_id="eps-load-replay", session_id=spec.session_id, session_revision=session.revision,
        operation="eps.load.set", target="subsystem.eps",
        parameters={"load_id": "payload", "enabled": False}, actor_id="operator-1",
        actor_role="operator", execute_at_sim_time_s=0.2,
    )
    assert session.submit_command(command).state is CommandState.QUEUED
    session.start()
    assert session.wait(5.0)

    replay = replay_interactive_workspace(tmp_path / spec.session_id)
    assert replay["result"] == "PASS"
    assert replay["command_count"] == 1
