from __future__ import annotations

from sat_sim.form_schema import capability_form_schema
from sat_sim.scenario_templates import instantiate_scenario_template
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
