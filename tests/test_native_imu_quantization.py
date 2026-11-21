"""DT-2: true Basilisk ImuSensor LSB quantization and seeded replay.

Do not replace native measurement output with Python rounding. These tests run
the existing whole-spacecraft Basilisk task and read its IMUSensorMsg recorder.
The LSB parameters are engineering fixtures, not as-built sensor calibration.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

GYRO = "imu_gyro_quantization_lsb_rad_s"
ACCEL = "imu_accel_quantization_lsb_m_s2"
RAW_GYRO = tuple(f"adcs.sensor.raw_gyro_rad_s_{axis}" for axis in "xyz")
RAW_ACCEL = tuple(f"adcs.sensor.raw_accel_m_s2_{axis}" for axis in "xyz")


def test_native_imu_quantization_registry_and_dialogue_exposure():
    fields = {field["name"]: field for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for field in group["fields"]}
    assert fields[GYRO]["default"] == fields[ACCEL]["default"] == 0.
    assert fields[GYRO]["unit"] == "rad/s"
    assert fields[ACCEL]["unit"] == "m/s²"
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {GYRO: .01, ACCEL: .02, "simulation_seed": 811}
    })
    assert cfg.native_values()[GYRO] == pytest.approx(.01)
    assert cfg.native_values()[ACCEL] == pytest.approx(.02)
    assert cfg.native_values()["simulation_seed"] == 811
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in (GYRO, ACCEL):
        assert f'data-native-key="{key}"' in html
    assert "ImuSensor.setLSBs" in html
    assert "不代表设备实装标定" in html


@pytest.mark.parametrize("bad", [
    {GYRO: -.001}, {ACCEL: -1.}, {GYRO: .101}, {ACCEL: 1.01},
    {GYRO: float("nan")}, {ACCEL: float("inf")},
    {GYRO: "0.01"}, {ACCEL: True}, {GYRO: None},
])
def test_quantization_prestart_validation_fails_closed(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def _native_run(extra):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    values = {
        "initial_pointing_error_deg": 14.,
        # Nonzero known bias makes the native accelerometer A/B observable
        # even for a freely coasting spacecraft, without a fake force.
        "imu_gyro_bias_rad_s_x": .0137,
        "imu_accel_bias_m_s2_x": .037,
        **extra,
    }
    result = get_adapter_for_capability(WHOLE_ID).run(
        _short_spec(WHOLE_ID, duration_s=8., values=values))
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert "imu" in result.metadata["model_source_boundary"]["basilisk_official_native"]
    assert len(result.trace_rows) >= 5
    return result


def _assert_native_lattice(rows, keys, lsb):
    assert lsb > 0
    for row in rows[1:]:  # Basilisk 2.11 first IMU frame is a startup zero
        for key in keys:
            measured = float(row[key])
            assert math.isfinite(measured)
            levels = measured / lsb
            assert abs(levels - round(levels)) < 1e-8, (key, measured, lsb)


def test_real_basilisk_imu_lsb_quantization_from_raw_sensor_messages():
    baseline = _native_run({})
    explicit_zero = _native_run({GYRO: 0., ACCEL: 0.})
    quantized = _native_run({GYRO: .01, ACCEL: .02})

    assert "imu_quantization" not in baseline.summary
    assert "imu_quantization" not in explicit_zero.summary
    evidence = quantized.summary["imu_quantization"]
    assert evidence["source"] == "Basilisk.simulation.imuSensor.ImuSensor.setLSBs"
    assert evidence["gyro_lsb_rad_s"] == pytest.approx(.01)
    assert evidence["accel_lsb_m_s2"] == pytest.approx(.02)
    assert evidence["hardware_calibration"] is False
    assert evidence["installation_evidence"] == "NOT_PROVIDED"

    assert [r["time_s"] for r in baseline.trace_rows] == [
        r["time_s"] for r in quantized.trace_rows]
    for key in (*RAW_GYRO, *RAW_ACCEL):
        assert [row[key] for row in baseline.trace_rows] == pytest.approx(
            [row[key] for row in explicit_zero.trace_rows], rel=1e-12, abs=1e-12)

    _assert_native_lattice(quantized.trace_rows, RAW_GYRO, .01)
    _assert_native_lattice(quantized.trace_rows, RAW_ACCEL, .02)

    gyro_delta = max(abs(float(a[k]) - float(b[k]))
                     for a, b in zip(baseline.trace_rows[1:], quantized.trace_rows[1:], strict=True)
                     for k in RAW_GYRO)
    accel_delta = max(abs(float(a[k]) - float(b[k]))
                      for a, b in zip(baseline.trace_rows[1:], quantized.trace_rows[1:], strict=True)
                      for k in RAW_ACCEL)
    assert gyro_delta > 1e-6
    assert accel_delta > 1e-6
    print(
        "DT2_NATIVE_IMU_QUANTIZATION_AB_MEASURED "
        f"gyro_lsb_rad_s=0.01 accel_lsb_m_s2=0.02 "
        f"gyro_raw_delta_max_rad_s={gyro_delta:.9e} "
        f"accel_raw_delta_max_m_s2={accel_delta:.9e} "
        "official_native_raw_message=true default_unchanged=true",
        flush=True,
    )


def test_seeded_white_noise_and_native_quantization_replay():
    selected = {GYRO: .002, ACCEL: .02,
                "imu_gyro_white_noise_std_rad_s": .01,
                "imu_accel_white_noise_std_m_s2": .04,
                "simulation_seed": 811}
    first = _native_run(selected)
    second = _native_run(selected)
    changed_seed = _native_run({**selected, "simulation_seed": 812})
    _assert_native_lattice(first.trace_rows, RAW_GYRO, .002)
    _assert_native_lattice(first.trace_rows, RAW_ACCEL, .02)
    for key in (*RAW_GYRO, *RAW_ACCEL):
        assert [row[key] for row in first.trace_rows] == pytest.approx(
            [row[key] for row in second.trace_rows], rel=1e-12, abs=1e-12)
    seed_delta = max(abs(float(a[k]) - float(b[k]))
                     for a, b in zip(first.trace_rows[1:], changed_seed.trace_rows[1:], strict=True)
                     for k in (*RAW_GYRO, *RAW_ACCEL))
    assert seed_delta > 1e-6
    print(
        "DT2_NATIVE_IMU_QUANTIZED_SEEDED_REPLAY_MEASURED "
        f"repeat_seed=811 alternative_seed=812 max_raw_difference={seed_delta:.9e} "
        "same_seed_exact_native_replay=true",
        flush=True,
    )


@pytest.mark.parametrize("bad", [
    {GYRO: float("inf")}, {ACCEL: float("nan")},
    {GYRO: -.01}, {ACCEL: 1.01}, {GYRO: True},
    {ACCEL: "0.02"}, {GYRO: None},
])
def test_runtime_rejects_nonphysical_native_imu_lsb(bad):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_IMU_QUANTIZATION_INVALID"):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad))
