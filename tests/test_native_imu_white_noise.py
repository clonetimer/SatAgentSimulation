"""Actual official Basilisk IMU white noise, distinct from project event faults.

Tests use the genuine unified Basilisk spacecraft, ImuSensor SWIG object and
its raw sensor message recorder. No post-hoc Python-generated measurement noise.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

GYRO = "imu_gyro_white_noise_std_rad_s"
ACCEL = "imu_accel_white_noise_std_m_s2"


def test_imu_white_noise_fields_available_in_prestart_and_finite_registry():
    fields = {f["name"]: f for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for f in group["fields"]}
    assert fields[GYRO]["unit"] == "rad/s"
    assert fields[ACCEL]["unit"] == "m/s²"
    assert fields[GYRO]["default"] == fields[ACCEL]["default"] == 0.
    assert fields["simulation_seed"]["default"] == 0
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {GYRO: .001, ACCEL: .03, "simulation_seed": 331},
    })
    assert cfg.native_values()[GYRO] == pytest.approx(.001)
    assert cfg.native_values()[ACCEL] == pytest.approx(.03)
    assert cfg.native_values()["simulation_seed"] == 331
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in (GYRO, ACCEL, "simulation_seed"):
        assert f'data-native-key="{key}"' in html
    assert "故障期间噪声增加" in html


@pytest.mark.parametrize("bad", [
    {GYRO: -.0001}, {ACCEL: -1.}, {GYRO: .101}, {ACCEL: 1.01},
    {GYRO: float("nan")}, {ACCEL: float("inf")}, {GYRO: "noise"},
    {"simulation_seed": -1}, {"simulation_seed": 2147483648},
    {"simulation_seed": 1.5}, {"simulation_seed": True},
])
def test_prestart_rejects_bad_sigma_and_seed(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def test_real_basilisk_imu_white_noise_direct_physical_a_b_and_seed_replay(monkeypatch):
    pytest.importorskip("Basilisk")
    from Basilisk.simulation import imuSensor
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    original = imuSensor.ImuSensor
    captured = []
    def capture():
        obj = original()
        captured.append(obj)
        return obj
    monkeypatch.setattr(imuSensor, "ImuSensor", capture)

    adapter = get_adapter_for_capability(WHOLE_ID)
    def run(values):
        start = len(captured)
        result = adapter.run(_short_spec(WHOLE_ID, duration_s=16., values={
            "initial_pointing_error_deg": 14., **values,
        }))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "imu" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(captured) == start + 1
        assert len(result.trace_rows) >= 5
        return result.trace_rows, captured[-1]

    baseline, unmodified = run({})
    noisy, official = run({GYRO: .004, ACCEL: .08, "simulation_seed": 331})
    repeated, _ = run({GYRO: .004, ACCEL: .08, "simulation_seed": 331})
    other_seed, _ = run({GYRO: .004, ACCEL: .08, "simulation_seed": 332})

    assert np.allclose(np.asarray(official.PMatrixGyro, dtype=float),
                       np.eye(3) * .004, atol=1e-12)
    assert np.allclose(np.asarray(official.PMatrixAccel, dtype=float),
                       np.eye(3) * .08, atol=1e-12)
    assert np.allclose(np.asarray(official.getAMatrixGyro(), dtype=float),
                       np.zeros((3, 3)), atol=1e-12)
    assert np.allclose(np.asarray(official.getAMatrixAccel(), dtype=float),
                       np.zeros((3, 3)), atol=1e-12)
    assert int(official.RNGSeed) == 331

    assert [r["time_s"] for r in noisy] == [r["time_s"] for r in baseline]
    raw_channels = [
        *(f"adcs.sensor.raw_gyro_rad_s_{axis}" for axis in "xyz"),
        *(f"adcs.sensor.raw_accel_m_s2_{axis}" for axis in "xyz"),
    ]
    # True sensor output must vary (not only project ImuFaultInjector telemetry).
    for group in (raw_channels[:3], raw_channels[3:]):
        delta = [abs(float(a[k]) - float(b[k]))
                 for a, b in zip(baseline[1:], noisy[1:]) for k in group]
        assert max(delta) > 1e-6, delta
    for key in raw_channels:
        assert [row[key] for row in repeated] == pytest.approx(
            [row[key] for row in noisy], abs=1e-12, rel=1e-12)
    seed_delta = [abs(float(a[k]) - float(b[k]))
                  for a, b in zip(other_seed[1:], noisy[1:]) for k in raw_channels]
    assert max(seed_delta) > 1e-6
    # Fault injector emits zero additive noise in the absence of fault events,
    # so raw and downstream corrected IMU must agree in that no-event case.
    for raw, downstream in zip(
        (f"adcs.sensor.raw_gyro_rad_s_{axis}" for axis in "xyz"),
        (f"adcs.sensor.gyro_measured_rad_s_{axis}" for axis in "xyz"),
    ):
        assert [row[raw] for row in noisy] == pytest.approx(
            [row[downstream] for row in noisy], abs=1e-10, rel=1e-10)


@pytest.mark.parametrize("bad,error", [
    ({GYRO: float("inf")}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({ACCEL: float("nan")}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({GYRO: -.001}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({ACCEL: 2.}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({GYRO: "bad"}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({GYRO: True}, "NATIVE_IMU_WHITE_NOISE_INVALID"),
    ({"simulation_seed": -1}, "NATIVE_SIMULATION_SEED_INVALID"),
    ({"simulation_seed": 1.25}, "NATIVE_SIMULATION_SEED_INVALID"),
    ({"simulation_seed": 2**31}, "NATIVE_SIMULATION_SEED_INVALID"),
])
def test_direct_finite_native_rejects_invalid_sensor_noise_and_seed(bad,error):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match=error):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
