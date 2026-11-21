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
# Only a numeric time-REPORT sanity bound (not spacecraft timing accuracy).
GMAT_FRAME_TIME_REPORT_SANITY_S = 1e-3
GMAT_MJD_JD_OFFSET_DAYS = 2430000.0
GMAT_FRAME_TIME_COLUMNS = ("elapsed_s", "utc_mjd", "tai_mjd", "tt_mjd", "a1_mjd", "mj_x_km", "mj_y_km", "mj_z_km", "mj_vx_km_s", "mj_vy_km_s", "mj_vz_km_s", "icrf_x_km", "icrf_y_km", "icrf_z_km", "icrf_vx_km_s", "icrf_vy_km_s", "icrf_vz_km_s")
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


def _gmat_script(initial_state: dict, *, report_path: str,
                 earth_mu_km3_s2: float | None = None,
                 state_frame: str = "EarthMJ2000Eq") -> str:
    """Official GMAT syntax; *not* an executed GMAT result or syntax certification.

    GMAT states are km and km/s in EarthMJ2000Eq, Basilisk records SI m/m/s
    in Earth-centered inertial N. No cross-engine coordinate equivalence is
    asserted until a genuinely executed GMAT report is checked.
    """
    if state_frame not in ("EarthMJ2000Eq", "EarthICRF"):
        raise ValueError("CROSS_LEO_GMAT_FRAME_NOT_WHITELISTED")
    if earth_mu_km3_s2 is not None:
        if (type(earth_mu_km3_s2) not in (int, float) or not math.isfinite(earth_mu_km3_s2)
                or earth_mu_km3_s2 <= 0):
            raise ValueError("CROSS_LEO_INVALID_GMAT_MU_CONTROL")
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
        f"CrossSat.CoordinateSystem = {state_frame};",
        "CrossSat.DisplayStateType = Cartesian;",
    ]
    if earth_mu_km3_s2 is not None:
        # Explicit built-in celestial-body constant override, BEFORE the
        # force model is constructed. Proven GMAT approach in Stanford-NavLab
        # LuPNT's independent cross-validation generator.
        items.insert(3, f"GMAT Earth.Mu = {earth_mu_km3_s2:.15g};")
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
        f"CrossSat.{state_frame}.{v}" for v in ("X", "Y", "Z", "VX", "VY", "VZ")
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


def run_gmat(out: Path, binary: Path, *, timeout_s: int = 180,
             matched_mu: bool = False) -> dict:
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
    mode = "gmat_mu_control" if matched_mu else "gmat"
    mu_override = None
    if matched_mu:
        original = json.loads((out / "gmat_execution.json").read_text(encoding="utf-8"))
        if (original.get("status") != "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"
            or not manifest["evidence"].get("gmat_executed")
            or _sha256(out / "gmat_raw.txt") != original.get("report_sha256")
            or _sha256(out / "gmat_state.csv") != original.get("normalized_csv_sha256")):
            raise ValueError("CROSS_LEO_ORIGINAL_GMAT_PROVENANCE_REQUIRED")
        if _sha256(executable) != original.get("binary_sha256"):
            raise ValueError("CROSS_LEO_MU_CONTROL_DIFFERENT_GMAT_BINARY")
        mu_override = float(manifest["gravity"]["mu_m3_s2"]) / 1e9
        if not math.isfinite(mu_override) or mu_override <= 0:
            raise ValueError("CROSS_LEO_MU_CONTROL_INVALID_BASILISK_CONSTANT")
    raw = (out / f"{mode}_raw.txt").resolve()
    script = (out / f"{mode}_executed.script").resolve()
    stdout_path, stderr_path = out / f"{mode}_stdout.log", out / f"{mode}_stderr.log"
    normalized = out / f"{mode}_state.csv"
    record_path = out / f"{mode}_execution.json"
    for path in (raw, script, stdout_path, stderr_path, normalized, record_path):
        if path.exists():
            raise FileExistsError(f"CROSS_LEO_REFUSES_OVERWRITE: {path.name}")
    script.write_text(_gmat_script(manifest["actual_initial_state_SI"],
                                   report_path=str(raw), earth_mu_km3_s2=mu_override),
                      encoding="utf-8")
    version_probe = subprocess.run([str(executable), "--version"], cwd=executable.parent,
                                   text=True, capture_output=True, timeout=30, check=False)
    run = subprocess.run([str(executable), "--run", str(script)], cwd=executable.parent,
                         text=True, capture_output=True, timeout=timeout_s, check=False)
    stdout_path.write_text(run.stdout, encoding="utf-8")
    stderr_path.write_text(run.stderr, encoding="utf-8")
    record = {
        "schema": SCHEMA, "engine": "GMATConsole", "binary_path": str(executable),
        "execution_mode": "BASILISK_MU_MATCHED_CONTROL" if matched_mu else "ORIGINAL_GMAT_DEFAULT_MU",
        "explicit_earth_mu_override_km3_s2": mu_override,
        "gm_engine_constant_independently_confirmed": False,
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
            _write_state_csv(normalized, rows)
            record["normalized_csv_sha256"] = _sha256(normalized)
            record["rows"] = len(rows)
            record["max_abs_time_skew_s"] = max(abs(row["time_s"] - idx*CASE["sample_s"])
                                                for idx,row in enumerate(rows))
            record["report_time_representation_budget_s"] = GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S
            record["time_frame_alignment_approved"] = False
            record["status"] = ("GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT"
                                if matched_mu else "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT")
        except (ValueError, OverflowError) as exc:
            record["parse_error"] = f"{type(exc).__name__}: {exc}"
    _write_json(record_path, record)
    if record["status"] in (
        "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT",
        "GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT",
    ):
        # Only a real console run with a valid, hashed 91-row report qualifies.
        ev = manifest["evidence"]
        if matched_mu:
            ev["gmat_mu_control_executed"] = True
            ev["gmat_mu_control_raw_sha256"] = record["report_sha256"]
            ev["gmat_mu_control_state_sha256"] = record["normalized_csv_sha256"]
            ev["gmat_mu_control_script_sha256"] = record["script_sha256"]
            ev["gmat_mu_control_requested_mu_km3_s2"] = mu_override
        else:
            ev["gmat_executed"] = True
            ev["gmat_report_sha256"] = record["report_sha256"]
            ev["gmat_normalized_csv_sha256"] = record["normalized_csv_sha256"]
            ev["gmat_binary_sha256"] = record["binary_sha256"]
            # The original manifest was correctly NOT_RUN before external
            # execution. Once a genuine GMAT report is hash-verified, do not
            # leave a contradictory "no GMAT execution" limitation beside the
            # evidence flag. Frame/time and accuracy remain unapproved.
            stale = "no external GMAT execution or input-constant/frame alignment"
            manifest["limitations"] = [
                ("actual GMAT executed; independent frame/time alignment "
                 "and mission acceptance limits not approved")
                if item == stale else item
                for item in manifest.get("limitations", [])
            ]
        manifest["external_vv_status"] = "INCONCLUSIVE"
        manifest["acceptance_limits_status"] = "NOT_APPROVED"
        _write_json(manifest_path, manifest)
    if record["status"] == "GMAT_RUN_FAILED":
        raise RuntimeError("CROSS_LEO_GMAT_NOT_VERIFIED: inspect gmat_execution.json")
    return record


def run_gmat_icrf_numeric_control(out: Path, binary: Path, *,
                                  timeout_s: int = 600) -> dict:
    """Real fourth GMAT process: same BSK numbers declared as EarthICRF.

    An actual independent GMAT run is required. The common physical N-to-ICRF
    initial frame is still CONDITIONAL and cannot be certified from this test.
    No original original-MJ, matched-mu, Sun/Luna or two-frame file is mutated.
    """
    out = out.resolve(strict=True)
    manifest = json.loads((out/"case_manifest.json").read_text(encoding="utf-8"))
    ev = manifest.get("evidence", {})
    original = json.loads((out/"gmat_execution.json").read_text(encoding="utf-8"))
    matched = json.loads((out/"gmat_mu_control_execution.json").read_text(encoding="utf-8"))
    frame = json.loads((out/"gmat_frame_time_execution.json").read_text(encoding="utf-8"))
    celestial = json.loads((out/"sun_anchor_execution.json").read_text(encoding="utf-8"))
    bridge = json.loads((out/"gmat_mj_naif_frame_probe.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != SCHEMA or not ev.get("basilisk_executed")
        or not ev.get("gmat_executed") or not ev.get("gmat_mu_control_executed")
        or not ev.get("gmat_frame_time_probe_executed")
        or original.get("status") != "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"
        or matched.get("status") != "GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT"
        or frame.get("status") != "GMAT_FRAME_TIME_REPORTED_UNQUALIFIED_ABSOLUTE_TIE"
        or celestial.get("status") != "REAL_THREE_ENGINE_CELESTIAL_SOURCE_OBSERVED_INCONCLUSIVE"
        or not celestial.get("basilisk_spice_kernel_pool_verified")
        or celestial.get("real_moon_sample_count") != 3
        or bridge.get("status") != "INCONCLUSIVE"
        or bridge.get("spacecraft_holdout_samples") != 91
        or bridge.get("original_basilisk_N_to_ICRF_approved") is not False):
        raise ValueError("DT2_ICRF_CONTROL_REQUIRES_AUTHENTIC_TWO_ENGINE_AND_FRAME_PROOFS")
    executable = binary.expanduser().resolve(strict=True)
    binary_sha = _sha256(executable)
    if (original.get("binary_sha256") != binary_sha
        or matched.get("binary_sha256") != binary_sha
        or frame.get("executable_sha256") != binary_sha
        or celestial.get("gmat_binary_sha256") != binary_sha):
        raise ValueError("DT2_ICRF_CONTROL_GMAT_BINARY_MISMATCH")
    source_checks = (
        ("basilisk_state.csv", ev.get("basilisk_state_csv_sha256")),
        ("gmat_raw.txt", original.get("report_sha256")),
        ("gmat_mu_control_raw.txt", matched.get("report_sha256")),
        ("gmat_frame_time_raw.txt", frame.get("frame_time_report_sha256")),
        ("gmat_sun_anchor_raw.txt", celestial.get("gmat_sun_report_sha256")),
        ("gmat_moon_anchor_raw.txt", celestial.get("gmat_moon_report_sha256")),
        ("gmat_mj_naif_frame_probe_rows.csv", bridge.get("holdout_rows_csv_sha256")),
        ("gmat_mj_naif_frame_probe.json", celestial.get("frame_bridge_diagnostic_sha256")),
    )
    for name, expected in source_checks:
        if not expected or _sha256(out/name) != expected:
            raise ValueError("DT2_ICRF_CONTROL_SOURCE_HASH_MISMATCH: " + name)

    files = {
        "script": out/"gmat_icrf_numeric_executed.script",
        "raw": out/"gmat_icrf_numeric_raw.txt",
        "stdout": out/"gmat_icrf_numeric_stdout.log",
        "stderr": out/"gmat_icrf_numeric_stderr.log",
        "state": out/"gmat_icrf_numeric_state.csv",
        "receipt": out/"gmat_icrf_numeric_execution.json",
        "difference": out/"gmat_icrf_numeric_difference_rows.csv",
        "diagnostic": out/"gmat_icrf_numeric_diagnostic.json",
    }
    if any(path.exists() for path in files.values()):
        raise FileExistsError("DT2_ICRF_CONTROL_REFUSES_OVERWRITE")
    mu_km3_s2 = float(manifest["gravity"]["mu_m3_s2"])/1e9
    script_source = _gmat_script(
        manifest["actual_initial_state_SI"],
        report_path=str(files["raw"].resolve()),
        earth_mu_km3_s2=mu_km3_s2, state_frame="EarthICRF",
    )
    files["script"].write_text(script_source, encoding="ascii")
    if ("CrossSat.CoordinateSystem = EarthICRF;" not in script_source
        or "CrossSat.EarthICRF.X" not in script_source
        or f"GMAT Earth.Mu = {mu_km3_s2:.15g};" not in script_source):
        raise ValueError("DT2_ICRF_CONTROL_SCRIPT_FRAME_OR_MU_NOT_EXPLICIT")
    run = subprocess.run(
        [str(executable),"--run",str(files["script"].resolve())],
        cwd=executable.parent, capture_output=True, text=True,
        timeout=timeout_s, check=False,
    )
    files["stdout"].write_text(run.stdout, encoding="utf-8")
    files["stderr"].write_text(run.stderr, encoding="utf-8")
    receipt = {
        "status": "GMAT_ICRF_CONTROL_NOT_VERIFIED",
        "scientific_verdict": "INCONCLUSIVE",
        "engine": "genuine NASA GMAT R2026a",
        "binary_sha256": binary_sha,
        "script_sha256": _sha256(files["script"]),
        "command": [str(executable),"--run",str(files["script"].resolve())],
        "return_code": run.returncode,
        "stdout_sha256": _sha256(files["stdout"]),
        "stderr_sha256": _sha256(files["stderr"]),
        "report_present": files["raw"].is_file(),
        "raw_report_sha256": _sha256(files["raw"]) if files["raw"].is_file() else None,
        "state_input": "actual original native Basilisk numeric Cartesian six-state",
        "state_input_frame_declared_in_GMAT": "EarthICRF",
        "earth_mu_km3_s2": mu_km3_s2,
        "original_basilisk_N_to_EarthICRF_tie_independently_approved": False,
        "absolute_time_alignment_approved": False,
        "mission_accuracy_budget_approved": False,
        "source_sha256": {name: expected for name, expected in source_checks},
    }
    _write_json(files["receipt"], receipt)
    if run.returncode != 0 or not files["raw"].is_file():
        raise RuntimeError("DT2_REAL_GMAT_ICRF_CONTROL_EXECUTION_FAILED")
    rows = _read_gmat_report(files["raw"], CASE["epoch_utc"])
    bsk = _read_states(out/"basilisk_state.csv", CASE["epoch_utc"])
    if len(rows) != 91 or len(bsk) != len(rows):
        raise ValueError("DT2_REAL_GMAT_ICRF_CONTROL_INCOMPLETE_GRID")
    _write_state_csv(files["state"], rows)
    difference = []
    for i,(a,b) in enumerate(zip(bsk, rows)):
        dp = [b[k]-a[k] for k in ("x_m","y_m","z_m")]
        dv = [b[k]-a[k] for k in ("vx_m_s","vy_m_s","vz_m_s")]
        difference.append({
            "sample_index":i, "basilisk_elapsed_s":a["time_s"],
            "gmat_original_elapsed_s":b["time_s"],
            "raw_time_skew_s":b["time_s"]-a["time_s"],
            "position_delta_m":math.sqrt(sum(v*v for v in dp)),
            "velocity_delta_m_s":math.sqrt(sum(v*v for v in dv)),
        })
    with files["difference"].open("w", newline="", encoding="utf-8") as stream:
        writer=csv.DictWriter(stream, fieldnames=list(difference[0]))
        writer.writeheader()
        writer.writerows(difference)
    diagnostic = {
        "status":"INCONCLUSIVE",
        "case_id": CASE_ID,
        "comparison_kind":"index-paired numeric EarthICRF GMAT vs Basilisk N; conditional physical frame hypothesis",
        "paired_samples":len(difference),
        "independently_frame_time_aligned_samples":0,
        "frame_hypothesis":"Basilisk spacecraft N is physically NAIF J2000/ICRF -- not independently certified",
        "frozen_source_initial_components_unchanged":True,
        "no_rotation_or_state_adjustment_applied":True,
        "raw_reported_gmat_times_retained":True,
        "max_reported_gmat_time_skew_s":max(abs(row["raw_time_skew_s"]) for row in difference),
        "position_delta_m_first":difference[0]["position_delta_m"],
        "position_delta_m_mid":difference[len(difference)//2]["position_delta_m"],
        "position_delta_m_last":difference[-1]["position_delta_m"],
        "position_delta_m_max":max(row["position_delta_m"] for row in difference),
        "velocity_delta_m_s_max":max(row["velocity_delta_m_s"] for row in difference),
        "mission_accuracy_budget_approved":False,
        "original_basilisk_N_to_EarthICRF_tie_independently_approved":False,
    }
    _write_json(files["diagnostic"], diagnostic)
    receipt.update({
        "status":"GMAT_ICRF_NUMERIC_INITIAL_EXECUTED_UNQUALIFIED_N_TIE",
        "rows":len(rows),
        "normalized_csv_sha256":_sha256(files["state"]),
        "difference_rows_sha256":_sha256(files["difference"]),
        "diagnostic_sha256":_sha256(files["diagnostic"]),
    })
    _write_json(files["receipt"],receipt)
    return receipt



# These are the EXACT official Basilisk 2.11.0 astroConstants.h values in
# km^3/s^2; not a substitute for checking GMAT's celestial-body mu settings.
# Source: AVSLab/basilisk v2.11.0, src/architecture/utilities/astroConstants.h
DT2_DIRECTIONAL_MU_KM3_S2 = {
    "sun": 132712440023.310,
    "moon": 4902.799,
}


def _dt2_tidal_acceleration(r_m: list[float], r_body_m: list[float],
                            mu_m3_s2: float) -> list[float]:
    """Exact Newtonian Earth-relative third-body DIFFERENTIAL acceleration.

    This is an analytic candidate force signal, not a native Basilisk or
    independently evaluated GMAT force-model output.
    """
    if (len(r_m)!=3 or len(r_body_m)!=3 or
        not all(math.isfinite(float(v)) for v in (*r_m,*r_body_m)) or
        not math.isfinite(float(mu_m3_s2)) or mu_m3_s2<=0):
        raise ValueError("DT2_DIRECTIONAL_INVALID_GEOMETRY_OR_MU")
    nr=math.sqrt(sum(v*v for v in r_body_m))
    delta=[r_body_m[i]-r_m[i] for i in range(3)]
    nd=math.sqrt(sum(v*v for v in delta))
    if nr<=0 or nd<=0:
        raise ValueError("DT2_DIRECTIONAL_SINGULAR_THIRD_BODY_GEOMETRY")
    return [mu_m3_s2*(delta[i]/nd**3-r_body_m[i]/nr**3) for i in range(3)]


def _dt2_norm(x: list[float]) -> float:
    return math.sqrt(sum(v*v for v in x))


def _dt2_directional_row(r: list[float], r_sun: list[float],
                          r_moon: list[float]) -> dict:
    a_sun=_dt2_tidal_acceleration(
        r,r_sun,DT2_DIRECTIONAL_MU_KM3_S2["sun"]*1e9)
    a_moon=_dt2_tidal_acceleration(
        r,r_moon,DT2_DIRECTIONAL_MU_KM3_S2["moon"]*1e9)
    total=[a_sun[i]+a_moon[i] for i in range(3)]
    return {"total":total,"sun":a_sun,"moon":a_moon}


def run_directional_tidal_preflight(out: Path) -> dict:
    """Observe actual source geometry, without executing a *new* force model.

    Existing native/GMAT/NAIF trajectories must have been executed, sealed
    and SHA-verified. The 90deg rotation changes ONLY the spacecraft position
    in this analytic counterfactual. It is not a simulated trajectory.
    """
    out=out.resolve(strict=True)
    manifest=json.loads((out/"case_manifest.json").read_text(encoding="utf-8"))
    celestial=json.loads((out/"sun_anchor_execution.json").read_text(encoding="utf-8"))
    icrf=json.loads((out/"gmat_icrf_numeric_execution.json").read_text(encoding="utf-8"))
    if (manifest.get("schema")!=SCHEMA
        or not manifest.get("evidence",{}).get("basilisk_executed")
        or not manifest.get("evidence",{}).get("gmat_executed")
        or not celestial.get("basilisk_spice_kernel_pool_verified")
        or celestial.get("real_sample_count")!=3
        or celestial.get("real_moon_sample_count")!=3
        or celestial.get("scientific_verdict")!="INCONCLUSIVE"
        or icrf.get("status")!="GMAT_ICRF_NUMERIC_INITIAL_EXECUTED_UNQUALIFIED_N_TIE"
        or icrf.get("rows")!=91 or icrf.get("return_code")!=0
        or icrf.get("scientific_verdict")!="INCONCLUSIVE"):
        raise ValueError("DT2_DIRECTIONAL_REQUIRES_REAL_BSK_GMAT_NAIF_EVIDENCE")
    checks=(
        ("basilisk_state.csv",manifest["evidence"]["basilisk_state_csv_sha256"]),
        ("gmat_icrf_numeric_state.csv",icrf.get("normalized_csv_sha256")),
        ("sun_anchor_three_engines.csv",celestial.get("sun_anchor_comparison_csv_sha256")),
        ("moon_anchor_three_engines.csv",celestial.get("moon_anchor_comparison_csv_sha256")),
    )
    for name,expected in checks:
        if not expected or _sha256(out/name)!=expected:
            raise ValueError("DT2_DIRECTIONAL_REAL_SOURCE_HASH_INVALID: "+name)
    import csv as csv_module
    with (out/"sun_anchor_three_engines.csv").open(newline="",encoding="utf-8") as stream:
        sun=list(csv_module.DictReader(stream))
    with (out/"moon_anchor_three_engines.csv").open(newline="",encoding="utf-8") as stream:
        moon=list(csv_module.DictReader(stream))
    if len(sun)!=3 or len(moon)!=3:
        raise ValueError("DT2_DIRECTIONAL_THREE_REAL_CELESTIAL_ROWS_REQUIRED")
    bsk=_read_states(out/"basilisk_state.csv",CASE["epoch_utc"])
    gmat=_read_states(out/"gmat_icrf_numeric_state.csv",CASE["epoch_utc"],
                      tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    body_names=("sun","moon")
    rows=[]
    for j,index in enumerate((0,45,90)):
        t=index*CASE["sample_s"]
        if int(float(sun[j]["time_s"]))!=t or int(float(moon[j]["time_s"]))!=t:
            raise ValueError("DT2_DIRECTIONAL_CELESTIAL_TIME_NOT_SOURCE_GRID")
        r_bsk=[float(bsk[index][k]) for k in ("x_m","y_m","z_m")]
        r_gmat=[float(gmat[index][k]) for k in ("x_m","y_m","z_m")]
        def pos(body,name,engine):
            return [float(body[f"{engine}_{name}_{axis}_km"])*1000
                    for axis in "xyz"]
        rn_sun=pos(sun[j],"sun","naif_j2000")
        rn_moon=pos(moon[j],"moon","naif_j2000")
        gm_sun=pos(sun[j],"sun","gmat_icrf")
        gm_moon=pos(moon[j],"moon","gmat_icrf")
        predicted_bsk=_dt2_directional_row(r_bsk,rn_sun,rn_moon)
        predicted_gmat=_dt2_directional_row(r_gmat,gm_sun,gm_moon)
        # Counterfactual orientation-only falsification: rotate spacecraft
        # position by +90deg about J2000 z; DO NOT rotate third-body ephemerides.
        rotated=[-r_bsk[1],r_bsk[0],r_bsk[2]]
        counterfactual=_dt2_directional_row(rotated,rn_sun,rn_moon)
        delta=[counterfactual["total"][k]-predicted_bsk["total"][k]
               for k in range(3)]
        agreement=[predicted_bsk["total"][k]-predicted_gmat["total"][k]
                   for k in range(3)]
        row={
            "time_s":t,
            "analytic_bsk_source_sun_tidal_accel_m_s2":_dt2_norm(predicted_bsk["sun"]),
            "analytic_bsk_source_moon_tidal_accel_m_s2":_dt2_norm(predicted_bsk["moon"]),
            "analytic_bsk_source_total_tidal_accel_m_s2":_dt2_norm(predicted_bsk["total"]),
            "analytic_gmat_source_total_tidal_accel_m_s2":_dt2_norm(predicted_gmat["total"]),
            "analytic_source_prediction_delta_norm_m_s2":_dt2_norm(agreement),
            "counterfactual_rotate_only_spacecraft_90deg_delta_norm_m_s2":_dt2_norm(delta),
            "earth_central_pointmass_accel_norm_m_s2":
                float(manifest["gravity"]["mu_m3_s2"])/_dt2_norm(r_bsk)**2,
        }
        if not all(math.isfinite(v) and v>=0 for v in row.values()):
            raise ValueError("DT2_DIRECTIONAL_NONFINITE_OR_NEGATIVE_DIAGNOSTIC")
        rows.append(row)
    outputs={
        "rows":out/"directional_thirdbody_analytic_preflight_rows.csv",
        "receipt":out/"directional_thirdbody_analytic_preflight.json",
    }
    if any(p.exists() for p in outputs.values()):
        raise FileExistsError("DT2_DIRECTIONAL_PREVIEW_REFUSES_OVERWRITE")
    with outputs["rows"].open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary={
        "case_id":CASE_ID,
        "status":"INCONCLUSIVE",
        "source_kind":"true archived BSK/GMAT/NAIF states and ephemerides; NEWTONIAN FORMULA POSTPROCESSING ONLY",
        "force_model_executed_in_new_basilisk_run":False,
        "force_model_executed_in_new_gmat_run":False,
        "counterfactual_is_real_trajectory":False,
        "conditional_Basilisk_N_as_J2000":True,
        "independent_absolute_frame_tie_approved":False,
        "mission_error_budget_approved":False,
        "physical_cross_engine_acceleration_VV_pass":False,
        "celestial_ephemeris_source":"pinned official GMAT R2026a DE421 SPK, exact hash from sun_anchor_execution.json",
        "mu_source":"official Basilisk 2.11.0 astroConstants.h, not independently verified GMAT body μ",
        "mu_sun_km3_s2":DT2_DIRECTIONAL_MU_KM3_S2["sun"],
        "mu_moon_km3_s2":DT2_DIRECTIONAL_MU_KM3_S2["moon"],
        "real_source_sample_count":len(rows),
        "sample_times_s":[r["time_s"] for r in rows],
        "max_analytic_actual_source_tidal_accel_m_s2":
            max(r["analytic_bsk_source_total_tidal_accel_m_s2"] for r in rows),
        "max_counterfactual_only_spacecraft_rotation_delta_m_s2":
            max(r["counterfactual_rotate_only_spacecraft_90deg_delta_norm_m_s2"] for r in rows),
        "max_analytic_BSK_source_vs_GMAT_source_prediction_delta_m_s2":
            max(r["analytic_source_prediction_delta_norm_m_s2"] for r in rows),
        "original_file_sha256":{name:sha for name,sha in checks},
        "three_sample_csv_sha256":_sha256(outputs["rows"]),
    }
    _write_json(outputs["receipt"],summary)
    return summary


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



def compare_mu_control(out: Path) -> dict:
    """Isolate real Earth-Mu defaults in two executed GMAT trajectories.

    This is an unqualified index-paired diagnostic, NOT frame/time V&V PASS.
    It never alters the original GMAT, Basilisk or time columns.
    """
    manifest = json.loads((out / "case_manifest.json").read_text(encoding="utf-8"))
    base = json.loads((out / "gmat_execution.json").read_text(encoding="utf-8"))
    control = json.loads((out / "gmat_mu_control_execution.json").read_text(encoding="utf-8"))
    ev = manifest.get("evidence", {})
    if (not ev.get("basilisk_executed") or not ev.get("gmat_executed")
            or not ev.get("gmat_mu_control_executed")
            or base.get("status") != "GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"
            or control.get("status") != "GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT"
            or base.get("binary_sha256") != control.get("binary_sha256")):
        raise ValueError("CROSS_LEO_MU_CONTROL_REQUIRES_TWO_REAL_GMAT_RUNS")
    for filename, expected in (
        ("basilisk_state.csv", ev.get("basilisk_state_csv_sha256")),
        ("gmat_raw.txt", base.get("report_sha256")),
        ("gmat_state.csv", base.get("normalized_csv_sha256")),
        ("gmat_mu_control_raw.txt", control.get("report_sha256")),
        ("gmat_mu_control_state.csv", control.get("normalized_csv_sha256")),
        ("gmat_mu_control_executed.script", control.get("script_sha256")),
    ):
        if not expected or _sha256(out / filename) != expected:
            raise ValueError(f"CROSS_LEO_MU_CONTROL_HASH_INVALID: {filename}")
    mu_actual = float(manifest["gravity"]["mu_m3_s2"])
    mu_requested = float(control["explicit_earth_mu_override_km3_s2"]) * 1e9
    if not math.isclose(mu_actual, mu_requested, rel_tol=1e-14, abs_tol=0.0):
        raise ValueError("CROSS_LEO_MU_CONTROL_CONSTANT_NOT_MATCHED")
    expected_cmd = f"GMAT Earth.Mu = {mu_actual / 1e9:.15g};"
    if expected_cmd not in (out / "gmat_mu_control_executed.script").read_text(encoding="ascii"):
        raise ValueError("CROSS_LEO_MU_CONTROL_SCRIPT_NOT_VERIFIED")

    bsk = _read_states(out / "basilisk_state.csv", CASE["epoch_utc"])
    default = _read_states(out / "gmat_state.csv", CASE["epoch_utc"],
                           tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    matched = _read_states(out / "gmat_mu_control_state.csv", CASE["epoch_utc"],
                           tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    out_rows = []
    for a,b,c in zip(bsk, default, matched):
        def diff(row):
            dp = [row[k] - a[k] for k in ("x_m", "y_m", "z_m")]
            dv = [row[k] - a[k] for k in ("vx_m_s", "vy_m_s", "vz_m_s")]
            return (math.sqrt(sum(v*v for v in dp)),
                    math.sqrt(sum(v*v for v in dv)))
        dp_orig,dv_orig = diff(b)
        dp_ctrl,dv_ctrl = diff(c)
        out_rows.append({
            "basilisk_time_s":a["time_s"],
            "gmat_original_reported_s":b["time_s"],
            "gmat_matched_mu_reported_s":c["time_s"],
            "default_position_delta_m":dp_orig,
            "matched_mu_position_delta_m":dp_ctrl,
            "default_velocity_delta_m_s":dv_orig,
            "matched_mu_velocity_delta_m_s":dv_ctrl,
        })
    with (out / "mu_control_difference_rows.csv").open("w", newline="", encoding="utf-8") as stream:
        w = csv.DictWriter(stream, fieldnames=list(out_rows[0]))
        w.writeheader()
        w.writerows(out_rows)
    result = {
        "schema":SCHEMA, "case_id":CASE_ID, "status":"INCONCLUSIVE",
        "reason":"REAL_MATCHED_GMAT_MU_SENSITIVITY_NOT_APPROVED_FRAME_TIME_OR_ACCURACY",
        "sample_pairing":"index_only__raw_clocks_retained",
        "paired_samples":len(out_rows), "approved_aligned_samples":0,
        "mu_actual_basilisk_m3_s2":mu_actual,
        "mu_overridden_in_actual_gmat_m3_s2":mu_requested,
        "default_gmat_mu_from_independent_documentation_not_runtime_measurement_km3_s2":398600.4415,
        "original_max_pos_m":max(x["default_position_delta_m"] for x in out_rows),
        "matched_mu_max_pos_m":max(x["matched_mu_position_delta_m"] for x in out_rows),
        "original_final_pos_m":out_rows[-1]["default_position_delta_m"],
        "matched_mu_final_pos_m":out_rows[-1]["matched_mu_position_delta_m"],
        "original_final_vel_m_s":out_rows[-1]["default_velocity_delta_m_s"],
        "matched_mu_final_vel_m_s":out_rows[-1]["matched_mu_velocity_delta_m_s"],
        "maximum_reported_time_skew_s":max(
            abs(t["gmat_matched_mu_reported_s"] - t["basilisk_time_s"])
            for t in out_rows),
        "original_gmat_report_sha256":base["report_sha256"],
        "matched_mu_report_sha256":control["report_sha256"],
        "basilisk_state_sha256":ev["basilisk_state_csv_sha256"],
        "model_acceptance_limits_approved":False,
        "independent_frame_time_alignment_approved":False,
    }
    _write_json(out / "mu_control_diagnostic.json", result)
    return result



def _closed_form_circular_two_body(initial: dict, mu_m3_s2: float, time_s: float
                                  ) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Exact circular Earth two-body solution from the *actual* initial 6-state.

    Mathematical solution only, NOT flight reference data or absolute frame tie.
    Reject non-circular inputs rather than approximating a general Kepler orbit.
    """
    if (type(mu_m3_s2) not in (int, float) or not math.isfinite(mu_m3_s2)
            or mu_m3_s2 <= 0):
        raise ValueError("CROSS_LEO_ANALYTIC_BAD_MU")
    if (type(time_s) not in (int, float) or not math.isfinite(time_s)
            or time_s < 0):
        raise ValueError("CROSS_LEO_ANALYTIC_BAD_TIME")
    r0 = tuple(float(initial[k]) for k in ("x_m", "y_m", "z_m"))
    v0 = tuple(float(initial[k]) for k in ("vx_m_s", "vy_m_s", "vz_m_s"))
    if not all(math.isfinite(v) for v in (*r0, *v0)):
        raise ValueError("CROSS_LEO_ANALYTIC_BAD_INITIAL_STATE")
    radius = math.sqrt(sum(x*x for x in r0))
    speed = math.sqrt(sum(x*x for x in v0))
    if radius <= 1.0 or speed <= 0:
        raise ValueError("CROSS_LEO_ANALYTIC_BAD_INITIAL_STATE")
    expected_speed = math.sqrt(mu_m3_s2 / radius)
    if abs(speed / expected_speed - 1.0) > 1e-12:
        raise ValueError("CROSS_LEO_ANALYTIC_REQUIRES_CIRCULAR_SPEED")
    if abs(sum(a*b for a,b in zip(r0,v0))) / (radius*speed) > 1e-12:
        raise ValueError("CROSS_LEO_ANALYTIC_REQUIRES_TANGENTIAL_VELOCITY")
    mean_motion = math.sqrt(mu_m3_s2 / (radius**3))
    phase = mean_motion * time_s
    c,s = math.cos(phase),math.sin(phase)
    position = tuple(r0[i]*c + v0[i]/mean_motion*s for i in range(3))
    velocity = tuple(v0[i]*c - r0[i]*mean_motion*s for i in range(3))
    return position, velocity


def analyze_closed_form_two_body(out: Path) -> dict:
    """Recompute a third-method closed-form diagnostic on REAL hashed records.

    Index pairing is for diagnostics only; use nominal clock ticks AND raw GMAT
    ElapsedSecs separately. No fabricated engine outputs, interpolated samples,
    nominal-timestamp replacement, external time/frame certification, or PASS.
    """
    earlier = compare_mu_control(out)
    if earlier.get("status") != "INCONCLUSIVE" or earlier.get("paired_samples") != 91:
        raise ValueError("CROSS_LEO_ANALYTIC_REQUIRES_REAL_DUAL_GMAT_CONTROL")
    manifest = json.loads((out / "case_manifest.json").read_text(encoding="utf-8"))
    mu = float(manifest["gravity"]["mu_m3_s2"])
    initial = manifest["actual_initial_state_SI"]
    if manifest.get("physical_flags") != MODEL_FLAGS:
        raise ValueError("CROSS_LEO_ANALYTIC_REQUIRES_PURE_POINT_MASS")
    if manifest.get("case", {}).get("initial_elements") != CASE["initial_elements"]:
        raise ValueError("CROSS_LEO_ANALYTIC_REQUIRES_EXACT_CIRCULAR_CASE")
    bsk = _read_states(out / "basilisk_state.csv", CASE["epoch_utc"])
    gmat_original = _read_states(out / "gmat_state.csv", CASE["epoch_utc"],
                                 tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    gmat_control = _read_states(out / "gmat_mu_control_state.csv", CASE["epoch_utc"],
                                tick_skew_budget_s=GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S)
    if len(bsk) != len(gmat_original) or len(bsk) != len(gmat_control):
        raise ValueError("CROSS_LEO_ANALYTIC_GRID_LENGTH_MISMATCH")

    output = []
    for idx,(a,b,c) in enumerate(zip(bsk,gmat_original,gmat_control)):
        nominal_t = idx * CASE["sample_s"]
        exact_pos,exact_vel = _closed_form_circular_two_body(initial,mu,nominal_t)
        raw_time_pos,raw_time_vel = _closed_form_circular_two_body(
            initial,mu,c["time_s"])
        def distance(row, axes, exact):
            return math.sqrt(sum((row[k]-v)**2 for k,v in zip(axes,exact)))
        pos_axes=("x_m","y_m","z_m")
        vel_axes=("vx_m_s","vy_m_s","vz_m_s")
        output.append({
            "nominal_time_s":nominal_t,
            "basilisk_reported_time_s":a["time_s"],
            "gmat_default_reported_time_s":b["time_s"],
            "gmat_control_reported_time_s":c["time_s"],
            "gmat_control_raw_clock_skew_s":c["time_s"]-nominal_t,
            "basilisk_vs_closed_form_nominal_position_m":
                distance(a,pos_axes,exact_pos),
            "basilisk_vs_closed_form_nominal_velocity_m_s":
                distance(a,vel_axes,exact_vel),
            "gmat_default_vs_closed_form_nominal_position_m":
                distance(b,pos_axes,exact_pos),
            "gmat_control_vs_closed_form_nominal_position_m":
                distance(c,pos_axes,exact_pos),
            "gmat_control_vs_closed_form_nominal_velocity_m_s":
                distance(c,vel_axes,exact_vel),
            "gmat_control_vs_closed_form_raw_time_position_m":
                distance(c,pos_axes,raw_time_pos),
            "gmat_control_vs_closed_form_raw_time_velocity_m_s":
                distance(c,vel_axes,raw_time_vel),
        })
    def summary(key):
        vals=[v[key] for v in output]
        return {"max":max(vals), "first":vals[0],
                "middle":vals[len(vals)//2], "final":vals[-1]}
    diagnostics={
        "schema":SCHEMA, "case_id":CASE_ID, "status":"INCONCLUSIVE",
        "reference_type":"MATHEMATICAL_CIRCULAR_EARTH_POINT_MASS_CLOSED_FORM",
        "reference_is_flight_measurement":False,
        "closed_form_requires_exact_circular_initial_conditions":True,
        "mu_m3_s2_from_actual_basilisk_manifest":mu,
        "initial_six_state_from_actual_basilisk_manifest":initial,
        "sample_pairing":"NOMINAL_60_SECOND_INDEX_ONLY; ORIGINAL_GMAT_TIMES_RETAINED",
        "paired_samples":len(output),"approved_frame_time_aligned_samples":0,
        "independent_frame_mapping_approved":False,
        "absolute_time_scale_mapping_approved":False,
        "mission_residual_acceptance_limits_approved":False,
        "gmat_normalized_utc_is_derived_from_elapsed_report_not_independent_utc":True,
        "nominal_time_closed_form_position_m":{
            "basilisk":summary("basilisk_vs_closed_form_nominal_position_m"),
            "gmat_default_mu":summary("gmat_default_vs_closed_form_nominal_position_m"),
            "gmat_basilisk_mu_control":summary("gmat_control_vs_closed_form_nominal_position_m"),
        },
        "nominal_time_closed_form_velocity_m_s":{
            "basilisk":summary("basilisk_vs_closed_form_nominal_velocity_m_s"),
            "gmat_basilisk_mu_control":summary("gmat_control_vs_closed_form_nominal_velocity_m_s"),
        },
        "raw_reported_time_closed_form_position_m":{
            "gmat_basilisk_mu_control":summary("gmat_control_vs_closed_form_raw_time_position_m"),
        },
        "max_abs_raw_gmat_clock_skew_s":max(
            abs(row["gmat_control_raw_clock_skew_s"]) for row in output),
        "reference_frame_warning":(
            "Basilisk N and GMAT EarthMJ2000Eq absolute coordinate-frame "
            "relationship is NOT independently qualified; use only the "
            "case-defined common numeric Cartesian initial vector."
        ),
        "data_hashes":{
            "basilisk":manifest["evidence"]["basilisk_state_csv_sha256"],
            "gmat_original":earlier["original_gmat_report_sha256"],
            "gmat_mu_control":earlier["matched_mu_report_sha256"],
        },
    }
    with (out/"analytic_reference_difference_rows.csv").open(
            "w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    _write_json(out/"analytic_reference_diagnostic.json",diagnostics)
    return diagnostics



def _gmat_frame_time_script(initial_state: dict, *, mu: float,
                            state_path: str, epoch_path: str) -> str:
    """Third real GMAT run: independently report two frames and time scales."""
    script = _gmat_script(initial_state, report_path=state_path,
                          earth_mu_km3_s2=mu)
    report_file = epoch_path.replace("\\", "/").replace("'", "")
    frame_decl = "\n".join([
        "Create ReportFile CrossFrame;",
        f"CrossFrame.Filename = '{report_file}';",
        "CrossFrame.Precision = 16;",
        "CrossFrame.WriteHeaders = false;",
        "CrossFrame.FixedWidth = false;",
        "CrossFrame.Delimiter = ',';",
        "CrossFrame.WriteReport = false;",
        "CrossFrame.AppendToExistingFile = false;",
    ]) + "\n"
    if script.count("BeginMissionSequence;") != 1:
        raise ValueError("CROSS_LEO_FRAME_TIME_NO_MISSION")
    script = script.replace("BeginMissionSequence;",
                            frame_decl + "BeginMissionSequence;")
    fields = (
        "CrossSat.ElapsedSecs CrossSat.UTCModJulian "
        "CrossSat.TAIModJulian CrossSat.TTModJulian CrossSat.A1ModJulian "
        + " ".join("CrossSat.EarthMJ2000Eq."+f
                   for f in ("X","Y","Z","VX","VY","VZ"))
        + " " + " ".join("CrossSat.EarthICRF."+f
                       for f in ("X","Y","Z","VX","VY","VZ"))
    )
    rows = []
    count = 0
    for line in script.splitlines():
        rows.append(line)
        if line.startswith("Report CrossState "):
            rows.append("Report CrossFrame "+fields+";")
            count += 1
    if count != 91:
        raise ValueError("CROSS_LEO_FRAME_TIME_REPORT_COUNT")
    result = "\n".join(rows)+"\n"
    if not result.isascii():
        raise ValueError("CROSS_LEO_FRAME_TIME_NON_ASCII_SCRIPT")
    return result


def _gmat_utc_modjulian(epoch: str, elapsed: float) -> float:
    dt = datetime.fromisoformat(epoch.replace("Z","+00:00"))
    if dt.utcoffset() != timedelta(0):
        raise ValueError("CROSS_LEO_FRAME_TIME_EPOCH_NOT_UTC")
    return 2440587.5 + (dt.timestamp()+elapsed)/86400.0 - GMAT_MJD_JD_OFFSET_DAYS


def _read_gmat_frame_time(raw: Path, epoch: str) -> list[dict]:
    """Only real GMAT report syntax is accepted; no synthetic fallback."""
    out = []
    with raw.open("r",encoding="utf-8-sig",newline="") as stream:
        for line in csv.reader(stream,delimiter=","):
            if not line or all(not x.strip() for x in line):
                continue
            if len(line)!=len(GMAT_FRAME_TIME_COLUMNS):
                raise ValueError("CROSS_LEO_FRAME_TIME_BAD_ROW_WIDTH")
            try:
                values=[float(x.strip()) for x in line]
            except ValueError as exc:
                raise ValueError("CROSS_LEO_FRAME_TIME_NOT_NUMERIC") from exc
            if not all(math.isfinite(x) for x in values):
                raise ValueError("CROSS_LEO_FRAME_TIME_NONFINITE")
            out.append(dict(zip(GMAT_FRAME_TIME_COLUMNS,values)))
    if len(out)!=91:
        raise ValueError("CROSS_LEO_FRAME_TIME_REQUIRES_91_REAL_ROWS")
    for i,row in enumerate(out):
        t=i*CASE["sample_s"]
        if abs(row["elapsed_s"]-t)>GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S:
            raise ValueError("CROSS_LEO_FRAME_TIME_ELAPSED_GRID_MISMATCH")
        if i and row["elapsed_s"]<=out[i-1]["elapsed_s"]:
            raise ValueError("CROSS_LEO_FRAME_TIME_NONMONOTONIC")
        if abs(row["utc_mjd"]-_gmat_utc_modjulian(epoch,t))*86400>GMAT_FRAME_TIME_REPORT_SANITY_S:
            raise ValueError("CROSS_LEO_FRAME_TIME_UTC_MJD_MISMATCH")
        if abs((row["tai_mjd"]-row["utc_mjd"])*86400-37.0)>GMAT_FRAME_TIME_REPORT_SANITY_S:
            raise ValueError("CROSS_LEO_FRAME_TIME_TAI_UTC_MISMATCH")
        if abs((row["tt_mjd"]-row["tai_mjd"])*86400-32.184)>GMAT_FRAME_TIME_REPORT_SANITY_S:
            raise ValueError("CROSS_LEO_FRAME_TIME_TT_TAI_MISMATCH")
    return out


def run_gmat_frame_time_probe(out: Path, binary: Path, *, timeout_s: int=600) -> dict:
    """Actual third GMAT process; never certify Basilisk N axes or time."""
    mpath = out/"case_manifest.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    default = json.loads((out/"gmat_execution.json").read_text(encoding="utf-8"))
    control = json.loads((out/"gmat_mu_control_execution.json").read_text(encoding="utf-8"))
    executable=binary.expanduser().resolve(strict=True)
    ev=manifest.get("evidence",{})
    if (manifest.get("schema")!=SCHEMA or not ev.get("basilisk_executed")
        or not ev.get("gmat_executed") or not ev.get("gmat_mu_control_executed")
        or default.get("status")!="GMAT_EXECUTED_UNQUALIFIED_ALIGNMENT"
        or control.get("status")!="GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT"
        or default.get("binary_sha256")!=control.get("binary_sha256")
        or _sha256(executable)!=control.get("binary_sha256")):
        raise ValueError("CROSS_LEO_FRAME_TIME_TWO_ACTUAL_GMAT_RUNS_REQUIRED")
    for name,expected in (
        ("basilisk_state.csv",ev.get("basilisk_state_csv_sha256")),
        ("gmat_raw.txt",default.get("report_sha256")),
        ("gmat_mu_control_raw.txt",control.get("report_sha256")),
        ("gmat_mu_control_state.csv",control.get("normalized_csv_sha256")),
    ):
        if not expected or _sha256(out/name)!=expected:
            raise ValueError("CROSS_LEO_FRAME_TIME_SOURCE_SHA_INVALID: "+name)
    names=("gmat_frame_time_executed.script","gmat_frame_state_raw.txt",
           "gmat_frame_time_raw.txt","gmat_frame_time_stdout.log",
           "gmat_frame_time_stderr.log","gmat_frame_time_execution.json",
           "gmat_frame_time_rows.csv","gmat_frame_time_diagnostic.json")
    if any((out/name).exists() for name in names):
        raise FileExistsError("CROSS_LEO_FRAME_TIME_NO_OVERWRITE")
    script=out/names[0]
    state=out/names[1]
    frame=out/names[2]
    mu=float(manifest["gravity"]["mu_m3_s2"])/1e9
    script.write_text(_gmat_frame_time_script(
        manifest["actual_initial_state_SI"],mu=mu,
        state_path=str(state.resolve()),epoch_path=str(frame.resolve())),
        encoding="ascii")
    run=subprocess.run(
        [str(executable),"--run",str(script.resolve())],cwd=executable.parent,
        capture_output=True,text=True,timeout=timeout_s,check=False)
    stdout,stderr=out/names[3],out/names[4]
    stdout.write_text(run.stdout,encoding="utf-8")
    stderr.write_text(run.stderr,encoding="utf-8")
    leap_table=executable.parent.parent/"data"/"time"/"tai-utc.dat"
    record={
        "schema":SCHEMA,"status":"GMAT_FRAME_TIME_EXECUTION_FAILED",
        "engine":"actual GMAT R2026a Console",
        "command":[str(executable),"--run",str(script.resolve())],
        "executable_sha256":_sha256(executable),"script_sha256":_sha256(script),
        "return_code":run.returncode,"stdout_sha256":_sha256(stdout),
        "stderr_sha256":_sha256(stderr),
        "state_report_sha256":_sha256(state) if state.is_file() else None,
        "frame_time_report_sha256":_sha256(frame) if frame.is_file() else None,
        "leap_table_in_release_sha256":_sha256(leap_table) if leap_table.is_file() else None,
        "verified_gmat_loaded_this_exact_leap_table":False,
    }
    if run.returncode==0 and state.is_file() and frame.is_file():
        try:
            states=_read_gmat_report(state,CASE["epoch_utc"])
            rows=_read_gmat_frame_time(frame,CASE["epoch_utc"])
            if len(states)!=len(rows):
                raise ValueError("CROSS_LEO_FRAME_TIME_STATE_ROW_MISMATCH")
            max_mj_icrf_m = max(math.sqrt(sum(
                (row["mj_"+axis+"_km"]-row["icrf_"+axis+"_km"])**2
                for axis in ("x","y","z")))*1000 for row in rows)
            # Verify two reports made in the SAME GMAT run agree on MJ state.
            max_self_error = max(math.sqrt(sum(
                (a[f]-b[other]*1000)**2
                for f,other in zip(
                    ("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s"),
                    ("mj_x_km","mj_y_km","mj_z_km",
                     "mj_vx_km_s","mj_vy_km_s","mj_vz_km_s"))))
                for a,b in zip(states,rows))
            if max_self_error>1e-3:
                raise ValueError("CROSS_LEO_FRAME_TIME_SAME_RUN_STATE_CONTRADICTION")
            path=out/names[6]
            with path.open("w",newline="",encoding="utf-8") as stream:
                writer=csv.DictWriter(stream,fieldnames=GMAT_FRAME_TIME_COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            record["status"]="GMAT_FRAME_TIME_REPORTED_UNQUALIFIED_ABSOLUTE_TIE"
            record["rows"]=len(rows)
            record["normalized_frame_time_sha256"]=_sha256(path)
            diagnostics={
                "schema":SCHEMA,"status":"INCONCLUSIVE","rows":len(rows),
                "gmat_epoch_jd_offset":GMAT_MJD_JD_OFFSET_DAYS,
                "first_utc_mjd":rows[0]["utc_mjd"],
                "last_utc_mjd":rows[-1]["utc_mjd"],
                "tai_minus_utc_s":(rows[0]["tai_mjd"]-rows[0]["utc_mjd"])*86400,
                "tt_minus_tai_s":(rows[0]["tt_mjd"]-rows[0]["tai_mjd"])*86400,
                "max_abs_reported_elapsed_skew_s":max(
                    abs(r["elapsed_s"]-i*CASE["sample_s"])
                    for i,r in enumerate(rows)),
                "max_same_gmat_mj2000eq_icrf_position_difference_m":max_mj_icrf_m,
                "max_same_run_mj_state_report_difference_si":max_self_error,
                "independent_basilisk_n_to_gmat_axis_mapping_approved":False,
                "independent_absolute_basilisk_clock_alignment_approved":False,
                "mission_accuracy_budget_approved":False,
                "certified_basilisk_n_axis_aligned_samples":0,
                "reported_gmat_frames":["EarthMJ2000Eq","EarthICRF"],
                "source_gmat_frame_report_sha256":record["frame_time_report_sha256"],
                "note":"GMAT internal two-frame/epoch report only; point-mass numeric initial state does not define physical Basilisk N axes.",
            }
            _write_json(out/names[7],diagnostics)
            record["diagnostic_sha256"]=_sha256(out/names[7])
            ev["gmat_frame_time_probe_executed"]=True
            ev["gmat_frame_time_report_sha256"]=record["frame_time_report_sha256"]
            manifest["external_vv_status"]="INCONCLUSIVE"
            manifest["acceptance_limits_status"]="NOT_APPROVED"
            _write_json(mpath,manifest)
        except (ValueError,OverflowError) as exc:
            record["parse_error"]=f"{type(exc).__name__}: {exc}"
    _write_json(out/names[5],record)
    if record["status"]!="GMAT_FRAME_TIME_REPORTED_UNQUALIFIED_ABSOLUTE_TIE":
        raise RuntimeError("CROSS_LEO_FRAME_TIME_PROBE_NOT_VERIFIED")
    return record


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
    m=sub.add_parser("gmat-mu-control",help="Actually rerun GMAT with Earth.Mu fixed to real Basilisk value")
    m.add_argument("--output",type=Path,required=True)
    m.add_argument("--executable",type=Path,required=True)
    m.add_argument("--timeout-s",type=int,default=180)
    c=sub.add_parser("compare",help="Only compute real dual-engine residual diagnostics; no PASS claim")
    c.add_argument("--output",type=Path,required=True)
    e=sub.add_parser("analytic-reference",help="Compare real records to exact circular two-body solution; INCONCLUSIVE")
    e.add_argument("--output",type=Path,required=True)
    f=sub.add_parser("gmat-frame-time-probe",help="Actually execute GMAT numeric UTC TAI TT MJ2000Eq ICRF report")
    f.add_argument("--output",type=Path,required=True)
    f.add_argument("--executable",type=Path,required=True)
    f.add_argument("--timeout-s",type=int,default=600)
    t=sub.add_parser("compare-mu-control",help="Unqualified real GMAT Earth-Mu sensitivity, no PASS")
    t.add_argument("--output",type=Path,required=True)
    q=sub.add_parser("gmat-icrf-numeric-control",help="Real independent GMAT ICRF numeric-state run; conditional N-frame hypothesis")
    q.add_argument("--output",type=Path,required=True)
    q.add_argument("--executable",type=Path,required=True)
    q.add_argument("--timeout-s",type=int,default=600)
    d=sub.add_parser("directional-preflight",help="Analytic Sun/Moon tidal sensitivity from SHA-verified real source data; no new force run")
    d.add_argument("--output",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action=="basilisk":
        result=run_basilisk(args.output,args.checkout_sha)
    elif args.action in {"gmat", "gmat-mu-control"}:
        result=run_gmat(args.output,args.executable,timeout_s=args.timeout_s,
                        matched_mu=(args.action=="gmat-mu-control"))
    elif args.action=="compare":
        result=compare(args.output)
    elif args.action=="compare-mu-control":
        result=compare_mu_control(args.output)
    elif args.action=="gmat-frame-time-probe":
        result=run_gmat_frame_time_probe(args.output,args.executable,timeout_s=args.timeout_s)
    elif args.action=="gmat-icrf-numeric-control":
        result=run_gmat_icrf_numeric_control(args.output,args.executable,timeout_s=args.timeout_s)
    elif args.action=="directional-preflight":
        result=run_directional_tidal_preflight(args.output)
    else:
        result=analyze_closed_form_two_body(args.output)
    print(json.dumps({"case_id":CASE_ID,"action":args.action,"result":result.get("external_vv_status",result.get("status","BASILISK_EXECUTED"))},sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
