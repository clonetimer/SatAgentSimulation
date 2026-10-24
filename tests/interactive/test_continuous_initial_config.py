"""Pre-run config is bounded and must reach the *actual* Basilisk graph.

Browser/control-plane tests use a protocol double; genuine physical response
is validated by the native-only test without substituting a Python orbit/EPS.
"""
from __future__ import annotations

import time
import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from sat_sim.interactive.continuous_initial_config import (
    ContinuousInitialConfiguration, initial_configuration_catalog,
)
from test_continuous_console import _client, _body, _ControlProtocolStub


def test_native_initial_catalog_has_exactly_supported_device_groups():
    catalog = initial_configuration_catalog()
    assert catalog["schema_version"] == "sat-native-initial-configuration.v1"
    assert catalog["graph_scope"] == "INTEGRATED_WHOLE_NATIVE_ONLY"
    assert catalog["standalone_subsystem_continuous_simulation_supported"] is False
    assert [row["group"] for row in catalog["groups"]] == [
        "orbit", "adcs", "eps", "payload", "comm_data", "propulsion", "thermal",
    ]
    for row in catalog["groups"]:
        assert row["fields"]
        assert all("native_value_key" in x and "default" in x and "unit" in x
                   for x in row["fields"])
    assert ContinuousInitialConfiguration().native_values() == {}
    propulsion_fields = {f["name"]: f for f in catalog["groups"][5]["fields"]}
    assert propulsion_fields["propulsion_min_pulse_s"]["default"] == .05
    assert propulsion_fields["propulsion_thruster_0_location_b_y_m"]["default"] == .5
    assert propulsion_fields["propulsion_thruster_1_location_b_y_m"]["default"] == -.5
    orbit_fields = {f["name"]: f for f in catalog["groups"][0]["fields"]}
    adcs_fields = {f["name"]: f for f in catalog["groups"][1]["fields"]}
    assert orbit_fields["spacecraft_mass_kg"]["unit"] == "kg"
    assert adcs_fields["sun_sensor_fov_deg"]["unit"] == "deg"
    assert orbit_fields["spacecraft_mass_kg"]["default"] == 100.
    assert adcs_fields["sun_sensor_fov_deg"]["default"] == 80.
    assert adcs_fields["rw_friction_mode"]["default"] == "disabled"
    for key in ("imu_gyro_bias_rad_s_x", "imu_gyro_bias_rad_s_y",
                "imu_gyro_bias_rad_s_z"):
        assert adcs_fields[key]["unit"] == "rad/s"
        assert adcs_fields[key]["default"] == 0.
    for key in ("imu_accel_bias_m_s2_x", "imu_accel_bias_m_s2_y",
                "imu_accel_bias_m_s2_z"):
        assert adcs_fields[key]["unit"] == "m/s²"
        assert adcs_fields[key]["default"] == 0.
    assert adcs_fields["imu_gyro_max_rad_s"]["unit"] == "rad/s"
    assert adcs_fields["imu_accel_max_m_s2"]["unit"] == "m/s²"
    assert adcs_fields["star_tracker_noise_std_arcsec"]["unit"] == "arcsec"
    assert adcs_fields["rw_mount_radius_m"]["unit"] == "m"
    assert adcs_fields["rw_coulomb_friction_nm"]["unit"] == "N·m"
    assert adcs_fields["rw_viscous_friction_nms"]["unit"] == "N·m·s/rad"
    for field in ("imu_mount_yaw_deg", "imu_mount_pitch_deg", "imu_mount_roll_deg",
                  "star_tracker_mount_yaw_deg", "star_tracker_mount_pitch_deg",
                  "star_tracker_mount_roll_deg"):
        assert adcs_fields[field]["unit"] == "deg"
        assert adcs_fields[field]["default"] == 0.
    for field in ("imu_position_b_x_m", "imu_position_b_y_m", "imu_position_b_z_m"):
        assert adcs_fields[field]["unit"] == "m"
        assert adcs_fields[field]["default"] == 0.
    from pydantic import ValidationError
    wheel4 = ContinuousInitialConfiguration.model_validate({
        "adcs": {"rw_configuration": "pyramid_4", "rw_initial_speed_rad_s_3": -50.},
    })
    assert wheel4.native_values()["rw_initial_speed_rad_s_3"] == -50.
    with pytest.raises(ValidationError, match="RW4_INITIAL_SPEED_REQUIRES_PYRAMID_4"):
        ContinuousInitialConfiguration.model_validate({
            "adcs": {"rw_configuration": "orthogonal_3", "rw_initial_speed_rad_s_3": 1.},
        })
    assert ContinuousInitialConfiguration.model_validate({
        "eps": {"battery_capacity_wh": 180., "initial_soc": .7},
    }).native_values() == {"battery_capacity_wh": 180., "initial_soc": .7}



def test_continuous_web_form_matches_registered_native_catalog():
    """Regress #48: previously API-available mass/FOV were missing in HTML."""
    root = Path(__file__).resolve().parents[2]
    html = (root / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    js = (root / "src/sat_sim/web/continuous.js").read_text(encoding="utf-8")
    offered = re.findall(
        r'<(?:input|select)\b[^>]*data-native-group="([^"]+)"[^>]*data-native-key="([^"]+)"',
        html,
    )
    expected = [(group["group"], item["name"])
                for group in initial_configuration_catalog()["groups"] for item in group["fields"]]
    assert len(offered) == len(set(offered)), "duplicate native controls"
    assert set(offered) == set(expected), (
        f"missing={sorted(set(expected) - set(offered))}; extra={sorted(set(offered) - set(expected))}"
    )
    assert "updateRwFrictionNativeInitialLayout" in js
    assert "updateRwNativeInitialLayout" in js
    assert "field.type === 'checkbox' ? field.checked" in js
    assert 'data-native-key="propulsion_enabled" type="checkbox"' in html
    assert "非官方 Basilisk 原生热模块" in html


@pytest.mark.parametrize("bad", [
    {"orbit": {"orbit_radius_m": 120}},
    {"orbit": {"spacecraft_mass_kg": 0}},
    {"orbit": {"spacecraft_mass_kg": 3000}},
    {"adcs": {"sun_sensor_fov_deg": 0}},
    {"adcs": {"sun_sensor_fov_deg": 185}},
    {"adcs": {"rw_friction_mode": "unlimited"}},
    {"adcs": {"imu_mount_yaw_deg": 180.1}},
    {"adcs": {"imu_mount_pitch_deg": -90}},
    {"adcs": {"imu_mount_roll_deg": float("inf")}},
    {"adcs": {"imu_position_b_x_m": 2.01}},
    {"adcs": {"imu_position_b_y_m": -2.01}},
    {"adcs": {"star_tracker_mount_pitch_deg": 90}},
    {"adcs": {"star_tracker_mount_roll_deg": float("nan")}},
    {"adcs": {"imu_gyro_bias_rad_s_x": 0.10001}},
    {"adcs": {"imu_gyro_bias_rad_s_y": -0.10001}},
    {"adcs": {"imu_accel_bias_m_s2_z": 1.01}},
    {"adcs": {"imu_accel_bias_m_s2_x": float("nan")}},
    {"adcs": {"imu_gyro_max_rad_s": -1}},
    {"adcs": {"imu_gyro_max_rad_s": 1001}},
    {"adcs": {"imu_accel_max_m_s2": float("inf")}},
    {"adcs": {"star_tracker_noise_std_arcsec": -1}},
    {"adcs": {"star_tracker_noise_std_arcsec": 3601}},
    {"adcs": {"rw_coulomb_friction_nm": -1e-5}},
    {"adcs": {"rw_viscous_friction_nms": 0.02}},
    {"adcs": {"rw_mount_radius_m": -0.01}},
    {"adcs": {"rw_mount_radius_m": 0.4}},
    {"adcs": {"rw_friction_mode": "disabled", "rw_coulomb_friction_nm": 1e-5}},
    {"adcs": {"rw_friction_mode": "coulomb_viscous"}},
    {"adcs": {"rw_friction_mode": "coulomb_viscous", "rw_coulomb_friction_nm": 0,
              "rw_viscous_friction_nms": 0}},
    {"adcs": {"rw_configuration": "arbitrary_python"}},
    {"eps": {"initial_soc": 1.5}},
    {"eps": {"solar_panel_area_m2": -1}},
    {"comm_data": {"storage_capacity_bits": 0}},
    {"payload": {"unknown_basilisk_function": "x"}},
    {"propulsion": {"propulsion_thrust_n": 0}},
    {"propulsion": {"propulsion_min_pulse_s": 0}},
    {"propulsion": {"propulsion_min_pulse_s": 10.1}},
    {"propulsion": {"propulsion_thruster_0_direction_b_x": 0}},
    {"propulsion": {"propulsion_thruster_1_direction_b_z": 0.1}},
    {"propulsion": {"propulsion_thruster_0_direction_b_y": float("nan")}},
    {"propulsion": {"propulsion_thruster_1_location_b_x_m": 2.01}},
    {"propulsion": {"propulsion_isp_s": -1}},
    {"propulsion": {"propulsion_burn_start_s": -1}},
    {"propulsion": {"propulsion_burn_on_time_s": 0}},
    {"propulsion": {"propulsion_min_burn_soc": 1.01}},
    {"propulsion": {"propulsion_initial_propellant_kg": 4, "propulsion_tank_capacity_kg": 2}},
    {"propulsion": {"propulsion_enabled": "exec(evil)"}},
    {"thermal": {"initial_payload_temp_k": 350}},
    {"thermal": {"initial_propulsion_temp_k": float("nan")}},
    {"thermal": {"comm_structure_conductance_w_k": -0.1}},
    {"thermal": {"propulsion_structure_conductance_w_k": 5.1}},
    {"unknown_subsystem": {"actor": "eval(...)"}},
    {"adcs": {"controller_k": "nan"}},
    {"adcs": {"rw_initial_speed_rad_s_0": 600}},
    {"adcs": {"rw_initial_speed_rad_s_3": 400}},
    {"adcs": {"rw_initial_speed_rad_s_0": "-inf"}},
    {"adcs": {"rw_max_torque_nm": 0}},
    {"adcs": {"rw_max_torque_nm": 0.3}},
])
def test_initial_config_rejects_unsafe_or_invalid_options(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ContinuousInitialConfiguration.model_validate(bad)


def test_continuous_creation_writes_immutable_original_native_initial_values(tmp_path, monkeypatch):
    captured = []
    def fake_factory(spec):
        captured.append(spec)
        return _ControlProtocolStub(), None
    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime", fake_factory)
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        catalog = client.get("/interactive/continuous/configuration/catalog")
        assert catalog.status_code == 200
        assert catalog.json()["model_source"].endswith("UnifiedNativeRuntime")
        request = {
            **_body("configured-native"),
            "initial_configuration": {
                "orbit": {"orbit_radius_m": 7_200_000., "inclination_deg": 46.,
                          "spacecraft_mass_kg": 145.},
                "adcs": {"rw_configuration": "orthogonal_3", "controller_k": 7.,
                         "sun_sensor_fov_deg": 43.,
                         "rw_friction_mode": "coulomb_viscous",
                         "rw_coulomb_friction_nm": 2e-5,
                         "rw_viscous_friction_nms": 3e-7,
                         "rw_mount_radius_m": .12,
                         "imu_mount_yaw_deg": 18., "imu_mount_pitch_deg": -6.,
                         "imu_mount_roll_deg": 4.,
                         "imu_position_b_x_m": .1, "imu_position_b_y_m": -.2,
                         "imu_position_b_z_m": .3,
                         "star_tracker_mount_yaw_deg": -14.,
                         "star_tracker_mount_pitch_deg": 7.,
                         "star_tracker_mount_roll_deg": -3.,
                         "imu_gyro_bias_rad_s_x": 0.001,
                         "imu_accel_bias_m_s2_z": 0.06,
                         "imu_gyro_max_rad_s": 0.5,
                         "imu_accel_max_m_s2": 20.,
                         "star_tracker_noise_std_arcsec": 10.},
                "eps": {"battery_capacity_wh": 200., "initial_soc": .75,
                        "solar_panel_area_m2": 3.},
                "payload": {"payload_data_rate_bps": 1_500_000.},
                "comm_data": {"storage_capacity_bits": 7_000_000_000.},
                "propulsion": {
                    "propulsion_enabled": True,
                    "propulsion_thrust_n": 2.5,
                    "propulsion_isp_s": 240.,
                    "propulsion_burn_start_s": 15.,
                    "propulsion_burn_on_time_s": .4,
                    "propulsion_initial_propellant_kg": .8,
                    "propulsion_tank_capacity_kg": 1.6,
                    "propulsion_min_burn_soc": .25,
                    "propulsion_power_w": 30.,
                    "propulsion_min_pulse_s": .08,
                    "propulsion_thruster_0_direction_b_x": .6,
                    "propulsion_thruster_0_direction_b_y": .8,
                    "propulsion_thruster_0_location_b_y_m": .75,
                    "propulsion_thruster_1_location_b_y_m": -.65,
                },
                "thermal": {
                    "initial_payload_temp_k": 300.,
                    "initial_comm_temp_k": 303.,
                    "initial_propulsion_temp_k": 302.,
                    "comm_structure_conductance_w_k": .7,
                    "propulsion_structure_conductance_w_k": .6,
                },
            },
        }
        created = client.post("/interactive/continuous/sessions", json=request)
        assert created.status_code == 201, created.text
        values = captured[0].task_spec["parameters"]["values"]
        assert values["orbit_radius_m"] == 7_200_000.
        assert values["spacecraft_mass_kg"] == 145.
        assert values["sun_sensor_fov_deg"] == 43.
        assert values["rw_friction_mode"] == "coulomb_viscous"
        assert values["rw_coulomb_friction_nm"] == 2e-5
        assert values["rw_viscous_friction_nms"] == 3e-7
        assert values["rw_mount_radius_m"] == .12
        assert values["imu_mount_yaw_deg"] == 18.
        assert values["imu_mount_pitch_deg"] == -6.
        assert values["imu_mount_roll_deg"] == 4.
        assert values["imu_position_b_x_m"] == .1
        assert values["imu_position_b_y_m"] == -.2
        assert values["imu_position_b_z_m"] == .3
        assert values["star_tracker_mount_yaw_deg"] == -14.
        assert values["star_tracker_mount_pitch_deg"] == 7.
        assert values["star_tracker_mount_roll_deg"] == -3.
        assert values["imu_gyro_bias_rad_s_x"] == .001
        assert values["imu_accel_bias_m_s2_z"] == .06
        assert values["imu_gyro_max_rad_s"] == .5
        assert values["imu_accel_max_m_s2"] == 20.
        assert values["star_tracker_noise_std_arcsec"] == 10.
        assert values["inclination_deg"] == 46.
        assert values["battery_capacity_wh"] == 200.
        assert values["rw_configuration"] == "orthogonal_3"
        assert values["storage_capacity_bits"] == 7_000_000_000.
        assert values["propulsion_enabled"] is True
        assert values["propulsion_thrust_n"] == 2.5
        assert values["propulsion_min_pulse_s"] == .08
        assert values["propulsion_thruster_0_direction_b_x"] == .6
        assert values["propulsion_thruster_0_direction_b_y"] == .8
        assert values["propulsion_thruster_0_location_b_y_m"] == .75
        assert values["propulsion_thruster_1_location_b_y_m"] == -.65
        assert values["propulsion_initial_propellant_kg"] == .8
        assert values["propulsion_tank_capacity_kg"] == 1.6
        assert values["initial_payload_temp_k"] == 300.
        assert values["initial_comm_temp_k"] == 303.
        assert values["initial_propulsion_temp_k"] == 302.
        assert values["comm_structure_conductance_w_k"] == .7
        assert values["propulsion_structure_conductance_w_k"] == .6
        assert "initial_true_anomaly_rad" not in values
        original = client.get("/interactive/continuous/sessions/configured-native/configuration")
        assert original.status_code == 200
        assert original.json()["native_initial_values"] == values
        assert original.json()["editable_after_prepare"] is False
        assert original.json()["full_native_resume_supported"] is False
        assert client.post("/interactive/continuous/sessions", json={
            **_body("reject-nested-unknown"),
            "initial_configuration": {"eps": {"basilisk_python_source": "__import__"}},
        }).status_code == 422
        assert client.post("/interactive/continuous/sessions", json={
            **_body("reject-invalid-soc"),
            "initial_configuration": {"eps": {"initial_soc": -1.}},
        }).status_code == 422
        assert len(captured) == 1



def test_rw4_operator_telemetry_fields_follow_selected_native_topology(tmp_path, monkeypatch):
    """Do not claim an uninstalled fourth wheel, even at the REST boundary."""
    captured = []

    def factory(spec):
        captured.append(spec)
        return _ControlProtocolStub(), None

    monkeypatch.setattr("sat_sim.interactive.api.build_persistent_runtime", factory)
    app = _client(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for name, configuration in (
            ("telemetry-three", "orthogonal_3"),
            ("telemetry-four", "pyramid_4"),
        ):
            body = {
                **_body(name),
                "initial_configuration": {"adcs": {
                    "rw_configuration": configuration,
                    **({"rw_initial_speed_rad_s_3": -120.}
                       if configuration == "pyramid_4" else {}),
                }},
            }
            created = client.post("/interactive/continuous/sessions", json=body)
            assert created.status_code == 201, created.text
        three, four = captured
        three_fields = three.task_spec["outputs"]["telemetry_streams"][0]["fields"]
        four_fields = four.task_spec["outputs"]["telemetry_streams"][0]["fields"]
        assert "adcs.rw.speed_rad_s_3" not in three_fields
        assert "adcs.rw.effective_max_torque_nm_3" not in three_fields
        assert "adcs.rw.speed_rad_s_3" in four_fields
        assert "adcs.rw.effective_max_torque_nm_3" in four_fields
        assert len(four_fields) == len(three_fields) + 2
        assert four.task_spec["parameters"]["values"]["rw_initial_speed_rad_s_3"] == -120.



def test_native_basilisk_configured_parameters_affect_real_output(tmp_path, monkeypatch):
    """Native-only test: compare two separately created Basilisk spacecraft.

    No mocked physics here: source and telemetry come from official Basilisk.
    """
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import simIncludeRW
    from Basilisk.simulation import coarseSunSensor, spacecraft, imuSensor, starTracker
    from Basilisk.utilities import RigidBodyKinematics
    from subsystems.propulsion import builder as propulsion_builder
    from subsystems.thermal import builder as thermal_builder
    import numpy as np
    propulsion_assemblies = []
    thermal_configs = []
    real_propulsion_builder = propulsion_builder.build_propulsion_basilisk_assembly_graph
    real_thermal_builder = thermal_builder.build_thermal_network_assembly_graph

    def capture_propulsion_assembly(cfg, **kwargs):
        graph = real_propulsion_builder(cfg, **kwargs)
        propulsion_assemblies.append((cfg, graph))
        return graph

    def capture_thermal_graph(*args, **kwargs):
        thermal_configs.append(kwargs["thermal_network_cfg"])
        return real_thermal_builder(*args, **kwargs)

    # Observe the genuine Basilisk thrust/fuel assembly and scheduled project
    # thermal network, never replace either calculation with mocked physics.
    monkeypatch.setattr(propulsion_builder, "build_propulsion_basilisk_assembly_graph",
                        capture_propulsion_assembly)
    monkeypatch.setattr(thermal_builder, "build_thermal_network_assembly_graph", capture_thermal_graph)
    factory_invocations = []
    native_imus = []
    native_trackers = []
    real_imu_ctor = imuSensor.ImuSensor
    real_tracker_ctor = starTracker.StarTracker

    def capture_imu(*args, **kwargs):
        obj = real_imu_ctor(*args, **kwargs)
        native_imus.append(obj)
        return obj

    def capture_tracker(*args, **kwargs):
        obj = real_tracker_ctor(*args, **kwargs)
        native_trackers.append(obj)
        return obj

    monkeypatch.setattr(imuSensor, "ImuSensor", capture_imu)
    monkeypatch.setattr(starTracker, "StarTracker", capture_tracker)
    native_spacecraft = []
    native_sun_sensors = []
    real_spacecraft_ctor = spacecraft.Spacecraft
    real_sun_sensor_ctor = coarseSunSensor.CoarseSunSensor

    def capture_spacecraft(*args, **kwargs):
        obj = real_spacecraft_ctor(*args, **kwargs)
        native_spacecraft.append(obj)
        return obj

    def capture_sun_sensor(*args, **kwargs):
        obj = real_sun_sensor_ctor(*args, **kwargs)
        native_sun_sensors.append(obj)
        return obj

    # These wrappers return genuine official Basilisk SWIG instances, not
    # stand-ins. The graphs execute their real UpdateState/dynamics code.
    monkeypatch.setattr(spacecraft, "Spacecraft", capture_spacecraft)
    monkeypatch.setattr(coarseSunSensor, "CoarseSunSensor", capture_sun_sensor)
    native_factory_create = simIncludeRW.rwFactory.create

    def capture_native_factory(self, wheel_model, axis, **kwargs):
        # Transparently wrap the *official* Basilisk factory and then invoke
        # the unchanged original constructor. This proves u_max and Omega
        # reach the physical StateEffector object creation.
        factory_invocations.append({
            "wheel_model": wheel_model, "u_max": kwargs["u_max"],
            "Omega": kwargs["Omega"], "label": kwargs["label"],
            "position": kwargs["rWB_B"], "friction": kwargs["useRWfriction"],
            "fCoulomb": kwargs.get("fCoulomb", 0.),
            "cViscous": kwargs.get("cViscous", 0.),
        })
        return native_factory_create(self, wheel_model, axis, **kwargs)
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture_native_factory)
    app = _client(tmp_path, monkeypatch)
    samples = {}
    with TestClient(app) as client:
        for label, initial in (
            ("config-default", {}),
            ("config-custom", {
                "orbit": {"orbit_radius_m": 7_350_000., "eccentricity": .001,
                          "spacecraft_mass_kg": 145.},
                "eps": {"battery_capacity_wh": 240., "initial_soc": .85,
                        "solar_panel_area_m2": 3.},
                "propulsion": {
                    "propulsion_enabled": True, "propulsion_thrust_n": 2.5,
                    "propulsion_isp_s": 240., "propulsion_burn_start_s": 30.,
                    "propulsion_burn_on_time_s": .4,
                    "propulsion_initial_propellant_kg": .8,
                    "propulsion_tank_capacity_kg": 1.6,
                    "propulsion_min_burn_soc": .25,
                    "propulsion_power_w": 30.,
                    "propulsion_min_pulse_s": .08,
                    "propulsion_thruster_0_direction_b_x": .6,
                    "propulsion_thruster_0_direction_b_y": .8,
                    "propulsion_thruster_0_location_b_y_m": .75,
                    "propulsion_thruster_1_location_b_y_m": -.65,
                },
                "thermal": {
                    "initial_payload_temp_k": 301.,
                    "initial_comm_temp_k": 303.,
                    "initial_propulsion_temp_k": 305.,
                    "comm_structure_conductance_w_k": .7,
                    "propulsion_structure_conductance_w_k": .6,
                },
                "adcs": {
                    "controller_k": 5., "controller_p": 25.,
                    "sun_sensor_fov_deg": 43.,
                    "imu_mount_yaw_deg": 18.,
                    "imu_mount_pitch_deg": -6.,
                    "imu_mount_roll_deg": 4.,
                    "imu_position_b_x_m": .1,
                    "imu_position_b_y_m": -.2,
                    "imu_position_b_z_m": .3,
                    "star_tracker_mount_yaw_deg": -14.,
                    "star_tracker_mount_pitch_deg": 7.,
                    "star_tracker_mount_roll_deg": -3.,
                    "imu_gyro_bias_rad_s_x": 0.001,
                    "imu_gyro_bias_rad_s_y": -0.002,
                    "imu_gyro_bias_rad_s_z": 0.003,
                    "imu_accel_bias_m_s2_x": 0.04,
                    "imu_accel_bias_m_s2_y": -0.05,
                    "imu_accel_bias_m_s2_z": 0.06,
                    "imu_gyro_max_rad_s": 0.5,
                    "imu_accel_max_m_s2": 20.,
                    "star_tracker_noise_std_arcsec": 10.,
                    "rw_friction_mode": "coulomb_viscous",
                    "rw_coulomb_friction_nm": 2e-5,
                    "rw_viscous_friction_nms": 3e-7,
                    "rw_mount_radius_m": .12,
                    "rw_max_torque_nm": 0.05,
                    "rw_initial_speed_rad_s_0": -50.,
                    "rw_initial_speed_rad_s_1": 140.,
                    "rw_initial_speed_rad_s_2": -200.,
                },
            }),
        ):
            body = {**_body(label), "initial_configuration": initial}
            created = client.post("/interactive/continuous/sessions", json=body)
            assert created.status_code == 201, created.text
            assert client.post("/interactive/sessions/" + label + "/control",
                               json={"action": "start"}).status_code == 200
            deadline = time.monotonic() + 20.
            while True:
                packet = client.get(
                    "/interactive/sessions/" + label + "/telemetry/whole.continuous"
                )
                assert packet.status_code == 200
                frames = packet.json()["frames"]
                if len(frames) >= 2:
                    samples[label] = frames[0]["values"]
                    break
                assert time.monotonic() < deadline, label
                time.sleep(.02)
            stop = client.post("/interactive/sessions/" + label + "/control",
                               json={"action": "stop"})
            assert stop.status_code == 200, stop.text
    default = samples["config-default"]
    custom = samples["config-custom"]
    # Native orbitalMotion initial elements and native simpleBattery capacity.
    assert abs(custom["orbit.radius_m"] - default["orbit.radius_m"]) > 200_000.
    # The continuous operator TM whitelist publishes SOC but not the
    # internal SimpleBattery capacity field; do not fabricate a missing TM
    # channel. Observe the real native SOC effect and verify the submitted
    # capacity separately through the immutable native-graph task spec.
    assert custom["eps.battery_soc"] > default["eps.battery_soc"] + .1
    assert "adcs.rw.speed_rad_s_0" in custom
    assert abs(custom["adcs.rw.speed_rad_s_0"] - default["adcs.rw.speed_rad_s_0"]) > 45.
    assert custom["adcs.rw.speed_rad_s_0"] == pytest.approx(-50., abs=12.)
    assert default["adcs.rw.speed_rad_s_0"] == pytest.approx(100. * 2. * __import__("math").pi / 60., abs=2.)
    assert custom["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(.05)
    assert len(propulsion_assemblies) == 1
    p_cfg, p_graph = propulsion_assemblies[0]
    assert p_cfg.fuel_tank.initial_mass_kg == pytest.approx(.8)
    assert p_cfg.fuel_tank.capacity_kg == pytest.approx(1.6)
    assert all(spec.thrust_n == pytest.approx(2.5) for spec in p_graph.thruster_specs)
    assert all(spec.isp_s == pytest.approx(240.) for spec in p_graph.thruster_specs)
    assert p_graph.fuel_tank is not None and p_graph.thruster_effector is not None
    assert p_cfg.thruster_command.min_pulse_s == pytest.approx(.08)
    assert p_graph.thruster_specs[0].direction_b == pytest.approx((.6, .8, 0.))
    assert p_graph.thruster_specs[1].direction_b == pytest.approx((1., 0., 0.))
    assert p_graph.thruster_specs[0].location_b_m == pytest.approx((0., .75, 0.))
    assert p_graph.thruster_specs[1].location_b_m == pytest.approx((0., -.65, 0.))
    assert all(spec.min_on_time_s == pytest.approx(.08) for spec in p_graph.thruster_specs)
    assert len(thermal_configs) == 2
    assert thermal_configs[0].nodes["payload"].initial_temp_k == pytest.approx(292.)
    assert thermal_configs[1].nodes["payload"].initial_temp_k == pytest.approx(301.)
    assert thermal_configs[1].nodes["comm"].initial_temp_k == pytest.approx(303.)
    assert thermal_configs[1].nodes["propulsion"].initial_temp_k == pytest.approx(305.)
    paths = {(item.from_node, item.to_node): item.conductance_w_per_k
             for item in thermal_configs[1].conduction_paths}
    assert paths[("comm", "structure")] == pytest.approx(.7)
    assert paths[("propulsion", "structure")] == pytest.approx(.6)
    assert len(native_spacecraft) == 2
    assert len(native_sun_sensors) == 2
    assert [obj.hub.mHub for obj in native_spacecraft] == pytest.approx([100., 145.])
    assert [obj.fov for obj in native_sun_sensors] == pytest.approx(
        [__import__("math").radians(80.), __import__("math").radians(43.)]
    )
    # These are *real* Basilisk SWIG sensor instances, not synthetic traces.
    assert len(native_imus) == 2 and len(native_trackers) == 2
    np.testing.assert_allclose(np.asarray(native_imus[0].dcm_PB), np.eye(3), atol=1e-12)
    np.testing.assert_allclose(np.asarray(native_trackers[0].dcm_CB), np.eye(3), atol=1e-12)
    np.testing.assert_allclose(np.asarray(native_imus[0].sensorPos_B).reshape(3), [0., 0., 0.], atol=1e-12)
    np.testing.assert_allclose(np.asarray(native_imus[1].sensorPos_B).reshape(3), [.1, -.2, .3], atol=1e-12)
    imu_angles = np.radians([18., -6., 4.])
    tracker_angles = np.radians([-14., 7., -3.])
    np.testing.assert_allclose(
        np.asarray(native_imus[1].dcm_PB),
        np.asarray(RigidBodyKinematics.euler3212C(imu_angles)),
        atol=1e-12,
    )
    np.testing.assert_allclose(
        np.asarray(native_trackers[1].dcm_CB),
        np.asarray(RigidBodyKinematics.euler3212C(tracker_angles)),
        atol=1e-12,
    )
    assert len(factory_invocations) == 6
    np.testing.assert_allclose(np.asarray(native_imus[0].senRotBias).reshape(3), [0., 0., 0.], atol=1e-12)
    np.testing.assert_allclose(np.asarray(native_imus[1].senRotBias).reshape(3), [.001, -.002, .003], atol=1e-12)
    np.testing.assert_allclose(np.asarray(native_imus[1].senTransBias).reshape(3), [.04, -.05, .06], atol=1e-12)
    assert native_imus[1].senRotMax == pytest.approx(.5)
    assert native_imus[1].senTransMax == pytest.approx(20.)
    # Official PMatrix stores the *standard deviation in radians*, not arcsec².
    np.testing.assert_allclose(
        np.asarray(native_trackers[1].PMatrix),
        np.eye(3) * __import__("math").radians(10. / 3600.),
        atol=1e-12,
    )
    assert [x["wheel_model"] for x in factory_invocations] == ["Honeywell_HR16"] * 6
    assert [x["Omega"] for x in factory_invocations[:3]] == pytest.approx([100., 200., 300.])
    assert [x["Omega"] for x in factory_invocations[3:]] == pytest.approx(
        [x * 60. / (2. * __import__("math").pi) for x in (-50., 140., -200.)]
    )
    assert [x["u_max"] for x in factory_invocations[:3]] == [.2] * 3
    assert [x["u_max"] for x in factory_invocations[3:]] == [.05] * 3
    assert [x["friction"] for x in factory_invocations] == [False] * 3 + [True] * 3
    assert [x["fCoulomb"] for x in factory_invocations[3:]] == pytest.approx([2e-5] * 3)
    assert [x["cViscous"] for x in factory_invocations[3:]] == pytest.approx([3e-7] * 3)
    assert [x["position"] for x in factory_invocations[:3]] == [[0., 0., 0.]] * 3
    assert [x["position"] for x in factory_invocations[3:]] == [
        [.12, 0., 0.], [0., .12, 0.], [0., 0., .12],
    ]


def test_native_basilisk_four_rw_telemetry_and_independent_hardware_initialization(
    tmp_path, monkeypatch,
):
    """Compare two *real* four-wheel integrated Basilisk graphs through REST.

    A factory spy captures arguments then forwards into the original upstream
    constructor. Actual RW4 wheelSpeeds telemetry proves a fourth physical
    wheel exists and its configurable initial speed changes the trajectory.
    """
    pytest.importorskip("Basilisk")
    import math
    from Basilisk.utilities import simIncludeRW

    factory_args = []
    actual_create = simIncludeRW.rwFactory.create

    def watch_factory(self, wheel_model, axis, **kwargs):
        factory_args.append({
            "wheel_model": wheel_model,
            "Omega": kwargs["Omega"],
            "u_max": kwargs["u_max"],
            "label": kwargs["label"],
        })
        return actual_create(self, wheel_model, axis, **kwargs)

    monkeypatch.setattr(simIncludeRW.rwFactory, "create", watch_factory)
    app = _client(tmp_path, monkeypatch)
    measured = {}
    cases = (
        ("rw4-baseline", 41.88790204786391, .2),
        ("rw4-custom", -120., .075),
    )
    with TestClient(app) as client:
        for name, initial_speed, max_torque in cases:
            response = client.post("/interactive/continuous/sessions", json={
                **_body(name),
                "initial_configuration": {"adcs": {
                    "rw_configuration": "pyramid_4",
                    "rw_initial_speed_rad_s_3": initial_speed,
                    "rw_max_torque_nm": max_torque,
                }},
            })
            assert response.status_code == 201, response.text
            assert client.post("/interactive/sessions/" + name + "/control",
                               json={"action": "start"}).status_code == 200
            deadline = time.monotonic() + 20.
            while True:
                response = client.get(
                    "/interactive/sessions/" + name + "/telemetry/whole.continuous"
                )
                assert response.status_code == 200, response.text
                frames = response.json()["frames"]
                if len(frames) >= 2:
                    measured[name] = frames[0]["values"]
                    break
                assert time.monotonic() < deadline, name
                time.sleep(.02)
            assert client.post("/interactive/sessions/" + name + "/control",
                               json={"action": "stop"}).status_code == 200

    assert len(factory_args) == 8  # exactly four native wheels in each graph
    assert [x["label"] for x in factory_args] == [
        "RW1", "RW2", "RW3", "RW4",
        "RW1", "RW2", "RW3", "RW4",
    ]
    assert all(x["wheel_model"] == "Honeywell_HR16" for x in factory_args)
    assert factory_args[3]["Omega"] == pytest.approx(400.)
    assert factory_args[7]["Omega"] == pytest.approx(-120. * 60. / (2. * math.pi))
    assert [x["u_max"] for x in factory_args[:4]] == [.2] * 4
    assert [x["u_max"] for x in factory_args[4:]] == [.075] * 4
    base = measured["rw4-baseline"]
    custom = measured["rw4-custom"]
    for row in (base, custom):
        assert all(f"adcs.rw.speed_rad_s_{i}" in row for i in range(4))
        assert "adcs.rw.effective_max_torque_nm_3" in row
    assert base["adcs.rw.speed_rad_s_3"] == pytest.approx(41.88790204786391, abs=15.)
    assert custom["adcs.rw.speed_rad_s_3"] == pytest.approx(-120., abs=15.)
    assert abs(base["adcs.rw.speed_rad_s_3"] - custom["adcs.rw.speed_rad_s_3"]) > 100.
    assert base["adcs.rw.effective_max_torque_nm_3"] == pytest.approx(.2)
    assert custom["adcs.rw.effective_max_torque_nm_3"] == pytest.approx(.075)
