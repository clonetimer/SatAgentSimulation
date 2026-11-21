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
