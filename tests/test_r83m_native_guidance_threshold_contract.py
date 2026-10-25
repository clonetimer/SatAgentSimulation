"""R83m fixed prospective RESEARCH contrast; no proxy physics is allowed."""
import importlib.util
import math
from pathlib import Path

import pytest

SOURCE=(Path(__file__).resolve().parents[1]/
        "src/sat_sim/experiments/native_fsw_guidance_threshold_source_r83m.py")
spec=importlib.util.spec_from_file_location("r83m_native_source_contract",SOURCE)
assert spec is not None and spec.loader is not None
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_strict_protocol_frozen_before_actual_native_runs():
    assert mod.WHEELS==(0,1,2)
    assert len(mod.INITIAL_STATES)==3
    assert mod.REFERENCE_MODES==("zero","pulse05","pulse09")
    assert mod.REFERENCE_AMPLITUDE_DEG=={"zero":0.,"pulse05":.5,"pulse09":.9}
    assert mod.CAPACITY_MODES==("healthy","loss75")
    assert mod.CAPACITY_FACTOR=={"healthy":1.,"loss75":.25}
    assert 3*3*3*2==54
    assert mod.NATIVE_WHEEL_LIMIT_NM==.02
    assert mod.NATIVE_WHEEL_LIMIT_NM*mod.CAPACITY_FACTOR["loss75"]==pytest.approx(.005)
    assert mod.MAX_REFERENCE_DEG==.9
    assert mod.FSW_REQUEST_LIMIT_NM==.02
    assert mod.PROVISIONAL_BODY_RATE_LIMIT_RAD_S==.005
    assert mod.SCHEDULE==((0.,0.),(1.8,.9),(3.8,0.))
    contract=mod.validate_schedule()
    assert contract["diagnostic_model_trained"] is False
    assert contract["safety_independently_certified"] is False
    assert contract["fault_physical_derating_experiment"] is True


@pytest.mark.parametrize("bad",[
    {"wheel":3,"initial":0,"reference_mode":"pulse09","capacity_mode":"loss75"},
    {"wheel":True,"initial":0,"reference_mode":"pulse09","capacity_mode":"loss75"},
    {"wheel":0,"initial":3,"reference_mode":"pulse09","capacity_mode":"loss75"},
    {"wheel":0,"initial":0,"reference_mode":"pulse10","capacity_mode":"loss75"},
    {"wheel":0,"initial":0,"reference_mode":"pulse09","capacity_mode":"loss95"},
    {"wheel":0,"initial":0,"reference_mode":"pulse09","capacity_mode":"severity_tuned_with_VAL"},
])
def test_unregistered_actuator_or_waveform_fails_before_basilisk_import(bad):
    with pytest.raises(ValueError,match="UNREGISTERED_PHYSICAL_ARM"):
        mod.run_native_parameter_case(**bad)


def test_real_native_rw_motor_torque_and_target_wheel_metadata_not_shadowed():
    s=SOURCE.read_text(encoding="utf8")
    for required in (
        "reactionWheelStateEffector.ReactionWheelStateEffector()",
        "rw.ReactionWheelData[wheel].u_max",
        "rw.rwOutMsgs[wheel].recorder",
        "rw0.u_max=requested_cap",
        "wheel_commands=_float3(motor_rec.motorTorque[i])",
        'source_fault_target_index_QA_ONLY":wheel',
        'native_independent_condition_id":f"rw{wheel}_initial{initial}"',
        "rw.rwMotorCmdInMsg.subscribeTo",
        "sim.ExecuteSimulation()",
        "no_protected_validation_test_final_cases_generated",
        "not_a_generalizable_diagnostic_benchmark",
        "max_body_rate<=PROVISIONAL_BODY_RATE_LIMIT_RAD_S",
    ):
        assert required in s
    assert "wheel=_float3(motor_rec.motorTorque[i])" not in s


def test_mrp_reference_is_target_wheel_axis_not_direct_motor_command():
    for wheel in mod.WHEELS:
        for ref,angle in mod.REFERENCE_AMPLITUDE_DEG.items():
            m=mod._reference_mrp(angle,wheel)
            assert len(m)==3
            assert m[wheel]==pytest.approx(math.tan(math.radians(angle)/4.))
            assert all(abs(m[j])<1e-12 for j in mod.WHEELS if j!=wheel)
