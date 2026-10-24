"""R83j research command timing contract: synthetic checks only, no physics."""
import math
import pytest

from sat_sim.experiments.native_fsw_scheduled_reference_lab import (
    SCHEMA, DURATION_S, FSW_REQUEST_LIMIT_NM, MAX_REFERENCE_DEG,
    STEP_S, SCHEDULE, WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S,
    _reference_mrp, run_native_fsw_reference_arm, validate_schedule
)


def test_static_schedule_is_small_bounded_deterministic_and_physics_only():
    d=validate_schedule()
    assert d["schema"]==SCHEMA
    assert d["reference_limit_deg"]==0.5
    assert d["wheel_hardware_torque_limit_nm"]==0.02
    assert d["request_observation_limit_nm"]==FSW_REQUEST_LIMIT_NM
    assert d["wheel_speed_observation_limit_rad_s"]==WHEEL_SPEED_OBSERVATION_LIMIT_RAD_S
    assert d["safety_independently_certified"] is False
    assert d["flight_qualified"] is False
    assert d["no_rw_fault_injected"] is True
    assert d["diagnostic_model_trained"] is False
    assert len(SCHEDULE)==3
    assert SCHEDULE == ((0.,0.),(1.8,0.5),(3.8,0.))
    assert STEP_S==.2 and DURATION_S==6.
    assert _reference_mrp(.5)[0]==pytest.approx(math.tan(math.radians(.5)/4.))


@pytest.mark.parametrize("bad,part",[
    (((0.,0.),(1.8,1.),(3.8,0.)),"EXCEEDS_BOUND"),
    (((0.,0.),(1.75,.5),(3.8,0.)),"UNALIGNED_REFERENCE_TIME"),
    (((0.,0.),(3.8,.5),(1.8,0.)),"MUST_USE_PREDECLARED"),
    (((0.,0.),(1.8,.5),(3.8,.1)),"MUST_USE_PREDECLARED"),
    (((0.,0.),(1.8,.5)),"UNSUPPORTED_SCHEDULE_LENGTH"),
    (((0.,0.),(float("nan"),.5),(3.8,0.)),"NONFINITE"),
    (((0.,0.),(float("inf"),.5),(3.8,0.)),"NONFINITE"),
    (((0.,0.),(1.8,True),(3.8,0.)),"NONFINITE"),
])
def test_unsupported_or_unbounded_scheduled_commands_fail_closed(bad,part):
    with pytest.raises(ValueError,match=part):
        validate_schedule(bad)


def test_invalid_arm_refused_without_importing_or_running_basilisk():
    with pytest.raises(ValueError, match="UNKNOWN_REFERENCE_ARM"):
        run_native_fsw_reference_arm("unsafe_raw_wheel_motor_override")
