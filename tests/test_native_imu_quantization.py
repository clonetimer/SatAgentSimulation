"""DT-2A: genuine Basilisk 2.11.0 IMU LSB, not postprocessed telemetry.

The underlying ImuSensor.setLSBs(accel, gyro) discretizes sensor output
before the raw message recorder and project fusion. A/B uses identical
initial conditions and preserves the zero-by-default compatibility contract.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

GYRO = "imu_gyro_lsb_rad_s"
ACCEL = "imu_accel_lsb_m_s2"


def test_dt2_native_imu_quantization_is_opt_in_across_capability_form_and_html():
    from sat_sim.capability_registry import get_capability

    params = get_capability("whole_spacecraft.unified_native.v1").data["parameters"]
    assert params[GYRO]["default"] == params[ACCEL]["default"] == 0
    assert params[GYRO]["unit"] == "rad/s"
    assert params[ACCEL]["unit"] == "m/s^2"
    fields = {
        item["name"]: item
        for group in initial_configuration_catalog()["groups"]
        if group["group"] == "adcs"
        for item in group["fields"]
    }
    assert fields[GYRO]["unit"] == "rad/s"
    assert fields[ACCEL]["unit"] == "m/s²"
    assert fields[GYRO]["default"] == fields[ACCEL]["default"] == 0
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {GYRO: 0.0043, ACCEL: 0.02}
    })
    assert cfg.native_values()[GYRO] == pytest.approx(0.0043)
    assert cfg.native_values()[ACCEL] == pytest.approx(0.02)
    html = (Path(__file__).parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in (GYRO, ACCEL):
        assert f'data-native-key="{key}"' in html


@pytest.mark.parametrize("bad", [
    {GYRO: -1e-6}, {ACCEL: -1e-6}, {GYRO: 0.2}, {ACCEL: 1.1},
    {GYRO: float("nan")}, {ACCEL: float("inf")}, {GYRO: "not-a-number"},
    {GYRO: True}, {ACCEL: False}, {GYRO: "0.0043"},
])
def test_dt2_prestart_rejects_invalid_lsb(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


@pytest.mark.parametrize("bad", [
    {GYRO: -1e-6}, {ACCEL: -1e-6}, {GYRO: 0.2}, {ACCEL: 1.1},
    {GYRO: float("nan")}, {ACCEL: float("inf")}, {GYRO: True},
    {ACCEL: False}, {GYRO: "not-a-number"},
])
def test_dt2_direct_native_rejects_invalid_lsb_before_execution(bad):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    with pytest.raises(ValueError, match="NATIVE_IMU_QUANTIZATION_INVALID"):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )


def test_dt2_basilisk_imu_raw_gyro_and_accel_are_quantized_before_fusion():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    values = {
        "initial_pointing_error_deg": 14.,
        "imu_gyro_bias_rad_s_x": 0.0121,
        "imu_gyro_bias_rad_s_y": -0.0093,
        "imu_gyro_bias_rad_s_z": 0.0151,
        "imu_accel_bias_m_s2_x": 0.131,
        "imu_accel_bias_m_s2_y": -0.117,
        "imu_accel_bias_m_s2_z": 0.079,
    }
    adapter = get_adapter_for_capability(WHOLE_ID)

    def run(extra):
        result = adapter.run(_short_spec(
            WHOLE_ID, duration_s=16., values={**values, **extra},
        ))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        assert "imu" in result.metadata["model_source_boundary"]["basilisk_official_native"]
        assert len(result.trace_rows) >= 5
        return result.trace_rows[1:]  # native bsk 2.11 first IMU tick is initialization only

    baseline = run({})
    unchanged = run({GYRO: 0., ACCEL: 0.})
    quantized = run({GYRO: 0.0043, ACCEL: 0.02})

    def measured(rows, kind, unit):
        return np.asarray([
            [float(row[f"adcs.sensor.raw_{kind}_{unit}_{axis}"]) for axis in "xyz"]
            for row in rows
        ], dtype=float)

    assert [x["time_s"] for x in baseline] == [x["time_s"] for x in quantized]
    for kind, unit, lsb in (("gyro", "rad_s", 0.0043), ("accel", "m_s2", 0.02)):
        original = measured(baseline, kind, unit)
        zero = measured(unchanged, kind, unit)
        quant = measured(quantized, kind, unit)
        assert np.all(np.isfinite(quant))
        assert np.array_equal(original, zero), "Explicit zero must not alter legacy native results"
        # Official Discretize uses TO_ZERO (no error carry): each sensor output
        # should lie exactly on the selected LSB lattice, not a Python rewrite.
        lattice_error = np.max(np.abs(quant / lsb - np.round(quant / lsb)))
        response_delta = np.max(np.abs(quant - original))
        assert lattice_error < 1e-8, (kind, lattice_error)
        # The sensor feeds a CLOSED LOOP. Once quantization alters FSW,
        # the reference and quantized spacecraft trajectories can diverge;
        # therefore their cross-run raw difference is NOT bounded by 1 LSB.
        # The per-sample raw LSB lattice test above is the valid native
        # quantization proof; this A/B only proves a nonzero physical change.
        assert response_delta > 1e-6, (kind, response_delta)
        print(
            "DT2_NATIVE_IMU_LSB_MEASURED "
            f"channel={kind} samples={len(quant)} lsb={lsb:.8f} "
            f"lattice_max_error={lattice_error:.9e} "
            f"ab_max_delta={response_delta:.9e} "
            "native_basilisk=true default_unchanged=true",
            flush=True,
        )


def test_dt2_basilisk_quantization_plus_white_noise_replays_same_seed():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    values = {
        GYRO: 0.0043, ACCEL: 0.02,
        "imu_gyro_white_noise_std_rad_s": 0.003,
        "imu_accel_white_noise_std_m_s2": 0.015,
        "simulation_seed": 331,
    }
    adapter = get_adapter_for_capability(WHOLE_ID)
    def run():
        return adapter.run(_short_spec(WHOLE_ID, duration_s=16., values=values)).trace_rows

    first, second = run(), run()
    assert [r["time_s"] for r in first] == [r["time_s"] for r in second]
    for unit, kind in (("rad_s", "gyro"), ("m_s2", "accel")):
        for axis in "xyz":
            key = f"adcs.sensor.raw_{kind}_{unit}_{axis}"
            assert np.asarray([r[key] for r in first]).tolist() == pytest.approx(
                [r[key] for r in second], abs=1e-12, rel=1e-12
            )
    print("DT2_NATIVE_IMU_LSB_SEEDED_REPLAY seed=331 repeat=2 status=PASS", flush=True)
