from __future__ import annotations

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates
from sat_sim.task_validator import validate_task_spec


def test_basilisk_native_reaction_wheel_template_is_a_valid_component_task() -> None:
    spec = instantiate_scenario_template(
        "component_reaction_wheel_basilisk_native",
        task_id="p9n_native_rw_template",
    )
    assert spec["simulation"]["level"] == "component"
    assert spec["model"]["capability_id"] == "component.reaction_wheel.v1"
    assert spec["model"]["target"]["level"] == "component"
    assert spec["model"]["target"]["name"] == "reaction_wheel"
    assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
    assert spec["parameters"]["values"]["num_wheels"] == 1
    assert spec["parameters"]["values"]["max_motor_torque_nm"] == 0.005
    assert spec["outputs"]["plots"] == [
        "adcs.reaction_wheel.speed_rad_s_0",
        "adcs.reaction_wheel.applied_torque_nm_0",
    ]
    assert "qoi.adcs.reaction_wheel.final_speed_rad_s_0" in spec["outputs"]["qoi"]
    validation = validate_task_spec(spec)
    assert validation.ok, validation.to_dict()


def test_native_backend_is_visible_in_recommended_component_parameters() -> None:
    schema = capability_form_schema("component.reaction_wheel.v1")
    backend = next(
        field for field in schema["fields"]
        if field["path"] == "parameters.values.physics_backend"
    )
    assert backend["importance"] == "common"
    assert backend["widget"] == "select"
    assert backend["enum"] == ["project_reference", "basilisk_native"]
    assert backend["enum_labels"]["basilisk_native"].startswith("Basilisk 原生物理")
    assert "不等同于 Basilisk 原生物理" in backend["description"]


def test_native_rw_template_does_not_enable_unsupported_events() -> None:
    spec = instantiate_scenario_template(
        "component_reaction_wheel_basilisk_native",
        task_id="p9n_native_rw_no_modifiers",
    )
    assert spec["events"]["faults"] == []
    assert spec["events"]["degradations"] == []
    assert spec["events"]["constraints"] == []
    assert spec["model"]["target"]["mode"] == "nominal"


def test_native_reaction_wheel_template_is_published_by_the_workbench_api(tmp_path) -> None:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    with TestClient(app) as client:
        catalog = client.get(
            "/scenario-templates",
            params={"level": "component", "capability_id": "component.reaction_wheel.v1"},
        )
        assert catalog.status_code == 200, catalog.text
        templates = {item["template_id"]: item for item in catalog.json()["templates"]}
        native = templates["component_reaction_wheel_basilisk_native"]
        assert native["object_id"] == "component.reaction_wheel"
        assert native["level"] == "component"

        instantiated = client.post(
            "/scenario-templates/component_reaction_wheel_basilisk_native/instantiate",
            json={"task_id": "p9n_native_rw_api"},
        )
        assert instantiated.status_code == 200, instantiated.text
        spec = instantiated.json()["task_spec"]
        assert spec["simulation"]["level"] == "component"
        assert spec["model"]["capability_id"] == "component.reaction_wheel.v1"
        assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"

        form = client.get("/forms/capabilities/component.reaction_wheel.v1")
        assert form.status_code == 200, form.text
        backend = next(
            field for field in form.json()["form_schema"]["fields"]
            if field["path"] == "parameters.values.physics_backend"
        )
        assert backend["importance"] == "common"
        assert backend["enum_labels"]["basilisk_native"].startswith("Basilisk 原生物理")


def test_native_backend_is_exposed_in_the_guided_parameter_workspace() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    html = (root / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "wizardApplyNativeRwPreset" in source
    assert "section.parameter_group === 'backend'" in source
    assert 'id="wizardApplyNativeRwPresetBtn"' in html
    assert "component_reaction_wheel_basilisk_native" in source
    for template_id in (
        "component_imu_basilisk_native",
        "component_star_tracker_basilisk_native",
        "component_magnetometer_basilisk_native",
        "component_battery_basilisk_native",
        "component_solar_panel_basilisk_native",
        "component_thruster_basilisk_native",
        "component_fuel_tank_basilisk_native",
        "component_thermal_node_basilisk_native",
    ):
        assert template_id in source
    assert "wizardEventHost" in source
    assert "nativeSingleComponent" in source


def test_native_rw_backend_limits_events_and_run_results_show_model_provenance() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    html = (root / "src/sat_sim/web/index.html").read_text(encoding="utf-8")
    assert "nativeRwNominalOnly" in source
    assert "eventBackendLimitNote" in source
    assert "nativeProvenance" in source
    assert "Basilisk 官方原生" in source
    assert "runSummary.adapter_metadata?.native_provenance" in source
    assert 'id="eventBackendLimitNote"' in html


def test_official_basilisk_reaction_wheel_template_is_product_visible() -> None:
    catalog = list_scenario_templates(
        level="component",
        capability_id="component.reaction_wheel.v1",
        product_visible_only=True,
    )
    row = next(
        item for item in catalog["templates"]
        if item["template_id"] == "component_reaction_wheel_basilisk_native"
    )
    assert row["recommended"] is True
    assert row["object_id"] == "component.reaction_wheel"
    assert row["execution_scope"] == "direct"


def test_native_sensor_templates_are_registered_valid_and_product_visible() -> None:
    cases = (
        (
            "component_imu_basilisk_native",
            "component.imu.v1",
            "component.imu",
            "adcs.imu.gyro_x_rad_s",
            "adcs.imu.accel_x_m_s2",
        ),
        (
            "component_star_tracker_basilisk_native",
            "component.star_tracker.v1",
            "component.star_tracker",
            "adcs.star_tracker.sigma_x",
            "adcs.star_tracker.drift_norm",
        ),
        (
            "component_magnetometer_basilisk_native",
            "component.magnetometer.v1",
            "component.magnetometer",
            "adcs.magnetometer.field_x_t",
            "adcs.magnetometer.field_norm_t",
        ),
    )
    for template_id, capability_id, object_id, first_trace, second_trace in cases:
        spec = instantiate_scenario_template(template_id, task_id=f"n4c_{object_id.replace('.', '_')}")
        assert spec["simulation"]["level"] == "component"
        assert spec["model"]["capability_id"] == capability_id
        assert spec["model"]["target"]["level"] == "component"
        assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
        assert spec["model"]["target"]["mode"] == "nominal"
        assert spec["events"]["faults"] == []
        assert spec["events"]["degradations"] == []
        assert first_trace in spec["outputs"]["plots"]
        assert second_trace in spec["outputs"]["plots"]
        validation = validate_task_spec(spec)
        assert validation.ok, (template_id, validation.to_dict())

        catalog = list_scenario_templates(
            level="component",
            capability_id=capability_id,
            product_visible_only=True,
        )
        row = next(item for item in catalog["templates"] if item["template_id"] == template_id)
        assert row["recommended"] is True
        assert row["object_id"] == object_id
        assert row["execution_scope"] == "direct"

        schema = capability_form_schema(capability_id)
        backend = next(field for field in schema["fields"] if field["path"] == "parameters.values.physics_backend")
        assert backend["widget"] == "select"
        assert backend["enum"] == ["project_reference", "basilisk_native"]


def test_native_sensor_templates_are_published_by_workbench_api(tmp_path) -> None:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    template_ids = (
        "component_imu_basilisk_native",
        "component_star_tracker_basilisk_native",
        "component_magnetometer_basilisk_native",
    )
    with TestClient(app) as client:
        for template_id in template_ids:
            response = client.post(f"/scenario-templates/{template_id}/instantiate", json={})
            assert response.status_code == 200, (template_id, response.text)
            task_spec = response.json()["task_spec"]
            assert task_spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
            assert task_spec["model"]["target"]["mode"] == "nominal"


def test_native_eps_propulsion_thermal_templates_are_registered_valid_and_product_visible() -> None:
    cases = (
        (
            "component_battery_basilisk_native",
            "component.battery.v1",
            "component.battery",
            "eps.battery.soc",
            "eps.battery.net_power_w",
        ),
        (
            "component_solar_panel_basilisk_native",
            "component.solar_panel.v1",
            "component.solar_panel",
            "eps.solar.array_power_w",
            "eps.solar.incidence_cos",
        ),
        (
            "component_thruster_basilisk_native",
            "component.thruster.v1",
            "component.thruster",
            "propulsion.thruster.effective_thrust_n",
            "propulsion.thruster.cumulative_impulse_ns",
        ),
        (
            "component_fuel_tank_basilisk_native",
            "component.fuel_tank.v1",
            "component.fuel_tank",
            "propulsion.fuel_tank.propellant_mass_kg",
            "propulsion.fuel_tank.mass_rate_kg_s",
        ),
        (
            "component_thermal_node_basilisk_native",
            "component.thermal_node.v1",
            "component.thermal_node",
            "thermal.node.temp_k",
            "thermal.node.temp_c",
        ),
    )
    for template_id, capability_id, object_id, first_trace, second_trace in cases:
        spec = instantiate_scenario_template(template_id, task_id=f"n4c2_{object_id.replace('.', '_')}")
        assert spec["simulation"]["level"] == "component"
        assert spec["model"]["capability_id"] == capability_id
        assert spec["model"]["target"]["level"] == "component"
        assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
        assert spec["model"]["target"]["mode"] == "nominal"
        assert spec["events"]["faults"] == []
        assert spec["events"]["degradations"] == []
        assert first_trace in spec["outputs"]["plots"]
        assert second_trace in spec["outputs"]["plots"]
        validation = validate_task_spec(spec)
        assert validation.ok, (template_id, validation.to_dict())

        catalog = list_scenario_templates(
            level="component",
            capability_id=capability_id,
            product_visible_only=True,
        )
        row = next(item for item in catalog["templates"] if item["template_id"] == template_id)
        assert row["recommended"] is True
        assert row["object_id"] == object_id
        assert row["execution_scope"] == "direct"

        schema = capability_form_schema(capability_id)
        backend = next(field for field in schema["fields"] if field["path"] == "parameters.values.physics_backend")
        assert backend["widget"] == "select"
        assert backend["enum"] == ["project_reference", "basilisk_native"]


def test_native_eps_templates_are_published_by_workbench_api(tmp_path) -> None:
    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    )
    template_ids = (
        "component_battery_basilisk_native",
        "component_solar_panel_basilisk_native",
        "component_thruster_basilisk_native",
        "component_fuel_tank_basilisk_native",
        "component_thermal_node_basilisk_native",
    )
    with TestClient(app) as client:
        for template_id in template_ids:
            response = client.post(f"/scenario-templates/{template_id}/instantiate", json={})
            assert response.status_code == 200, (template_id, response.text)
            task_spec = response.json()["task_spec"]
            assert task_spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
            assert task_spec["model"]["target"]["mode"] == "nominal"
