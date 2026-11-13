from copy import deepcopy
import json

from sat_sim.workbench_object_tree import build_object_tree


def inputs():
    scene = {"objects": [{"object_id": "whole_spacecraft", "kind": "whole_spacecraft", "name_zh": "整星", "parent_object_id": None, "execution_capability_id": "whole_spacecraft.unified_native.v1"}], "relationship_objects": []}
    schemas = {"whole_spacecraft.unified_native.v1": {"fields": [{"path": "parameters.values.mass_kg", "label": "质量", "unit": "kg", "type": "number", "default": 12.0}], "outputs": {"trace_options": [{"name": "orbit.position_m_x", "label": "位置 X 分量", "unit": "m", "dtype": "number"}], "summary_options": [{"name": "mission_success_score", "label": "任务效能评分", "unit": "ratio", "dtype": "number"}]}}}
    return scene, schemas


def walk(row):
    yield row
    for child in row.get("children", []):
        yield from walk(child)


def test_simulation_tree_contains_process_data_not_evaluation_metrics():
    scene, schemas = inputs()
    result = build_object_tree(scene, schemas)
    nodes = list(walk(result["tree"]))
    assert not any(row.get("kind") in {"summary", "summary_group"} for row in nodes)
    assert not any(row.get("name_zh") == "结果指标" for row in nodes)
    assert any(row.get("source_field") == "orbit.position_m_x" for row in nodes)
    assert any(row.get("source_field") == "mass_kg" for row in nodes)
    assert result["schema_version"] == "workbench-object-tree.v2"


def test_summary_changes_do_not_change_the_simulation_tree():
    scene, schemas = inputs()
    before = build_object_tree(scene, schemas)
    schemas["whole_spacecraft.unified_native.v1"]["outputs"]["summary_options"].append({"name": "custom_score", "label": "自定义评分", "unit": "ratio"})
    assert build_object_tree(scene, schemas) == before


def test_building_tree_never_rewrites_recording_or_evaluation_inputs():
    scene, schemas = inputs()
    original = deepcopy((scene, schemas))
    json.dumps(build_object_tree(scene, schemas), allow_nan=False)
    assert (scene, schemas) == original


def test_quantities_have_owner_without_pretending_groups_are_devices():
    scene, schemas = inputs()
    result = build_object_tree(scene, schemas)
    for node in walk(result["tree"]):
        if node.get("kind") in {"parameter", "observable", "parameter_group", "observable_group"}:
            assert node["owner_object_id"] == "whole_spacecraft"
            assert "object_id" not in node


def test_summary_failure_cannot_block_simulation_tree_construction():
    scene, schemas = inputs()
    schemas["whole_spacecraft.unified_native.v1"]["outputs"]["summary_options"] = None
    result = build_object_tree(scene, schemas)
    assert result["totals"]["observable"] == 1
    assert result["totals"]["summary"] == 0


def test_quantity_dictionary_json_is_declared_as_package_data():
    from pathlib import Path
    import fnmatch
    import tomllib
    project = tomllib.loads(Path("pyproject.toml").read_text())
    patterns = project["tool"]["setuptools"]["package-data"]["sat_sim"]
    for path in Path("src/sat_sim").glob("workbench_quantity_dictionary_*.json"):
        assert any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns), path.name


def test_tree_rejects_parent_cycles_instead_of_losing_unreachable_objects():
    import pytest
    scene, schemas = inputs()
    scene['objects'].extend([
        {'object_id': 'a', 'kind': 'subsystem', 'parent_object_id': 'b'},
        {'object_id': 'b', 'kind': 'subsystem', 'parent_object_id': 'a'},
    ])
    with pytest.raises(ValueError, match='CYCLE'):
        build_object_tree(scene, schemas)


def test_missing_relationship_descriptor_cannot_silently_drop_a_link():
    import pytest
    scene, schemas = inputs()
    scene['objects'].append({'object_id': 'component.link_budget', 'kind': 'component', 'parent_object_id': 'whole_spacecraft'})
    with pytest.raises(ValueError, match='RELATIONSHIP_DEFINITION_MISSING'):
        build_object_tree(scene, schemas)
