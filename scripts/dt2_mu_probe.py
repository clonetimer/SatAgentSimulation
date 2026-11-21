#!/usr/bin/env python3
"""Actual NASA GMAT R2026a Sun/Luna Mu causal sensitivity experiment.

用途：从已核验的真实 GMAT Sun+Luna ON 轨道出发，分别加倍 Sun 和 Luna Mu 并实跑 GMAT。
参数：sun2x、moon2x、aggregate；--output、--gmat-root、--gmat-executable。
输出：两组真实 GMAT 原始报告、脚本、日志、91 点响应和 SHA-256；科学结论 INCONCLUSIVE。

The twofold force-parameter interventions are intentionally NONPHYSICAL.
They demonstrate behavioral sensitivity, NOT an effective-Mu object readback.
"""
from __future__ import annotations
import argparse
import csv
import json
import math
from pathlib import Path
import subprocess

import dt2_cross_leo_001 as base
import dt2_thirdbody_real as actual

TARGETS=("sun2x","moon2x")
AXES=("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s")


def _paths(out:Path,mode:str)->dict:
    if mode not in TARGETS:
        raise ValueError("DT2_MU_PROBE_BAD_INTERVENTION")
    prefix="thirdbody_gmat_"+mode+"_"
    return {name:out/(prefix+suffix) for name,suffix in (
        ("script","executed.script"),("raw","raw.txt"),
        ("state","state.csv"),("stdout","stdout.log"),
        ("stderr","stderr.log"),("receipt","execution.json"),
    )}


def _verified_original(out:Path)->tuple[list[dict],dict]:
    r=json.loads((out/"thirdbody_gmat_on_execution.json").read_text(encoding="utf-8"))
    if (r.get("status")!="REAL_GMAT_THIRDBODY_EXECUTED_INCONCLUSIVE"
        or r.get("rows")!=91 or r.get("third_body_enabled") is not True
        or r.get("real_force_model_executed") is not True
        or r.get("effective_mu_independently_verified") is not False):
        raise ValueError("DT2_MU_PROBE_REAL_GMAT_ON_REQUIRED")
    for suffix,field in (("state.csv","state_sha256"),
                         ("raw.txt","raw_sha256"),
                         ("executed.script","script_sha256")):
        if base._sha256(out/("thirdbody_gmat_on_"+suffix))!=r.get(field):
            raise ValueError("DT2_MU_PROBE_ORIGINAL_GMAT_BYTES_CHANGED")
    states=base._read_states(
        out/"thirdbody_gmat_on_state.csv",base.CASE["epoch_utc"],
        tick_skew_budget_s=base.GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    return states,r


def script_for_intervention(initial:dict,raw:Path,mode:str)->str:
    if mode not in TARGETS:
        raise ValueError("DT2_MU_PROBE_BAD_INTERVENTION")
    original=actual.gmat_script(initial,raw,"on")
    key=mode.removesuffix("2x")
    name="Sun" if key=="sun" else "Luna"
    old="GMAT "+name+".Mu = "+format(actual.MU[key],".15g")+";"
    new="GMAT "+name+".Mu = "+format(2.0*actual.MU[key],".15g")+";"
    if original.count(old)!=1:
        raise ValueError("DT2_MU_PROBE_ORIGINAL_DECLARATION_NOT_UNIQUE")
    modified=original.replace(old,new,1)
    if modified==original or modified.replace(new,old)!=original:
        raise ValueError("DT2_MU_PROBE_MORE_THAN_ONE_INPUT_CHANGED")
    if "CrossGravity.PointMasses = {Earth, Sun, Luna};" not in modified:
        raise ValueError("DT2_MU_PROBE_MISSING_THIRDBODY_FORCE")
    return modified


def execute(out:Path,gmat_root:Path,binary:Path,mode:str)->dict:
    if mode not in TARGETS:
        raise ValueError("DT2_MU_PROBE_BAD_INTERVENTION")
    source=actual._source(out,gmat_root,binary)
    _,prior=_verified_original(source["out"])
    p=_paths(source["out"],mode)
    if any(x.exists() for x in p.values()):
        raise FileExistsError("DT2_MU_PROBE_REFUSES_SOURCE_OVERWRITE")
    script=script_for_intervention(source["initial"],p["raw"].resolve(),mode)
    p["script"].write_text(script,encoding="ascii")
    run=subprocess.run(
        [str(source["binary"]),"--run",str(p["script"].resolve())],
        cwd=source["binary"].parent,capture_output=True,text=True,
        timeout=600,check=False)
    p["stdout"].write_text(run.stdout,encoding="utf-8")
    p["stderr"].write_text(run.stderr,encoding="utf-8")
    receipt={
        "status":"GMAT_MU_PROBE_NOT_VERIFIED",
        "scientific_verdict":"INCONCLUSIVE",
        "mode":mode,"counterfactual_mu_multiplier":2.0,
        "nonphysical_parameter_falsification_only":True,
        "one_declared_celestial_Mu_changed":mode.removesuffix("2x"),
        "GMAT_effective_Mu_independently_read_back":False,
        "GMAT_mu_input_causes_measured_dynamics":False,
        "independent_N_to_EarthICRF_frame_approved":False,
        "absolute_GMAT_to_Basilisk_epoch_approved":False,
        "mission_accuracy_budget_approved":False,
        "return_code":run.returncode,
        "GMAT_binary_sha256":source["binary_sha256"],
        "GMAT_startup_sha256":source["startup_sha256"],
        "pinned_DE421_sha256":{key:base._sha256(path) for key,path in source["kernels"].items()},
        "source_sha256":source["source_sha256"],
        "nominal_GMAT_ON_sha256":prior["state_sha256"],
        "script_sha256":base._sha256(p["script"]),
        "stdout_sha256":base._sha256(p["stdout"]),
        "stderr_sha256":base._sha256(p["stderr"]),
        "real_GMAT_report_produced":p["raw"].is_file(),
    }
    base._write_json(p["receipt"],receipt)
    if run.returncode!=0 or not p["raw"].is_file():
        raise RuntimeError("DT2_MU_PROBE_REAL_GMAT_ENGINE_NOT_EXECUTED")
    states=base._read_gmat_report(p["raw"],base.CASE["epoch_utc"])
    if len(states)!=91:
        raise ValueError("DT2_MU_PROBE_MISSING_91_REAL_GMAT_STATES")
    base._write_state_csv(p["state"],states)
    receipt.update(
        status="REAL_GMAT_MU_INTERVENTION_EXECUTED_INCONCLUSIVE",
        real_GMAT_samples=91,
        GMAT_mu_input_causes_measured_dynamics=None,
        raw_sha256=base._sha256(p["raw"]),
        state_sha256=base._sha256(p["state"]),
    )
    base._write_json(p["receipt"],receipt)
    return receipt


def aggregate(out:Path,gmat_root:Path,binary:Path)->dict:
    source=actual._source(out,gmat_root,binary)
    nominal,original_receipt=_verified_original(source["out"])
    rows=[]
    maxima={}
    receipts={}
    for mode in TARGETS:
        p=_paths(source["out"],mode)
        rec=json.loads(p["receipt"].read_text(encoding="utf-8"))
        if (rec.get("status")!="REAL_GMAT_MU_INTERVENTION_EXECUTED_INCONCLUSIVE"
            or rec.get("mode")!=mode or rec.get("return_code")!=0
            or rec.get("real_GMAT_samples")!=91
            or rec.get("GMAT_effective_Mu_independently_read_back") is not False
            or rec.get("counterfactual_mu_multiplier")!=2.0
            or rec.get("nonphysical_parameter_falsification_only") is not True
            or rec.get("GMAT_binary_sha256")!=source["binary_sha256"]
            or rec.get("GMAT_startup_sha256")!=source["startup_sha256"]
            or rec.get("nominal_GMAT_ON_sha256")!=original_receipt["state_sha256"]):
            raise ValueError("DT2_MU_PROBE_REAL_EXECUTION_RECEIPT_INVALID")
        for kind,sha in (("script","script_sha256"),("raw","raw_sha256"),
                         ("state","state_sha256"),("stdout","stdout_sha256"),
                         ("stderr","stderr_sha256")):
            if base._sha256(p[kind])!=rec.get(sha):
                raise ValueError("DT2_MU_PROBE_ACTUAL_OUTPUT_SHA_CHANGED: "+kind)
        if p["script"].read_text(encoding="ascii")!=script_for_intervention(
            source["initial"],p["raw"].resolve(),mode):
            raise ValueError("DT2_MU_PROBE_EXECUTED_SCRIPT_NOT_EXACT_VARIANT")
        for filename,sha in source["source_sha256"].items():
            if base._sha256(source["out"]/filename)!=sha:
                raise ValueError("DT2_MU_PROBE_AUTHENTIC_SOURCE_CHANGED")
        real_states=base._read_states(
            p["state"],base.CASE["epoch_utc"],
            tick_skew_budget_s=base.GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
        differences=[]
        for i,(base_row,new_row) in enumerate(zip(nominal,real_states)):
            dx=[new_row[k]-base_row[k] for k in AXES[:3]]
            dv=[new_row[k]-base_row[k] for k in AXES[3:]]
            if not all(math.isfinite(v) for v in (*dx,*dv)):
                raise ValueError("DT2_MU_PROBE_NONFINITE_REAL_ENGINE_STATE")
            differences.append({
                "mode":mode,"sample_index":i,
                "nominal_GMAT_raw_elapsed_s":base_row["time_s"],
                "probe_GMAT_raw_elapsed_s":new_row["time_s"],
                "actual_position_response_m":math.sqrt(sum(x*x for x in dx)),
                "actual_velocity_response_m_s":math.sqrt(sum(x*x for x in dv)),
            })
        mx=max(row["actual_position_response_m"] for row in differences)
        if mx<=0:
            raise ValueError("DT2_MU_PROBE_GMAT_DID_NOT_REACT_TO_DECLARED_MU")
        maxima[mode]=mx
        rows.extend(differences)
        receipts[mode]=base._sha256(p["receipt"])
    csv_path=source["out"]/"thirdbody_gmat_mu2x_causal_response_rows.csv"
    json_path=source["out"]/"thirdbody_gmat_mu2x_causal_diagnostic.json"
    if csv_path.exists() or json_path.exists():
        raise FileExistsError("DT2_MU_PROBE_FINAL_RECEIPT_COLLISION")
    with csv_path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result={
        "status":"INCONCLUSIVE","scientific_verdict":"INCONCLUSIVE",
        "real_independent_GMAT_engine_interventions":2,
        "real_GMAT_samples_per_intervention":91,
        "nonphysical_mu_multiplier":2.0,
        "real_mu_targets":["sun2x","moon2x"],
        "causal_nonzero_GMAT_trajectory_response_observed":True,
        "max_real_GMAT_position_responses_m":maxima,
        "GMAT_effective_mu_runtime_readback_verified":False,
        "physical_N_to_GMAT_EarthICRF_frame_approved":False,
        "absolute_clock_alignment_approved":False,
        "mission_accuracy_budget_approved":False,
        "original_GMAT_ON_state_sha256":original_receipt["state_sha256"],
        "intervention_receipt_sha256":receipts,
        "response_csv_sha256":base._sha256(csv_path),
        "original_source_sha256":source["source_sha256"],
    }
    base._write_json(json_path,result)
    return result


def main()->None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("sun2x","moon2x","aggregate"))
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--gmat-root",required=True,type=Path)
    parser.add_argument("--gmat-executable",required=True,type=Path)
    a=parser.parse_args()
    result=(aggregate(a.output,a.gmat_root,a.gmat_executable) if a.action=="aggregate"
            else execute(a.output,a.gmat_root,a.gmat_executable,a.action))
    print("DT2_REAL_GMAT_MU_CAUSAL",json.dumps({
        "action":a.action,"status":result["status"],"scientific_verdict":"INCONCLUSIVE"
    },sort_keys=True))


if __name__=="__main__":
    main()
