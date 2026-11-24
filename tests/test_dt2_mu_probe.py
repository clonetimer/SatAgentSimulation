"""DT-2 Mu parameter sensitivity: synthetic contracts, never real GMAT proof."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import dt2_mu_probe as probe
import dt2_thirdbody_real as real


def example():
    return {
        "time_s":0.,"utc":"2026-03-04T21:48:00.000Z",
        "x_m":6650000.,"y_m":0.,"z_m":0.,
        "vx_m_s":0.,"vy_m_s":7742.,"vz_m_s":0.,
    }


def test_one_body_mu_input_change_only_and_causal_probe_is_nonphysical():
    baseline=real.gmat_script(example(),Path("/tmp/real.txt"),"on")
    for mode in probe.TARGETS:
        changed=probe.script_for_intervention(example(),Path("/tmp/real.txt"),mode)
        body="Sun" if mode=="sun2x" else "Luna"
        key="sun" if mode=="sun2x" else "moon"
        old=f"GMAT {body}.Mu = {real.MU[key]:.15g};"
        new=f"GMAT {body}.Mu = {real.MU[key]*2:.15g};"
        assert changed==baseline.replace(old,new)
        assert changed.count(new)==1
        assert changed.isascii()
        assert "CrossGravity.PointMasses = {Earth, Sun, Luna};" in changed
        assert "Report ThirdBodyMu" not in changed
    for invalid in ("sun","earth2x","SUN2x",None):
        with pytest.raises(ValueError,match="DT2_MU_PROBE_BAD_INTERVENTION"):
            probe.script_for_intervention(example(),Path("/tmp/real.txt"),invalid)


def test_twofold_mu_probe_never_fabricates_real_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe.execute(tmp_path,tmp_path,tmp_path/"no-real-GMAT","sun2x")
    with pytest.raises(FileNotFoundError):
        probe.aggregate(tmp_path,tmp_path,tmp_path/"no-real-GMAT")
