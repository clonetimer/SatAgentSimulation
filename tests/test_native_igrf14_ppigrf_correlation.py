"""Independent IAGA-V-MOD PPIGRF 2.0.0 vs native Basilisk 2.11.0 IGRF-14.

Correlate *field vector components* in the same inertial frame at recorded
spacecraft locations, not model-name strings or only two magnetic magnitudes.
Independent numerical code uses IAGA V-MOD's IGRF14.shc; Basilisk uses the
separately generated, hash-pinned 2025-30 .COF of the same source model.

The independent implementation is a separate mathematical implementation,
NOT independent flight measurements or a BGS/NOAA online-calculator response.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha1
from importlib.metadata import version
from pathlib import Path
import math
import os

import numpy as np
import pytest
from ppigrf import igrf_gc

from sat_sim.bsk_engine.igrf14_asset import verify_igrf14_assets
from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID
)

PPIGRF_COMMIT = "9f3f2f726e2d3a5367fe6685036c8ce3af66c185"
PPIGRF_SCRIPT_BLOB_SHA1 = "2459c72360fe2446449259088ab36625b70fb613"
PPIGRF_SHC_BLOB_SHA1 = "782112655bfab891f8d24d3b2a0225e72f0ad6fc"
MAX_ACCEPTABLE_VECTOR_RESIDUAL_NT = 1.0
UTC_EPOCHS = (
    ("2026-01-01T00:00:00Z", 52.0, 25.0),
    ("2029-02-01T03:10:00Z", 69.0, 135.0),
)


def _git_blob_sha1(data: bytes) -> str:
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _pin_ppigrf_reference():
    assert version("ppigrf") == "2.0.0"
    library_py = Path(igrf_gc.__code__.co_filename).resolve()
    original_shc = library_py.with_name("IGRF14.shc")
    assert _git_blob_sha1(library_py.read_bytes()) == PPIGRF_SCRIPT_BLOB_SHA1, library_py
    assert _git_blob_sha1(original_shc.read_bytes()) == PPIGRF_SHC_BLOB_SHA1
    return library_py, original_shc


def _config(epoch: str, inclination: float, raan: float, *, model="igrf14"):
    return UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=60.0,
        step_s=1.0,
        sample_s=10.0,
        adcs_only=False,
        events=(),
        values={
            "environment_profile": "spice_wmm",
            "magnetic_field_model": model,
            "epoch_utc": epoch,
            "orbit_radius_m": 7_000_000.,
            "eccentricity": 0.001,
            "inclination_deg": inclination,
            "raan_deg": raan,
            "arg_perigee_deg": 45.0,
            "controller_k": 0.0,
            "controller_p": 0.0,
        },
    )


def _vector(row, key):
    return np.array([row[f"{key}_{axis}"] for axis in "xyz"], dtype=float)


def _native_and_reference(row, epoch: str, coeff_fn: Path) -> tuple[np.ndarray, np.ndarray, float]:
    assert row["environment.igrf14.earth_orientation_computed"] == 1
    dcm_np = np.array([
        row[f"environment.igrf14.earth_j20002pfix_{i}{j}"]
        for i in range(3) for j in range(3)
    ], dtype=float).reshape(3, 3)
    assert abs(np.linalg.det(dcm_np) - 1.) < 1.e-6
    assert np.linalg.norm(dcm_np.T @ dcm_np - np.eye(3)) < 1.e-6
    earth_center_n = _vector(row, "environment.igrf14.earth_center_n_m")
    sc_position_n = _vector(row, "orbit.position_m")
    r_p = dcm_np @ (sc_position_n - earth_center_n)
    radius = float(np.linalg.norm(r_p))
    theta = math.acos(float(np.clip(r_p[2] / radius, -1.0, 1.0)))
    phi = math.atan2(float(r_p[1]), float(r_p[0]))
    assert 6.5e6 < radius < 8.0e6
    assert 0.15 < theta < math.pi - 0.15
    date = datetime.fromisoformat(epoch.replace("Z", "+00:00")).replace(tzinfo=None)
    date += timedelta(seconds=float(row["time_s"]))

    # This equation converts the *independent* PPIGRF spherical result into
    # Cartesian Earth-fixed, then uses the recorded SPICE attitude for ECI.
    # It does not implement or re-evaluate any IGRF spherical harmonics.
    br, btheta, bphi = igrf_gc(
        radius / 1000.0, math.degrees(theta), math.degrees(phi),
        date, coeff_fn=str(coeff_fn)
    )
    br_nt, btheta_nt, bphi_nt = [
        float(np.asarray(component).reshape(-1)[0])
        for component in (br, btheta, bphi)
    ]
    radial = np.array([math.sin(theta)*math.cos(phi),
                       math.sin(theta)*math.sin(phi), math.cos(theta)])
    south = np.array([math.cos(theta)*math.cos(phi),
                      math.cos(theta)*math.sin(phi), -math.sin(theta)])
    east = np.array([-math.sin(phi), math.cos(phi), 0.0])
    expected_eci_t = dcm_np.T @ (br_nt * radial + btheta_nt * south + bphi_nt * east) * 1e-9
    native_eci_t = _vector(row, "adcs.magnetometer.reference_n_tesla")
    assert np.all(np.isfinite(expected_eci_t))
    assert np.all(np.isfinite(native_eci_t))
    return native_eci_t, expected_eci_t, radius


def test_ppigrf_exact_official_reference_is_pinned_and_not_native_basilisk():
    library_py, shc = _pin_ppigrf_reference()
    assert library_py.name == "ppigrf.py"
    assert shc.stat().st_size == 42115
    assert shc.name == "IGRF14.shc"
    assert "Basilisk" not in str(library_py)
    assert "IGRF" in shc.read_text(encoding="utf-8")[:1100] or shc.stat().st_size == 42115
    print(
        "DT1_PPIGRF_SOURCE_PIN "
        f"version={version('ppigrf')} "
        f"code_git_blob_sha1={PPIGRF_SCRIPT_BLOB_SHA1} "
        f"igrf14_shc_git_blob_sha1={PPIGRF_SHC_BLOB_SHA1} "
        f"commit={PPIGRF_COMMIT}", flush=True
    )


@pytest.mark.parametrize("epoch,inclination,raan", UTC_EPOCHS)
def test_basilisk_native_igrf14_correlates_independent_ppigrf_at_actual_positions(
    epoch, inclination, raan,
):
    _, shc = _pin_ppigrf_reference()
    root = Path(os.getenv("SAT_SIM_ASSET_ROOT", str(Path(__file__).parents[1] / "third_party")))
    upstream_evidence = verify_igrf14_assets(root / "igrf")
    run = UnifiedNativeRuntime(_config(epoch, inclination, raan)).run()
    assert run.summary["orbital_environment"]["magnetic_field_model_id"] == "IGRF14_2025_2030"
    assert run.summary["orbital_environment"]["magnetic_source_sha256"] == upstream_evidence["source_sha256"]
    assert run.summary["runtime_message_read_error_count"] == 0
    assert len(run.trace_rows) == 7
    # t=0 precedes the first complete physical update. We compare only
    # measured, non-startup samples, and require every one to be within spec.
    residuals_nt, field_strengths_nt, orbital_radii = [], [], []
    for row in run.trace_rows[1:]:
        native_t, independent_t, r_m = _native_and_reference(row, epoch, shc)
        residuals_nt.append(float(np.linalg.norm(native_t - independent_t)) * 1e9)
        field_strengths_nt.append(float(np.linalg.norm(native_t)) * 1e9)
        orbital_radii.append(r_m)
    assert len(residuals_nt) == 6
    assert 10_000. < min(field_strengths_nt) < max(field_strengths_nt) < 80_000.
    assert all(math.isfinite(x) for x in residuals_nt)
    print(
        "DT1_IGRF14_PPIGRF_CORRELATION_MEASURED "
        f"epoch={epoch} samples={len(residuals_nt)} "
        f"max_vector_residual_nt={max(residuals_nt):.9e} "
        f"mean_vector_residual_nt={float(np.mean(residuals_nt)):.9e} "
        f"rms_vector_residual_nt={float(np.sqrt(np.mean(np.square(residuals_nt)))):.9e} "
        f"native_field_min_nt={min(field_strengths_nt):.9e} "
        f"native_field_max_nt={max(field_strengths_nt):.9e} "
        f"radius_min_m={min(orbital_radii):.6f} "
        f"max_accepted_residual_nt={MAX_ACCEPTABLE_VECTOR_RESIDUAL_NT:.3f}",
        flush=True,
    )
    assert max(residuals_nt) < MAX_ACCEPTABLE_VECTOR_RESIDUAL_NT, residuals_nt

    if epoch.startswith("2026-"):
        # A mislabeled WMM model cannot pass this IGRF reference correlation
        # at the known nonzero model-difference position.
        wmm = UnifiedNativeRuntime(_config(epoch, inclination, raan, model="wmm2025")).run()
        native_igrf = [_vector(row, "adcs.magnetometer.reference_n_tesla")
                       for row in run.trace_rows[1:]]
        native_wmm = [_vector(row, "adcs.magnetometer.reference_n_tesla")
                      for row in wmm.trace_rows[1:]]
        differences_nt = [float(np.linalg.norm(a-b)) * 1e9
                          for a,b in zip(native_igrf,native_wmm)]
        print("DT1_IGRF14_PPIGRF_NEGATIVE_CONTROL "
              f"wmm_vs_igrf_min_nt={min(differences_nt):.9e} "
              f"wmm_vs_igrf_max_nt={max(differences_nt):.9e}", flush=True)
        assert min(differences_nt) > MAX_ACCEPTABLE_VECTOR_RESIDUAL_NT
