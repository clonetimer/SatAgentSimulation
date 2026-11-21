#!/usr/bin/env python3
"""Genuine DT-2 Sun/Luna ON/OFF dynamics in separate Basilisk and GMAT engines.

Original 91-state point-mass runs are immutable. Require actual GMAT body-mu
readbacks, real DE421 kernels and raw independent engine outputs. No scientific
frame/timing/tolerance PASS is allowed by this experiment.
"""
from __future__ import annotations
import argparse
import csv
import importlib.metadata
import json
import math
import subprocess
from datetime import datetime,timezone
from pathlib import Path

import dt2_cross_leo_001 as baseline
import dt2_sun_anchor as anchor

MU={"earth":398600.436,"sun":132712440023.310,"moon":4902.799}
AXES=("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s")
MODES=("off","on")


def _source(out:Path,root:Path,binary:Path)->dict:
    """Fail closed unless all four independent original-source identities hold."""
    out=out.resolve(strict=True)
    root=root.resolve(strict=True)
    exe=binary.resolve(strict=True)
    m=json.loads((out/"case_manifest.json").read_text())
    c=json.loads((out/"sun_anchor_execution.json").read_text())
    g=json.loads((out/"gmat_icrf_numeric_execution.json").read_text())
    p=json.loads((out/"directional_thirdbody_analytic_preflight.json").read_text())
    if (m.get("schema")!=baseline.SCHEMA or m.get("physical_flags")!=baseline.MODEL_FLAGS
        or not m["evidence"].get("basilisk_executed")
        or c.get("status")!="REAL_THREE_ENGINE_CELESTIAL_SOURCE_OBSERVED_INCONCLUSIVE"
        or not c.get("basilisk_spice_kernel_pool_verified")
        or c.get("real_moon_sample_count")!=3
        or g.get("status")!="GMAT_ICRF_NUMERIC_INITIAL_EXECUTED_UNQUALIFIED_N_TIE"
        or g.get("rows")!=91
        or p.get("status")!="INCONCLUSIVE"
        or p.get("force_model_executed_in_new_basilisk_run") is not False
        or p.get("force_model_executed_in_new_gmat_run") is not False):
        raise ValueError("DT2_THIRDBODY_GENUINE_BASELINE_PRECONDITION_MISSING")
    binary_sha=baseline._sha256(exe)
    if binary_sha!=c.get("gmat_binary_sha256") or binary_sha!=g.get("binary_sha256"):
        raise ValueError("DT2_THIRDBODY_WRONG_GMAT_EXECUTABLE_SHA")
    kernels={
        "spk":root/"data/planetary_ephem/spk/DE421AllPlanets.bsp",
        "lsk":root/"data/time/SPICELeapSecondKernel.tls",
        "pck":root/"data/planetary_coeff/SPICEPlanetaryConstantsKernel.tpc",
    }
    startup=anchor.verified_gmat_startup_kernels(root,kernels)
    if startup["sha256"]!=c.get("gmat_startup_sha256"):
        raise ValueError("DT2_THIRDBODY_WRONG_GMAT_STARTUP")
    for k,path in kernels.items():
        if baseline._sha256(path)!=c.get("kernel_sha256",{}).get(k):
            raise ValueError("DT2_THIRDBODY_WRONG_KERNEL_SHA: "+k)
    sources={
        "basilisk_state.csv":m["evidence"]["basilisk_state_csv_sha256"],
        "gmat_icrf_numeric_state.csv":g["normalized_csv_sha256"],
        "sun_anchor_three_engines.csv":c["sun_anchor_comparison_csv_sha256"],
        "moon_anchor_three_engines.csv":c["moon_anchor_comparison_csv_sha256"],
        "directional_thirdbody_analytic_preflight_rows.csv":p["three_sample_csv_sha256"],
    }
    for filename,sha in sources.items():
        if baseline._sha256(out/filename)!=sha:
            raise ValueError("DT2_THIRDBODY_ORIGINAL_SOURCE_HASH_MISMATCH: "+filename)
    if float(m["gravity"]["mu_m3_s2"])/1e9!=MU["earth"]:
        raise ValueError("DT2_THIRDBODY_BASILISK_MU_CHANGED")
    return {"out":out,"binary":exe,"kernels":kernels,"source_sha256":sources,
            "startup_sha256":startup["sha256"],"binary_sha256":binary_sha,
            "mu_km3_s2":MU,"initial":m["actual_initial_state_SI"]}


def _name(out:Path,engine:str,mode:str,key:str)->Path:
    if engine not in ("bsk","gmat") or mode not in MODES:
        raise ValueError("DT2_THIRDBODY_BAD_ENGINE_MODE")
    return out/("thirdbody_"+engine+"_"+mode+"_"+key)


def native(out:Path,root:Path,binary:Path,mode:str)->dict:
    if mode not in MODES: raise ValueError("DT2_THIRDBODY_BAD_MODE")
    src=_source(out,root,binary)
    state=_name(src["out"],"bsk",mode,"state.csv")
    receipt=_name(src["out"],"bsk",mode,"execution.json")
    if state.exists() or receipt.exists():
        raise FileExistsError("DT2_THIRDBODY_NATIVE_REFUSES_OVERWRITE")
    from Basilisk.utilities import SimulationBaseClass,macros,simIncludeGravBody
    from Basilisk.simulation import spacecraft,spiceInterface
    import Basilisk
    if (Basilisk.__version__!="2.11.0" or
        importlib.metadata.version("bsk")!="2.11.0"):
        raise RuntimeError("DT2_THIRDBODY_NATIVE_REQUIRES_REAL_2_11_0")
    if spiceInterface.countKernelsLoaded()!=0:
        raise RuntimeError("DT2_THIRDBODY_SPICE_POOL_ALREADY_USED")
    sim=SimulationBaseClass.SimBaseClass()
    process=sim.CreateNewProcess("dt2_real_thirdbody_process")
    task="dt2_thirdbody_5s"
    process.addTask(sim.CreateNewTask(task,macros.sec2nano(5.0)))
    spice=spiceInterface.SpiceInterface(auto_configure_kernels=False)
    spice.ModelTag="DT2_ACTUAL_DE421_THIRDBODY"
    spice.addKernelPaths([str(src["kernels"][k]) for k in ("lsk","pck","spk")])
    spice.addPlanetNames(["earth","sun","moon"])
    spice.zeroBase="earth"
    spice.referenceBase="J2000"
    epoch=datetime.fromisoformat(baseline.CASE["epoch_utc"].replace("Z","+00:00")).astimezone(timezone.utc)
    spice.UTCCalInit=epoch.strftime("%Y %B %d %H:%M:%S.%f")+" (UTC)"
    # Ephemerides published before spacecraft gravity each 5s task tick.
    sim.AddModelToTask(task,spice)
    grav=simIncludeGravBody.gravBodyFactory()
    earth=grav.createEarth()
    earth.isCentralBody=True
    bodies={"earth":earth}
    if mode=="on":
        bodies["sun"]=grav.createSun()
        bodies["moon"]=grav.createMoon()
    for i,name in enumerate(("earth","sun","moon")):
        if name in bodies:
            bodies[name].planetBodyInMsg.subscribeTo(spice.planetStateOutMsgs[i])
    for name,body in bodies.items():
        if not math.isclose(float(body.mu)/1e9,MU[name],rel_tol=0,abs_tol=1e-9):
            raise ValueError("DT2_THIRDBODY_NATIVE_MU_NOT_PINNED: "+name)
    craft=spacecraft.Spacecraft()
    craft.ModelTag="DT2_BSK_THIRDBODY_"+mode.upper()
    craft.hub.mHub=100.0
    craft.hub.IHubPntBc_B=[[100.,0.,0.],[0.,100.,0.],[0.,0.,100.]]
    craft.hub.r_CN_NInit=[float(src["initial"][k]) for k in AXES[:3]]
    craft.hub.v_CN_NInit=[float(src["initial"][k]) for k in AXES[3:]]
    grav.addBodiesTo(craft)
    sim.AddModelToTask(task,craft)
    recorder=craft.scStateOutMsg.recorder(macros.sec2nano(60.0))
    sim.AddModelToTask(task,recorder)
    sim.InitializeSimulation()
    if (spiceInterface.countKernelsLoaded()!=3 or
        not all(spiceInterface.isKernelLoaded(str(p)) for p in src["kernels"].values())):
        raise RuntimeError("DT2_THIRDBODY_NATIVE_KERNEL_INVENTORY_MISMATCH")
    sim.ConfigureStopTime(macros.sec2nano(5400.0))
    sim.ExecuteSimulation()
    times=recorder.times()*macros.NANO2SEC
    if len(times)!=91:
        raise ValueError("DT2_THIRDBODY_NATIVE_REAL_91_SAMPLE_COUNT")
    rows=[]
    for i,t in enumerate(times):
        row=dict(zip(AXES,[float(v) for v in (
            *recorder.r_BN_N[i],*recorder.v_BN_N[i])]))
        row.update(time_s=float(t),utc=baseline._utc_at(baseline.CASE["epoch_utc"],float(t)))
        rows.append(row)
    baseline._assert_grid(rows)
    if any(abs(rows[0][k]-float(src["initial"][k]))>1e-6 for k in AXES):
        raise ValueError("DT2_THIRDBODY_NATIVE_INITIAL_STATE_CHANGED")
    baseline._write_state_csv(state,rows)
    rec={"engine":"ACTUAL_BASILISK_2_11_0","mode":mode,"third_body_enabled":mode=="on",
         "status":"REAL_NATIVE_THIRDBODY_EXECUTED_INCONCLUSIVE","real_force_model_executed":True,
         "actual_gravity_bodies":list(bodies),
         "actual_mu_km3_s2":{k:float(v.mu)/1e9 for k,v in bodies.items()},
         "true_spice_kernel_pool_count":3,
         "kernel_sha256":{k:baseline._sha256(v) for k,v in src["kernels"].items()},
         "source_sha256":src["source_sha256"],
         "step_s":5,"output_s":60,"rows":91,"state_sha256":baseline._sha256(state),
         "physical_N_to_GMAT_ICRF_approved":False,"mission_budget_approved":False,
         "scientific_verdict":"INCONCLUSIVE"}
    baseline._write_json(receipt,rec)
    return rec


def gmat_script(initial:dict,raw:Path,mu_report:Path,mode:str)->str:
    if mode not in MODES: raise ValueError("DT2_THIRDBODY_BAD_MODE")
    script=baseline._gmat_script(
        initial,report_path=str(raw),earth_mu_km3_s2=MU["earth"],state_frame="EarthICRF")
    earth_line=f"GMAT Earth.Mu = {MU['earth']:.15g};"
    if script.count(earth_line)!=1: raise ValueError("DT2_THIRDBODY_NO_EARTH_MU")
    script=script.replace(earth_line,earth_line+
        f"\nGMAT Sun.Mu = {MU['sun']:.15g};\nGMAT Luna.Mu = {MU['moon']:.15g};",1)
    script=script.replace("Create Spacecraft CrossSat;",
        "GMAT SolarSystem.EphemerisSource = 'SPICE';\nCreate Spacecraft CrossSat;",1)
    if mode=="on":
        if script.count("CrossGravity.PointMasses = {Earth};")!=1:
            raise ValueError("DT2_THIRDBODY_MISSING_FORCE_SWITCH")
        script=script.replace("CrossGravity.PointMasses = {Earth};",
            "CrossGravity.PointMasses = {Earth, Sun, Luna};",1)
    declaration="\n".join([
        "Create ReportFile ThirdBodyMu;",
        f"ThirdBodyMu.Filename = '{str(mu_report.resolve()).replace(chr(92),'/').replace(chr(39),'')}';",
        "ThirdBodyMu.Precision = 16;",
        "ThirdBodyMu.WriteHeaders = false;",
        "ThirdBodyMu.FixedWidth = false;",
        "ThirdBodyMu.Delimiter = ',';",
        "ThirdBodyMu.WriteReport = false;",
        "ThirdBodyMu.AppendToExistingFile = false;",
    ])
    if script.count("BeginMissionSequence;")!=1: raise ValueError("DT2_THIRDBODY_GMAT_BAD_MISSION")
    script=script.replace("BeginMissionSequence;",
        declaration+"\nBeginMissionSequence;\nReport ThirdBodyMu Earth.Mu Sun.Mu Luna.Mu;",1)
    if not script.isascii():raise ValueError("DT2_THIRDBODY_GMAT_NONASCII")
    return script


def read_mu(path:Path)->dict:
    with path.open(newline="",encoding="utf-8-sig") as f:
        rows=[r for r in csv.reader(f) if any(x.strip() for x in r)]
    if len(rows)!=1 or len(rows[0])!=3:
        raise ValueError("DT2_THIRDBODY_REAL_MU_READBACK_REQUIRED")
    vals=[float(x.strip()) for x in rows[0]]
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("DT2_THIRDBODY_NONFINITE_MU_READBACK")
    reported=dict(zip(("earth","sun","moon"),vals))
    for name,v in reported.items():
        # Strict input serialization gate, not a post-hoc orbit tolerance.
        if not math.isclose(v,MU[name],rel_tol=5e-14,abs_tol=1e-9):
            raise ValueError("DT2_THIRDBODY_ACTUAL_GMAT_MU_MISMATCH: "+name)
    return reported


def gmat(out:Path,root:Path,binary:Path,mode:str)->dict:
    if mode not in MODES: raise ValueError("DT2_THIRDBODY_BAD_MODE")
    src=_source(out,root,binary)
    files={k:_name(src["out"],"gmat",mode,k) for k in (
        "state.csv","raw.txt","mu.txt","executed.script","stdout.log","stderr.log","execution.json")}
    if any(p.exists() for p in files.values()):
        raise FileExistsError("DT2_THIRDBODY_GMAT_IMMUTABLE_OUTPUT")
    script=gmat_script(src["initial"],files["raw.txt"],files["mu.txt"],mode)
    files["executed.script"].write_text(script,encoding="ascii")
    run=subprocess.run(
        [str(src["binary"]),"--run",str(files["executed.script"].resolve())],
        cwd=src["binary"].parent,text=True,capture_output=True,timeout=600,check=False)
    files["stdout.log"].write_text(run.stdout,encoding="utf-8")
    files["stderr.log"].write_text(run.stderr,encoding="utf-8")
    receipt={"status":"NOT_VERIFIED","engine":"REAL_GMAT_R2026a","mode":mode,
             "third_body_enabled":mode=="on","scientific_verdict":"INCONCLUSIVE",
             "return_code":run.returncode,
             "binary_sha256":src["binary_sha256"],"startup_sha256":src["startup_sha256"],
             "source_sha256":src["source_sha256"],
             "script_sha256":baseline._sha256(files["executed.script"]),
             "stdout_sha256":baseline._sha256(files["stdout.log"]),
             "stderr_sha256":baseline._sha256(files["stderr.log"]),
             "raw_report_present":files["raw.txt"].is_file(),
             "mu_report_present":files["mu.txt"].is_file(),
             "physical_N_to_EarthICRF_approved":False,"mission_budget_approved":False}
    baseline._write_json(files["execution.json"],receipt)
    if run.returncode!=0 or not files["raw.txt"].is_file() or not files["mu.txt"].is_file():
        raise RuntimeError("DT2_THIRDBODY_REAL_GMAT_MISSING_SOURCE_OR_MU_REPORT")
    mu=read_mu(files["mu.txt"])
    rows=baseline._read_gmat_report(files["raw.txt"],baseline.CASE["epoch_utc"])
    baseline._write_state_csv(files["state.csv"],rows)
    receipt.update(status="REAL_GMAT_THIRDBODY_EXECUTED_INCONCLUSIVE",
        real_force_model_executed=True,rows=len(rows),
        actual_mu_readback_km3_s2=mu,
        mu_sha256=baseline._sha256(files["mu.txt"]),
        raw_sha256=baseline._sha256(files["raw.txt"]),
        state_sha256=baseline._sha256(files["state.csv"]))
    baseline._write_json(files["execution.json"],receipt)
    return receipt


def compare(out:Path,root:Path,binary:Path)->dict:
    src=_source(out,root,binary)
    traces={};state_hashes={}
    for engine in ("bsk","gmat"):
        for mode in MODES:
            sf=_name(src["out"],engine,mode,"state.csv")
            receipt=json.loads(_name(src["out"],engine,mode,"execution.json").read_text())
            if (receipt.get("status")!=f"REAL_{'NATIVE' if engine=='bsk' else 'GMAT'}_THIRDBODY_EXECUTED_INCONCLUSIVE"
                or receipt.get("rows")!=91 or receipt.get("real_force_model_executed") is not True
                or receipt.get("third_body_enabled") is not (mode=="on")
                or receipt.get("scientific_verdict")!="INCONCLUSIVE"
                or baseline._sha256(sf)!=receipt.get("state_sha256")):
                raise ValueError("DT2_THIRDBODY_ENGINE_OUTPUT_INVALID: "+engine+mode)
            if engine=="gmat":
                mu=_name(src["out"],engine,mode,"mu.txt")
                raw=_name(src["out"],engine,mode,"raw.txt")
                if (baseline._sha256(mu)!=receipt["mu_sha256"] or
                    baseline._sha256(raw)!=receipt["raw_sha256"]):
                    raise ValueError("DT2_THIRDBODY_GMAT_RAW_HASH_INVALID")
                read_mu(mu)
            traces[engine,mode]=baseline._read_states(
                sf,baseline.CASE["epoch_utc"],
                tick_skew_budget_s=(
                    baseline.GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S if engine=="gmat" else 1e-6))
            state_hashes[engine+"_"+mode]=receipt["state_sha256"]
    result=[]
    for i in range(91):
        effects={}
        for engine in ("bsk","gmat"):
            on=traces[engine,"on"][i];off=traces[engine,"off"][i]
            effects[engine]=([on[k]-off[k] for k in AXES[:3]],
                             [on[k]-off[k] for k in AXES[3:]])
        br,bv=effects["bsk"];gr,gv=effects["gmat"]
        result.append({
            "sample":i,
            "actual_BSK_elapsed_s":traces["bsk","on"][i]["time_s"],
            "actual_GMAT_report_elapsed_s":traces["gmat","on"][i]["time_s"],
            "BSK_true_ON_OFF_position_response_m":math.dist(br,[0.,0.,0.]),
            "GMAT_true_ON_OFF_position_response_m":math.dist(gr,[0.,0.,0.]),
            "BSK_true_ON_OFF_velocity_response_m_s":math.dist(bv,[0.,0.,0.]),
            "GMAT_true_ON_OFF_velocity_response_m_s":math.dist(gv,[0.,0.,0.]),
            "conditional_UNAPPROVED_vector_response_delta_m":
                math.dist(br,gr),
            "conditional_UNAPPROVED_velocity_response_delta_m_s":
                math.dist(bv,gv),
        })
    if not all(math.isfinite(x) for row in result for x in row.values()):
        raise ValueError("DT2_THIRDBODY_NONFINITE_ENGINE_RESPONSE")
    bmax=max(x["BSK_true_ON_OFF_position_response_m"] for x in result)
    gmax=max(x["GMAT_true_ON_OFF_position_response_m"] for x in result)
    if bmax<=0 or gmax<=0:
        raise ValueError("DT2_THIRDBODY_ACTUAL_FORCE_EFFECT_NOT_OBSERVED")
    csv_path=src["out"]/"thirdbody_two_engine_ON_OFF_response_rows.csv"
    json_path=src["out"]/"thirdbody_two_engine_ON_OFF_diagnostic.json"
    if csv_path.exists() or json_path.exists():
        raise FileExistsError("DT2_THIRDBODY_COMPARE_REFUSES_OVERWRITE")
    with csv_path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    diag={
        "status":"INCONCLUSIVE","samples_per_engine_mode":91,
        "true_native_Basilisk_on_off_executed":True,
        "true_GMAT_R2026a_on_off_executed":True,
        "actual_independently_executed_directional_force_response_observed":True,
        "max_Basilisk_ON_OFF_position_m":bmax,"max_GMAT_ON_OFF_position_m":gmax,
        "max_conditional_unapproved_response_vector_delta_m":
            max(x["conditional_UNAPPROVED_vector_response_delta_m"] for x in result),
        "source_frame_equivalence_unapproved":True,
        "source_absolute_clock_alignment_unapproved":True,
        "mission_accuracy_budget_approved":False,
        "scientific_verdict":"INCONCLUSIVE",
        "real_engine_state_sha256":state_hashes,
        "comparison_rows_sha256":baseline._sha256(csv_path)}
    baseline._write_json(json_path,diag)
    return diag


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("bsk-off","bsk-on","gmat-off","gmat-on","compare"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--gmat-root",type=Path,required=True)
    parser.add_argument("--gmat-executable",type=Path,required=True)
    a=parser.parse_args()
    engine,_,mode=a.action.partition("-")
    if engine=="bsk":
        result=native(a.output,a.gmat_root,a.gmat_executable,mode)
    elif engine=="gmat":
        result=gmat(a.output,a.gmat_root,a.gmat_executable,mode)
    else:
        result=compare(a.output,a.gmat_root,a.gmat_executable)
    print("DT2_REAL_THIRDBODY",json.dumps({
        "action":a.action,"status":result["status"],"scientific_verdict":"INCONCLUSIVE"
    },sort_keys=True))

if __name__=="__main__":
    main()
