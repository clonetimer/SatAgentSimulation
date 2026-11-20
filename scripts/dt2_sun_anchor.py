#!/usr/bin/env python3
"""DT-2 CROSS-LEO-001: real shared-SPK Sun-vector frame anchor.

用途：在已执行 Basilisk/GMAT 的封存证据上，用同一官方 GMAT DE421 SPK、
      SPICE LSK 在 NAIF/真实 Basilisk SPICE 消息/真实 GMAT 中观测太阳地心矢量。
参数：--output 已封存案例目录 --gmat-root 真实 GMAT R2026a 解包目录
      --gmat-executable 真实 GmatConsole-R2026a。
输出：真实 GMAT 原始太阳矢量、NAIF 与 Basilisk 参考矢量、逐点诊断和哈希凭证；
      不生成虚构的 GMAT 数据，不自动通过绝对 N 轴或飞行精度验收。
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

INDICES = (0, 45, 90)
AXES = ("X", "Y", "Z", "VX", "VY", "VZ")
FIELDS = tuple(
    [f"icrf_{k.lower()}" for k in AXES] +
    [f"mj_{k.lower()}" for k in AXES]
)


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")


def sun_script(a1_mjd: list[float], *, spk: Path, lsk: Path,
               pck: Path, report_path: Path) -> str:
    if len(a1_mjd)!=3 or not all(math.isfinite(t) for t in a1_mjd):
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_THREE_REAL_A1_EPOCHS")
    for a,b in zip(a1_mjd,a1_mjd[1:]):
        if b<=a:
            raise ValueError("DT2_SUN_ANCHOR_A1_EPOCH_NOT_INCREASING")
    def q(path: Path) -> str:
        name=str(path.resolve()).replace("\\","/")
        if "'" in name or not name.isascii():
            raise ValueError("DT2_SUN_ANCHOR_INVALID_GMAT_PATH")
        return name
    lines=[
        "% Real R2026a Sun geocentric ephemeris, not a fake spacecraft vector.",
        "GMAT SolarSystem.EphemerisSource = 'SPICE';",
        f"GMAT SolarSystem.SPKFilename = '{q(spk)}';",
        f"GMAT SolarSystem.LSKFilename = '{q(lsk)}';",
        f"GMAT SolarSystem.PCKFilename = '{q(pck)}';",
        "Create ReportFile SunAnchor;",
        f"SunAnchor.Filename = '{q(report_path)}';",
        "SunAnchor.Precision = 16;",
        "SunAnchor.WriteHeaders = false;",
        "SunAnchor.FixedWidth = false;",
        "SunAnchor.Delimiter = ',';",
        "SunAnchor.WriteReport = false;",
        "SunAnchor.AppendToExistingFile = false;",
        "BeginMissionSequence;",
    ]
    parameters=" ".join(
        [f"Sun.EarthICRF.{axis}" for axis in AXES]+
        [f"Sun.EarthMJ2000Eq.{axis}" for axis in AXES])
    for epoch in a1_mjd:
        lines += [
            f"Sun.Epoch.A1ModJulian = {epoch:.16g};",
            f"Report SunAnchor {parameters};",
        ]
    result="\n".join(lines)+"\n"
    if not result.isascii():
        raise ValueError("DT2_SUN_ANCHOR_NON_ASCII_GMAT_SCRIPT")
    return result


def parse_gmat_sun(path: Path) -> list[list[float]]:
    result=[]
    with path.open("r",encoding="utf-8-sig",newline="") as stream:
        for values in csv.reader(stream,delimiter=","):
            if not values or all(not s.strip() for s in values):
                continue
            if len(values)!=12:
                raise ValueError("DT2_SUN_ANCHOR_WRONG_GMAT_WIDTH")
            try:
                floats=[float(x.strip()) for x in values]
            except ValueError as ex:
                raise ValueError("DT2_SUN_ANCHOR_BAD_GMAT_NUMBER") from ex
            if not all(math.isfinite(x) for x in floats):
                raise ValueError("DT2_SUN_ANCHOR_NONFINITE_GMAT_STATE")
            if not 1.0e8 < math.sqrt(sum(x*x for x in floats[:3])) < 2.0e8:
                raise ValueError("DT2_SUN_ANCHOR_IMPLAUSIBLE_SUN_DISTANCE_KM")
            result.append(floats)
    if len(result)!=3:
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_THREE_GMAT_EPOCHS")
    return result


def same_source_basilisk_sun(kernel_root: Path, epoch: str) -> list[list[float]]:
    """Actually execute Basilisk 2.11.0 SpiceInterface with the pinned SPK.

    This is a separate real BSK environment run, not a claim that the old
    central-point-mass simulation was physically tied to these axes.
    """
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeGravBody
    import Basilisk
    if Basilisk.__version__!="2.11.0":
        raise RuntimeError("DT2_SUN_ANCHOR_WRONG_BASILISK")
    sc_sim=SimulationBaseClass.SimBaseClass()
    process=sc_sim.CreateNewProcess("dt2_sun_anchor_process")
    task="dt2_sun_anchor_task"
    process.addTask(sc_sim.CreateNewTask(task,macros.sec2nano(2700.0)))
    factory=simIncludeGravBody.gravBodyFactory()
    factory.createBodies(["earth","sun"])
    when=datetime.fromisoformat(epoch.replace("Z","+00:00")).astimezone(timezone.utc)
    spice_time=when.strftime("%Y %B %d %H:%M:%S.%f")+" (UTC)"
    spice=factory.createSpiceInterface(
        str(kernel_root)+os.sep,spice_time,
        spiceKernelFileNames=[
            "DE421AllPlanets.bsp","SPICELeapSecondKernel.tls",
            "SPICEPlanetaryConstantsKernel.tpc"],
        epochInMsg=False)
    spice.zeroBase="earth"
    spice.referenceBase="J2000"
    sc_sim.AddModelToTask(task,spice)
    if len(spice.planetStateOutMsgs)!=2:
        raise RuntimeError("DT2_SUN_ANCHOR_BSK_PLANET_MESSAGE_COUNT")
    recorder=spice.planetStateOutMsgs[1].recorder(macros.sec2nano(2700.0))
    sc_sim.AddModelToTask(task,recorder)
    try:
        sc_sim.InitializeSimulation()
        sc_sim.ConfigureStopTime(macros.sec2nano(5400.0))
        sc_sim.ExecuteSimulation()
        seconds=recorder.times()*macros.NANO2SEC
        pos=recorder.PositionVector
        vel=recorder.VelocityVector
        if len(seconds)!=3 or any(abs(float(t)-i*2700)>1e-6
                                   for i,t in enumerate(seconds)):
            raise ValueError("DT2_SUN_ANCHOR_BSK_MESSAGE_TIME_GRID")
        result=[[float(v)/1000.0 for v in (*pos[i],*vel[i])]
                for i in range(3)]
        if not all(all(math.isfinite(x) for x in row) for row in result):
            raise ValueError("DT2_SUN_ANCHOR_BSK_NONFINITE")
        return result
    finally:
        factory.unloadSpiceKernels()


def dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))


def angle_arcsec(a: list[float],b: list[float]) -> float:
    n1=math.sqrt(sum(x*x for x in a))
    n2=math.sqrt(sum(x*x for x in b))
    if min(n1,n2)<=0:
        raise ValueError("DT2_SUN_ANCHOR_ZERO_VECTOR")
    # atan2(cross,dot) is stable for sub-arcsecond differences; acos of a
    # near-unit cosine loses many significant digits to cancellation.
    x=a[1]*b[2]-a[2]*b[1]
    y=a[2]*b[0]-a[0]*b[2]
    z=a[0]*b[1]-a[1]*b[0]
    sinang=math.sqrt(x*x+y*y+z*z)/(n1*n2)
    cosang=sum(x*y for x,y in zip(a,b))/(n1*n2)
    return math.atan2(sinang,cosang)*206264.806247


def run(out: Path, root: Path, binary: Path) -> dict:
    out=out.resolve(strict=True)
    root=root.resolve(strict=True)
    binary=binary.resolve(strict=True)
    if not binary.is_file():
        raise ValueError("DT2_SUN_ANCHOR_REAL_GMAT_BINARY_REQUIRED")
    manifest=json.loads((out/"case_manifest.json").read_text(encoding="utf-8"))
    evidence=manifest.get("evidence",{})
    if (not evidence.get("basilisk_executed")
        or not evidence.get("gmat_executed")
        or not evidence.get("gmat_mu_control_executed")
        or not evidence.get("gmat_frame_time_probe_executed")
        or digest(binary)!=evidence.get("gmat_binary_sha256")):
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_PRIOR_REAL_ENGINE_RECEIPTS")
    old=json.loads((out/"gmat_frame_time_execution.json").read_text(encoding="utf-8"))
    frame=out/"gmat_frame_time_rows.csv"
    if old.get("status")!="GMAT_FRAME_TIME_REPORTED_UNQUALIFIED_ABSOLUTE_TIE":
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_REAL_FRAME_RECEIPT")
    if digest(frame)!=old.get("normalized_frame_time_sha256"):
        raise ValueError("DT2_SUN_ANCHOR_PRIOR_FRAME_HASH_INVALID")
    with frame.open(newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=91:
        raise ValueError("DT2_SUN_ANCHOR_BAD_PRIOR_CLOCK_GRID")
    a1_epochs=[float(rows[i]["a1_mjd"]) for i in INDICES]
    paths={
        "spk":root/"data/planetary_ephem/spk/DE421AllPlanets.bsp",
        "lsk":root/"data/time/SPICELeapSecondKernel.tls",
        "pck":root/"data/planetary_coeff/SPICEPlanetaryConstantsKernel.tpc",
    }
    for name,path in paths.items():
        if not path.is_file() or path.stat().st_size<100:
            raise FileNotFoundError("DT2_SUN_ANCHOR_GMAT_OFFICIAL_KERNEL_MISSING: "+name)
    outputs={
        "script":out/"gmat_sun_anchor_executed.script",
        "raw":out/"gmat_sun_anchor_raw.txt",
        "stdout":out/"gmat_sun_anchor_stdout.log",
        "stderr":out/"gmat_sun_anchor_stderr.log",
        "rows":out/"sun_anchor_three_engines.csv",
        "receipt":out/"sun_anchor_execution.json",
    }
    if any(p.exists() for p in outputs.values()):
        raise FileExistsError("DT2_SUN_ANCHOR_IMMUTABLE_OUTPUT_COLLISION")
    outputs["script"].write_text(sun_script(
        a1_epochs,spk=paths["spk"],lsk=paths["lsk"],pck=paths["pck"],
        report_path=outputs["raw"]),encoding="ascii")
    proc=subprocess.run([str(binary),"--run",str(outputs["script"])],
        cwd=binary.parent,capture_output=True,text=True,timeout=240,check=False)
    outputs["stdout"].write_text(proc.stdout,encoding="utf-8")
    outputs["stderr"].write_text(proc.stderr,encoding="utf-8")
    receipt={
        "status":"INCONCLUSIVE",
        "engine":"genuine GMAT R2026a + Basilisk 2.11.0 SpiceInterface + spiceypy 8.4.0",
        "command":[str(binary),"--run",str(outputs["script"])],
        "gmat_exit_code":proc.returncode,
        "gmat_script_sha256":digest(outputs["script"]),
        "gmat_binary_sha256":digest(binary),
        "gmat_stdout_sha256":digest(outputs["stdout"]),
        "kernel_sha256":{k:digest(v) for k,v in paths.items()},
        "gmat_sun_report_produced":outputs["raw"].is_file(),
        "gmat_sun_report_sha256":digest(outputs["raw"]) if outputs["raw"].is_file() else None,
        "source_frame_report_sha256":digest(frame),
        "date_utc":manifest["case"]["epoch_utc"],
        "clock_epochs_A1_mjd":a1_epochs,
        "independent_physical_basilisk_n_to_icrf_tie_approved":False,
        "mission_orbit_accuracy_budget_approved":False,
        "scientific_verdict":"INCONCLUSIVE",
    }
    write_json(outputs["receipt"],receipt)
    if proc.returncode!=0 or not outputs["raw"].is_file():
        raise RuntimeError("DT2_SUN_ANCHOR_REAL_GMAT_EXECUTION_FAILED")
    gmats=parse_gmat_sun(outputs["raw"])
    # Third-party dependency is needed ONLY after actual GMAT execution and
    # immutable source checks, never to import the CLI or reject missing data.
    import spiceypy as spice
    epoch=datetime.fromisoformat(manifest["case"]["epoch_utc"].replace("Z","+00:00"))
    spice.furnsh(str(paths["lsk"]))
    spice.furnsh(str(paths["spk"]))
    naif=[]
    try:
        for index in INDICES:
            at=(epoch+timedelta(seconds=index*60)).isoformat().replace("+00:00","Z")
            et=spice.str2et(at)
            state,_=spice.spkezr("SUN",et,"J2000","NONE","EARTH")
            naif.append([float(x) for x in state])
    finally:
        spice.kclear()
    with tempfile.TemporaryDirectory(prefix="dt2_sun_spice_") as td:
        tmp=Path(td)
        for name,src in (("DE421AllPlanets.bsp",paths["spk"]),
                         ("SPICELeapSecondKernel.tls",paths["lsk"]),
                         ("SPICEPlanetaryConstantsKernel.tpc",paths["pck"])):
            os.symlink(src,tmp/name)
        basilisk=same_source_basilisk_sun(tmp,manifest["case"]["epoch_utc"])
    compared=[]
    for i,(gm,na,bs) in enumerate(zip(gmats,naif,basilisk)):
        compared.append({
            "time_s":INDICES[i]*60,
            "gmat_a1_mjd":a1_epochs[i],
            "gmat_icrf_vs_naif_j2000_pos_m":dist(gm[:3],na[:3])*1e3,
            "gmat_icrf_vs_naif_j2000_angle_arcsec":angle_arcsec(gm[:3],na[:3]),
            "basilisk_n_vs_naif_j2000_pos_m":dist(bs[:3],na[:3])*1e3,
            "basilisk_n_vs_naif_j2000_angle_arcsec":angle_arcsec(bs[:3],na[:3]),
            "gmat_mj_vs_icrf_pos_m":dist(gm[6:9],gm[:3])*1e3,
            "gmat_icrf_sun_x_km":gm[0],"gmat_icrf_sun_y_km":gm[1],
            "gmat_icrf_sun_z_km":gm[2],
            "naif_j2000_sun_x_km":na[0],"naif_j2000_sun_y_km":na[1],
            "naif_j2000_sun_z_km":na[2],
            "basilisk_spice_n_sun_x_km":bs[0],
            "basilisk_spice_n_sun_y_km":bs[1],
            "basilisk_spice_n_sun_z_km":bs[2],
        })
    with outputs["rows"].open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(compared[0]))
        writer.writeheader()
        writer.writerows(compared)
    receipt.update({
        "real_sample_count":len(compared),
        "sun_anchor_comparison_csv_sha256":digest(outputs["rows"]),
        "max_gmat_icrf_vs_naif_j2000_angle_arcsec":
            max(x["gmat_icrf_vs_naif_j2000_angle_arcsec"] for x in compared),
        "max_basilisk_n_vs_naif_j2000_angle_arcsec":
            max(x["basilisk_n_vs_naif_j2000_angle_arcsec"] for x in compared),
        "max_basilisk_n_vs_naif_j2000_pos_m":
            max(x["basilisk_n_vs_naif_j2000_pos_m"] for x in compared),
        "spice_j2000_frame_semantics":"NAIF SPICE J2000 uses ICRF axes",
        "gmat_solar_ephemeris_declared":"SPICE with packaged DE421 SPK",
        "naif_ephemeris":"same packaged DE421 SPK; independent spiceypy query",
        "basilisk_spice_interface":"actual model using same explicit SPK and J2000 referenceBase",
        "original_pointmass_n_axes_externally_certified":False,
        "status":"REAL_THREE_ENGINE_CELESTIAL_SOURCE_OBSERVED_INCONCLUSIVE",
    })
    write_json(outputs["receipt"],receipt)
    if len(compared)!=3 or not all(math.isfinite(v) for x in compared for v in x.values()):
        raise RuntimeError("DT2_SUN_ANCHOR_INVALID_CELESTIAL_DIAGNOSTIC")
    return receipt


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--gmat-root",required=True,type=Path)
    parser.add_argument("--gmat-executable",required=True,type=Path)
    args=parser.parse_args()
    r=run(args.output,args.gmat_root,args.gmat_executable)
    print("DT2_REAL_SUN_CELESTIAL_ANCHOR "
          f"rows={r['real_sample_count']} "
          f"gmat_naif_direction_max_arcsec={r['max_gmat_icrf_vs_naif_j2000_angle_arcsec']:.9e} "
          f"bsk_naif_direction_max_arcsec={r['max_basilisk_n_vs_naif_j2000_angle_arcsec']:.9e} "
          "status=INCONCLUSIVE absolute_pointmass_frame_tie=false")


if __name__=="__main__":
    main()
