from __future__ import annotations

import pytest


def test_native_star_tracker_probe_uses_official_sensor_and_records_truth_response():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.native_sensor_component import run_native_star_tracker_probe

    result = run_native_star_tracker_probe(
        duration_s=1.0,
        sample_s=0.1,
        truth_sigma_bn=(0.04, -0.01, 0.02),
        truth_omega_bn_b_rad_s=(0.01, 0.0, -0.005),
        noise_std_rad=0.0,
        random_walk_std_rad_s=0.0,
    )

    assert result.model_source == "Basilisk.simulation.starTracker.StarTracker"
    assert result.input_source == "explicit_SCStatesMsg_truth_fixture"
    assert result.claim_scope.endswith("without_optical_star_catalog")
    assert len(result.times_s) == len(result.q_inrtl_to_case)
    assert len(result.times_s) >= 2
    assert result.times_s[0] >= 0.0
    assert result.times_s[-1] >= 1.0
    assert all(abs(sum(value * value for value in q) - 1.0) < 0.02 for q in result.q_inrtl_to_case)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"duration_s": 0.0}, "DURATION"),
        ({"sample_s": 0.0}, "SAMPLE"),
        ({"truth_sigma_bn": (0.0, 1.0)}, "TRUTH_SIGMA"),
        ({"truth_omega_bn_rad_s": (0.0, float("nan"), 0.0)}, "TRUTH_OMEGA"),
        ({"seed": -1}, "SEED"),
        ({"noise_std_rad": float("inf")}, "NOISE"),
    ],
)
def test_native_star_tracker_probe_rejects_invalid_inputs_before_basilisk_import(kwargs, reason):
    from sat_sim.bsk_engine.native_sensor_component import run_native_star_tracker_probe

    with pytest.raises(ValueError, match=reason):
        run_native_star_tracker_probe(**kwargs)


def test_native_imu_probe_records_official_gyro_against_prescribed_truth():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.native_sensor_component import run_native_imu_probe

    result = run_native_imu_probe(
        duration_s=1.0,
        sample_s=0.1,
        truth_sigma_bn=(0.0, 0.0, 0.0),
        truth_omega_bn_b_rad_s=(0.012, -0.004, 0.003),
    )
    assert result.model_source == "Basilisk.simulation.imuSensor.ImuSensor"
    assert result.input_source == "explicit_SCStatesMsg_truth_fixture"
    assert result.claim_scope.endswith("without_navigation_or_closed_loop")
    assert len(result.times_s) == len(result.angular_velocity_platform_rad_s) == len(result.acceleration_platform_m_s2)
    assert len(result.times_s) >= 2
    for measured in result.angular_velocity_platform_rad_s:
        assert measured == pytest.approx(result.truth_omega_bn_b_rad_s, abs=1e-6)
    for measured in result.acceleration_platform_m_s2:
        assert measured == pytest.approx(result.truth_specific_force_b_m_s2, abs=1e-6)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"duration_s": -1.0}, "DURATION"),
        ({"sample_s": 2.0}, "SAMPLE"),
        ({"truth_omega_bn_b_rad_s": (0.0, 0.0)}, "TRUTH_OMEGA"),
        ({"sensor_pos_b_m": (0.1, 0.0, 0.0)}, "NONZERO_SENSOR_OFFSET"),
    ],
)
def test_native_imu_probe_rejects_unsupported_inputs(kwargs, reason):
    from sat_sim.bsk_engine.native_sensor_component import run_native_imu_probe

    with pytest.raises(ValueError, match=reason):
        run_native_imu_probe(**kwargs)


def test_native_magnetometer_probe_records_official_sensor_frame_vector():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.native_sensor_component import run_native_magnetometer_probe

    result = run_native_magnetometer_probe(
        duration_s=1.0,
        sample_s=0.1,
        truth_magnetic_field_n_t=(2.0e-5, -1.0e-5, 3.0e-5),
        bias_tesla=(1.0e-7, 0.0, -1.0e-7),
        scale_factor=1.0,
    )
    assert result.model_source == "Basilisk.simulation.magnetometer.Magnetometer"
    assert result.input_source == "explicit_MagneticFieldMsg_and_SCStatesMsg_fixtures"
    assert len(result.times_s) == len(result.magnetic_field_sensor_t)
    assert len(result.times_s) >= 2
    expected = (2.01e-5, -1.0e-5, 2.99e-5)
    for measured in result.magnetic_field_sensor_t:
        assert measured == pytest.approx(expected, abs=1e-10)


def test_native_coarse_sun_sensor_probe_uses_official_sun_and_eclipse_inputs():
    pytest.importorskip("Basilisk")
    from sat_sim.bsk_engine.native_sensor_component import run_native_coarse_sun_sensor_probe

    illuminated = run_native_coarse_sun_sensor_probe(duration_s=1.0, sample_s=0.1, illumination_factor=1.0)
    eclipsed = run_native_coarse_sun_sensor_probe(duration_s=1.0, sample_s=0.1, illumination_factor=0.0)
    assert illuminated.model_source == "Basilisk.simulation.coarseSunSensor.CoarseSunSensor"
    assert illuminated.input_source == "explicit_SpicePlanetStateMsg_EclipseMsg_and_SCStatesMsg_fixtures"
    assert len(illuminated.times_s) == len(illuminated.output_intensity)
    assert max(illuminated.output_intensity) > 0.9
    assert max(abs(value) for value in eclipsed.output_intensity) < 1e-9


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"truth_magnetic_field_n_t": (1.0e-5, 2.0e-5)}, "FIELD"),
        ({"scale_factor": 0.0}, "SCALE"),
        ({"illumination_factor": 1.1}, "ILLUMINATION"),
        ({"n_hat_b": (2.0, 0.0, 0.0)}, "NOT_UNIT"),
        ({"fov_rad": 0.0}, "FOV"),
    ],
)
def test_native_environment_sensor_probes_reject_invalid_inputs(kwargs, reason):
    from sat_sim.bsk_engine.native_sensor_component import (
        run_native_coarse_sun_sensor_probe,
        run_native_magnetometer_probe,
    )

    function = run_native_magnetometer_probe if "truth_magnetic_field_n_t" in kwargs or "scale_factor" in kwargs else run_native_coarse_sun_sensor_probe
    with pytest.raises(ValueError, match=reason):
        function(**kwargs)
