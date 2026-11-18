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
    from Basilisk.utilities import RigidBodyKinematics as rbk
    for quaternion in result.q_inrtl_to_case:
        assert tuple(float(value) for value in rbk.EP2MRP(quaternion)) == pytest.approx(result.truth_sigma_bn, abs=1e-6)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"duration_s": 0.0}, "DURATION"),
        ({"sample_s": 0.0}, "SAMPLE"),
        ({"truth_sigma_bn": (0.0, 1.0)}, "TRUTH_SIGMA"),
        ({"truth_omega_bn_b_rad_s": (0.0, float("nan"), 0.0)}, "TRUTH_OMEGA"),
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


def test_imu_component_adapter_routes_explicit_native_backend_into_run_result():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_source_native_misc import ImuAdapter

    spec = {
        "task_id": "p9n2-native-imu-adapter",
        "capability_id": "component.imu.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "imu", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {
            "physics_backend": "basilisk_native",
            "true_gyro_rad_s": [0.012, -0.004, 0.003],
            "true_accel_m_s2": [0.2, -0.1, 0.05],
            "gyro_scale": [1.0, 1.0, 1.0],
            "accel_scale": [1.0, 1.0, 1.0],
            "gyro_bias_rad_s": [0.0, 0.0, 0.0],
            "accel_bias_m_s2": [0.0, 0.0, 0.0],
        },
    }
    result = ImuAdapter().run(spec)
    assert result.metadata["physics_backend"] == "basilisk_native"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.imuSensor.ImuSensor"
    assert result.summary["capability_id"] == "component.imu.v1"
    assert result.trace_rows
    assert result.trace_rows[-1]["adcs.imu.gyro_x_rad_s"] == pytest.approx(0.012, abs=1e-6)
    assert result.trace_rows[-1]["adcs.imu.accel_x_m_s2"] == pytest.approx(0.2, abs=1e-6)


def test_imu_component_adapter_fails_closed_for_native_fault_mode():
    from sat_sim.adapters.component_source_native_misc import ImuAdapter

    spec = {
        "capability_id": "component.imu.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "imu", "mode": "fault"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {"physics_backend": "basilisk_native"},
        "faults": [{"fault_type": "bias_step", "start_s": 0.0}],
    }
    with pytest.raises(ValueError, match="ONLY_SUPPORTS_NOMINAL"):
        ImuAdapter().run(spec)


def test_magnetometer_component_adapter_routes_explicit_native_backend_into_run_result():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_source_native_misc import MagnetometerAdapter

    spec = {
        "task_id": "p9n2-native-magnetometer-adapter",
        "capability_id": "component.magnetometer.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "magnetometer", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {
            "physics_backend": "basilisk_native",
            "magnetic_field_t": [2.0e-5, -1.0e-5, 3.0e-5],
            "axis_scale": [1.0, 1.0, 1.0],
            "bias_t": [1.0e-7, 0.0, -1.0e-7],
        },
    }
    result = MagnetometerAdapter().run(spec)
    assert result.metadata["physics_backend"] == "basilisk_native"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.magnetometer.Magnetometer"
    assert result.trace_rows
    assert result.trace_rows[-1]["adcs.magnetometer.field_x_t"] == pytest.approx(2.01e-5, abs=1e-10)


def test_magnetometer_component_adapter_rejects_nonuniform_scale_for_native_backend():
    from sat_sim.adapters.component_source_native_misc import MagnetometerAdapter

    spec = {
        "capability_id": "component.magnetometer.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "magnetometer", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {"physics_backend": "basilisk_native", "axis_scale": [1.0, 0.9, 1.0]},
    }
    with pytest.raises(ValueError, match="UNIFORM_AXIS_SCALE"):
        MagnetometerAdapter().run(spec)


def test_star_tracker_component_adapter_routes_native_quaternion_into_mrp_run_trace():
    pytest.importorskip("Basilisk")
    from sat_sim.adapters.component_source_native_misc import StarTrackerAdapter

    spec = {
        "task_id": "p9n2-native-star-tracker-adapter",
        "capability_id": "component.star_tracker.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "star_tracker", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {
            "physics_backend": "basilisk_native",
            "true_sigma_bn": [0.04, -0.01, 0.02],
            "drift_rate_mrp_s": [0.0, 0.0, 0.0],
        },
    }
    result = StarTrackerAdapter().run(spec)
    assert result.metadata["physics_backend"] == "basilisk_native"
    assert result.metadata["native_provenance"] == "Basilisk.simulation.starTracker.StarTracker"
    assert "not_optical_star_visibility" in result.metadata["validity_semantics"]
    assert result.trace_rows
    assert result.trace_rows[-1]["adcs.star_tracker.sigma_x"] == pytest.approx(0.04, abs=1e-6)


def test_star_tracker_component_adapter_fails_closed_for_native_drift_modifier():
    from sat_sim.adapters.component_source_native_misc import StarTrackerAdapter

    spec = {
        "capability_id": "component.star_tracker.v1",
        "task_type": "component",
        "target": {"level": "component", "name": "star_tracker", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.1},
        "parameters": {"physics_backend": "basilisk_native", "drift_rate_mrp_s": [0.01, 0.0, 0.0]},
    }
    with pytest.raises(ValueError, match="DRIFT_RATE_NOT_SUPPORTED"):
        StarTrackerAdapter().run(spec)
