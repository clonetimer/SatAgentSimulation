"""Genuine Basilisk magnetometer orientation and fixed calibration acceptance.

The fields are wired to the official C++ Magnetometer, not reconstructed
magnetic telemetry. For Basilisk 2.11, do not claim Gaussian white noise:
upstream RNGSeed and default propagation were corrected only in 2.12.
"""
from __future__ import annotations

from pathlib import Path
import math

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)


FIELDS = (
    "magnetometer_mount_yaw_deg",
    "magnetometer_mount_pitch_deg",
    "magnetometer_mount_roll_deg",
    "magnetometer_bias_tesla_x",
    "magnetometer_bias_tesla_y",
    "magnetometer_bias_tesla_z",
    "magnetometer_scale_factor",
)
MAG = tuple(f"adcs.magnetometer.tesla_{axis}" for axis in "xyz")


def test_magnetometer_prestart_controls_and_zero_calibration_baseline():
    assert ContinuousInitialConfiguration().native_values() == {}
    fields = {
        f["name"]: f
        for group in initial_configuration_catalog()["groups"]
        if group["group"] == "adcs" for f in group["fields"]
    }
    for key in FIELDS:
        assert key in fields
        assert fields[key]["unit"] in ("deg", "T", "1")
        assert fields[key]["default"] == (1. if key == "magnetometer_scale_factor" else 0.)
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {"magnetometer_mount_yaw_deg": 90.,
                 "magnetometer_bias_tesla_z": .000001,
                 "magnetometer_scale_factor": 1.3}
    })
    assert cfg.native_values()["magnetometer_mount_yaw_deg"] == 90.
    assert cfg.native_values()["magnetometer_bias_tesla_z"] == pytest.approx(.000001)
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in FIELDS:
        assert f'data-native-key="{key}"' in html
    assert "2.11 磁强计正常噪声" in html


@pytest.mark.parametrize("invalid", [
    {"magnetometer_mount_yaw_deg": 190.},
    {"magnetometer_mount_pitch_deg": 90.},
    {"magnetometer_mount_roll_deg": float("nan")},
    {"magnetometer_bias_tesla_x": .0002},
    {"magnetometer_bias_tesla_y": float("inf")},
    {"magnetometer_scale_factor": 0.},
    {"magnetometer_scale_factor": 2.1},
])
def test_magnetometer_invalid_prestart_input_rejected(invalid):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": invalid})


def test_real_basilisk_magnetometer_mount_bias_scale_affect_native_sensor_tam(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import magnetometer
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = magnetometer.Magnetometer
    created = []
    def capture():
        obj = original()
        created.append(obj)
        return obj
    monkeypatch.setattr(magnetometer, "Magnetometer", capture)
    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(values):
        initial = len(created)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=5., values={
            "initial_pointing_error_deg": 8., **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "magnetometer" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(created) == initial + 1
        assert len(result.trace_rows) >= 3
        return result.trace_rows, created[-1]

    baseline, default = run({})
    rotate, sensor = run({"magnetometer_mount_yaw_deg": 90.})
    bias, biased = run({"magnetometer_bias_tesla_x": .000005})
    scale, scaled = run({"magnetometer_scale_factor": 1.5})

    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in rotate]
    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in bias]
    assert [row["time_s"] for row in baseline] == [row["time_s"] for row in scale]
    assert np.asarray(default.dcm_SB, dtype=float) == pytest.approx(np.eye(3), abs=1e-12)
    assert not np.allclose(np.asarray(sensor.dcm_SB, dtype=float), np.eye(3), atol=1e-6)
    assert np.asarray(sensor.dcm_SB, dtype=float) @ np.array([0., 0., 1.]) == pytest.approx(
        np.array([0., 0., 1.]), abs=1e-10)
    assert np.asarray(biased.senBias, dtype=float) == pytest.approx(
        np.array([.000005, 0., 0.]), abs=1e-12)
    assert float(scaled.scaleFactor) == pytest.approx(1.5)

    # The same official dipole/Basilisk spacecraft provides the input field.
    # In zero-bias case rotating the real sensor frame must rotate TAM output
    # without an invented Python magnetometer response.
    first = next(i for i, row in enumerate(baseline)
                 if np.linalg.norm([row[k] for k in MAG]) > 1e-12)
    b = np.array([baseline[first][k] for k in MAG], dtype=float)
    r = np.array([rotate[first][k] for k in MAG], dtype=float)
    assert r == pytest.approx(np.asarray(sensor.dcm_SB, dtype=float) @ b,
                              abs=5e-9, rel=2e-3)
    assert np.linalg.norm(r - b) > 1e-7

    # Static axis bias enters real TAM data in Tesla; scaleFactor linearly
    # changes original sensor data (no pseudo-noise or event involved).
    biased_vec = np.array([bias[first][k] for k in MAG], dtype=float)
    scaled_vec = np.array([scale[first][k] for k in MAG], dtype=float)
    assert abs(biased_vec[0] - b[0]) > 1e-6
    assert biased_vec[0] - b[0] == pytest.approx(5e-6, abs=5e-9, rel=1e-3)
    assert scaled_vec == pytest.approx(b * 1.5, abs=5e-9, rel=2e-3)


@pytest.mark.parametrize("bad,error", [
    ({"magnetometer_mount_yaw_deg": float("inf")}, "NATIVE_MAGNETOMETER_MOUNT_INVALID"),
    ({"magnetometer_mount_pitch_deg": 90.}, "NATIVE_MAGNETOMETER_MOUNT_INVALID"),
    ({"magnetometer_mount_roll_deg": "bad"}, "NATIVE_MAGNETOMETER_MOUNT_INVALID"),
    ({"magnetometer_bias_tesla_z": float("nan")}, "NATIVE_MAGNETOMETER_BIAS_INVALID"),
    ({"magnetometer_bias_tesla_y": .002}, "NATIVE_MAGNETOMETER_BIAS_INVALID"),
    ({"magnetometer_scale_factor": 0.}, "NATIVE_MAGNETOMETER_SCALE_INVALID"),
    ({"magnetometer_scale_factor": True}, "NATIVE_MAGNETOMETER_SCALE_INVALID"),
])
def test_finite_native_bypassing_prestart_fails_closed(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match=error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
