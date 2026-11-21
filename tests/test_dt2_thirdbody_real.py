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
    a=tb.gmat_script(sample(),Path("/tmp/off.txt"),Path("/tmp/off_mu.txt"),"off")
    b=tb.gmat_script(sample(),Path("/tmp/on.txt"),Path("/tmp/on_mu.txt"),"on")
    assert b.replace("/tmp/on.txt","/tmp/off.txt").replace(
        "/tmp/on_mu.txt","/tmp/off_mu.txt").replace(
        "CrossGravity.PointMasses = {Earth, Sun, Luna};",
        "CrossGravity.PointMasses = {Earth};")==a
    assert a.isascii() and b.isascii()
    assert b.count("Propagate CrossProp(CrossSat)") == 90
    assert b.count("Report CrossState CrossSat.ElapsedSecs") == 91
    assert "Create Variable EarthMuRead SunMuRead LunaMuRead;" in b
    assert "GMAT EarthMuRead = Earth.Mu;" in b
    assert "GMAT SunMuRead = Sun.Mu;" in b
    assert "GMAT LunaMuRead = Luna.Mu;" in b
    assert b.count("Report ThirdBodyMu EarthMuRead SunMuRead LunaMuRead;") == 1
    assert "Report ThirdBodyMu Earth.Mu" not in b
    assert "GMAT SolarSystem.EphemerisSource = 'SPICE';" in b
    assert "CrossSat.CoordinateSystem = EarthICRF;" in b
    for obj,key in (("Earth","earth"),("Sun","sun"),("Luna","moon")):
        assert f"GMAT {obj}.Mu = {tb.MU[key]:.15g};" in b
    assert "CrossGravity.Drag = None;" in b
    assert "CrossGravity.SRP = Off;" in b


def test_mismatch_real_mu_readback_fails_closed(tmp_path):
    path=tmp_path/"mu.txt"
    path.write_text(f"{tb.MU['earth']},{tb.MU['sun']},{tb.MU['moon']}\n")
    assert tb.read_mu(path)==pytest.approx(tb.MU)
    path.write_text("398600.4415,132712440017.99,4902.8005821478\n")
    with pytest.raises(ValueError,match="DT2_THIRDBODY_ACTUAL_GMAT_MU_MISMATCH"):
        tb.read_mu(path)
    path.write_text("398600.436,4902.799\n")
    with pytest.raises(ValueError,match="DT2_THIRDBODY_REAL_MU_READBACK_REQUIRED"):
        tb.read_mu(path)
    path.write_text("398600.436,nan,4902.799\n")
    with pytest.raises(ValueError,match="DT2_THIRDBODY_NONFINITE_MU_READBACK"):
        tb.read_mu(path)


def test_true_run_cannot_start_without_real_source_files(tmp_path):
    for mode in tb.MODES:
        with pytest.raises(FileNotFoundError):
            tb.native(tmp_path,tmp_path,tmp_path/"missing-GmatConsole",mode)
        with pytest.raises(FileNotFoundError):
            tb.gmat(tmp_path,tmp_path,tmp_path/"missing-GmatConsole",mode)
    with pytest.raises(FileNotFoundError):
        tb.compare(tmp_path,tmp_path,tmp_path/"missing-GmatConsole")
