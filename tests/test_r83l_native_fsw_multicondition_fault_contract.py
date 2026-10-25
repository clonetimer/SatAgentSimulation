"""R83l prospective source registry tests. No synthetic dynamics advertised."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import math
import pytest

SOURCE=(Path(__file__).resolve().parents[1] /
        "src/sat_sim/experiments/native_fsw_multicondition_fault_source_r83l.py")
spec=importlib.util.spec_from_file_location("r83l_immutable_source_contract",SOURCE)
assert spec and spec.loader
lab=importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


def test_frozen_balanced_54_case_design_and_no_hidden_roles():
    assert lab.WHEELS==(0,1,2)
    assert len(lab.INITIAL_STATES)==3
    assert len({tuple(a) for a in lab.INITIAL_STATES})==3
    assert lab.REFERENCE_MODES==("zero","pulse")
    assert lab.CAPACITY_MODES==("healthy","loss75","loss95")
    assert lab.CAPACITY_FACTOR=={"healthy":1.,"loss75":.25,"loss95":.05}
    assert len(lab.WHEELS)*len(lab.INITIAL_STATES)*len(lab.REFERENCE_MODES)*len(lab.CAPACITY_MODES)==54
    assert lab.FAULT_ONSET_S==2.4 and lab.STEP_S==.2
    assert lab.NATIVE_WHEEL_LIMIT_NM==.02
    assert lab.MAX_REFERENCE_DEG==.5
    assert lab.FSW_REQUEST_LIMIT_NM==.02
    assert lab.DURATION_S==6.
    assert lab.NATIVE_WHEEL_LIMIT_NM*lab.CAPACITY_FACTOR["loss75"]==pytest.approx(.005)
    assert lab.NATIVE_WHEEL_LIMIT_NM*lab.CAPACITY_FACTOR["loss95"]==pytest.approx(.001)
    assert lab.validate_schedule()["fault_physical_derating_experiment"] is True


def test_expected_axis_guidance_not_hidden_motor_torque_profile():
    amp=math.tan(math.radians(.5)/4.)
    for target in lab.WHEELS:
        ref=lab._reference_mrp(.5,target)
        assert [i for i,v in enumerate(ref) if abs(v)>1e-15]==[target]
        assert ref[target]==pytest.approx(amp)
        assert lab._reference_mrp(0.,target)==[0.,0.,0.]


@pytest.mark.parametrize("wheel,initial,ref,capacity",[
    (3,0,"zero","healthy"),(-1,0,"zero","healthy"),
    (True,0,"zero","healthy"),(0,4,"zero","healthy"),
    (0,-1,"zero","healthy"),(0,False,"zero","healthy"),
    (0,0,"override_direct_motor","healthy"),
    (0,0,"zero","loss99.99"),
    (0,0,"zero","trained_from_validation"),
])
def test_any_not_preregistered_condition_denied_before_native_import(
    wheel,initial,ref,capacity
):
    with pytest.raises(ValueError,match="UNREGISTERED_PHYSICAL_ARM"):
        lab.run_native_parameter_case(wheel=wheel,initial=initial,
                                      reference_mode=ref,capacity_mode=capacity)


def test_real_physics_fields_present_and_protected_from_model_input():
    s=SOURCE.read_text(encoding="utf8")
    for exact in (
        "reactionWheelStateEffector.ReactionWheelStateEffector()",
        "rw.ReactionWheelData[wheel].u_max",
        "rw.rwOutMsgs[wheel].recorder",
        "rw0.u_max=requested_cap",
        "rw.rwMotorCmdInMsg.subscribeTo",
        "sim.ExecuteSimulation()",
        "original_hardware_limit_nm",
        "actual_physical_torque_manifestation",
        "pre_event_max_native_rw_speed_difference_rad_s",
        "fault_truth_only_for_offline_physics_QA",
        "independent_wheel_and_initial_condition_groups",
        "not_a_generalizable_diagnostic_benchmark",
    ):
        assert exact in s



def test_wheel_target_id_not_shadowed_by_allocator_recorder_vector():
    # Regression for a genuine metadata defect disclosed in the FIRST
    # original 54-case artifact: a local 'wheel' assignment accidentally
    # overwrote the original integer target with final motor torque values.
    # Raw original physical source is immutable; later generators must fix it.
    code=SOURCE.read_text(encoding="utf8")
    assert "wheel_commands=_float3(motor_rec.motorTorque[i])" in code
    assert "wheel=_float3(motor_rec.motorTorque[i])" not in code
    assert 'source_fault_target_index_QA_ONLY":wheel' in code
    assert 'native_independent_condition_id":f"rw{wheel}_initial{initial}"' in code
