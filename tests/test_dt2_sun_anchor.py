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
    assert "MoonAnchor" not in s  # legacy Sun-only generation remains valid
    both = anchor.sun_script(
        [31104.4, 31104.43, 31104.46],
        report_path=tmp_path/"real_sun.txt",
        moon_report_path=tmp_path/"real_moon.txt",
    )
    assert both.isascii()
    assert both.count("Report SunAnchor ") == 3
    assert both.count("Report MoonAnchor ") == 3
    assert both.count("Luna.Epoch.A1ModJulian = ") == 3
    assert "Luna.EarthICRF.X" in both
    assert "Luna.EarthMJ2000Eq.VZ" in both
    assert "MoonAnchor.Delimiter = ',';" in both
    assert "SolarSystem.LSKFilename" not in both


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


def test_moon_report_parser_requires_real_shape_and_plausible_distance(tmp_path):
    f=tmp_path/"lunar_reference_fixture.csv"
    sample=[384400.,0.,0.,0.,1.,0.,384400.,0.,0.,0.,1.,0.]
    line=",".join(str(v) for v in sample)
    f.write_text((line+"\n")*3,encoding="utf-8")
    assert len(anchor.parse_gmat_moon(f))==3
    f.write_text(line+"\n",encoding="utf-8")
    with pytest.raises(ValueError,match="DT2_MOON_ANCHOR_REQUIRES_THREE_GMAT_EPOCHS"):
        anchor.parse_gmat_moon(f)
    f.write_text((",".join(["nan"]+list(map(str,sample[1:])))+"\n")*3,encoding="utf-8")
    with pytest.raises(ValueError,match="DT2_MOON_ANCHOR_NONFINITE_GMAT_STATE"):
        anchor.parse_gmat_moon(f)
    f.write_text((",".join(map(str,[149597870.]+sample[1:]))+"\n")*3,encoding="utf-8")
    with pytest.raises(ValueError,match="DT2_MOON_ANCHOR_IMPLAUSIBLE_MOON_DISTANCE_KM"):
        anchor.parse_gmat_moon(f)


def test_two_celestial_directions_report_rotation_geometry_not_frame_approval():
    import numpy as np
    perpendicular=anchor.two_celestial_direction_geometry(
        [[1,0,0]]*3, [[0,1,0]]*3)
    assert perpendicular["sun_moon_separation_deg_by_epoch"]==pytest.approx([90.]*3)
    assert perpendicular["rotation_information_eigenvalues_by_epoch"][0]==pytest.approx([1,1,2])
    assert perpendicular["rotation_information_condition_by_epoch"]==pytest.approx([2.]*3)
    assert perpendicular["scientific_verdict"]=="INCONCLUSIVE"
    assert not perpendicular["original_pointmass_n_axes_externally_certified"]
    collinear=anchor.two_celestial_direction_geometry(
        [[1,0,0]]*3, [[1,0,0]]*3)
    assert collinear["rotation_information_condition_by_epoch"]==[None]*3
    for bad in ([], [[1,0,0]]*2, [[float("nan"),0,0]]*3, [[0,0,0]]*3):
        with pytest.raises(ValueError,match="DT2_SUN_MOON_DIRECTION"):
            anchor.two_celestial_direction_geometry(bad,[[0,1,0]]*3)


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
    assert independent["rotation_information_eigenvalues"] == pytest.approx([2.0, 2.0, 2.0])
    assert math.isclose(independent["rotation_information_condition_number"], 1.0)
    assert math.isclose(independent["max_pairwise_sun_direction_separation_deg"], 90.0)
    assert independent["absolute_inertial_frame_tie_approved"] is False
    degenerate = anchor.sun_triplet_condition([[1, 0, 0]] * 3)
    assert degenerate["sun_triplet_condition_number"] is None
    assert degenerate["rotation_information_eigenvalues"] == pytest.approx([0.0, 3.0, 3.0])
    assert degenerate["rotation_information_condition_number"] is None
    assert degenerate["max_pairwise_sun_direction_separation_deg"] == 0.0
    near = anchor.sun_triplet_condition([
        [1.0, 0.0, 0.0],
        [1.0, 0.0005, 0.0],
        [1.0, 0.0010, 1e-11],
    ])
    assert near["sun_triplet_condition_number"] > 1e9
    assert near["rotation_information_condition_number"] > 1e6
    assert near["absolute_inertial_frame_tie_approved"] is False


def test_sun_triplet_geometry_rejects_invalid_vectors():
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


def test_celestial_frame_probe_uses_no_spacecraft_fit_and_keeps_raw_times():
    # Synthetic rotation/control only, NEVER described as real GMAT evidence.
    import numpy as np
    r=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    sun=np.array([150000000.,0.,0.])
    moon=np.array([0.,384400.,0.])
    gmat_sun=[[*list(r@sun),0.,0.,0.,*list(sun),0.,0.,0.]]*3
    gmat_moon=[[*list(r@moon),0.,0.,0.,*list(moon),0.,0.,0.]]*3
    naif_sun=[[*list(r@sun),0.,0.,0.]]*3
    naif_moon=[[*list(r@moon),0.,0.,0.]]*3
    frame_rows=[]
    for i in range(91):
        p=np.array([float(i),0.,0.])
        v=np.array([0.,1.,0.])
        row={"elapsed_s": i*60.0-(i/90)*0.0000106869}
        for label,position,velocity in (
            ("mj",p,v),("icrf",r@p,r@v)
        ):
            for axis,x in zip("xyz",position):
                row[label+"_"+axis+"_km"]=float(x)
            for axis,x in zip("xyz",velocity):
                row[label+"_v"+axis+"_km_s"]=float(x)
        frame_rows.append(row)
    diag,out=anchor.frozen_celestial_frame_probe(
        gmat_sun,gmat_moon,naif_sun,naif_moon,frame_rows)
    assert diag["spacecraft_holdout_samples"]==91
    assert diag["reference_rotation_determinant"]==pytest.approx(1.)
    assert np.asarray(diag["reference_rotation_matrix_mj_to_naif_j2000"])==pytest.approx(r)
    assert diag["max_position_residual_m"]<1e-9
    assert diag["max_velocity_residual_m_s"]<1e-9
    assert out[-1]["gmat_original_elapsed_s"]==pytest.approx(frame_rows[-1]["elapsed_s"])
    assert diag["original_basilisk_N_to_ICRF_approved"] is False
    assert diag["mission_accuracy_budget_approved"] is False
    assert diag["status"]=="INCONCLUSIVE"


def test_celestial_frame_probe_fail_closed_invalid_reference_and_state():
    import numpy as np
    base=[1e8,0.,0.,0.,0.,0.,1e8,0.,0.,0.,0.,0.]
    lunar=[0.,384400.,0.,0.,0.,0.,0.,384400.,0.,0.,0.,0.]
    sun=[base]*3
    moon=[lunar]*3
    naif_sun=[[1e8,0.,0.,0.,0.,0.]]*3
    naif_moon=[[0.,384400.,0.,0.,0.,0.]]*3
    frames=[{}]*91
    with pytest.raises(ValueError,match="DT2_FRAME_BRIDGE_MISSING_OR_INVALID_STATE"):
        anchor.frozen_celestial_frame_probe(sun,moon,naif_sun,naif_moon,frames)
    with pytest.raises(ValueError,match="DT2_FRAME_BRIDGE_REQUIRES_91"):
        anchor.frozen_celestial_frame_probe(sun,moon,naif_sun,naif_moon,frames[:-1])
    bad_moon=[[1e8,0.,0.,0.,0.,0.,1e8,0.,0.,0.,0.,0.]]*3
    with pytest.raises(ValueError,match="DT2_FRAME_BRIDGE_DEGENERATE_REFERENCE"):
        anchor.frozen_celestial_frame_probe(sun,bad_moon,naif_sun,naif_moon,frames)
    bad_sun=[list(base),list(base),list(base)]
    bad_sun[0][0]=float("nan")
    with pytest.raises(ValueError,match="DT2_FRAME_BRIDGE_BAD_CELESTIAL_INPUT"):
        anchor.frozen_celestial_frame_probe(bad_sun,moon,naif_sun,naif_moon,frames)

def test_same_process_native_clock_audit_synthetic_contract_only():
    # Synthetic records validate math/provenance restrictions; NO native engine
    # execution is faked and no external V&V PASS is derived here.
    state={"x_m":6650000.,"y_m":0.,"z_m":0.,
           "vx_m_s":0.,"vy_m_s":7742.,"vz_m_s":0.}
    original=[dict(state,time_s=float(i*60)) for i in range(91)]
    replay=[dict(s) for s in original]
    epoch=[{"time_s":float(t),"et_s":810000000.+float(t)}
           for t in (0,2700,5400)]
    gmat=[{"elapsed_s":float(i*60),
           "a1_mjd":31104.+float(i)/1440.,
           "utc_mjd":31104.,"tai_mjd":31104.00043,
           "tt_mjd":31104.0008}
          for i in range(91)]
    diag,raw=anchor.audit_native_same_process_clock(
        replay,original,epoch,[810000000.,810002700.,810005400.],gmat)
    assert diag["native_spacecraft_replay_rows"]==91
    assert diag["native_spice_time_message_rows"]==3
    assert diag["max_native_spice_ET_minus_naif_ET_abs_s"]==0
    assert diag["max_native_spacecraft_replay_position_vs_sealed_baseline_m"]==0
    assert [r["sample_index"] for r in raw]==[0,45,90]
    assert not diag["absolute_Basilisk_spacecraft_N_to_EarthICRF_approved"]
    assert not diag["absolute_GMAT_A1_to_Basilisk_ET_approved"]
    assert diag["independently_qualified_cross_engine_samples"]==0
    replay[-1]["y_m"]+=0.002
    changed,_=anchor.audit_native_same_process_clock(
        replay,original,epoch,[810000000.,810002700.,810005400.],gmat)
    assert changed["max_native_spacecraft_replay_position_vs_sealed_baseline_m"]==pytest.approx(0.002)
    replay[0]["x_m"]+=0.003
    with pytest.raises(ValueError,match="DT2_NATIVE_CLOCK_REPLAY_ORIGINAL_INITIAL_STATE_CHANGED"):
        anchor.audit_native_same_process_clock(
            replay,original,epoch,[810000000.,810002700.,810005400.],gmat)
    with pytest.raises(ValueError,match="DT2_NATIVE_CLOCK_THREE_CELESTIAL_EPOCHS_REQUIRED"):
        anchor.audit_native_same_process_clock(
            original,original,epoch[:2],[810000000.,810002700.],gmat)
    replay[0]["x_m"]=state["x_m"]
    gmat[45]["elapsed_s"]=2700.001
    with pytest.raises(ValueError,match="DT2_NATIVE_CLOCK_GMAT_INPUT_REPRESENTATION_BAD"):
        anchor.audit_native_same_process_clock(
            replay,original,epoch,[810000000.,810002700.,810005400.],gmat)

