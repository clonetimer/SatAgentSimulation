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
        report_path=tmp_path/"real_gmat_report.txt",
    )
    assert s.isascii()
    assert "SolarSystem.EphemerisSource = 'SPICE';" in s
    assert "SolarSystem.SPKFilename" not in s
    assert "SolarSystem.LSKFilename" not in s
    assert "SolarSystem.PCKFilename" not in s
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
            anchor.sun_script(invalid,report_path=f)


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

def test_real_release_startup_must_resolve_exact_packaged_kernels(tmp_path):
    root = tmp_path / "GMAT" / "R2026a"
    entries = {
        "spk": root / "data/planetary_ephem/spk/DE421AllPlanets.bsp",
        "lsk": root / "data/time/SPICELeapSecondKernel.tls",
        "pck": root / "data/planetary_coeff/SPICEPlanetaryConstantsKernel.tpc",
    }
    for file in entries.values():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"X" * 101)  # Path contract only; NOT genuine kernels.
    startup = root / "bin/gmat_startup_file.txt"
    startup.parent.mkdir(parents=True)
    content = (
        "ROOT_PATH = ../\n"
        "DATA_PATH = ROOT_PATH/data/\n"
        "PLANETARY_EPHEM_SPK_PATH = DATA_PATH/planetary_ephem/spk/\n"
        "PLANETARY_SPK_FILE = PLANETARY_EPHEM_SPK_PATH/DE421AllPlanets.bsp\n"
        "PLANETARY_COEFF_PATH = DATA_PATH/planetary_coeff/\n"
        "PLANETARY_PCK_FILE = PLANETARY_COEFF_PATH/SPICEPlanetaryConstantsKernel.tpc\n"
        "TIME_PATH = DATA_PATH/time/\n"
        "LSK_FILE = TIME_PATH/SPICELeapSecondKernel.tls\n"
    )
    startup.write_text(content, encoding="utf-8")
    observed = anchor.verified_gmat_startup_kernels(root, entries)
    assert observed["sha256"] == anchor.digest(startup)
    assert observed["kernel_paths"] == {k: str(v.resolve()) for k, v in entries.items()}
    for name, old, altered in (
        ("lsk", "LSK_FILE = TIME_PATH/SPICELeapSecondKernel.tls",
         "LSK_FILE = TIME_PATH/another.tls"),
        ("spk", "PLANETARY_SPK_FILE = PLANETARY_EPHEM_SPK_PATH/DE421AllPlanets.bsp",
         "PLANETARY_SPK_FILE = PLANETARY_EPHEM_SPK_PATH/DE405AllPlanets.bsp"),
        ("pck", "PLANETARY_PCK_FILE = PLANETARY_COEFF_PATH/SPICEPlanetaryConstantsKernel.tpc",
         "PLANETARY_PCK_FILE = PLANETARY_COEFF_PATH/alternate.tpc"),
    ):
        startup.write_text(content.replace(old, altered), encoding="utf-8")
        with pytest.raises(ValueError, match="DT2_SUN_ANCHOR_STARTUP_KERNEL_MISMATCH: " + name):
            anchor.verified_gmat_startup_kernels(root, entries)
    startup.unlink()
    with pytest.raises(FileNotFoundError, match="DT2_SUN_ANCHOR_GMAT_STARTUP_MISSING"):
        anchor.verified_gmat_startup_kernels(root, entries)

def test_sun_triplet_geometry_reports_condition_not_fictitious_frame_certification():
    # Synthetic math contract, not a real celestial observation.
    import numpy as np
    independent = anchor.sun_triplet_condition(np.eye(3).tolist())
    assert math.isclose(independent["sun_triplet_condition_number"], 1.0)
    assert math.isclose(independent["max_pairwise_sun_direction_separation_deg"], 90.0)
    assert independent["absolute_inertial_frame_tie_approved"] is False
    degenerate = anchor.sun_triplet_condition([[1, 0, 0]] * 3)
    assert degenerate["sun_triplet_condition_number"] is None
    assert degenerate["max_pairwise_sun_direction_separation_deg"] == 0.0
    near = anchor.sun_triplet_condition([
        [1.0, 0.0, 0.0],
        [1.0, 0.0005, 0.0],
        [1.0, 0.0010, 1e-11],
    ])
    assert near["sun_triplet_condition_number"] > 1e9
    assert near["absolute_inertial_frame_tie_approved"] is False


def test_sun_triplet_geometry_rejects_invalid_vectors():
    import numpy as np
    invalid = (
        [],
        [[1, 0, 0], [0, 1, 0]],
        [[1, 0, 0], [0, 1, 0], [0, 0, float("nan")]],
        [[1, 0, 0], [0, 0, 0], [0, 0, 1]],
        [[1, 0], [0, 1], [1, 1]],
    )
    for vectors in invalid:
        with pytest.raises(ValueError, match="DT2_SUN_ANCHOR_DIRECTION"):
            anchor.sun_triplet_condition(vectors)

