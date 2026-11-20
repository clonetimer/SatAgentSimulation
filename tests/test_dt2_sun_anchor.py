"""Unit contracts for DT-2 true shared-SPK Sun anchor; all fake vectors are test-only."""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import pytest

FILE=Path(__file__).resolve().parents[1]/"scripts"/"dt2_sun_anchor.py"
spec=importlib.util.spec_from_file_location("dt2_sun_anchor",FILE)
assert spec and spec.loader
anchor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(anchor)


def test_sun_report_script_has_actual_gmat_source_and_three_epochs(tmp_path):
    s=anchor.sun_script(
        [31104.4,31104.43,31104.46],
        spk=tmp_path/"DE421AllPlanets.bsp",
        lsk=tmp_path/"SPICELeapSecondKernel.tls",
        pck=tmp_path/"SPICEPlanetaryConstantsKernel.tpc",
        report_path=tmp_path/"real_gmat_report.txt",
    )
    assert s.isascii()
    assert "SolarSystem.EphemerisSource = 'SPICE';" in s
    assert "SolarSystem.SPKFilename" in s
    assert "Sun.EarthICRF.X" in s
    assert "Sun.EarthMJ2000Eq.VZ" in s
    assert s.count("Report SunAnchor ")==3
    assert s.count("Sun.Epoch.A1ModJulian = ")==3
    assert "SunAnchor.Delimiter = ',';" in s


def test_sun_report_script_rejects_invalid_epoch_grid(tmp_path):
    f=tmp_path/"source"
    for invalid in ([], [31104.4,31104.5], [31104.4,31104.4,31104.6],
                    [31104.4,float("nan"),31104.6]):
        with pytest.raises(ValueError):
            anchor.sun_script(invalid,spk=f,lsk=f,pck=f,report_path=f)


def test_sun_vector_parser_rejects_invalid_external_records(tmp_path):
    path=tmp_path/"raw_from_test_only.csv"
    vector=[149597870.0,0,0,0,30,0,
            149597870.0,0,0,0,30,0]
    line=",".join(str(v) for v in vector)
    path.write_text((line+"\n")*3,encoding="utf-8")
    data=anchor.parse_gmat_sun(path)
    assert len(data)==3 and data[0][0]==149597870.0
    path.write_text(line+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="THREE_GMAT_EPOCHS"):
        anchor.parse_gmat_sun(path)
    path.write_text((",".join(["nan"]+list(map(str,vector[1:])))+"\n")*3,encoding="utf-8")
    with pytest.raises(ValueError,match="NONFINITE"):
        anchor.parse_gmat_sun(path)


def test_angle_is_stable_for_tiny_nonzero_rotations():
    # Synthetic vectors: mathematical function test, never published as GMAT evidence.
    a=[1.0,0.0,0.0]
    rotation=1e-9
    b=[math.cos(rotation),math.sin(rotation),0.0]
    assert math.isclose(anchor.angle_arcsec(a,b),206264.806247e-9,rel_tol=1e-8)
    assert anchor.dist(a,a)==0.0
    with pytest.raises(ValueError,match="ZERO_VECTOR"):
        anchor.angle_arcsec([0.0,0,0],b)


def test_no_gmat_and_no_real_manifest_cannot_start_anchor(tmp_path):
    with pytest.raises(FileNotFoundError):
        anchor.run(tmp_path,tmp_path,tmp_path/"not-a-genuine-gmat-binary")
