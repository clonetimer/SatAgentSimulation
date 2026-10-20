"""Compatibility and integrity tests for streaming continuous Run Bundle sealing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.interactive.continuous_seal import _StreamingPhysicalChecks
from sat_sim.interactive.evidence import seal_interactive_workspace, semantic_trace_hash
from sat_sim.interactive.models import (
    ActorRole, CommandAck, CommandState, InteractiveSessionSpec, SessionState,
    Telecommand, TelemetryFrame,
)
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.validation.physical_checks import evaluate_physical_validation


def _build_workspace(root: Path, *, continuous: bool) -> SessionWorkspace:
    session_id = "continuous-seal" if continuous else "finite-seal"
    spec = InteractiveSessionSpec(
        session_id=session_id,
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={},
        continuous=continuous,
        quantum_s=1.0, paced=False,
        max_sim_time_s=10.0,
    )
    workspace = SessionWorkspace(root, spec)
    workspace.append_state(SessionState.CREATED, 0, 0, "SESSION_CREATED")
    command = Telecommand(
        command_id="command-1", session_id=session_id, session_revision=0,
        operation="adcs.target.set", target="subsystem.adcs",
        parameters={"sigma_rn": [0.1, 0.0, 0.0]},
        actor_id="operator", actor_role=ActorRole.OPERATOR,
        execute_at_sim_time_s=1.0,
    )
    workspace.append_command_ack(command, CommandAck(
        command_id=command.command_id, session_id=session_id,
        state=CommandState.ACKED, sim_time_s=1.0, sequence=0,
    ))
    for index in range(21):
        sim_time_s = float(index) / 2.0
        telemetry = {
            "time_s": sim_time_s,
            "battery_soc": 0.8 - 0.001 * index,
            "adcs.pointing_error_deg": 2.0 - 0.025 * index,
            "orbit.altitude_m": 520000.0,
            "thermal.node.temp_c": 22.5,
            "propulsion.propellant.remaining_kg": 10.0,
            "eps.solar.array_power_w": 20.0,
        }
        workspace.append_telemetry(TelemetryFrame(
            session_id=session_id, stream="runtime.trace", sequence=index,
            sim_time_s=sim_time_s, values=telemetry,
        ))
        if index % 2 == 0:
            workspace.append_telemetry(TelemetryFrame(
                session_id=session_id, stream="whole.fast", sequence=index//2,
                sim_time_s=sim_time_s, values={"battery_soc": telemetry["battery_soc"]},
            ))
    workspace.append_state(SessionState.COMPLETED, 1, 10.0, "OK")
    workspace.finalize_telemetry()
    return workspace


def _read_bundle(ws: SessionWorkspace) -> dict:
    bundle = ws.path / "run_bundle"
    return {
        "trace": json.loads((bundle / "results/semantic_trace.json").read_text(encoding="utf-8")),
        "validation": json.loads((bundle / "validation/validation_outcome.json").read_text(encoding="utf-8")),
        "effects": json.loads((bundle / "results/command_effect_windows.json").read_text(encoding="utf-8")),
    }


def test_continuous_seal_matches_legacy_evidence_semantics(tmp_path) -> None:
    from sat_sim.interactive.telemetry_segments import roll_active
    finite = _build_workspace(tmp_path, continuous=False)
    continuous = _build_workspace(tmp_path, continuous=True)
    # Compare v1 compatibility after the full original journal was rotated
    # out of the active telemetry.jsonl file, then append a new active frame
    # without changing the physical trace/effect content.
    roll_active(continuous.path)
    assert not (continuous.path / "telemetry.jsonl").exists()
    continuous.finalize_telemetry()
    assert seal_interactive_workspace(finite.path)["ok"] is True
    assert seal_interactive_workspace(continuous.path)["ok"] is True
    assert not (continuous.path / ".run_bundle.pending").exists()
    a = _read_bundle(finite)
    b = _read_bundle(continuous)
    assert a["trace"] == b["trace"]
    assert b["trace"]["sha256"] == semantic_trace_hash(b["trace"]["rows"])
    assert a["effects"] == b["effects"]
    assert a["validation"]["result"] == b["validation"]["result"] == "PASS"
    assert a["validation"]["physical_validation"] == b["validation"]["physical_validation"]
    assert seal_interactive_workspace(continuous.path)["ok"] is True


def test_incremental_hf8_checks_match_original_full_trace() -> None:
    rows = [
        {"battery_soc": 0.99, "adcs.pointing_error_deg": 2.0, "propellant_remaining_kg": 10.0},
        {"battery_soc": 0.8, "adcs.pointing_error_deg": 1.5, "propellant_remaining_kg": 9.0},
        {"battery_soc": 0.6, "adcs.pointing_error_deg": 1.6, "propellant_remaining_kg": 9.2},
        {"battery_soc": 0.5, "adcs.pointing_error_deg": 3.0, "propellant_remaining_kg": 9.1},
    ]
    verifier = _StreamingPhysicalChecks()
    for row in rows:
        verifier.add(row)
    assert verifier.result() == evaluate_physical_validation(rows, summary={}, strict=False)


def test_continuous_sealing_rejects_tampered_workspace(tmp_path) -> None:
    ws = _build_workspace(tmp_path, continuous=True)
    source = ws.telemetry_path
    original = source.read_text(encoding="utf-8")
    source.write_text(original.replace("520000.0", "0.0", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="INTERACTIVE_WORKSPACE_INTEGRITY_FAILED"):
        seal_interactive_workspace(ws.path)
    assert not (ws.path / "run_bundle" / "SEALED.json").exists()


def test_continuous_streaming_seal_uses_no_legacy_bulk_loader(monkeypatch, tmp_path) -> None:
    ws = _build_workspace(tmp_path, continuous=True)
    import sat_sim.interactive.evidence as evidence
    monkeypatch.setattr(
        evidence, "_load_workspace",
        lambda path: (_ for _ in ()).throw(AssertionError("bulk loader invoked")),
    )
    assert seal_interactive_workspace(ws.path)["ok"]



def test_failed_continuous_seal_keeps_unpublished_staging_for_recovery(
    monkeypatch, tmp_path,
) -> None:
    ws = _build_workspace(tmp_path, continuous=True)
    import sat_sim.interactive.evidence as evidence
    with monkeypatch.context() as patch:
        patch.setattr(
            evidence, "_validation",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("injected-validation-error")
            ),
        )
        with pytest.raises(RuntimeError, match="injected-validation-error"):
            seal_interactive_workspace(ws.path)
    assert not (ws.path / "run_bundle").exists()
    assert (ws.path / ".run_bundle.pending" / "results" / "semantic_trace.json").exists()
    with pytest.raises(ValueError, match="INTERACTIVE_PARTIAL_STAGING_EXISTS"):
        seal_interactive_workspace(ws.path)
    assert SessionWorkspace.inspect(ws.path).valid



def test_rotated_trace_has_continuous_hash_and_bundle_copy(tmp_path) -> None:
    from sat_sim.interactive import telemetry_segments
    ws = _build_workspace(tmp_path, continuous=True)
    original = ws.telemetry_path.read_bytes()
    rotated = telemetry_segments.roll_active(ws.path)
    assert rotated.exists()
    ws.append_telemetry(TelemetryFrame(
        session_id=ws.path.name, stream="runtime.trace", sequence=21,
        sim_time_s=11.0,
        values={"time_s": 11.0, "battery_soc": 0.7, "adcs.pointing_error_deg": 1.0},
    ))
    ws.finalize_telemetry()
    assert SessionWorkspace.inspect(ws.path).valid
    assert seal_interactive_workspace(ws.path)["ok"]
    bundle_source = ws.path / "run_bundle" / "interactive" / "telemetry.jsonl"
    assert bundle_source.read_bytes() == original + ws.telemetry_path.read_bytes()
    assert len(list(SessionWorkspace.iter_telemetry(ws.path))) == 33



def test_gzip_closed_segment_seals_to_legacy_v1_bundle(tmp_path) -> None:
    import hashlib
    from sat_sim.interactive import telemetry_segments

    finite = _build_workspace(tmp_path, continuous=False)
    continuous = _build_workspace(tmp_path, continuous=True)
    telemetry_segments.roll_active(continuous.path)
    outcome = telemetry_segments.compress_closed(continuous.path)
    assert outcome["compressed"] == 1
    original_logical_hash = telemetry_segments.logical_sha256(continuous.path)
    continuous.finalize_telemetry()
    assert seal_interactive_workspace(finite.path)["ok"]
    assert seal_interactive_workspace(continuous.path)["ok"]
    a, b = _read_bundle(finite), _read_bundle(continuous)
    assert a["trace"] == b["trace"]
    assert a["effects"] == b["effects"]
    assert a["validation"]["physical_validation"] == b["validation"]["physical_validation"]
    legacy_log = continuous.path / "run_bundle" / "interactive" / "telemetry.jsonl"
    assert hashlib.sha256(legacy_log.read_bytes()).hexdigest() == original_logical_hash
    assert SessionWorkspace.inspect(continuous.path).valid
