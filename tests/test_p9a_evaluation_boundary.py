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
