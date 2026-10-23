"""Pre-run config is bounded and must reach the *actual* Basilisk graph.

Browser/control-plane tests use a protocol double; genuine physical response
is validated by the native-only test without substituting a Python orbit/EPS.
"""
from __future__ import annotations

import time
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
        "orbit", "adcs", "eps", "payload", "comm_data",
    ]
    for row in catalog["groups"]:
        assert row["fields"]
        assert all("native_value_key" in x and "default" in x and "unit" in x
                   for x in row["fields"])
    assert ContinuousInitialConfiguration().native_values() == {}
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


@pytest.mark.parametrize("bad", [
    {"orbit": {"orbit_radius_m": 120}},
    {"adcs": {"rw_configuration": "arbitrary_python"}},
    {"eps": {"initial_soc": 1.5}},
    {"eps": {"solar_panel_area_m2": -1}},
    {"comm_data": {"storage_capacity_bits": 0}},
    {"payload": {"unknown_basilisk_function": "x"}},
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
                "orbit": {"orbit_radius_m": 7_200_000., "inclination_deg": 46.},
                "adcs": {"rw_configuration": "orthogonal_3", "controller_k": 7.},
                "eps": {"battery_capacity_wh": 200., "initial_soc": .75,
                        "solar_panel_area_m2": 3.},
                "payload": {"payload_data_rate_bps": 1_500_000.},
                "comm_data": {"storage_capacity_bits": 7_000_000_000.},
            },
        }
        created = client.post("/interactive/continuous/sessions", json=request)
        assert created.status_code == 201, created.text
        values = captured[0].task_spec["parameters"]["values"]
        assert values["orbit_radius_m"] == 7_200_000.
        assert values["inclination_deg"] == 46.
        assert values["battery_capacity_wh"] == 200.
        assert values["rw_configuration"] == "orthogonal_3"
        assert values["storage_capacity_bits"] == 7_000_000_000.
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
    factory_invocations = []
    native_factory_create = simIncludeRW.rwFactory.create

    def capture_native_factory(self, wheel_model, axis, **kwargs):
        # Transparently wrap the *official* Basilisk factory and then invoke
        # the unchanged original constructor. This proves u_max and Omega
        # reach the physical StateEffector object creation.
        factory_invocations.append({
            "wheel_model": wheel_model, "u_max": kwargs["u_max"],
            "Omega": kwargs["Omega"], "label": kwargs["label"],
        })
        return native_factory_create(self, wheel_model, axis, **kwargs)
    monkeypatch.setattr(simIncludeRW.rwFactory, "create", capture_native_factory)
    app = _client(tmp_path, monkeypatch)
    samples = {}
    with TestClient(app) as client:
        for label, initial in (
            ("config-default", {}),
            ("config-custom", {
                "orbit": {"orbit_radius_m": 7_350_000., "eccentricity": .001},
                "eps": {"battery_capacity_wh": 240., "initial_soc": .85,
                        "solar_panel_area_m2": 3.},
                "adcs": {
                    "controller_k": 5., "controller_p": 25.,
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
    assert len(factory_invocations) == 6
    assert [x["wheel_model"] for x in factory_invocations] == ["Honeywell_HR16"] * 6
    assert [x["Omega"] for x in factory_invocations[:3]] == pytest.approx([100., 200., 300.])
    assert [x["Omega"] for x in factory_invocations[3:]] == pytest.approx(
        [x * 60. / (2. * __import__("math").pi) for x in (-50., 140., -200.)]
    )
    assert [x["u_max"] for x in factory_invocations[:3]] == [.2] * 3
    assert [x["u_max"] for x in factory_invocations[3:]] == [.05] * 3


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
