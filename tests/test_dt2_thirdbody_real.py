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
