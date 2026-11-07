import pytest

from sat_sim.experiments.r84r3b_wheel_speed_measurement import (
    WheelSpeedMeasurementAssumptions,
    observe_wheel_speeds,
)


def assumptions(**changes):
    values = dict(sample_every_n_native_points=2, resolution_rad_s=0.5,
                  noise_sigma_rad_s=0.0, bias_rad_s=0.25,
                  dropout_probability=0.0, timestamp_latency_s=0.1,
                  timestamp_jitter_sigma_s=0.0)
    values.update(changes)
    return WheelSpeedMeasurementAssumptions(**values)


def rows():
    return [dict(time_s=i * 0.2, **{f"sensor.adcs.rw.speed_rad_s_{j}": float(i + j) for j in range(3)}) for i in range(5)]


def test_decimation_quantization_and_capture_receipt_clocks():
    observed = observe_wheel_speeds(rows(), assumptions(), seed=5)
    assert [row["source_time_s"] for row in observed] == [0.0, 0.4, 0.8]
    assert [row["sensor.adcs.rw.speed_rad_s_0"] for row in observed] == [0.0, 2.0, 4.0]
    assert observed[1]["wheel_0_receipt_time_s"] == pytest.approx(0.5)
    assert all("QA_ONLY" not in key and "fault" not in key for row in observed for key in row)


def test_dropout_is_explicit_and_repeatable():
    config = assumptions(dropout_probability=1.0)
    a = observe_wheel_speeds(rows(), config, seed=19)
    assert a == observe_wheel_speeds(rows(), config, seed=19)
    assert all(row[f"wheel_{i}_valid"] is False and row[f"sensor.adcs.rw.speed_rad_s_{i}"] is None
               for row in a for i in range(3))


def test_rejects_invalid_assumptions_and_nonmonotonic_native_time():
    with pytest.raises(ValueError):
        assumptions(resolution_rad_s=-1)
    with pytest.raises(ValueError):
        assumptions(dropout_probability=1.1)
    bad = rows()
    bad[2]["time_s"] = bad[1]["time_s"]
    with pytest.raises(ValueError):
        observe_wheel_speeds(bad, assumptions(), seed=1)


def test_clock_jitter_never_puts_receipt_before_capture():
    observed = observe_wheel_speeds(rows(), assumptions(timestamp_jitter_sigma_s=1.0), seed=17)
    assert all(row[f"wheel_{i}_receipt_time_s"] >= row["source_time_s"]
               for row in observed for i in range(3))
