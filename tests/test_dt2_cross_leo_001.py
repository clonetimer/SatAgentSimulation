"""Unit contracts only: synthetic parser rows are NOT independent GMAT evidence."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "dt2_cross_leo_001.py"
spec = importlib.util.spec_from_file_location("dt2_cross_leo_001", MODULE)
assert spec and spec.loader
cross = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cross)


def make_zero_sample():
    return {
        "time_s": 0.0, "utc": "2026-03-04T21:48:00.000Z",
        "x_m": 6650000., "y_m": 0., "z_m": 0.,
        "vx_m_s": 0., "vy_m_s": 7742., "vz_m_s": 0.,
    }


def make_test_grid():
    # Synthetically constructed values only for parser/grid invariants;
    # never committed as a real GMAT or Basilisk orbit trajectory.
    return [dict(make_zero_sample(), time_s=float(t), utc=cross._utc_at(cross.CASE["epoch_utc"], t))
            for t in range(0, 5401, 60)]


def test_gmat_script_is_generated_from_explicit_measured_initial_state():
    script = cross._gmat_script(make_zero_sample(), report_path="gmat_raw.txt")
    assert "CrossSat.DisplayStateType = Cartesian" in script
    assert "CrossSat.CoordinateSystem = EarthMJ2000Eq" in script
    assert "CrossGravity.PointMasses = {Earth}" in script
    assert "CrossGravity.SRP = Off" in script
    assert "CrossGravity.Drag = None" in script
    assert "CrossGravity.RelativisticCorrection = Off" in script
    assert "CrossProp.Type = RungeKutta89" in script
    assert "CrossState.FixedWidth = false" in script
    assert script.isascii()  # genuine R2026a Console rejects Unicode comments
    # The *actual* GMAT R2026a ReportFile emitted literal 'C' for enum
    # value Comma; the quoted comma is required for its CSV output.
    assert "CrossState.Delimiter = ',';" in script
    assert "CrossState.Delimiter = Comma;" not in script
    assert "CrossSat.X = 6650" in script
    assert "CrossSat.VY = 7.742" in script
    assert "04 Mar 2026 21:48:00.000" in script
    assert script.count("Propagate CrossProp(CrossSat)") == 90
    assert script.count("Report CrossState CrossSat.ElapsedSecs") == 91
    assert script.count("CrossSat.ElapsedSecs = 60}") == 90
    assert "CrossSat.ElapsedSecs = 5400" not in script
    assert "CrossSat.ElapsedSecs = 120" not in script
    assert "GGM03S" not in script and "SRP = On" not in script


def test_gmat_script_refuses_nonfinite_initial_state():
    bad=make_zero_sample()
    bad["vz_m_s"]=float("nan")
    with pytest.raises(ValueError,match="BAD_GMAT_INITIAL_STATE"):
        cross._gmat_script(bad,report_path="gmat_raw.txt")


def test_utc_epoch_and_grid_must_be_complete_and_continuous():
    samples=make_test_grid()
    cross._assert_grid(samples)
    assert cross._utc_at(cross.CASE["epoch_utc"],5400)=="2026-03-04T23:18:00.000Z"
    with pytest.raises(ValueError,match="INCOMPLETE_GRID"):
        cross._assert_grid(samples[:-1])
    samples[2]["time_s"] = 121.
    with pytest.raises(ValueError,match="TIME_GRID_MISMATCH"):
        cross._assert_grid(samples)


def test_exact_8_column_si_csv_and_timestamp_roundtrip(tmp_path):
    path=tmp_path/"basilisk_state.csv"
    cross._write_state_csv(path,make_test_grid())
    text=path.read_text()
    assert text.startswith(",".join(cross.FIELDNAMES))
    rows=cross._read_states(path,cross.CASE["epoch_utc"])
    assert len(rows)==91
    assert rows[0]["x_m"]==6650000
    assert rows[-1]["time_s"]==5400
    path.write_text(text.replace("2026-03-04T21:48:00.000Z", "2001-01-01T00:00:00Z", 1))
    with pytest.raises(ValueError,match="UTC_MISMATCH"):
        cross._read_states(path,cross.CASE["epoch_utc"])


def test_parse_only_well_formed_gmat_numeric_report(tmp_path):
    raw=tmp_path/"gmat_raw.txt"
    raw.write_text("".join(
        f"{float(t)},{float(t)/1000+6650},0,0,0,7.742,0\n"
        for t in range(0,5401,60)
    ),encoding="utf-8")
    rows=cross._read_gmat_report(raw,cross.CASE["epoch_utc"])
    assert len(rows)==91
    assert rows[0]["x_m"]==6650000
    assert math.isclose(rows[0]["vy_m_s"],7742.)
    raw.write_text("0,6650,0,0,0,7.742\n")
    with pytest.raises(ValueError,match="BAD_GMAT_REPORT_WIDTH"):
        cross._read_gmat_report(raw,cross.CASE["epoch_utc"])
    raw.write_text("0,6650,0,0,0,nan,0\n")
    with pytest.raises(ValueError,match="NONFINITE_GMAT_REPORT"):
        cross._read_gmat_report(raw,cross.CASE["epoch_utc"])


def test_real_gmat_float_elapsed_seconds_are_preserved_and_bounded(tmp_path):
    # Synthetic parser contract only, not a real external reference output.
    # Reproduce the 10.69 microsecond END skew observed in R2026a. The raw
    # time values must be retained; the physical V&V remains INCONCLUSIVE.
    raw = tmp_path / "gmat_raw.txt"
    rows = []
    for i in range(91):
        t = i * 60.0 - i * (1.0686927453207318e-5 / 90.0)
        rows.append(f"{t:.16g},6650,0,0,0,7.742,0")
    raw.write_text("\n".join(rows) + "\n", encoding="utf-8")
    result = cross._read_gmat_report(raw, cross.CASE["epoch_utc"])
    assert len(result) == 91
    assert result[-1]["time_s"] < 5400.
    assert abs(result[-1]["time_s"]-5400.) < cross.GMAT_REPORT_FLOAT_TIME_SKEW_BUDGET_S
    # Original Basilisk strict parser threshold remains unchanged.
    with pytest.raises(ValueError, match="TIME_GRID_MISMATCH"):
        cross._assert_grid(result)
    rows[45] = "2700.001,6650,0,0,0,7.742,0"
    raw.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="TIME_GRID_MISMATCH"):
        cross._read_gmat_report(raw, cross.CASE["epoch_utc"])



def test_gmat_mu_control_preserves_original_and_overrides_only_constant():
    base = cross._gmat_script(make_zero_sample(), report_path="original_raw.txt")
    matched = cross._gmat_script(
        make_zero_sample(), report_path="control_raw.txt",
        earth_mu_km3_s2=398600.436)
    assert "GMAT Earth.Mu =" not in base
    assert "GMAT Earth.Mu = 398600.436;" in matched
    assert matched.index("GMAT Earth.Mu =") < matched.index("Create Spacecraft")
    assert "CrossGravity.PointMasses = {Earth}" in matched
    assert "CrossGravity.SRP = Off" in matched
    assert matched.count("Propagate CrossProp(CrossSat)") == 90
    assert matched.count("Report CrossState CrossSat.ElapsedSecs") == 91
    assert matched.isascii()
    for bad in [0, -1, float("nan"), float("inf"), True]:
        with pytest.raises(ValueError,match="INVALID_GMAT_MU_CONTROL"):
            cross._gmat_script(make_zero_sample(), report_path="control.txt",
                               earth_mu_km3_s2=bad)


def test_mu_control_cannot_be_faked_or_run_without_original_real_execution(tmp_path):
    # A successful Basilisk export or synthetic CSV can never qualify as a
    # real GMAT Earth-Mu control run, nor can it be used to assert PASS.
    with pytest.raises(FileNotFoundError):
        cross.compare_mu_control(tmp_path)
    (tmp_path/"case_manifest.json").write_text(
        json.dumps({"schema":cross.SCHEMA, "gravity":{"mu_m3_s2":3.98600436e14},
                    "evidence":{"basilisk_executed":True}}),encoding="utf-8")
    (tmp_path/"gmat_execution.json").write_text(json.dumps({
        "status":"NOT_RUN", "binary_sha256":"no_real_binary"
    }),encoding="utf-8")
    (tmp_path/"gmat_mu_control_execution.json").write_text(json.dumps({
        "status":"GMAT_MU_CONTROL_EXECUTED_UNQUALIFIED_ALIGNMENT",
        "binary_sha256":"no_real_binary"
    }),encoding="utf-8")
    with pytest.raises(ValueError,match="MU_CONTROL_REQUIRES_TWO_REAL_GMAT_RUNS"):
        cross.compare_mu_control(tmp_path)



def test_closed_form_circle_is_exact_at_quarter_turn():
    # Mathematical unit case only: not actual GMAT or flight truth data.
    mu = 3.98600436e14
    radius = 6650000.0
    initial = make_zero_sample()
    initial["vy_m_s"] = math.sqrt(mu / radius)
    n = math.sqrt(mu / (radius**3))
    r0,v0 = cross._closed_form_circular_two_body(initial,mu,0.0)
    assert math.isclose(r0[0],radius,rel_tol=0,abs_tol=1e-9)
    assert math.isclose(v0[1],initial["vy_m_s"],rel_tol=0,abs_tol=1e-12)
    r,v = cross._closed_form_circular_two_body(initial,mu,math.pi/(2*n))
    assert math.isclose(r[0],0,abs_tol=1e-6)
    assert math.isclose(r[1],radius,rel_tol=0,abs_tol=1e-8)
    assert math.isclose(v[0],-initial["vy_m_s"],rel_tol=0,abs_tol=1e-9)
    assert math.isclose(v[1],0,abs_tol=1e-9)


def test_closed_form_rejects_invalid_mu_clock_or_non_circular_initial():
    mu = 3.98600436e14
    valid = make_zero_sample()
    valid["vy_m_s"] = math.sqrt(mu/6650000)
    with pytest.raises(ValueError,match="BAD_MU"):
        cross._closed_form_circular_two_body(valid,float("nan"),60)
    with pytest.raises(ValueError,match="BAD_TIME"):
        cross._closed_form_circular_two_body(valid,mu,-60)
    noncircular = dict(valid,vy_m_s=valid["vy_m_s"] + 1)
    with pytest.raises(ValueError,match="REQUIRES_CIRCULAR_SPEED"):
        cross._closed_form_circular_two_body(noncircular,mu,60)
    # Preserve circular *speed* while introducing a radial velocity.
    radial = dict(valid,vx_m_s=1,vy_m_s=math.sqrt(mu/6650000 - 1))
    with pytest.raises(ValueError,match="REQUIRES_TANGENTIAL_VELOCITY"):
        cross._closed_form_circular_two_body(radial,mu,60)


def test_closed_form_requires_real_hashed_basilisk_and_two_gmat_runs(tmp_path):
    # An unexecuted draft must not be able to claim analytic V&V.
    with pytest.raises(FileNotFoundError):
        cross.analyze_closed_form_two_body(tmp_path)



def test_genuine_frame_time_probe_script_is_ascii_and_reports_both_frames():
    # This unit only inspects a generated script, never fabricates GMAT output.
    s=cross._gmat_frame_time_script(
        make_zero_sample(),mu=398600.436,
        state_path="state.txt",epoch_path="frame.txt")
    assert s.isascii()
    assert "GMAT Earth.Mu = 398600.436;" in s
    assert "CrossFrame.Delimiter = ',';" in s
    assert "CrossSat.UTCModJulian" in s and "CrossSat.TAIModJulian" in s
    assert "CrossSat.TTModJulian" in s and "CrossSat.A1ModJulian" in s
    assert "CrossSat.EarthMJ2000Eq.X" in s
    assert "CrossSat.EarthICRF.VZ" in s
    assert s.count("Report CrossFrame CrossSat.ElapsedSecs") == 91
    assert s.count("Report CrossState CrossSat.ElapsedSecs") == 91
    assert s.count("CrossSat.ElapsedSecs = 60}") == 90


def test_gmat_time_probe_checks_nonstandard_jd_offset_and_leap_seconds(tmp_path):
    # Synthetic FILE FORMAT fixture only, not a GMAT execution result.
    assert cross.GMAT_MJD_JD_OFFSET_DAYS==2430000.0
    utc0=cross._gmat_utc_modjulian(cross.CASE["epoch_utc"],0.)
    assert 20000 < utc0 < 50000  # GMAT MJD, not conventional
    rows=[]
    for i in range(91):
        t=i*60.0
        mjd=utc0 + t/86400
        tai=mjd + 37./86400.
        tt=tai + 32.184/86400.
        numbers=[t,mjd,tai,tt,tt,6650,0,0,0,7.742,0,
                 6650,0,0,0,7.742,0]
        rows.append(",".join(format(n,".16g") for n in numbers))
    f=tmp_path/"not_real_gmat_report.txt"
    f.write_text("\n".join(rows)+"\n",encoding="utf-8")
    a=cross._read_gmat_frame_time(f,cross.CASE["epoch_utc"])
    assert len(a)==91
    assert abs((a[0]["tai_mjd"]-a[0]["utc_mjd"])*86400-37)<.001
    assert abs((a[0]["tt_mjd"]-a[0]["tai_mjd"])*86400-32.184)<.001
    corrupted=rows.copy()
    vals=corrupted[40].split(",")
    vals[1]=str(float(vals[1])+1./86400)  # One second wrong: must fail
    corrupted[40]=",".join(vals)
    f.write_text("\n".join(corrupted)+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="UTC_MJD_MISMATCH"):
        cross._read_gmat_frame_time(f,cross.CASE["epoch_utc"])


def test_real_frame_time_probe_requires_prior_hash_verified_gmat_receipts(tmp_path):
    # No dummy GmatConsole and no synthesis escape path.
    with pytest.raises(FileNotFoundError):
        cross.run_gmat_frame_time_probe(tmp_path,tmp_path/"does-not-exist")


def test_no_external_engine_data_cannot_be_marked_pass(tmp_path):
    with pytest.raises(FileNotFoundError):
        cross.compare(tmp_path)
    case=tmp_path/"case_manifest.json"
    case.write_text(json.dumps({"schema":cross.SCHEMA}),encoding="utf-8")
    (tmp_path/"gmat_execution.json").write_text(json.dumps({"status":"NOT_RUN"}),encoding="utf-8")
    with pytest.raises(ValueError,match="REAL_DUAL_ENGINE_EVIDENCE_REQUIRED"):
        cross.compare(tmp_path)


def test_invalid_git_sha_is_not_silently_replaced_by_claim():
    with pytest.raises(ValueError,match="GIT_SHA_REQUIRED"):
        cross.run_basilisk(Path("/tmp/dt2-cross-leo-unexecuted"),"UNKNOWN")


def test_basilisk_initial_phase_is_not_falsely_claimed_to_be_213_phase():
    assert cross.CASE["initial_elements"]["true_anomaly_deg"] == 0.
    assert "NOT claimed" in cross.CASE["source_note"]
    assert cross.MODEL_FLAGS["earth_point_mass"]
    assert sum(bool(x) for x in cross.MODEL_FLAGS.values()) == 1
