#!/usr/bin/env python3
"""DT-2 authentic ON/OFF numerical plus SPICE scheduler convergence.

用途：复核 Basilisk 和真实 GMAT 在 5、2.5、1 秒下的日月第三体 ON/OFF 动力学响应。
参数：aggregate、--output、--gmat-root、--gmat-executable。
输出：12 组真实 91 点引擎轨道、273 点时标及响应、哈希凭证，科学判定 INCONCLUSIVE。

Basilisk additionally refines its real DE421 SPICE message cadence.
GMAT InitialStepSize/MaxStep are requested caps, not measured accepted steps.
"""
from __future__ import annotations
import argparse,csv,json,math
from pathlib import Path
import dt2_cross_leo_001 as base
import dt2_thirdbody_real as third

ENGINES=("bsk","gmat")
STAGES=("baseline","step2p5","step1")
FORCES=("off","on")
STEPS={"baseline":5.0,"step2p5":2.5,"step1":1.0}
AXES=("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s")


def _mode(stage:str,force:str)->str:
    if stage not in STAGES or force not in FORCES:
        raise ValueError("DT2_CONVERGENCE_BAD_MODE")
    return force if stage=="baseline" else stage+"-"+force


def read_true_trace(out:Path,engine:str,stage:str,force:str,src:dict)->tuple[list[dict],str]:
    mode=_mode(stage,force)
    state=third._name(out,engine,mode,"state.csv")
    receipt=json.loads(third._name(out,engine,mode,"execution.json").read_text())
    expected=("REAL_NATIVE" if engine=="bsk" else "REAL_GMAT")+"_THIRDBODY_EXECUTED_INCONCLUSIVE"
    if (receipt.get("status")!=expected or receipt.get("mode")!=mode
        or receipt.get("scientific_verdict")!="INCONCLUSIVE"
        or receipt.get("real_force_model_executed") is not True
        or receipt.get("rows")!=91
        or receipt.get("third_body_enabled") is not (force=="on")
        or base._sha256(state)!=receipt.get("state_sha256")):
        raise ValueError("DT2_CONVERGENCE_TRUE_ENGINE_SHA_OR_RECEIPT_INVALID")
    if engine=="bsk":
        actual_bodies={"earth":third.MU["earth"]}
        if force=="on":
            actual_bodies.update(sun=third.MU["sun"],moon=third.MU["moon"])
        if (receipt.get("true_spice_kernel_pool_count")!=3
            or receipt.get("step_s")!=STEPS[stage]
            or receipt.get("actual_mu_km3_s2")!=actual_bodies):
            raise ValueError("DT2_CONVERGENCE_NATIVE_STEP_OR_FORCE_CHANGED")
    else:
        script=third._name(out,engine,mode,"executed.script")
        raw=third._name(out,engine,mode,"raw.txt")
        if (receipt.get("return_code")!=0
            or receipt.get("effective_mu_independently_verified") is not False
            or receipt.get("declared_earth_sun_moon_mu_km3_s2")!=third.MU
            or base._sha256(script)!=receipt.get("script_sha256")
            or base._sha256(raw)!=receipt.get("raw_sha256")
            or script.read_text(encoding="ascii")!=third.gmat_script(src["initial"],raw,mode)):
            raise ValueError("DT2_CONVERGENCE_GMAT_SCRIPT_OR_BINARY_UNQUALIFIED")
        if stage!="baseline" and (
            receipt.get("requested_initial_step_s")!=STEPS[stage]
            or receipt.get("requested_max_step_s")!=STEPS[stage]
            or receipt.get("actual_RK89_internal_accepted_steps_independently_logged") is not False):
            raise ValueError("DT2_CONVERGENCE_GMAT_UNVERIFIED_REQUESTED_STEP")
    rows=base._read_states(state,base.CASE["epoch_utc"],
        tick_skew_budget_s=(base.GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S if engine=="gmat" else 1e-6))
    if len(rows)!=91:
        raise ValueError("DT2_CONVERGENCE_REQUIRES_REAL_91_SAMPLES")
    return rows,receipt["state_sha256"]


def measure(states:dict)->tuple[dict,list[dict]]:
    if set(states)!={(e,s,f) for e in ENGINES for s in STAGES for f in FORCES}:
        raise ValueError("DT2_CONVERGENCE_REQUIRES_12_GENUINE_ENGINE_TRACES")
    if any(len(x)!=91 for x in states.values()):
        raise ValueError("DT2_CONVERGENCE_MISSING_91_ACTUAL_ROWS")
    responses={};changes={}
    for engine in ENGINES:
        for stage in STAGES:
            samples=[]
            for i in range(91):
                on=states[engine,stage,"on"][i]
                off=states[engine,stage,"off"][i]
                dp=[float(on[k])-float(off[k]) for k in AXES[:3]]
                dv=[float(on[k])-float(off[k]) for k in AXES[3:]]
                if not all(math.isfinite(v) for v in dp+dv):
                    raise ValueError("DT2_CONVERGENCE_NONFINITE_RESPONSE")
                samples.append((dp,dv))
            responses[engine,stage]=samples
        changes[engine]={}
        for coarse,fine in (("baseline","step2p5"),("step2p5","step1"),("baseline","step1")):
            maximum=max(math.dist(responses[engine,coarse][i][0],
                                  responses[engine,fine][i][0]) for i in range(91))
            changes[engine][coarse+"_to_"+fine+"_max_position_change_m"]=maximum
    result=[];cross={}
    for stage in STAGES:
        deltas=[]
        for i in range(91):
            bp,bv=responses["bsk",stage][i]
            gp,gv=responses["gmat",stage][i]
            residual=math.dist(bp,gp)
            vresidual=math.dist(bv,gv)
            if not math.isfinite(residual) or not math.isfinite(vresidual):
                raise ValueError("DT2_CONVERGENCE_NONFINITE_INTERENGINE_RESIDUAL")
            result.append({
                "step_label":stage,"requested_step_s":STEPS[stage],"sample_index":i,
                "original_BSK_elapsed_s":states["bsk",stage,"on"][i]["time_s"],
                "original_GMAT_report_elapsed_s":states["gmat",stage,"on"][i]["time_s"],
                "BSK_ON_OFF_response_position_m":math.dist(bp,[0.,0.,0.]),
                "GMAT_ON_OFF_response_position_m":math.dist(gp,[0.,0.,0.]),
                "conditional_interengine_response_position_delta_m":residual,
                "conditional_interengine_response_velocity_delta_m_s":vresidual,
            })
            deltas.append(residual)
        cross[stage]=max(deltas)
    return {
        "status":"INCONCLUSIVE","scientific_verdict":"INCONCLUSIVE",
        "prior_true_5s_BSK_GMAT_engine_runs":4,
        "additional_true_BSK_GMAT_refined_engine_runs":8,
        "genuine_samples_per_engine_mode":91,
        "comparison_source_rows":len(result),
        "requested_step_seconds":STEPS,
        "native_BSK_integrator_and_SPICE_message_joint_refinement":True,
        "GMAT_requested_maxstep_not_observed_internal_substep":True,
        "effective_GMAT_mu_independently_read_back":False,
        "independent_N_to_EarthICRF_frame_approved":False,
        "absolute_GMAT_A1_to_BSK_ET_approved":False,
        "mission_accuracy_budget_approved":False,
        "within_engine_real_response_changes_m":changes,
        "conditional_cross_engine_response_maximum_by_step_m":cross,
    },result


def aggregate(out:Path,root:Path,binary:Path)->dict:
    src=third._source(out,root,binary)
    states={};hashes={}
    for engine in ENGINES:
        for stage in STAGES:
            for force in FORCES:
                mode=_mode(stage,force)
                states[engine,stage,force],hashes[engine+"_"+mode]=read_true_trace(
                    src["out"],engine,stage,force,src)
    report,rows=measure(states)
    csv_path=src["out"]/"thirdbody_step_convergence_real_rows.csv"
    doc_path=src["out"]/"thirdbody_step_convergence_real_diagnostic.json"
    if csv_path.exists() or doc_path.exists():
        raise FileExistsError("DT2_CONVERGENCE_IMMUTABLE_OUTPUT_COLLISION")
    with csv_path.open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    report["engine_state_sha256"]=hashes
    report["comparison_rows_sha256"]=base._sha256(csv_path)
    report["prior_real_source_sha256"]=src["source_sha256"]
    report["gmat_binary_sha256"]=src["binary_sha256"]
    report["gmat_startup_sha256"]=src["startup_sha256"]
    report["true_DE421_LSK_PCK_sha256"]={
        k:base._sha256(v) for k,v in src["kernels"].items()}
    base._write_json(doc_path,report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("aggregate",))
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--gmat-root",required=True,type=Path)
    p.add_argument("--gmat-executable",required=True,type=Path)
    a=p.parse_args()
    r=aggregate(a.output,a.gmat_root,a.gmat_executable)
    print("DT2_TRUE_CONVERGENCE",json.dumps({
        "status":r["status"],"max_cross_engine_m":r["conditional_cross_engine_response_maximum_by_step_m"],
        "scientific_verdict":"INCONCLUSIVE"},sort_keys=True))


if __name__=="__main__":
    main()
