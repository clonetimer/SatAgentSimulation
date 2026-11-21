"""DT-1 IGRF-14: immutable model coefficients + real native magnetometer A/B."""
from __future__ import annotations

from pathlib import Path
import hashlib
import math
import os

import numpy as np
import pytest

from sat_sim.bsk_engine.igrf14_asset import (
    COF_NAME, SOURCE_NAME, SOURCE_BYTES, SOURCE_BLOB_SHA1,
    parse_igrf14, render_igrf14_cof, verify_igrf14_assets,
)
from sat_sim.bsk_engine.orbital_environment import NativeOrbitalEnvironment
from sat_sim.bsk_engine.unified_native import (
    UnifiedNativeRuntime, UnifiedRuntimeConfig, WHOLE_UNIFIED_CAPABILITY_ID,
)

ROOT = Path(__file__).resolve().parents[1]


def _asset_dir() -> Path:
    return Path(os.getenv("SAT_SIM_ASSET_ROOT", str(ROOT / "third_party"))) / "igrf"


def _config(**values) -> UnifiedRuntimeConfig:
    return UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=60., step_s=1.0, sample_s=10.0, adcs_only=False, events=(),
        values={
            "environment_profile": "spice_wmm",
            "epoch_utc": "2026-01-01T00:00:00Z",
            "controller_k": 0.0,
            "controller_p": 0.0,
            **values,
        },
    )


def _vector(row: dict, prefix: str) -> np.ndarray:
    return np.array([row[f"{prefix}_{axis}"] for axis in "xyz"], dtype=float)


def test_invalid_magnetic_selectors_and_missing_assets_fail_closed(tmp_path, monkeypatch):
    for value in ("IGRF", "igrf13", None, 7, True, ""):
        with pytest.raises(ValueError, match="NATIVE_MAGNETIC_FIELD_MODEL_UNSUPPORTED"):
            NativeOrbitalEnvironment({"magnetic_field_model":value}, 2.)
    with pytest.raises(ValueError, match="NATIVE_IGRF14_REQUIRES_SPICE_WMM"):
        NativeOrbitalEnvironment({"magnetic_field_model":"igrf14"}, 2.)
    with pytest.raises(FileNotFoundError, match="IGRF14_PINNED_SOURCE_OR_COF_MISSING"):
        verify_igrf14_assets(tmp_path)
    src = tmp_path / SOURCE_NAME
    dst = tmp_path / COF_NAME
    src.write_bytes(b"modified fake IGRF")
    dst.write_bytes(b"2025.0 WMM-2025 fake\n")
    with pytest.raises(ValueError, match="IGRF14_SOURCE_SIZE_MISMATCH"):
        verify_igrf14_assets(tmp_path)
    src.write_bytes(b"X" * SOURCE_BYTES)
    with pytest.raises(ValueError, match="IGRF14_SOURCE_GIT_BLOB_MISMATCH"):
        verify_igrf14_assets(tmp_path)


def test_genuine_IAGA_IGRF14_source_and_cof_are_bitwise_deterministic(tmp_path):
    folder = _asset_dir()
    if not (folder / SOURCE_NAME).is_file():
        pytest.skip("IGRF14 source intentionally staged only by explicit setup")
    info = verify_igrf14_assets(folder)
    assert info["model_id"] == "IGRF14_2025_2030"
    assert info["source_git_blob_sha1"] == SOURCE_BLOB_SHA1
    assert info["source_bytes"] == SOURCE_BYTES
    assert info["coefficient_rows"] == 195
    assert info["main_degree"] == 13
    assert info["secular_variation_degree"] == 8
    assert info["base_epoch_year"] == 2025.0
    assert info["g10_2025_nt"] == -29350.0
    assert info["g10_sv_nt_per_year"] == 12.6
    assert info["cof_degree_and_order"] == 13
    assert len(info["source_sha256"]) == len(info["cof_sha256"]) == 64
    source = (folder / SOURCE_NAME).read_bytes()
    cof, independently_parsed = render_igrf14_cof(source)
    assert cof == (folder / COF_NAME).read_bytes()
    assert info["cof_sha256"] == hashlib.sha256(cof).hexdigest()
    assert independently_parsed["source_sha256"] == info["source_sha256"]
    records, _ = parse_igrf14(source)
    assert records[1, 0] == (-29350.0, 0.0, 12.6, 0.0)
    assert records[1, 1] == (-1410.3, 4545.5, 10.0, -21.5)
    assert all(records[n, m][2] == records[n, m][3] == 0.
               for n in range(9,14) for m in range(n+1))
    # Reject a same-named conversion that has the correct source but altered
    # output numeric rows; no silent regeneration during runtime.
    altered = tmp_path
    (altered / SOURCE_NAME).write_bytes(source)
    corruption = bytearray(cof); corruption[60] ^= 1
    (altered / COF_NAME).write_bytes(corruption)
    with pytest.raises(ValueError, match="IGRF14_DERIVED_COF_CONTENT_MISMATCH"):
        verify_igrf14_assets(altered)
    print(
        "DT1_IGRF14_COEFFICIENT_MEASURED "
        f"source_sha256={info['source_sha256']} "
        f"cof_sha256={info['cof_sha256']} "
        "rows=195 max_degree=13 sv_degree=8 "
        "g10_2025_nt=-29350.0 g10_sv_nt_per_year=12.6",
        flush=True,
    )


def test_default_WMM_and_explicit_WMM_are_identical_native_baselines():
    baseline = UnifiedNativeRuntime(_config()).run()
    explicit = UnifiedNativeRuntime(_config(magnetic_field_model="wmm2025")).run()
    for result in (baseline, explicit):
        assert result.summary["orbital_environment"]["magnetic_field"] == "WMM2025"
        assert "igrf14" not in result.summary["orbital_environment"]["assets"]
        assert "magnetic_field_model_id" not in result.summary["orbital_environment"]
        assert result.summary["runtime_message_read_error_count"] == 0
    assert len(baseline.trace_rows) == len(explicit.trace_rows) == 7
    for a, b in zip(baseline.trace_rows, explicit.trace_rows):
        for prefix in ("adcs.magnetometer.reference_n_tesla", "adcs.magnetometer.tesla"):
            assert np.array_equal(_vector(a, prefix), _vector(b,prefix))


def test_native_IGRF14_changes_actual_magnetic_field_and_sensor_measurement():
    folder = _asset_dir()
    if not (folder / SOURCE_NAME).is_file():
        pytest.skip("Explicit IGRF14 source setup not performed")
    source = verify_igrf14_assets(folder)
    wmm = UnifiedNativeRuntime(_config()).run()
    igrf = UnifiedNativeRuntime(_config(magnetic_field_model="igrf14")).run()
    evidence = igrf.summary["orbital_environment"]
    assert evidence["magnetic_field"] == "IGRF14_2025_2030"
    assert evidence["magnetic_field_model_id"] == "IGRF14_2025_2030"
    assert evidence["magnetic_source_is_WMM2025"] is False
    assert evidence["magnetic_main_degree"] == 13
    assert evidence["magnetic_secular_variation_degree"] == 8
    assert evidence["magnetic_source_sha256"] == source["source_sha256"]
    assert evidence["magnetic_derived_cof_sha256"] == source["cof_sha256"]
    assert evidence["assets"]["igrf14"]["source_git_blob_sha1"] == SOURCE_BLOB_SHA1
    assert "wmm" not in evidence["assets"]
    assert evidence["magnetic_model_engine"] == "Basilisk.magneticFieldWMM_NOAA_MAG_with_IGRF14_COF"
    assert evidence["spacecraft_flight_validation"] is False
    assert igrf.summary["runtime_message_read_error_count"] == 0
    assert len(wmm.trace_rows) == len(igrf.trace_rows) == 7
    ref_differences = []
    sensor_differences = []
    magnitudes = []
    for roww, rowi in zip(wmm.trace_rows[1:], igrf.trace_rows[1:]):
        reference_w = _vector(roww, "adcs.magnetometer.reference_n_tesla")
        reference_i = _vector(rowi, "adcs.magnetometer.reference_n_tesla")
        sensor_w = _vector(roww, "adcs.magnetometer.tesla")
        sensor_i = _vector(rowi, "adcs.magnetometer.tesla")
        assert np.isfinite(reference_i).all() and np.isfinite(sensor_i).all()
        ref_differences.append(float(np.linalg.norm(reference_i - reference_w)))
        sensor_differences.append(float(np.linalg.norm(sensor_i - sensor_w)))
        magnitudes.append(float(np.linalg.norm(reference_i)))
    assert min(magnitudes) > 1e-6
    assert max(magnitudes) < 1e-3
    # The field generator and the OFFICIAL Basilisk magnetometer both must
    # respond, not just the capability manifest or output label.
    assert min(ref_differences) > 1e-10, ref_differences
    assert min(sensor_differences) > 1e-10, sensor_differences
    print(
        "DT1_IGRF14_NATIVE_AB_MEASURED "
        f"samples={len(igrf.trace_rows)} "
        f"reference_difference_min_t={min(ref_differences):.9e} "
        f"reference_difference_max_t={max(ref_differences):.9e} "
        f"magnetometer_difference_min_t={min(sensor_differences):.9e} "
        f"magnetometer_difference_max_t={max(sensor_differences):.9e} "
        f"igrf14_magnitude_min_t={min(magnitudes):.9e} "
        f"igrf14_magnitude_max_t={max(magnitudes):.9e} "
        f"model_id={evidence['magnetic_field_model_id']}",
        flush=True,
    )


def test_igrf14_epoch_boundary_is_explicit_and_not_clamped():
    # 2030.0 and later: the 2025-30 IGRF predictive SV is out of range.
    with pytest.raises(ValueError, match="WMM2025_EPOCH_OUTSIDE_VALIDITY_2025_2030"):
        NativeOrbitalEnvironment(
            {"environment_profile":"spice_wmm","magnetic_field_model":"igrf14",
             "epoch_utc":"2030-01-01T00:00:00Z"}, 2.)
