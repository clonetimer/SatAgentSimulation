"""Synthetic contract tests only, NEVER substitute for true native/GMAT runs."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import dt2_thirdbody_real as tb


def sample():
    return {"time_s":0.,"utc":"2026-03-04T21:48:00.000Z",
            "x_m":6650000.,"y_m":0.,"z_m":0.,
            "vx_m_s":0.,"vy_m_s":7742.,"vz_m_s":0.}


def test_genuine_GMAT_sun_moon_on_off_scripts_same_except_force_switch():
    a=tb.gmat_script(sample(),Path("/tmp/off.txt"),"off")
    b=tb.gmat_script(sample(),Path("/tmp/on.txt"),"on")
    assert b.replace("/tmp/on.txt","/tmp/off.txt").replace(
        "CrossGravity.PointMasses = {Earth, Sun, Luna};",
        "CrossGravity.PointMasses = {Earth};")==a
    assert a.isascii() and b.isascii()
    assert b.count("Propagate CrossProp(CrossSat)") == 90
    assert b.count("Report CrossState CrossSat.ElapsedSecs") == 91
    assert "Report ThirdBodyMu" not in b
    assert "Create Variable EarthMuRead" not in b
    assert "GMAT EarthMuRead = Earth.Mu;" not in b
    assert "GMAT SolarSystem.EphemerisSource = 'SPICE';" in b
    assert "CrossSat.CoordinateSystem = EarthICRF;" in b
    for obj,key in (("Earth","earth"),("Sun","sun"),("Luna","moon")):
        assert f"GMAT {obj}.Mu = {tb.MU[key]:.15g};" in b
    assert "CrossGravity.Drag = None;" in b
    assert "CrossGravity.SRP = Off;" in b


def test_declared_mu_does_not_imply_runtime_mu_verified():
    assert tb.MU == {
        "earth":398600.436,
        "sun":132712440023.310,
        "moon":4902.799,
    }
    for mode in tb.MODES:
        s=tb.gmat_script(sample(),Path("state.csv"),mode)
        assert "Report ThirdBodyMu" not in s
        assert "GMAT Sun.Mu = 132712440023.31;" in s


def test_true_run_cannot_start_without_real_source_files(tmp_path):
    for mode in tb.MODES:
        with pytest.raises(FileNotFoundError):
            tb.native(tmp_path,tmp_path,tmp_path/"missing-GmatConsole",mode)
        with pytest.raises(FileNotFoundError):
            tb.gmat(tmp_path,tmp_path,tmp_path/"missing-GmatConsole",mode)
    with pytest.raises(FileNotFoundError):
        tb.compare(tmp_path,tmp_path,tmp_path/"missing-GmatConsole")

def test_true_Qz90_initial_input_and_GMAT_absolute_source_contract():
    base=sample()
    rotated=tb.rotate_input_qz90(base)
    assert [rotated[k] for k in tb.AXES]==[-0.,6650000.,0.,-7742.,0.,0.]
    assert base["x_m"]==6650000.
    assert tb.q90([2.,3.,4.])==[-3.,2.,4.]
    assert tb.base_mode("rot-off")=="off"
    assert tb.base_mode("rot-on")=="on"
    for bad in ("off-rot","unknown"):
        with pytest.raises(ValueError,match="DT2_ROT90_INVALID_MODE"):
            tb.base_mode(bad)
    for mode in tb.MODES:
        orig=tb.gmat_script(base,Path("/tmp/original.csv"),mode)
        counter=tb.gmat_script(rotated,Path("/tmp/original.csv"),mode)
        assert orig!=counter
        assert counter.count("Propagate CrossProp(CrossSat)")==90
        assert "CrossSat.CoordinateSystem = EarthICRF;" in counter
        assert "GMAT Sun.Mu = 132712440023.31;" in counter
    for invalid in (dict(base,x_m=float("nan")),{"x_m":123.}):
        with pytest.raises(ValueError,match="DT2_ROT90_"):
            tb.rotate_input_qz90(invalid)


def test_Qz90_comparison_fail_closed_when_authentic_replay_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        tb.compare_qz90(tmp_path,tmp_path,tmp_path/"no-real-GMATConsole")



def _four_synthetic_engine_traces(*, rotate_gmat=False):
    """Mathematics-only inputs, explicitly NOT physical engine evidence."""
    import math
    traces={}
    for engine in ("bsk","gmat"):
        for mode in ("off","on"):
            rows=[]
            for i in range(91):
                dp=[0.012*i,0.02*math.sin(i/13.),-0.03*math.cos(i/17.)]
                dv=[0.0009*i,0.0003*math.sin(i/9.),-0.0002*math.cos(i/8.)]
                if engine=="gmat" and rotate_gmat:
                    dp=tb.q90(dp)
                    dv=tb.q90(dv)
                row={k:0. for k in tb.AXES}
                row["time_s"]=float(60*i)
                if mode=="on":
                    row.update(zip(tb.AXES,dp+dv,strict=True))
                rows.append(row)
            traces[engine,mode]=rows
    return traces


def test_rotation_invariant_geometry_resists_unknown_constant_frame_rotation():
    baseline=tb.frame_invariant_response_geometry(_four_synthetic_engine_traces())
    rotated=tb.frame_invariant_response_geometry(
        _four_synthetic_engine_traces(rotate_gmat=True))
    metric_keys=("position_norm_gap_m","velocity_norm_gap_m_s",
                 "position_chord_gap_m","velocity_chord_gap_m_s",
                 "position_dot_gap_m2")
    assert rotated["sample_count_per_engine_mode"]==91
    assert rotated["cross_time_pair_count"]==4095
    assert all(rotated[k]==pytest.approx(baseline[k],abs=1e-12)
               for k in metric_keys)
    assert all(rotated[k]<1e-12 for k in metric_keys)
    assert rotated["status"]==rotated["scientific_verdict"]=="INCONCLUSIVE"
    assert rotated["frame_rotation_fitted"] is False
    assert rotated["original_BSK_N_to_GMAT_EarthICRF_approved"] is False
    assert rotated["absolute_BSK_ET_to_GMAT_A1_approved"] is False
    assert rotated["effective_GMAT_mu_readback_verified"] is False
    assert rotated["mission_accuracy_budget_approved"] is False


def test_invariant_geometry_detects_true_response_shape_change_not_axis_rotation():
    original=_four_synthetic_engine_traces(rotate_gmat=True)
    control=tb.frame_invariant_response_geometry(original)
    original["gmat","on"][50]["y_m"] += 1.0
    changed=tb.frame_invariant_response_geometry(original)
    assert changed["position_norm_gap_m"]>0.01
    assert changed["position_chord_gap_m"]>0.01
    assert changed["position_dot_gap_m2"]>0.01
    assert changed["position_chord_gap_m"]>control["position_chord_gap_m"]


def test_invariant_geometry_fails_closed_on_misaligned_missing_or_nan_traces():
    rows=_four_synthetic_engine_traces()
    rows["gmat","on"][7]["time_s"] += 0.01
    with pytest.raises(ValueError,match="DT2_INVARIANT_TIMELINE_NOT_INDEX_ALIGNED"):
        tb.frame_invariant_response_geometry(rows)
    rows=_four_synthetic_engine_traces()
    rows["bsk","off"][15]["x_m"]=float("nan")
    with pytest.raises(ValueError,match="DT2_INVARIANT_NONFINITE_PHYSICAL_RESPONSE"):
        tb.frame_invariant_response_geometry(rows)
    rows=_four_synthetic_engine_traces()
    rows.pop(("gmat","off"))
    with pytest.raises(ValueError,match="DT2_INVARIANT_REQUIRES_FOUR_REAL_91_ROW_TRACES"):
        tb.frame_invariant_response_geometry(rows)
