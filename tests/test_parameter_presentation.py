from __future__ import annotations

from sat_sim.form_schema import capability_form_schema


def test_reaction_wheel_parameters_have_physical_groups_and_chinese_backend_labels():
    schema = capability_form_schema("component.reaction_wheel.v1")
    fields = {field["path"].rsplit(".", 1)[-1]: field for field in schema["fields"]}
    sections = {section["id"]: section for section in schema["sections"]}

    assert fields["physics_backend"]["label"] == "物理计算后端"
    assert fields["physics_backend"]["enum_labels"]["project_reference"].startswith("项目参考模型")
    assert "不等同于 Basilisk 原生物理" in fields["physics_backend"]["description"]
    assert "parameters_backend" in sections
    assert "parameters_actuator_mechanics" in sections
    assert "parameters_initial_state" in sections
    assert fields["max_motor_torque_nm"]["group_title"] == "执行机构与机械特性"
    assert fields["initial_wheel_speeds_rad_s"]["group_title"] == "初始状态"


def test_component_parameter_sections_preserve_every_registered_field():
    schema = capability_form_schema("component.reaction_wheel.v1")
    parameter_fields = {
        field["path"] for field in schema["fields"]
        if field["path"].startswith("parameters.")
    }
    section_fields = {
        path for section in schema["sections"]
        if section["id"].startswith("parameters")
        for path in section["field_paths"]
    }
    assert parameter_fields == section_fields
    assert len([section for section in schema["sections"] if section["id"].startswith("parameters_")]) >= 3


def test_parameter_explanations_are_collapsed_by_default_in_the_shared_renderer():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "className='field-help'" in source
    assert "helpSummary.textContent='参数说明'" in source
    assert "wrapper.append(heading,input,help)" in source
