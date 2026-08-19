from __future__ import annotations

import pytest

from sat_sim.bsk_engine.unified_native import ADCS_UNIFIED_CAPABILITY_ID, UnifiedRuntimeConfig
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import ActorRole, CommandState, InteractiveSessionSpec, Telecommand
from sat_sim.interactive.subsystem_runtimes import CommDataPersistentRuntime, EpsPersistentRuntime
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
from subsystems.comm_data.runner import run_comm_data_basilisk_scenario
from subsystems.comm_data.schemas import CommDataBasiliskConfig
from subsystems.eps.runner import run_eps_basilisk_scenario
from subsystems.eps.schemas import EPSBasiliskConfig


def _command(session: InteractiveSession, operation: str, target: str, parameters: dict) -> Telecommand:
    return Telecommand(
        command_id=f"{operation}:test", session_id=session.spec.session_id, session_revision=session.revision,
        operation=operation, target=target, parameters=parameters,
        actor_id="operator-1", actor_role="operator", execute_at_sim_time_s=0.5,
    )


def test_eps_main_capability_segmented_runtime_matches_one_shot_runner() -> None:
    config = EPSBasiliskConfig(duration_s=2.0, step_s=0.5, initial_soc=0.8)
    _, one_shot, _ = run_eps_basilisk_scenario(config, return_context=True)
    runtime = EpsPersistentRuntime(config)
    runtime.prepare()
    rows = tuple(row for stop in (0.5, 1.0, 1.5, 2.0) for row in runtime.advance_to(stop))
    runtime.finalize()
    assert [row["time_s"] for row in rows] == [row.time_s for row in one_shot]
    for actual, expected in zip(rows, one_shot, strict=True):
        assert actual["eps.battery_soc"] == pytest.approx(expected.battery_soc, rel=1e-12)
        assert actual["eps.net_power_w"] == pytest.approx(expected.net_power_w, rel=1e-12)


def test_comm_data_main_capability_segmented_runtime_matches_one_shot_runner() -> None:
    config = CommDataBasiliskConfig(duration_s=2.0, step_s=0.5, native_storage_drain_enabled=True)
    _, one_shot, _ = run_comm_data_basilisk_scenario(config, return_context=True)
    runtime = CommDataPersistentRuntime(config)
    runtime.prepare()
    rows = tuple(row for stop in (0.5, 1.0, 1.5, 2.0) for row in runtime.advance_to(stop))
    runtime.finalize()
    assert [row["time_s"] for row in rows] == [row.time_s for row in one_shot]
    for actual, expected in zip(rows, one_shot, strict=True):
        assert actual["comm_data.storage_level_bits"] == pytest.approx(expected.storage_level_bits, rel=1e-12)
        assert actual["comm_data.instrument_baud_bps"] == pytest.approx(expected.instrument_baud_bps, rel=1e-12)


@pytest.mark.parametrize("kind", ["eps", "comm_data"])
def test_subsystem_main_capability_session_executes_registered_command_with_ack(kind: str) -> None:
    if kind == "eps":
        runtime = EpsPersistentRuntime(EPSBasiliskConfig(duration_s=1.0, step_s=0.5, initial_soc=0.8))
        capability_id = runtime.capability_id
        operation, target = "eps.load.set", "subsystem.eps"
        parameters = {"load_id": "payload", "enabled": False}
        effect_field = "eps.payload_load_enabled_w"
    else:
        runtime = CommDataPersistentRuntime(CommDataBasiliskConfig(duration_s=1.0, step_s=0.5, native_storage_drain_enabled=True))
        capability_id = runtime.capability_id
        operation, target = "comm_data.downlink.set", "subsystem.comm_data"
        parameters = {"enabled": False, "rate_bps": 0.0}
        effect_field = "comm_data.transmitter_storage_node_baud_bps"
    spec = InteractiveSessionSpec(
        session_id=f"{kind}-main-session", capability_id=capability_id,
        task_spec={}, quantum_s=0.5, paced=False, max_sim_time_s=1.0,
    )
    session = InteractiveSession(spec, runtime, end_time_s=1.0)
    session.prepare()
    command = _command(session, operation, target, parameters)
    assert session.submit_command(command).state is CommandState.QUEUED
    session.start()
    assert session.wait(2.0)
    assert session.command_acks(command.command_id)[-1].state is CommandState.ACKED
    effect = next(frame for frame in session.telemetry.read_after("runtime.trace").frames if frame.sim_time_s >= 0.5)
    assert effect.values[effect_field] == pytest.approx(0.0)


def test_adcs_main_capability_session_executes_target_command() -> None:
    config = UnifiedRuntimeConfig(
        capability_id=ADCS_UNIFIED_CAPABILITY_ID, duration_s=1.0, step_s=0.1,
        sample_s=0.5, adcs_only=True, values={}, events=(),
    )
    baseline = UnifiedPersistentRuntime(config)
    baseline.prepare()
    baseline.advance_to(0.5)
    baseline_final = baseline.advance_to(1.0)[-1]
    baseline.finalize()

    runtime = UnifiedPersistentRuntime(config)
    spec = InteractiveSessionSpec(
        session_id="adcs-main-session", capability_id=ADCS_UNIFIED_CAPABILITY_ID,
        task_spec={}, quantum_s=0.5, paced=False, max_sim_time_s=1.0,
    )
    session = InteractiveSession(spec, runtime, end_time_s=1.0)
    session.prepare()
    command = _command(session, "adcs.target.set", "subsystem.adcs", {"sigma_rn": [0.2, 0.0, 0.0]})
    session.submit_command(command)
    session.start()
    assert session.wait(2.0)
    assert session.command_acks(command.command_id)[-1].state is CommandState.ACKED
    frames = session.telemetry.read_after("runtime.trace").frames
    assert frames[-1].values["adcs.pointing_error_deg"] != pytest.approx(
        baseline_final["adcs.pointing_error_deg"], abs=1e-6,
    )


def test_subsystem_fault_commands_change_eps_and_comm_native_inputs() -> None:
    eps = EpsPersistentRuntime(EPSBasiliskConfig(duration_s=1.0, step_s=0.5, initial_soc=0.8))
    eps.prepare()
    eps.advance_to(0.5)
    eps.apply_command(Telecommand(
        command_id="eps-fault", session_id="eps", session_revision=0,
        operation="eps.fault.inject", target="subsystem.eps",
        parameters={"fault_id": "battery_capacity_loss", "severity": 0.5},
        actor_id="fault-operator", actor_role=ActorRole.FAULT_OPERATOR, execute_at_sim_time_s=0.5,
    ))
    eps_row = eps.advance_to(1.0)[-1]
    assert eps_row["label.fault_active"] is True
    assert eps_row["eps.battery_capacity_j"] == pytest.approx(0.5 * 120.0 * 3600.0)
    eps.finalize()

    comm = CommDataPersistentRuntime(CommDataBasiliskConfig(duration_s=1.5, step_s=0.5, native_storage_drain_enabled=True))
    comm.prepare()
    comm.advance_to(0.5)
    comm.apply_command(Telecommand(
        command_id="comm-generate", session_id="comm", session_revision=0,
        operation="comm_data.generate", target="subsystem.comm_data", parameters={"bits": 1000},
        actor_id="operator", actor_role=ActorRole.OPERATOR, execute_at_sim_time_s=0.5,
    ))
    generated = comm.advance_to(1.0)[-1]
    assert generated["comm_data.instrument_baud_bps"] == pytest.approx(2000.0)
    comm.apply_command(Telecommand(
        command_id="comm-fault", session_id="comm", session_revision=0,
        operation="comm_data.fault.inject", target="subsystem.comm_data",
        parameters={"fault_id": "transmitter_loss", "severity": 1.0},
        actor_id="fault-operator", actor_role=ActorRole.FAULT_OPERATOR, execute_at_sim_time_s=1.0,
    ))
    faulted = comm.advance_to(1.5)[-1]
    assert faulted["label.fault_active"] is True
    assert faulted["comm_data.transmitter_storage_node_baud_bps"] == pytest.approx(0.0)
    comm.finalize()
