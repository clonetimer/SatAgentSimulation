"""Synthetic contracts only; genuine Basilisk/GMAT runs are mandatory in CI."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import dt2_thirdbody_real as tb
import dt2_step_convergence as sc


def initial():
    return {"x_m":6650000.,"y_m":0.,"z_m":0.,"vx_m_s":0.,"vy_m_s":7742.,"vz_m_s":0.}


def test_gmat_5_to_2p5_to_1_s_only_changes_initial_and_maxstep():
    for stage,sec in (("step2p5",2.5),("step1",1.0)):
        for force in sc.FORCES:
            mode=stage+"-"+force
            prior=tb.gmat_script(initial(),Path("/tmp/real_report.txt"),force)
            updated=tb.gmat_script(initial(),Path("/tmp/real_report.txt"),mode)
            expected=prior.replace("CrossProp.InitialStepSize = 5;",
                                   "CrossProp.InitialStepSize = "+format(sec,".15g")+";")
            expected=expected.replace("CrossProp.MaxStep = 5;",
                                      "CrossProp.MaxStep = "+format(sec,".15g")+";")
            assert updated==expected
            assert tb.step_s(mode)==sec
            assert tb._physical_mode(mode)==force
            assert "CrossSat.CoordinateSystem = EarthICRF;" in updated
    for bad in ("step3-on","step4-off","",None):
        with pytest.raises(ValueError,match="DT2_CONVERGENCE_INVALID_STEP_MODE"):
            tb.step_s(bad)


def test_response_convergence_math_is_diagnostic_not_precision_threshold():
    states={}
    for engine in sc.ENGINES:
        for stage in sc.STAGES:
            for force in sc.FORCES:
                state=[]
                for i in range(91):
                    x=float(i)+(sc.STEPS[stage]*0.005 if force=="on" else 0.)
                    state.append({
                        "time_s":float(i*60),"x_m":x,"y_m":0.,"z_m":0.,
                        "vx_m_s":0.,"vy_m_s":0.,"vz_m_s":0.,
                    })
                states[engine,stage,force]=state
    doc,rows=sc.measure(states)
    assert doc["status"]=="INCONCLUSIVE"
    assert len(rows)==273
    assert doc["additional_true_BSK_GMAT_refined_engine_runs"]==8
    assert doc["native_BSK_integrator_and_SPICE_message_joint_refinement"]
    assert doc["GMAT_requested_maxstep_not_observed_internal_substep"]
    assert doc["mission_accuracy_budget_approved"] is False
    assert doc["conditional_cross_engine_response_maximum_by_step_m"]["step1"]==0
    with pytest.raises(ValueError,match="DT2_CONVERGENCE_REQUIRES_12_GENUINE_ENGINE_TRACES"):
        sc.measure({("bsk","baseline","on"):[]})
    with pytest.raises(FileNotFoundError):
        sc.aggregate(Path("/tmp/no_true_dt2_bundle"),Path("/tmp"),Path("/tmp/no_GMAT"))
