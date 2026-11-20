"""DT-1 GGM03S: strict full coefficients and *actual* Basilisk J2/3/4 dynamics."""
from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path

import numpy as np
import pytest

from sat_sim.bsk_engine.gravity_harmonics_asset import (
    SOURCE_SIZE_BYTES, SOURCE_BLOB_SHA1, inspect_ggm03s,
)
from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment
from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
)

ROOT = Path(__file__).resolve().parents[1]


def _config(**values):
    return UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=1800., step_s=5., sample_s=20., adcs_only=False,
        events=(), values={
            "environment_profile": "spice_wmm",
            "epoch_utc": "2026-01-01T00:00:00Z",
            "orbit_radius_m": 7_000_000.,
            "eccentricity": 0.001,
            "inclination_deg": 52.,
            "raan_deg": 25.,
            "arg_perigee_deg": 45.,
            "controller_k": 0.,
            "controller_p": 0.,
            **values,
        },
    )


def _state(row):
    return np.array([
        *(row[f"orbit.position_m_{axis}"] for axis in "xyz"),
        *(row[f"orbit.velocity_m_s_{axis}"] for axis in "xyz"),
    ], dtype=float)


def _dcm(row):
    return np.array([
        row[f"orbit.earth_fixed_dcm_{i}{j}"] for i in range(3) for j in range(3)
    ], dtype=float).reshape(3, 3)


def test_invalid_or_unsupported_gravity_request_fails_closed():
    for value in ("ggm03s_degree5", "j4", "", None, 3, True):
        with pytest.raises(ValueError, match="NATIVE_EARTH_GRAVITY_MODEL_UNSUPPORTED"):
            NativeOrbitalEnvironment({"earth_gravity_model": value}, 2.)
    with pytest.raises(ValueError, match="NATIVE_GGM03S_REQUIRES_SPICE_WMM"):
        NativeOrbitalEnvironment({"earth_gravity_model": "ggm03s_degree4"}, 2.)


def test_missing_and_tampered_full_coefficients_are_rejected(tmp_path):
    candidate = tmp_path / "GGM03S.txt"
    with pytest.raises(FileNotFoundError, match="NATIVE_GGM03S_FULL_ASSET_MISSING"):
        inspect_ggm03s(candidate)
    candidate.write_bytes(b"not the original file")
    with pytest.raises(ValueError, match="NATIVE_GGM03S_SIZE_MISMATCH"):
        inspect_ggm03s(candidate)
    candidate.write_bytes(b"X" * SOURCE_SIZE_BYTES)
    with pytest.raises(ValueError, match="NATIVE_GGM03S_PINNED_BLOB_SHA1_MISMATCH"):
        inspect_ggm03s(candidate)


def test_actual_official_coefficient_identity_and_nonzero_J3_J4():
    file = Path(os.getenv("SAT_SIM_ASSET_ROOT", str(ROOT / "third_party"))) / "gravity" / "GGM03S.txt"
    if not file.is_file():
        pytest.skip("Full GGM03S only loaded by explicit asset preflight")
    report = inspect_ggm03s(file)
    assert report["size_bytes"] == SOURCE_SIZE_BYTES
    assert report["source_git_blob_sha1"] == SOURCE_BLOB_SHA1
    assert len(report["sha256"]) == 64
    assert report["normalized"] is True
    assert report["declared_max_degree"] >= 4
    assert report["degree3_degree4_full_order_terms_present"] is True
    assert abs(report["cbar_30"]) > 1.0e-10
    assert abs(report["cbar_40"]) > 1.0e-10
    print("DT1_GGM03S_COEFFICIENT_MEASURED "
          f"sha256={report['sha256']} "
          f"cbar_30={report['cbar_30']:.12e} "
          f"cbar_40={report['cbar_40']:.12e} "
          f"reference_radius_m={report['reference_radius_m']:.3f} "
          f"reference_mu_m3_s2={report['reference_mu_m3_s2']:.9e}", flush=True)


def test_native_full_GGM03S_degree2_degree3_degree4_and_rotating_earth():
    file = Path(os.getenv("SAT_SIM_ASSET_ROOT", str(ROOT / "third_party"))) / "gravity" / "GGM03S.txt"
    if not file.is_file():
        pytest.skip("Requires exact GGM03S asset staged by native workflow")
    baseline = UnifiedNativeRuntime(_config()).run()
    by_degree = {}
    for degree in (2, 3, 4):
        label = f"ggm03s_degree{degree}"
        result = UnifiedNativeRuntime(_config(earth_gravity_model=label)).run()
        by_degree[degree] = result
        summary = result.summary["orbital_environment"]
        assert summary["gravity"] == f"earth_GGM03S_full_degree_{degree}_plus_sun_moon"
        assert summary["gravity_degree"] == degree
        assert summary["spherical_harmonic_scope"] == "full_degree_and_order_includes_tesseral_terms"
        assert summary["gravity_source"]["source_git_blob_sha1"] == SOURCE_BLOB_SHA1
        assert result.summary["gravity_harmonics"]["degree_and_order"] == degree
        assert result.summary["gravity_harmonics"]["flight_validation"] is False
        assert result.summary["runtime_message_read_error_count"] == 0
        assert len(result.trace_rows) == 91
        dcm_start, dcm_end = _dcm(result.trace_rows[0]), _dcm(result.trace_rows[-1])
        assert result.trace_rows[-1]["orbit.earth_orientation_computed"] == 1
        for matrix in (dcm_start, dcm_end):
            assert np.all(np.isfinite(matrix))
            assert abs(np.linalg.det(matrix) - 1.) < 1e-6
            assert np.linalg.norm(matrix.T @ matrix - np.eye(3)) < 1e-5
        # At the same time the actual rotating planet-fixed frame changes.
        assert np.linalg.norm(dcm_start - dcm_end) > 0.02

    ends = {degree: _state(res.trace_rows[-1]) for degree, res in by_degree.items()}
    differences = {
        "legacy_to_full2_m": float(np.linalg.norm(
            ends[2][:3] - _state(baseline.trace_rows[-1])[:3])),
        "degree2_to3_m": float(np.linalg.norm(ends[3][:3] - ends[2][:3])),
        "degree3_to4_m": float(np.linalg.norm(ends[4][:3] - ends[3][:3])),
        "degree2_to3_velocity_m_s": float(np.linalg.norm(ends[3][3:] - ends[2][3:])),
        "degree3_to4_velocity_m_s": float(np.linalg.norm(ends[4][3:] - ends[3][3:])),
    }
    # These are actual native orbit response differences under one real source
    # file with truncation at degrees 2/3/4; not a fabricated J3-only curve.
    assert differences["degree2_to3_m"] > 0.001
    assert differences["degree3_to4_m"] > 0.001
    assert differences["degree2_to3_velocity_m_s"] > 1e-7
    assert differences["degree3_to4_velocity_m_s"] > 1e-7
    assert all(math.isfinite(value) for value in differences.values())
    print(
        "DT1_GGM03S_NATIVE_DEGREE_EFFECT "
        "samples=91 duration_s=1800 step_s=5 "
        + " ".join(f"{key}={value:.9e}" for key, value in differences.items())
        + " full_degree_and_order=true",
        flush=True,
    )


def test_native_full_GGM03S_degree4_timestep_refinement():
    file = Path(os.getenv("SAT_SIM_ASSET_ROOT", str(ROOT / "third_party"))) / "gravity" / "GGM03S.txt"
    if not file.is_file():
        pytest.skip("Requires exact official coefficients")
    original = _config(earth_gravity_model="ggm03s_degree4")
    endpoints = []
    for dt in (10., 5., 2.5):
        run = UnifiedNativeRuntime(replace(original, step_s=dt)).run()
        endpoints.append(_state(run.trace_rows[-1])[:3])
    coarse = float(np.linalg.norm(endpoints[0] - endpoints[1]))
    fine = float(np.linalg.norm(endpoints[1] - endpoints[2]))
    assert coarse > fine > 0.
    observed = math.log(coarse / fine, 2.)
    print(
        "DT1_GGM03S_NATIVE_REFINEMENT "
        f"coarse_delta_m={coarse:.9e} fine_delta_m={fine:.9e} "
        f"observed_order={observed:.8f}",
        flush=True,
    )
    # Same documented SPICE-coupled orbit numerical acceptance as the existing
    # J2 baseline: do not infer RK4 order from the integrator name.
    assert 1.5 < observed < 4.5, (coarse, fine, observed)
    assert fine < 2., (coarse, fine)
