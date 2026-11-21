"""DT-2/BE-15: RW driver deadband must change the real Basilisk wheel dynamics.

The deadband is an optional project SysModel upstream of official RW dynamics;
it is not a claim about actual motor hardware or an OEM internal deadband.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)
from sat_sim.native_rw_preflight import validate_native_rw_settings

KEY = "rw_command_deadband_nm"


def test_rw_deadband_registry_form_and_disabled_baseline():
    assert ContinuousInitialConfiguration().native_values() == {}
    catalog = initial_configuration_catalog()
    fields = {f["name"]: f for group in catalog["groups"] if group["group"] == "adcs"
              for f in group["fields"]}
    assert fields[KEY]["default"] == 0.0
    assert fields[KEY]["unit"] == "N·m"
    assert ContinuousInitialConfiguration.model_validate(
        {"adcs": {KEY: 0.02}}).native_values()[KEY] == pytest.approx(0.02)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(
        encoding="utf-8")
    assert f'data-native-key="{KEY}"' in html
    assert "项目 SysModel" in html
    validate_native_rw_settings({})
    validate_native_rw_settings({KEY: 0.0})
    validate_native_rw_settings({KEY: 0.2})
    assert fields["rw_motor_response_time_constant_s"]["default"] == 0.0
    assert fields["rw_motor_response_time_constant_s"]["unit"] == "s"
    validate_native_rw_settings({"rw_motor_response_time_constant_s": 0.0})
    validate_native_rw_settings({"rw_motor_response_time_constant_s": 5.0})


@pytest.mark.parametrize("invalid", [-0.001, .201, float("nan"), float("inf"),
                                      True, "0.01", None])
def test_invalid_deadband_fails_closed_before_basilisk(invalid):
    with pytest.raises(ValueError, match="NATIVE_RW_COMMAND_DEADBAND_INVALID"):
        validate_native_rw_settings({KEY: invalid})
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": {KEY: invalid}})


def test_real_basilisk_deadband_changes_actual_motor_torque_and_rw_speed():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(values):
        result = adapter.run(_short_spec(
            WHOLE_ID, duration_s=25.0, values={
                "initial_pointing_error_deg": 35.0, **values,
            }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        return list(result.trace_rows)

    baseline = run({})
    explicit_zero = run({KEY: 0.0})
    deadband = run({KEY: 0.2})
    assert len(baseline) == len(explicit_zero) == len(deadband) >= 5
    assert [x["time_s"] for x in baseline] == [x["time_s"] for x in deadband]
    for a, b in zip(baseline, explicit_zero):
        for wheel in range(3):
            for stem in ("adcs.control.applied_torque_nm_", "adcs.rw.speed_rad_s_"):
                assert b[f"{stem}{wheel}"] == pytest.approx(
                    a[f"{stem}{wheel}"], rel=1e-12, abs=1e-12)
    # The official RW hardware+driver limit is at most 0.2 N.m. At
    # deadband=0.2 N.m the bounded *drive* is therefore zero, while the
    # controller still publishes nonzero raw torque and native wheel speed
    # responds differently from the unchanged baseline.
    for row in deadband:
        for wheel in range(3):
            assert abs(float(row[f"adcs.control.applied_torque_nm_{wheel}"])) < 1e-12
    assert max(abs(float(row["adcs.rw.command_torque_nm_0"]))
               for row in deadband[1:]) > 1e-5
    delta = max(abs(float(a["adcs.rw.speed_rad_s_0"]) -
                    float(b["adcs.rw.speed_rad_s_0"]))
                for a, b in zip(baseline[1:], deadband[1:]))
    print(f"DT2_RW_DEADBAND_NATIVE_AB_MEASURED wheel0_speed_delta_max_rad_s={delta:.9e} "
          "fixture_deadband_nm=0.2 fixture_duration_s=25 zero_default_nm=0", flush=True)
    assert math.isfinite(delta) and delta > 1e-5


def test_real_four_rw_actuation_is_suppressed_by_explicit_deadband():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    result = get_adapter_for_capability(WHOLE_ID).run(_short_spec(
        WHOLE_ID, duration_s=4.0, values={
            "rw_configuration": "pyramid_4",
            "initial_pointing_error_deg": 35.0, KEY: 0.2,
        }))
    assert len(result.trace_rows) >= 2
    for row in result.trace_rows:
        for wheel in range(4):
            assert abs(float(row[f"adcs.control.applied_torque_nm_{wheel}"])) < 1e-12



def _scheduled_rw_command_snapshot(commands, speeds, *, deadband_nm, events=(),
                                   current_limits_a=None, response_time_constant_s=0.0):
    """Read the actual Basilisk ArrayMotorTorqueMsg output of the project driver."""
    pytest.importorskip("Basilisk")
    from Basilisk.architecture import messaging
    from sat_sim.bsk_engine.project_native_modules import ReactionWheelCommandFaultManager

    manager = ReactionWheelCommandFaultManager(
        events=events, wheel_count=3, command_deadband_nm=deadband_nm,
        wheel_current_limits_a=current_limits_a,
        motor_response_time_constant_s=response_time_constant_s,
    )
    cmd_payload = messaging.ArrayMotorTorqueMsgPayload()
    values = list(cmd_payload.motorTorque)
    values[:3] = [float(x) for x in commands]
    cmd_payload.motorTorque = values
    cmd_msg = messaging.ArrayMotorTorqueMsg().write(cmd_payload)

    speed_payload = messaging.RWSpeedMsgPayload()
    wheel_speeds = list(speed_payload.wheelSpeeds)
    wheel_speeds[:3] = [float(x) for x in speeds]
    speed_payload.wheelSpeeds = wheel_speeds
    speed_msg = messaging.RWSpeedMsg().write(speed_payload)

    manager.commandInMsg.subscribeTo(cmd_msg)
    manager.speedInMsg.subscribeTo(speed_msg)
    manager.UpdateState(0)
    applied = tuple(float(x) for x in manager.commandOutMsg.read().motorTorque[:3])
    available = tuple(float(x) for x in manager.torqueLimitOutMsg.read().motorTorque[:3])
    return applied, available


def test_partial_deadband_suppresses_only_subthreshold_native_motor_messages():
    """Exercise the command-path message directly, not a cosmetic telemetry field."""
    applied, available = _scheduled_rw_command_snapshot(
        (0.04, 0.08, -0.12), (0., 0., 0.), deadband_nm=0.05)
    assert applied == pytest.approx((0., 0.08, -0.12), abs=1e-12)
    assert available == pytest.approx((0.2, 0.2, 0.2), abs=1e-12)

    # Pre-clamp before deadband: even a large FSW torque request cannot
    # bypass a deadband larger than the physical driver's current authority.
    bounded, limited = _scheduled_rw_command_snapshot(
        (0.15, 0., 0.), (0., 0., 0.), deadband_nm=0.001,
        current_limits_a=(0.005, 10., 10.))
    assert bounded == pytest.approx((0., 0., 0.), abs=1e-12)
    assert limited == pytest.approx((0.0001, 0.2, 0.2), abs=1e-12)
    print("DT2_RW_PARTIAL_DEADBAND_MSG_VERIFIED "
          "threshold_nm=0.05 input_nm=0.04,0.08,-0.12 "
          "applied_nm=0,0.08,-0.12 current_clamped_bypass=false", flush=True)


@pytest.mark.parametrize("effect,parameters,command,speed,expected,authority", [
    ("adcs_rw_jamming", {"wheel_index": 0, "brake_torque_nm": 0.1},
     0., 25., -0.1, 0.),
    ("adcs_reaction_wheel_speed_limit",
     {"wheel_index": 0, "max_speed_rad_s": 10., "overspeed_brake_gain_nms": 0.02},
     0., 25., -0.2, 0.2),
    ("adcs_rw_motor_failure", {"wheel_index": 0},
     0.12, 25., 0., 0.),
])
def test_deadband_never_masks_jam_or_overspeed_braking_and_respects_motor_open(
        effect, parameters, command, speed, expected, authority):
    """A new drive nonideality must not silently suppress existing fault actions."""
    from sat_sim.bsk_engine.types import BSKEventSpec

    fault = BSKEventSpec(
        event_id="dt2_deadband_event", category="fault", effect=effect,
        target="adcs", start_s=0., end_s=10., parameters=parameters)
    applied, available = _scheduled_rw_command_snapshot(
        (command, 0., 0.), (speed, 0., 0.),
        deadband_nm=0.2, events=(fault,))
    assert applied[0] == pytest.approx(expected, abs=1e-12)
    assert available[0] == pytest.approx(authority, abs=1e-12)
    assert applied[1:] == pytest.approx((0., 0.), abs=1e-12)



@pytest.mark.parametrize("invalid", [-0.001, 5.001, float("nan"), float("inf"),
                                      True, "0.5", None])
def test_motor_response_time_constant_rejects_invalid_native_and_dialogue_values(invalid):
    with pytest.raises(ValueError, match="NATIVE_RW_RESPONSE_TIME_CONSTANT_INVALID"):
        validate_native_rw_settings({"rw_motor_response_time_constant_s": invalid})
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({
            "adcs": {"rw_motor_response_time_constant_s": invalid}
        })


def _scheduled_rw_lag_trace(commands, times_nanos, *, response_time_constant_s, events=()):
    """Run the actual project SysModel against Basilisk message readers/writers."""
    pytest.importorskip("Basilisk")
    from Basilisk.architecture import messaging
    from sat_sim.bsk_engine.project_native_modules import ReactionWheelCommandFaultManager

    manager = ReactionWheelCommandFaultManager(
        events=events, wheel_count=3,
        motor_response_time_constant_s=response_time_constant_s,
    )
    command_msg = messaging.ArrayMotorTorqueMsg()
    speed_msg = messaging.RWSpeedMsg()
    manager.commandInMsg.subscribeTo(command_msg)
    manager.speedInMsg.subscribeTo(speed_msg)
    outputs = []
    for command, now in zip(commands, times_nanos, strict=True):
        payload = messaging.ArrayMotorTorqueMsgPayload()
        motor_torque = list(payload.motorTorque)
        motor_torque[:3] = [float(command), 0.0, 0.0]
        payload.motorTorque = motor_torque
        command_msg.write(payload, int(now), 0)
        speed = messaging.RWSpeedMsgPayload()
        speed_msg.write(speed, int(now), 0)
        manager.UpdateState(int(now))
        outputs.append(float(manager.commandOutMsg.read().motorTorque[0]))
    return outputs


def test_motor_response_time_constant_matches_exact_scheduler_time_first_order_solution():
    tau = 1.0
    commands = [0.1, 0.1, 0.1, -0.1]
    times = [0, 250_000_000, 1_000_000_000, 2_500_000_000]
    actual = _scheduled_rw_lag_trace(commands, times, response_time_constant_s=tau)
    expected = []
    state = 0.0
    previous = times[0]
    for index, (command, now) in enumerate(zip(commands, times, strict=True)):
        dt_s = 0.0 if index == 0 else (now - previous) * 1e-9
        alpha = -math.expm1(-dt_s / tau) if dt_s > 0.0 else 0.0
        state += alpha * (command - state)
        expected.append(state)
        previous = now
    assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert actual[0] == 0.0
    assert 0.0 < actual[1] < actual[2] < 0.1
    assert actual[3] < actual[2]
    print("DT2_RW_FIRST_ORDER_RESPONSE_NATIVE_MSG_VERIFIED "
          f"tau_s={tau} times_s=0,0.25,1,2.5 output_nm="
          + ",".join(f"{x:.9e}" for x in actual), flush=True)


def test_motor_response_lag_rejects_nonmonotonic_scheduler_time():
    with pytest.raises(ValueError, match="NATIVE_RW_RESPONSE_TIME_NOT_MONOTONIC"):
        _scheduled_rw_lag_trace(
            [0.1, 0.1, 0.1], [0, 1_000_000_000, 500_000_000],
            response_time_constant_s=1.0)


def test_motor_response_lag_fault_overrides_bypass_filter_and_clear_drive_memory():
    from sat_sim.bsk_engine.types import BSKEventSpec

    jam = BSKEventSpec(
        event_id="dt2_lag_jam", category="fault", effect="adcs_rw_jamming",
        target="adcs", start_s=0., end_s=2.,
        parameters={"wheel_index": 0, "brake_torque_nm": 0.1})
    applied, authority = _scheduled_rw_command_snapshot(
        (0.1, 0., 0.), (25., 0., 0.), deadband_nm=0.0,
        events=(jam,), response_time_constant_s=1.0)
    assert applied[0] == pytest.approx(-0.1, abs=1e-12)
    assert authority[0] == pytest.approx(0.0, abs=1e-12)

    motor_open = BSKEventSpec(
        event_id="dt2_lag_motor_open", category="fault",
        effect="adcs_rw_motor_failure", target="adcs",
        start_s=2.0, end_s=2.5, parameters={"wheel_index": 0})
    trace = _scheduled_rw_lag_trace(
        [0.1, 0.1, 0.1, 0.1],
        [0, 1_000_000_000, 2_000_000_000, 3_000_000_000],
        response_time_constant_s=1.0, events=(motor_open,))
    assert trace[0] == pytest.approx(0.0, abs=1e-12)
    assert trace[1] == pytest.approx(0.06321205588285576, rel=1e-12)
    assert trace[2] == pytest.approx(0.0, abs=1e-12)
    assert trace[3] == pytest.approx(0.06321205588285576, rel=1e-12)


def test_real_basilisk_motor_response_lag_changes_applied_torque_and_native_wheel_speed():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(values):
        result = adapter.run(_short_spec(
            WHOLE_ID, duration_s=25.0, values={
                "initial_pointing_error_deg": 35.0, **values,
            }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "reaction_wheels" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        return list(result.trace_rows)

    baseline = run({})
    explicit_zero = run({"rw_motor_response_time_constant_s": 0.0})
    lagged = run({"rw_motor_response_time_constant_s": 0.5})
    assert len(baseline) == len(explicit_zero) == len(lagged) >= 5
    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in lagged]
    for a, b in zip(baseline, explicit_zero, strict=True):
        for wheel in range(3):
            for key in ("adcs.control.applied_torque_nm_", "adcs.rw.speed_rad_s_"):
                assert b[f"{key}{wheel}"] == pytest.approx(
                    a[f"{key}{wheel}"], rel=1e-12, abs=1e-12)
    raw = max(abs(float(row["adcs.rw.command_torque_nm_0"])) for row in lagged[1:])
    torque_delta = max(abs(float(a["adcs.control.applied_torque_nm_0"]) -
                            float(b["adcs.control.applied_torque_nm_0"]))
                       for a, b in zip(baseline[1:], lagged[1:], strict=True))
    speed_delta = max(abs(float(a["adcs.rw.speed_rad_s_0"]) -
                           float(b["adcs.rw.speed_rad_s_0"]))
                      for a, b in zip(baseline[1:], lagged[1:], strict=True))
    assert raw > 1e-5
    assert math.isfinite(torque_delta) and torque_delta > 1e-5
    assert math.isfinite(speed_delta) and speed_delta > 1e-5
    print(f"DT2_RW_RESPONSE_LAG_NATIVE_AB_MEASURED tau_s=0.5 "
          f"applied_torque_delta_max_nm={torque_delta:.9e} "
          f"rw0_speed_delta_max_rad_s={speed_delta:.9e} "
          "duration_s=25 step_s=0.2 zero_default=0", flush=True)
