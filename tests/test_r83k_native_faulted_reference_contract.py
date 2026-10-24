"""R83k frozen paired ORIGINAL-Basilisk experiment contract; NOT physics fixture."""
from pathlib import Path
import importlib.util
import math
import pytest

SOURCE=Path(__file__).resolve().parents[1]/"src/sat_sim/experiments/native_fsw_rw_faulted_reference_pilot.py"
spec=importlib.util.spec_from_file_location("r83k_native_original_contract",SOURCE)
assert spec and spec.loader
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_original_native_4arm_design_predeclared_and_bounded():
    assert m.TRIAL_ARMS == (
        "zero_healthy","step_healthy","zero_derated","step_derated")
    assert m.FAULTED_RW_INDEX==0
    assert m.FAULT_ONSET_S==2.4
    assert m.NATIVE_WHEEL_LIMIT_NM==.02
    assert m.FAULTED_RW_CAP_NM==.0005
    assert m.FSW_REQUEST_LIMIT_NM==.02
    assert m.MAX_REFERENCE_DEG==.5
    assert m.STEP_S==.2
    assert m.DURATION_S==6.
    assert m.FAULTED_RW_CAP_NM/m.NATIVE_WHEEL_LIMIT_NM==pytest.approx(.025)
    schedule=m.validate_schedule()
    assert schedule["diagnostic_model_trained"] is False
    assert schedule["safety_independently_certified"] is False
    assert m._reference_mrp(.5)[0]==pytest.approx(math.tan(math.radians(.5)/4.))


@pytest.mark.parametrize("bad",[
    "fault_torque_inference_proof","feedback_success",
    "step_jammed","unsafe_raw_motor","validation_fault_tuned"])
def test_undeclared_arms_denied_before_importing_native_basilisk(bad):
    with pytest.raises(ValueError,match="UNKNOWN_PREREGISTERED_TRIAL_ARM"):
        m.run_native_faulted_reference_arm(bad)


def test_native_source_has_no_synthetic_python_dynamics_path():
    code=SOURCE.read_text(encoding="utf8")
    assert "reactionWheelStateEffector.ReactionWheelStateEffector()" in code
    assert "rw.rwOutMsgs[FAULTED_RW_INDEX].recorder" in code
    assert "rw.ReactionWheelData[FAULTED_RW_INDEX].u_max" in code
    assert "sim.ExecuteSimulation()" in code
    assert "fault_effect_observed_in_true_native_speed" in code
    assert "fault_effect_observed_in_true_native_actuator_torque" in code
    assert "healthy_vs_fault_model_trained" in code
    assert "Validation" not in code
