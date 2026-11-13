"""No lexical inference of wheel instance numbers or group quantities."""
import importlib.util
from sat_sim.form_schema import capability_form_schema

RW = "component.reaction_wheel.v1"

def dictionary():
    assert importlib.util.find_spec("sat_sim.quantity_dictionary"), "Reviewed quantity dictionary is missing"
    from sat_sim import quantity_dictionary
    return quantity_dictionary

def test_all_rw_schema_outputs_have_reviewed_names():
    schema = capability_form_schema(RW)
    result = dictionary().quantity_catalog(RW)
    assert not result["unreviewed"]
    rows = {(r["kind"], r["source_field"]): r for r in result["reviewed"]}
    for kind, key in [("trace", "trace_options"), ("summary", "summary_options")]:
        for field in schema["outputs"][key]:
            row = rows[(kind, field["name"])]
            assert row["source_capability_id"] == RW
            assert row["unit"] == field["unit"]
            assert " · " in row["display_name_zh"]
            assert "质量指标" not in row["display_name_zh"]
            assert row["review_status"] == "reviewed"
    assert len(schema["outputs"]["trace_options"]) == 13
    assert len(schema["outputs"]["summary_options"]) == 13

def test_wheel_index_is_not_a_body_axis():
    row = dictionary().lookup_quantity(RW, "trace", "adcs.reaction_wheel.speed_rad_s_0")
    assert row["display_name_zh"] == "反作用轮1 · 角速度"
    assert row["instance_index"] == 0
    assert row["owner_scope"] == "instance"
    assert "X轴" not in row["display_name_zh"]
    assert dictionary().lookup_quantity(RW, "trace", "adcs.reaction_wheel.speed_rad_s_9") is None

def test_group_momentum_and_energy_are_not_single_wheel_measurements():
    for field in ["adcs.reaction_wheel.momentum_norm_nms", "adcs.reaction_wheel.rotational_energy_j"]:
        row = dictionary().lookup_quantity(RW, "trace", field)
        assert row["owner_scope"] == "group"
        assert row["instance_index"] is None
        assert row["display_name_zh"].startswith("反作用轮组 · ")

def test_parameters_preserve_original_units_and_schema_paths():
    rows = dictionary().quantity_catalog(RW)["reviewed"]
    rows = {r["source_field"]: r for r in rows if r["kind"] == "parameter"}
    assert rows["parameters.values.wheel_inertia_kg_m2"]["unit"] == "kg*m^2"
    assert rows["parameters.values.command_torque_nm"]["unit"] == "N*m"
    assert rows["parameters.values.num_wheels"]["display_name_zh"] == "反作用轮组 · 数量"

def test_unreviewed_other_objects_are_visible_to_audit_not_mislabelled():
    result = dictionary().quantity_catalog("component.battery.v1")
    assert result["unreviewed"]
    for row in result["unreviewed"]:
        assert row["review_status"] == "unreviewed"
        assert row["display_name_zh"] is None
        assert row["selectable"] is False
        assert row["source_field"]

def test_dictionary_validation_rejects_stale_mapping_and_unit_change():
    import copy
    mod = dictionary()
    assert mod.validate_dictionary()["ok"] is True
    rows = copy.deepcopy(mod.REVIEWED_QUANTITIES)
    rows[0]["unit"] = "bad-unit"
    report = mod.validate_dictionary(rows)
    assert report["ok"] is False
    assert any(r["code"] == "UNIT_MISMATCH" for r in report["errors"])
    rows = copy.deepcopy(mod.REVIEWED_QUANTITIES)
    rows[0]["source_field"] = "invented.field"
    report = mod.validate_dictionary(rows)
    assert report["ok"] is False
    assert any(r["code"] == "SOURCE_FIELD_MISSING" for r in report["errors"])

def test_dictionary_schema_covers_reviewed_and_unreviewed():
    import json
    from importlib.resources import files
    import jsonschema
    path = files("sat_sim.schemas").joinpath("quantity_dictionary.schema.json")
    assert path.is_file(), "Dictionary JSON Schema is missing"
    schema = json.loads(path.read_text())
    for capability in (RW, "component.battery.v1"):
        jsonschema.validate(dictionary().quantity_catalog(capability), schema)
