"""DT-2: seeded IMU bias random walk injected before official Basilisk measurements.

The random walk is a project-owned SysModel scheduled before the official
ImuSensor.UpdateState. Its output therefore enters the sensor's native bias,
delta-angle/delta-velocity, quantization and saturation stages. It is not an
OEM stochastic characterization or a Python post-processing perturbation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)

GYRO = "imu_gyro_bias_random_walk_std_rad_s_sqrt_s"
ACCEL = "imu_accel_bias_random_walk_std_m_s2_sqrt_s"


def test_imu_bias_random_walk_parameters_are_registered_and_strict():
    fields = {field["name"]: field for group in initial_configuration_catalog()["groups"]
              if group["group"] == "adcs" for field in group["fields"]}
    assert fields[GYRO]["default"] == fields[ACCEL]["default"] == 0.
    assert fields[GYRO]["unit"] == "rad/s/√s"
    assert fields[ACCEL]["unit"] == "m/s²/√s"
    assert ContinuousInitialConfiguration().native_values() == {}
    cfg = ContinuousInitialConfiguration.model_validate({
        "adcs": {GYRO: 2e-4, ACCEL: 2e-3, "simulation_seed": 811}
    })
    assert cfg.native_values()[GYRO] == pytest.approx(2e-4)
    assert cfg.native_values()[ACCEL] == pytest.approx(2e-3)
    assert cfg.native_values()["simulation_seed"] == 811
    html = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    for key in (GYRO, ACCEL):
        assert f'data-native-key="{key}"' in html
    assert "官方 ImuSensor 更新之前" in html


@pytest.mark.parametrize("bad", [
    {GYRO: -1e-5}, {ACCEL: -1e-4},
    {GYRO: .0101}, {ACCEL: .1001},
    {GYRO: float("nan")}, {ACCEL: float("inf")},
    {GYRO: "0.0002"}, {ACCEL: True}, {GYRO: None},
])
def test_prestart_rejects_invalid_bias_random_walk_coefficients(bad):
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate({"adcs": bad})


def _run_official_imu_bias_walk(seed: int, *, duration_s: float = 200.0,
                                step_s: float = .2):
    pytest.importorskip("Basilisk")
    from Basilisk.architecture import messaging
    from Basilisk.simulation import imuSensor
    from Basilisk.utilities import SimulationBaseClass, macros
    from sat_sim.bsk_engine.project_native_modules import ImuBiasRandomWalk

    gyro_q = 2e-4
    accel_q = 2e-3
    truth = messaging.SCStatesMsgPayload()
    truth.sigma_BN = [0., 0., 0.]
    truth.omega_BN_B = [0., 0., 0.]
    truth.r_BN_N = [7_000_000., 0., 0.]
    truth.v_BN_N = [0., 7_500., 0.]
    truth.omegaDot_BN_B = [0., 0., 0.]
    truth.nonConservativeAccelpntB_B = [0., 0., 0.]
    truth_msg = messaging.SCStatesMsg().write(truth)

    sensor = imuSensor.ImuSensor()
    sensor.ModelTag = "dt2_official_imu_bias_walk"
    sensor.scStateInMsg.subscribeTo(truth_msg)
    sensor.PMatrixGyro = np.zeros((3, 3)).tolist()
    sensor.PMatrixAccel = np.zeros((3, 3)).tolist()
    sensor.RNGSeed = seed

    walk = ImuBiasRandomWalk(
        sensor,
        gyro_bias_rad_s=(0., 0., 0.),
        accel_bias_m_s2=(0., 0., 0.),
        gyro_random_walk_std_rad_s_sqrt_s=gyro_q,
        accel_random_walk_std_m_s2_sqrt_s=accel_q,
        seed=seed,
    )
    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("DT2ImuBiasWalkProcess")
    process.addTask(sim.CreateNewTask("DT2ImuBiasWalkTask", macros.sec2nano(step_s)))
    # Higher priority executes first: update official ImuSensor bias before its
    # native measurement, integration, quantization and saturation stages.
    sim.AddModelToTask("DT2ImuBiasWalkTask", walk, None, 91)
    sim.AddModelToTask("DT2ImuBiasWalkTask", sensor, None, 90)
    raw_recorder = sensor.sensorOutMsg.recorder(macros.sec2nano(step_s))
    bias_recorder = walk.biasWalkOutMsg.recorder(macros.sec2nano(step_s))
    sim.AddModelToTask("DT2ImuBiasWalkTask", raw_recorder, None, 5)
    sim.AddModelToTask("DT2ImuBiasWalkTask", bias_recorder, None, 4)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration_s))
    sim.ExecuteSimulation()

    times = np.asarray(raw_recorder.times(), dtype=np.float64) * 1e-9
    raw_gyro = np.asarray(raw_recorder.AngVelPlatform, dtype=float)
    raw_accel = np.asarray(raw_recorder.AccelPlatform, dtype=float)
    walk_gyro = np.asarray(bias_recorder.AngVelPlatform, dtype=float)
    walk_accel = np.asarray(bias_recorder.AccelPlatform, dtype=float)
    assert len(times) > 500
    # Basilisk 2.11 deliberately emits one zero startup payload.
    return times[1:], raw_gyro[1:], raw_accel[1:], walk_gyro[1:], walk_accel[1:]


def test_real_basilisk_imu_bias_walk_has_sqrt_dt_statistics_independent_streams_and_seed_replay():
    first = _run_official_imu_bias_walk(811)
    repeated = _run_official_imu_bias_walk(811)
    other_seed = _run_official_imu_bias_walk(812)
    times, raw_gyro, raw_accel, walk_gyro, walk_accel = first
    assert times == pytest.approx(repeated[0], abs=1e-12)
    for left, right in zip(first[1:], repeated[1:], strict=True):
        assert left == pytest.approx(right, rel=1e-12, abs=1e-12)

    # Constant zero truth means the official sensor's raw output is the
    # scheduled random-walk bias itself, proving the pre-update connection.
    assert raw_gyro == pytest.approx(walk_gyro, rel=1e-10, abs=1e-10)
    assert raw_accel == pytest.approx(walk_accel, rel=1e-10, abs=1e-10)

    dt_s = float(times[1] - times[0])
    gyro_increments = np.diff(walk_gyro[:, 0])
    accel_increments = np.diff(walk_accel[:, 0])
    gyro_expected = 2e-4 * np.sqrt(dt_s)
    accel_expected = 2e-3 * np.sqrt(dt_s)
    gyro_measured = float(np.std(gyro_increments, ddof=1))
    accel_measured = float(np.std(accel_increments, ddof=1))
    assert gyro_measured == pytest.approx(gyro_expected, rel=.08)
    assert accel_measured == pytest.approx(accel_expected, rel=.08)
    cross_channel_corr = float(np.corrcoef(gyro_increments, accel_increments)[0, 1])
    assert abs(cross_channel_corr) < .08

    changed_seed_delta = max(
        float(np.max(np.abs(left - right)))
        for left, right in zip(first[3:], other_seed[3:], strict=True)
    )
    assert changed_seed_delta > 1e-5
    print(
        "DT2_NATIVE_IMU_BIAS_RANDOM_WALK_MEASURED "
        f"gyro_sigma_rad_s_sqrt_s=2e-4 accel_sigma_m_s2_sqrt_s=2e-3 "
        f"dt_s={dt_s:.9e} gyro_increment_std_rad_s={gyro_measured:.9e} "
        f"accel_increment_std_m_s2={accel_measured:.9e} "
        f"cross_channel_increment_corr={cross_channel_corr:.9e} "
        "official_imu_raw_message=true same_seed_exact=true distinct_seed=true",
        flush=True,
    )


def test_whole_spacecraft_bias_walk_is_seeded_recorded_and_precedes_native_quantization():
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec

    adapter = get_adapter_for_capability(WHOLE_ID)
    values = {
        "initial_pointing_error_deg": 14.,
        GYRO: 2e-4,
        ACCEL: 2e-3,
        "imu_gyro_quantization_lsb_rad_s": 1e-5,
        "simulation_seed": 811,
    }

    def run(params):
        result = adapter.run(_short_spec(
            WHOLE_ID, duration_s=20., values=params))
        assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
        return result

    baseline = run({"initial_pointing_error_deg": 14., "simulation_seed": 811})
    first = run(values)
    repeated = run(values)
    other_seed = run({**values, "simulation_seed": 812})
    assert "imu_bias_random_walk" not in baseline.summary
    evidence = first.summary["imu_bias_random_walk"]
    assert evidence["source"] == "project_native_modules.ImuBiasRandomWalk"
    assert evidence["target"].startswith("official Basilisk ImuSensor.senRotBias")
    assert evidence["gyro_increment_std_rad_s_sqrt_s"] == pytest.approx(2e-4)
    assert evidence["accel_increment_std_m_s2_sqrt_s"] == pytest.approx(2e-3)
    assert evidence["hardware_calibration"] is False

    for result in (first, repeated, other_seed):
        assert len(result.trace_rows) >= 10
        for row in result.trace_rows:
            assert all(f"adcs.sensor.gyro_bias_walk_rad_s_{axis}" in row for axis in "xyz")
            assert all(f"adcs.sensor.accel_bias_walk_m_s2_{axis}" in row for axis in "xyz")
    gyro_walk = [
        [row[f"adcs.sensor.gyro_bias_walk_rad_s_{axis}"] for axis in "xyz"]
        for row in first.trace_rows
    ]
    gyro_repeat = [
        [row[f"adcs.sensor.gyro_bias_walk_rad_s_{axis}"] for axis in "xyz"]
        for row in repeated.trace_rows
    ]
    assert np.allclose(np.asarray(gyro_walk), np.asarray(gyro_repeat), rtol=1e-12, atol=1e-12)
    alternate = [
        [row[f"adcs.sensor.gyro_bias_walk_rad_s_{axis}"] for axis in "xyz"]
        for row in other_seed.trace_rows
    ]
    assert np.max(np.abs(np.asarray(gyro_walk) - np.asarray(alternate))) > 1e-6

    # Bias random walk is applied to ImuSensor before setLSBs discretizes the
    # native measurement; all recorded raw gyro samples remain on the LSB grid.
    lsb = 1e-5
    for row in first.trace_rows[1:]:
        for axis in "xyz":
            value = float(row[f"adcs.sensor.raw_gyro_rad_s_{axis}"])
            assert abs(value / lsb - round(value / lsb)) < 1e-7
    baseline_rows = baseline.trace_rows
    assert [row["time_s"] for row in baseline_rows] == [
        row["time_s"] for row in first.trace_rows]
    assert first.summary["project_native_module_count"] == (
        baseline.summary["project_native_module_count"] + 1
    )


@pytest.mark.parametrize("bad", [
    {GYRO: float("inf")}, {ACCEL: float("nan")},
    {GYRO: -.001}, {ACCEL: .101}, {GYRO: True},
    {ACCEL: "0.002"}, {GYRO: None},
])
def test_direct_whole_spacecraft_runtime_rejects_invalid_bias_walk_values(bad):
    pytest.importorskip("Basilisk")
    from sat_sim.capability_registry import get_adapter_for_capability
    from test_unified_native_migration import WHOLE_ID, _short_spec
    with pytest.raises(ValueError, match="NATIVE_IMU_BIAS_RANDOM_WALK_INVALID"):
        get_adapter_for_capability(WHOLE_ID).run(
            _short_spec(WHOLE_ID, duration_s=2., values=bad)
        )
