from __future__ import annotations

import pytest

from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID, UnifiedRuntimeConfig
from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.models import ActorRole, CommandState, InteractiveSessionSpec, Telecommand
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime


def _config(duration_s: float = 2.0) -> UnifiedRuntimeConfig:
    return UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=duration_s,
        step_s=0.1,
        sample_s=0.5,
        adcs_only=False,
        values={},
        events=(),
    )


def _command(operation: str, target: str, parameters: dict, command_id: str = "command") -> Telecommand:
    return Telecommand(
        command_id=command_id, session_id="adapter-session", session_revision=2,
        operation=operation, target=target, parameters=parameters,
        actor_id="operator-1", actor_role="operator", execute_at_sim_time_s=0.5,
    )


def _fault(operation: str, target: str, fault_id: str, severity: float = 1.0) -> Telecommand:
    return _command(operation, target, {"fault_id": fault_id, "severity": severity}, f"fault-{fault_id}").model_copy(
        update={"actor_role": ActorRole.FAULT_OPERATOR}
    )


def test_registered_adcs_eps_and_comm_commands_change_real_unified_runtime_inputs() -> None:
    runtime = UnifiedPersistentRuntime(_config())
    runtime.prepare()
    baseline = runtime.advance_to(0.5)[-1]
    runtime.apply_command(_command("adcs.target.set", "subsystem.adcs", {"sigma_rn": [0.2, 0.0, 0.0]}, "adcs"))
    adcs = runtime.advance_to(1.0)[-1]
    assert adcs["adcs.pointing_error_deg"] > baseline["adcs.pointing_error_deg"]

    runtime.apply_command(_command("eps.load.set", "subsystem.eps", {"load_id": "payload", "enabled": False}, "eps"))
    eps = runtime.advance_to(1.5)[-1]
    assert eps["eps.pdu.payload_enabled"] == 0
    assert eps["payload.active"] == 0

    runtime.apply_command(_command("comm_data.downlink.set", "subsystem.comm_data", {"enabled": False, "rate_bps": 0.0}, "comm"))
    comm = runtime.advance_to(2.0)[-1]
    assert comm["eps.pdu.comm_enabled"] == 0
    assert comm["comm.command_permitted"] == 0
    metadata = runtime.finalize()
    assert metadata["summary"]["execution_status"] == "PASS"


def test_data_generation_command_is_a_single_segment_request() -> None:
    runtime = UnifiedPersistentRuntime(_config(1.5))
    runtime.prepare()
    runtime.advance_to(0.5)
    runtime.apply_command(_command("comm_data.generate", "subsystem.comm_data", {"bits": 1000}, "generate"))
    generated = runtime.advance_to(1.0)[-1]
    resumed = runtime.advance_to(1.5)[-1]
    assert generated["payload.generated_bps"] == pytest.approx(2000.0)
    assert resumed["payload.generated_bps"] == pytest.approx(2_500_000.0)
    runtime.finalize()


def test_session_ack_is_emitted_after_real_effect_quantum() -> None:
    runtime = UnifiedPersistentRuntime(_config(1.0))
    spec = InteractiveSessionSpec(
        session_id="adapter-session", capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        task_spec={}, quantum_s=0.5, paced=False, max_sim_time_s=1.0,
    )
    session = InteractiveSession(spec, runtime, end_time_s=1.0)
    session.prepare()
    command = _command("eps.load.set", "subsystem.eps", {"load_id": "payload", "enabled": False}, "session-eps")
    assert session.submit_command(command).state is CommandState.QUEUED
    session.start()
    assert session.wait(2.0)
    ack = session.command_acks(command.command_id)[-1]
    assert ack.state is CommandState.ACKED
    assert ack.sim_time_s == pytest.approx(0.5)
    frames = session.telemetry.read_after("runtime.trace").frames
    effect = next(frame for frame in frames if frame.sim_time_s >= ack.sim_time_s)
    assert effect.values["eps.pdu.payload_enabled"] == 0


@pytest.mark.parametrize(
    ("command", "field", "expected"),
    [
        (_fault("adcs.fault.inject", "subsystem.adcs", "rw_jam"), "adcs.rw.effective_max_torque_nm_0", 0.0),
        (_fault("eps.fault.inject", "subsystem.eps", "battery_capacity_loss", 0.5), "eps.battery_capacity_ratio", 0.5),
        (_fault("comm_data.fault.inject", "subsystem.comm_data", "transmitter_loss"), "comm.command_permitted", 0),
    ],
)
def test_approved_runtime_fault_commands_use_existing_event_modules(command: Telecommand, field: str, expected: float) -> None:
    runtime = UnifiedPersistentRuntime(_config(1.0))
    runtime.prepare()
    runtime.advance_to(0.5)
    runtime.apply_command(command)
    row = runtime.advance_to(1.0)[-1]
    metadata = runtime.finalize()
    assert row["label.fault_active"] is True
    assert row[field] == pytest.approx(expected)
    assert metadata["summary"]["runtime_event_count"] == 1
    assert metadata["metadata"]["fault_environment"]["episode_count"] == 1


def test_whole_mode_and_adcs_control_commands_use_registered_model_inputs() -> None:
    runtime = UnifiedPersistentRuntime(_config(1.5))
    runtime.prepare()
    runtime.advance_to(0.5)
    runtime.apply_command(_command("session.mode.set", "whole_spacecraft", {"mode": "safe"}, "safe-mode"))
    safe = runtime.advance_to(1.0)[-1]
    assert safe["eps.pdu.payload_enabled"] == 0
    assert safe["eps.pdu.comm_enabled"] == 0
    assert safe["eps.pdu.heater_enabled"] == 0
    runtime.apply_command(_command("adcs.control.set", "subsystem.adcs", {"enabled": False}, "adcs-off"))
    disabled = runtime.advance_to(1.5)[-1]
    assert disabled["adcs.rw.command_torque_nm_0"] == pytest.approx(0.0, abs=1e-12)
    runtime.finalize()


def test_eps_protection_command_changes_safe_mode_threshold_not_postprocessed_output() -> None:
    config = _config(1.0)
    config = UnifiedRuntimeConfig(**{**config.__dict__, "values": {"initial_soc": 0.1, "min_operational_soc": 0.2}})
    runtime = UnifiedPersistentRuntime(config)
    runtime.prepare()
    baseline = runtime.advance_to(0.5)[-1]
    assert baseline["label.power_safe_mode_engaged"] is True
    runtime.apply_command(_command("eps.protection.set", "subsystem.eps", {"enabled": False}, "protection-off"))
    changed = runtime.advance_to(1.0)[-1]
    assert changed["label.power_safe_mode_engaged"] is False
    runtime.finalize()
