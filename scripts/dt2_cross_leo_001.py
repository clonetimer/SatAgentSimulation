#!/usr/bin/env python3
"""CROSS-LEO-001: real Basilisk two-body state export and GMAT input handoff.

用途：执行真实 Basilisk 二体六维状态导出；准备 GMAT 输入并保守计算独立轨迹残差，不伪造外部观测。
参数：basilisk --output --checkout-sha；gmat --output --executable --timeout-s；compare --output。
输出：真实 basilisk_state.csv、case_manifest.json、gmat_two_body.script；仅执行 GMAT 后才输出真实 gmat_raw.txt、gmat_state.csv 和比较诊断报告。

No fabricated external-engine output. A GMAT CSV is usable only when an actual
GMATConsole executable has been invoked and its hash/logs have been retained.
The comparison remains INCONCLUSIVE until independent alignment and a frozen
mission accuracy budget have been approved.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from importlib.metadata import version as distribution_version

CASE_ID = "CROSS-LEO-001"
SCHEMA = "dt2.cross-leo001.v1"
FIELDNAMES = ("time_s", "utc", "x_m", "y_m", "z_m", "vx_m_s", "vy_m_s", "vz_m_s")
# Predeclared *input representation* allowance for the NEXT GMAT run, not
# a physical position/velocity error budget and not approval of time/frame
# equivalence. R2026a reported -10.6869 microseconds at 5400 s in raw pilot.
GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S = 20e-6
# Only the a/e/i/epoch/duration/step/telemetry cadence are carried forward from
# the actual #213 test. The initial phase was NOT recoverable from sealed
# P9-O3 evidence; set f=0 *explicitly* for a fresh, reproducible two-body case.
CASE = {
    "case_id": CASE_ID,
    "epoch_utc": "2026-03-04T21:48:00Z",
    "duration_s": 5400,
    "integrator_step_s": 5,
    "sample_s": 60,
    "initial_elements": {
        "semi_major_axis_m": 6650000.0,
        "eccentricity": 0.0,
        "inclination_deg": 0.0,
        "raan_deg": 0.0,
        "arg_perigee_deg": 0.0,
        "true_anomaly_deg": 0.0,
    },
    "source_note": (
        "a/e/i/epoch/5400/5/60 from PR213 real integrated native orbit test; "
        "f=0 deg is a NEW declared CROSS-LEO-001 phase, NOT claimed to "
        "match PR213 SPICE-Sun-aligned anomaly"
    ),
}
MODEL_FLAGS = {
    "earth_point_mass": True,
    "j2_and_higher_gravity": False,
    "sun_moon_third_body": False,
    "atmospheric_drag": False,
    "solar_radiation_pressure": False,
    "solid_ocean_tides": False,
    "relativity": False,
    "atmospheric_wind": False,
    "magnetic_force": False,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _utc_at(start: str, elapsed: float) -> str:
    d = datetime.fromisoformat(start.replace("Z", "+00:00"))
    if d.tzinfo is None or d.utcoffset() != timedelta(0):
        raise ValueError("CROSS_LEO_EPOCH_MUST_BE_UTC")
    return (d + timedelta(seconds=elapsed)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_state_csv(path: Path, states: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in states:
            writer.writerow({key: row[key] for key in FIELDNAMES})


def _read_states(path: Path, epoch: str, *, tick_skew_budget_s: float = 1e-5) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != FIELDNAMES:
            raise ValueError(f"CROSS_LEO_BAD_STATE_HEADER: {reader.fieldnames!r}")
        result = []
        for row in reader:
            if None in row or any(row.get(key) is None for key in FIELDNAMES):
                raise ValueError("CROSS_LEO_MALFORMED_STATE_ROW")
            item = {key: float(row[key]) for key in FIELDNAMES if key not in {"utc"}}
            if not all(math.isfinite(value) for value in item.values()):
                raise ValueError("CROSS_LEO_NONFINITE_STATE")
            if row["utc"] != _utc_at(epoch, item["time_s"]):
                raise ValueError("CROSS_LEO_UTC_MISMATCH")
            item["utc"] = row["utc"]
            result.append(item)
    _assert_grid(result, tick_skew_budget_s=tick_skew_budget_s)
    return result


def _assert_grid(rows: list[dict], *, tick_skew_budget_s: float = 1e-5) -> None:
    expected_len = CASE["duration_s"] // CASE["sample_s"] + 1
    if len(rows) != expected_len:
        raise ValueError(f"CROSS_LEO_INCOMPLETE_GRID: {len(rows)} != {expected_len}")
    for index, row in enumerate(rows):
        expected = index * CASE["sample_s"]
        if not math.isfinite(float(row["time_s"])) or abs(float(row["time_s"]) - expected) > tick_skew_budget_s:
            raise ValueError(f"CROSS_LEO_TIME_GRID_MISMATCH: {index}, {row['time_s']!r}")
        for key in FIELDNAMES:
            if key not in row:
                raise ValueError(f"CROSS_LEO_MISSING_FIELD: {key}")


def _gmat_script(initial_state: dict, *, report_path: str) -> str:
    """Official GMAT syntax; *not* an executed GMAT result or syntax certification.

    GMAT states are km and km/s in EarthMJ2000Eq, Basilisk records SI m/m/s
    in Earth-centered inertial N. No cross-engine coordinate equivalence is
    asserted until a genuinely executed GMAT report is checked.
    """
    vals = [initial_state[key] for key in FIELDNAMES[2:]]
    if any(not math.isfinite(float(v)) for v in vals):
        raise ValueError("CROSS_LEO_BAD_GMAT_INITIAL_STATE")
    posvel_km = [v / 1000.0 for v in vals]
    stamp = datetime.fromisoformat(CASE["epoch_utc"].replace("Z", "+00:00"))
    epoch_gmat = stamp.strftime("%d %b %Y %H:%M:%S.%f")[:-3]
    path = report_path.replace("\\", "/").replace("'", "")
    items = [
        "% CROSS-LEO-001 - fresh two-body baseline, not #213 Sun-aligned phase",
        "% Input six-state was measured/derived from real Basilisk 2.11.0 execution.",
        "% GMAT run must be performed separately; generated script is not proof of GMAT output.",
        "Create Spacecraft CrossSat;",
        "CrossSat.DateFormat = UTCGregorian;",
        f"CrossSat.Epoch = '{epoch_gmat}';",
        "CrossSat.CoordinateSystem = EarthMJ2000Eq;",
        "CrossSat.DisplayStateType = Cartesian;",
    ]
    for key, value in zip(("X", "Y", "Z", "VX", "VY", "VZ"), posvel_km):
        items.append(f"CrossSat.{key} = {float(value):.16g};")
    items += [
        "CrossSat.DryMass = 100;",
        "Create ForceModel CrossGravity;",
        "CrossGravity.CentralBody = Earth;",
        "CrossGravity.PointMasses = {Earth};",
        "CrossGravity.Drag = None;",
        "CrossGravity.SRP = Off;",
        "CrossGravity.RelativisticCorrection = Off;",
        "CrossGravity.ErrorControl = RSSStep;",
        "Create Propagator CrossProp;",
        "CrossProp.FM = CrossGravity;",
        "CrossProp.Type = RungeKutta89;",
        "CrossProp.InitialStepSize = 5;",
        "CrossProp.MinStep = 0.0001;",
        "CrossProp.MaxStep = 5;",
        "CrossProp.Accuracy = 1e-12;",
        "CrossProp.MaxStepAttempts = 50;",
        "CrossProp.StopIfAccuracyIsViolated = true;",
        "Create ReportFile CrossState;",
        f"CrossState.Filename = '{path}';",
        "CrossState.Precision = 16;",
        "CrossState.WriteHeaders = false;",
        "CrossState.FixedWidth = false;",
        "CrossState.Delimiter = ',';",
        "CrossState.WriteReport = false;",
        "CrossState.AppendToExistingFile = false;",
        "BeginMissionSequence;",
    ]
    fields = "CrossSat.ElapsedSecs " + " ".join(
        f"CrossSat.EarthMJ2000Eq.{v}" for v in ("X", "Y", "Z", "VX", "VY", "VZ")
    )
    items.append(f"Report CrossState {fields};")
    # Actual R2026a Console interprets a Propagate ElapsedSecs target
    # relative to the START OF THIS PROPAGATE command (not mission t=0).
    # A 60,120,180,... sequence incorrectly yields triangular 0..245700s.
    # Keep each command at 60 seconds to obtain t=0,60,...5400s.
    for _ in range(CASE["sample_s"], CASE["duration_s"] + 1, CASE["sample_s"]):
        items.append(f"Propagate CrossProp(CrossSat) {{CrossSat.ElapsedSecs = {CASE['sample_s']}}};")
        items.append(f"Report CrossState {fields};")
    script = "\n".join(items) + "\n"
    # GMAT R2026a Console deliberately rejects non-ASCII script bytes.
    # Refuse at generation time rather than mislabeling a runtime parser failure.
    if not script.isascii():
        raise ValueError("CROSS_LEO_GMAT_SCRIPT_MUST_BE_ASCII")
    return script


def run_basilisk(out: Path, source_sha: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("CROSS_LEO_GIT_SHA_REQUIRED")
    from Basilisk.simulation import spacecraft  # installed upstream only
    from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody
    import Basilisk

    if distribution_version("bsk") != "2.11.0" or Basilisk.__version__ != "2.11.0":
        raise RuntimeError("CROSS_LEO_REQUIRES_REAL_UPSTREAM_BASILISK_2_11_0")
    if out.exists() and any(out.iterdir()):
        raise FileExistsError("CROSS_LEO_REFUSES_OVERWRITE_OF_EXISTING_EVIDENCE")
    out.mkdir(parents=True, exist_ok=True)
    sim = SimulationBaseClass.SimBaseClass()
    proc = sim.CreateNewProcess("cross_leo_001_process")
    task = "cross_leo_001_task"
    proc.addTask(sim.CreateNewTask(task, macros.sec2nano(CASE["integrator_step_s"])))
    body = spacecraft.Spacecraft()
    body.ModelTag = "CROSS-LEO-001-actual-basilisk-two-body"
    body.hub.mHub = 100.0
    body.hub.IHubPntBc_B = [[100.0, 0., 0.], [0., 100.0, 0.], [0., 0., 100.0]]
    sim.AddModelToTask(task, body)
    gravity = simIncludeGravBody.gravBodyFactory()
    earth = gravity.createEarth()
    earth.isCentralBody = True
    gravity.addBodiesTo(body)
    elements = orbitalMotion.ClassicElements()
    oe = CASE["initial_elements"]
    elements.a = oe["semi_major_axis_m"]
    elements.e = oe["eccentricity"]
    elements.i = oe["inclination_deg"] * macros.D2R
    elements.Omega = oe["raan_deg"] * macros.D2R
    elements.omega = oe["arg_perigee_deg"] * macros.D2R
    elements.f = oe["true_anomaly_deg"] * macros.D2R
    r0, v0 = orbitalMotion.elem2rv(earth.mu, elements)
    body.hub.r_CN_NInit = r0
    body.hub.v_CN_NInit = v0
    recorder = body.scStateOutMsg.recorder(macros.sec2nano(CASE["sample_s"]))
    sim.AddModelToTask(task, recorder)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(CASE["duration_s"]))
    sim.ExecuteSimulation()
    times = recorder.times() * macros.NANO2SEC
    position, velocity = recorder.r_BN_N, recorder.v_BN_N
    rows = []
    for index, seconds in enumerate(times):
        t = float(seconds)
        values = [*map(float, position[index]), *map(float, velocity[index])]
        row = dict(zip(FIELDNAMES[2:], values))
        row.update(time_s=t, utc=_utc_at(CASE["epoch_utc"], t))
        rows.append(row)
    _assert_grid(rows)
    actual_initial = rows[0]
    for field, actual in zip(FIELDNAMES[2:], [*r0, *v0]):
        if not math.isclose(float(actual_initial[field]), float(actual), rel_tol=0, abs_tol=1e-6):
            raise RuntimeError(f"CROSS_LEO_TRUE_INITIAL_STATE_DRIFT: {field}")
    baseline = out / "basilisk_state.csv"
    _write_state_csv(baseline, rows)
    gmat_script = out / "gmat_two_body.script"
    gmat_script.write_text(_gmat_script(actual_initial, report_path="gmat_raw.txt"), encoding="utf-8")
    manifest = {
        "schema": SCHEMA,
        "case": CASE,
        "source": {"repository": "clonetimer/SatAgentSimulation", "git_checkout_sha": source_sha},
        "actual_engine": {"name": "Basilisk", "package": "bsk", "version": Basilisk.__version__,
                          "python": platform.python_version(), "system": platform.platform()},
        "actual_initial_state_SI": {key: actual_initial[key] for key in FIELDNAMES},
        "gravity": {"body": "Earth", "source": "Basilisk gravBodyFactory.createEarth()",
                    "mu_m3_s2": float(earth.mu)},
        "frames": {"basilisk": "Earth-centered N inertial", "gmat_expected": "EarthMJ2000Eq",
                   "independent_alignment_approved": False},
        "physical_flags": MODEL_FLAGS,
        "numerics": {"basilisk_integrator": "Basilisk spacecraft integrated process, fixed 5s task",
                     "gmat_integrator_requested": "RungeKutta89, max step 5s, accuracy 1e-12",
                     "duration_s": CASE["duration_s"], "sample_s": CASE["sample_s"]},
        "evidence": {"basilisk_state_csv_sha256": _sha256(baseline),
                     "gmat_script_template_sha256": _sha256(gmat_script),
                     "basilisk_state_rows": len(rows),
                     "basilisk_executed": True, "gmat_executed": False},
        "external_vv_status": "NOT_RUN",
        "acceptance_limits_status": "NOT_APPROVED",
        "limitations": ["not the Sun-aligned full-four-effect #213 initial phase",
                         "no external GMAT execution or input-constant/frame alignment",
                         "synthetic engineering test, not flight-calibrated"],
    }
    _write_json(out / "case_manifest.json", manifest)
    return manifest


def _read_gmat_report(raw: Path, epoch: str) -> list[dict]:
    """Read actual headerless GMAT Report command output: elapsed_s, km*3, km/s*3."""
    rows = []
    with raw.open("r", encoding="utf-8-sig", newline="") as stream:
        for line in csv.reader(stream, delimiter=","):
            if not line or all(not v.strip() for v in line):
                continue
            if len(line) != 7:
                raise ValueError(f"CROSS_LEO_BAD_GMAT_REPORT_WIDTH: {len(line)}")
            nums = [float(v.strip()) for v in line]
            if any(not math.isfinite(v) for v in nums):
                raise ValueError("CROSS_LEO_NONFINITE_GMAT_REPORT")
            row = {"time_s": nums[0], "utc": _utc_at(epoch, nums[0])}
            row.update({k: v * 1000.0 for k, v in zip(FIELDNAMES[2:], nums[1:])})
            rows.append(row)
    # GMAT ElapsedSecs is recorded in floating point via its internal
    # calendar/elapsed conversion. Preserve every raw timestamp; constrain
    # representation skew for this test without inventing an exact clock tick.
    _assert_grid(rows, tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    return rows


def run_gmat(out: Path, binary: Path, *, timeout_s: int = 180) -> dict:
    """Invoke a real GMATConsole; never accept an unverified report by itself."""
    manifest_path = out / "case_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or not manifest["evidence"].get("basilisk_executed"):
        raise ValueError("CROSS_LEO_BASILISK_BUNDLE_REQUIRED")
    if _sha256(out / "basilisk_state.csv") != manifest["evidence"]["basilisk_state_csv_sha256"]:
        raise ValueError("CROSS_LEO_BASILISK_DATA_HASH_CHANGED")
    if _sha256(out / "gmat_two_body.script") != manifest["evidence"]["gmat_script_template_sha256"]:
        raise ValueError("CROSS_LEO_GMAT_TEMPLATE_HASH_CHANGED")
    executable = binary.expanduser().resolve(strict=True)
    if not executable.is_file():
        raise ValueError("CROSS_LEO_GMAT_BINARY_INVALID")
    raw = (out / "gmat_raw.txt").resolve()
    if raw.exists():
        raise FileExistsError("CROSS_LEO_REFUSES_STALE_GMAT_REPORT")
    script = (out / "gmat_executed.script").resolve()
    script.write_text(_gmat_script(manifest["actual_initial_state_SI"], report_path=str(raw)), encoding="utf-8")
    version_probe = subprocess.run([str(executable), "--version"], cwd=executable.parent,
                                   text=True, capture_output=True, timeout=30, check=False)
    stdout_path, stderr_path = out / "gmat_stdout.log", out / "gmat_stderr.log"
    run = subprocess.run([str(executable), "--run", str(script)], cwd=executable.parent,
                         text=True, capture_output=True, timeout=timeout_s, check=False)
    stdout_path.write_text(run.stdout, encoding="utf-8")
    stderr_path.write_text(run.stderr, encoding="utf-8")
    record = {
        "schema": SCHEMA, "engine": "GMATConsole", "binary_path": str(executable),
        "binary_sha256": _sha256(executable), "command": [str(executable), "--run", str(script)],
        "script_sha256": _sha256(script), "version_return_code": version_probe.returncode,
        "version_stdout": version_probe.stdout[:4000], "version_stderr": version_probe.stderr[:2000],
        "release_r2026a_verified": bool(re.search(r"R2026a", version_probe.stdout + run.stdout, re.I)),
        "return_code": run.returncode, "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
        "report_present": raw.is_file(), "report_sha256": _sha256(raw) if raw.is_file() else None,
        "status": "GMAT_RUN_FAILED",
    }
    if run.returncode == 0 and raw.is_file():
        try:
            rows = _read_gmat_report(raw, CASE["epoch_utc"])
            _write_state_csv(out / "gmat_state.csv", rows)
            record["normalized_csv_sha256"] = _sha256(out / "gmat_state.csv")
            record["rows"] = len(rows)
            record["max_abs_time_skew_s"] = max(abs(row["time_s"] - idx*CASE["sample_s"])
                                                for idx,row in enumerate(rows))
            record["report_time_representation_budget_s"] = GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S
            record["time_frame_alignment_approved"] = False
            record["status"] = "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"
        except (ValueError, OverflowError) as exc:
            record["parse_error"] = f"{type(exc).__name__}: {exc}"
    _write_json(out / "gmat_execution.json", record)
    if record["status"] == "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT":
        # The initial manifest correctly starts NOT_RUN. Change its state
        # only after a real console run AND a valid, hashed 91-row report.
        manifest["evidence"]["gmat_executed"] = True
        manifest["evidence"]["gmat_report_sha256"] = record["report_sha256"]
        manifest["evidence"]["gmat_normalized_csv_sha256"] = record["normalized_csv_sha256"]
        manifest["evidence"]["gmat_binary_sha256"] = record["binary_sha256"]
        manifest["external_vv_status"] = "INCONCLUSIVE"
        manifest["acceptance_limits_status"] = "NOT_APPROVED"
        _write_json(manifest_path, manifest)
    if record["status"] == "GMAT_RUN_FAILED":
        raise RuntimeError("CROSS_LEO_GMAT_NOT_VERIFIED: inspect gmat_execution.json")
    return record


def compare(out: Path) -> dict:
    """Diagnostic only. Never label an unreviewed two-engine residual PASS."""
    manifest = json.loads((out / "case_manifest.json").read_text(encoding="utf-8"))
    record = json.loads((out / "gmat_execution.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != SCHEMA
            or not manifest.get("evidence", {}).get("gmat_executed")
            or record.get("status") != "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"):
        raise ValueError("CROSS_LEO_REAL_DUAL_ENGINE_EVIDENCE_REQUIRED")
    if not record.get("report_present") or _sha256(out / "gmat_raw.txt") != record.get("report_sha256"):
        raise ValueError("CROSS_LEO_GMAT_RAW_HASH_INVALID")
    if _sha256(out / "gmat_state.csv") != record.get("normalized_csv_sha256"):
        raise ValueError("CROSS_LEO_GMAT_NORMALIZED_HASH_INVALID")
    if _sha256(out / "basilisk_state.csv") != manifest["evidence"].get("basilisk_state_csv_sha256"):
        raise ValueError("CROSS_LEO_BASILISK_HASH_INVALID")
    bsk = _read_states(out / "basilisk_state.csv", CASE["epoch_utc"])
    gmat = _read_states(out / "gmat_state.csv", CASE["epoch_utc"],
                        tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    deltas = []
    clock_skew = [b["time_s"] - a["time_s"] for a,b in zip(bsk,gmat)]
    for a, b in zip(bsk, gmat):
        dpos = [b[k] - a[k] for k in ("x_m", "y_m", "z_m")]
        dvel = [b[k] - a[k] for k in ("vx_m_s", "vy_m_s", "vz_m_s")]
        pos = [a[k] for k in ("x_m", "y_m", "z_m")]
        vel = [a[k] for k in ("vx_m_s", "vy_m_s", "vz_m_s")]
        rn = math.sqrt(sum(x*x for x in pos))
        if rn < 1.0:
            raise ValueError("CROSS_LEO_RTN_BAD_RADIUS")
        rhat = [x / rn for x in pos]
        h = (pos[1]*vel[2]-pos[2]*vel[1], pos[2]*vel[0]-pos[0]*vel[2], pos[0]*vel[1]-pos[1]*vel[0])
        hn = math.sqrt(sum(x*x for x in h))
        if hn < 1.0:
            raise ValueError("CROSS_LEO_RTN_BAD_ANGULAR_MOMENTUM")
        nhat = [x / hn for x in h]
        that = [nhat[1]*rhat[2]-nhat[2]*rhat[1], nhat[2]*rhat[0]-nhat[0]*rhat[2], nhat[0]*rhat[1]-nhat[1]*rhat[0]]
        deltas.append({"time_s": a["time_s"], "gmat_raw_time_s": b["time_s"],
                       "clock_skew_s": b["time_s"] - a["time_s"],
                       "delta_position_m": math.sqrt(sum(x*x for x in dpos)),
                       "delta_velocity_m_s": math.sqrt(sum(x*x for x in dvel)),
                       "delta_R_m": sum(x*y for x,y in zip(dpos,rhat)),
                       "delta_T_m": sum(x*y for x,y in zip(dpos,that)),
                       "delta_N_m": sum(x*y for x,y in zip(dpos,nhat))})
    def percentile(seq: list[float], frac: float) -> float:
        ordered = sorted(seq)
        ix = (len(ordered)-1)*frac
        lower=int(ix)
        f=ix-lower
        return ordered[lower]*(1-f)+ordered[min(lower+1,len(ordered)-1)]*f
    numbers=[(name,[r[name] for r in deltas]) for name in ("delta_position_m","delta_velocity_m_s")]
    diagnostics={name:{"first":values[0],"mid":values[len(values)//2],"last":values[-1],
                       "max":max(values),"p95":percentile(values,.95),"p99":percentile(values,.99)} for name,values in numbers}
    result={"schema":SCHEMA,"case_id":CASE_ID,"status":"INCONCLUSIVE",
            "reason":"INDEPENDENT_TIME_FRAME_MU_ALIGNMENT_AND_FROZEN_TOLERANCES_NOT_APPROVED",
            "time_alignment_status":"UNAPPROVED_RAW_ENGINE_TIME_SKEW_RETAINED",
            "reported_gmat_time_skew_max_abs_s":max(abs(v) for v in clock_skew),
            "input_representation_budget_s":GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S,
            "clock_skew_position_equivalent_upper_m":max(
                abs(dt)*math.sqrt(sum(a[k]**2 for k in ("vx_m_s","vy_m_s","vz_m_s")))
                for dt,a in zip(clock_skew,bsk)),
            "gmat_executable_sha256":record["binary_sha256"],
            "gmat_release_verified":record["release_r2026a_verified"],
            "measurements":diagnostics,"paired_samples":len(deltas),
            "aligned_samples":0,  # no approved epoch/frame/GM alignment
            "data_hashes":{"basilisk":manifest["evidence"]["basilisk_state_csv_sha256"],
                           "gmat_report":record["report_sha256"]}}
    _write_json(out / "comparison_diagnostic.json", result)
    with (out / "difference_rows.csv").open("w", newline="", encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(deltas[0]))
        writer.writeheader()
        writer.writerows(deltas)
    return result


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    b=sub.add_parser("basilisk",help="Actually execute Basilisk; generate 6D CSV+manifest+GMAT script")
    b.add_argument("--output",type=Path,required=True)
    b.add_argument("--checkout-sha",required=True)
    g=sub.add_parser("gmat",help="Actually invoke installed GMATConsole and hash its raw report")
    g.add_argument("--output",type=Path,required=True)
    g.add_argument("--executable",type=Path,required=True)
    g.add_argument("--timeout-s",type=int,default=180)
    c=sub.add_parser("compare",help="Only compute real dual-engine residual diagnostics; no PASS claim")
    c.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action=="basilisk":
        result=run_basilisk(args.output,args.checkout_sha)
    elif args.action=="gmat":
        result=run_gmat(args.output,args.executable,timeout_s=args.timeout_s)
    else:
        result=compare(args.output)
    print(json.dumps({"case_id":CASE_ID,"action":args.action,"result":result.get("external_vv_status",result.get("status","BASILISK_EXECUTED"))},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
