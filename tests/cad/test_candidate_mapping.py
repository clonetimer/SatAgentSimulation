from __future__ import annotations

from sat_sim.cad_candidate_mapping import SCHEMA_VERSION, build_candidate_mapping


def _engineering_tree():
    return {
        "tree": {
            "object_id": "whole_spacecraft", "kind": "spacecraft", "children": [
                {"object_id": "subsystem.adcs", "kind": "subsystem", "children": [
                    {"object_id": "component.reaction_wheel", "kind": "equipment", "name_zh": "反作用轮",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                    {"object_id": "component.sun_sensor", "kind": "equipment", "name_zh": "太阳敏感器",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                    {"object_id": "component.star_tracker", "kind": "equipment", "name_zh": "星敏感器",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                    {"object_id": "component.mtb", "kind": "equipment", "name_zh": "磁力矩器",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                ]},
                {"object_id": "subsystem.eps", "kind": "subsystem", "children": [
                    {"object_id": "component.battery", "kind": "equipment", "name_zh": "蓄电池",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                    {"object_id": "component.solar_panel", "kind": "equipment", "name_zh": "太阳电池阵",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                ]},
                {"object_id": "subsystem.payload", "kind": "subsystem", "children": [
                    {"object_id": "component.payload", "kind": "equipment", "name_zh": "载荷仪器",
                     "instance_semantics": "capability_model_not_vehicle_inventory", "inventory_count": None, "children": []},
                ]},
            ],
        },
        "roots": [
            {"object_id": "external_environment", "kind": "external_group", "children": [
                {"object_id": "component.ground_station", "kind": "external_object", "children": []}
            ]}
        ],
    }


def _cad_node(path, name, parent=None, occurrence=1):
    return {
        "source_node_path": path,
        "parent_node_path": parent,
        "source_node_name": name,
        "source_label_name": name,
        "duplicate_occurrence": occurrence,
        "direct_shape_count": 1,
        "child_count": 1,
        "local_transform_m": None,
        "world_transform_m": None,
    }


def test_builds_candidates_for_specific_component_aliases_without_promoting_instances():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node("root/B4_RW_Assembly", "B4_RW_Assembly v55", "root"),
            _cad_node("root/B4_battery", "B4_battery v1", "root"),
            _cad_node("root/B4_FM_sun_sensor", "B4_FM_SNSS v2", "root"),
            _cad_node("root/B4_EM_sun_sensor", "B4_EM_SNSS v1", "root"),
            _cad_node("root/B4_FM_star_tracker", "B4_FM_star_tracker v1", "root"),
            _cad_node("root/B4_FM_MTB", "B4_FM_MTB v1", "root"),
            _cad_node("root/B4_FM_panel", "B4_FM_-X_panel v1", "root"),
        ]},
        source_model="BIRDS4_FM_Final v7.step",
        source_revision="3bab47a",
    )
    assert result["schema_version"] == SCHEMA_VERSION
    rows = result["candidates"]
    by_label = {row["cad_source_label_name"]: row for row in rows}
    assert by_label["B4_RW_Assembly v55"]["engineering_object_id"] == "component.reaction_wheel"
    assert by_label["B4_battery v1"]["engineering_object_id"] == "component.battery"
    assert by_label["B4_FM_SNSS v2"]["engineering_object_id"] == "component.sun_sensor"
    assert by_label["B4_FM_star_tracker v1"]["engineering_object_id"] == "component.star_tracker"
    assert by_label["B4_FM_MTB v1"]["engineering_object_id"] == "component.mtb"
    assert "B4_EM_SNSS v1" in by_label
    assert by_label["B4_EM_SNSS v1"]["identity_review_required"] is True
    assert all(row["review_status"] == "candidate_only" for row in rows)
    assert all(row["verification_status"] == "draft" for row in rows)
    assert all(row["instance_id"] is None and row["spatial_anchor"] is None for row in rows)
    assert all(row["render_eligible"] is False for row in rows)
    assert result["semantics"]["cad_nodes_are_not_installed_inventory"] is True
    assert result["summary"]["render_eligible_candidate_count"] == 0


def test_generic_panel_and_generic_board_labels_do_not_create_false_candidates():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node("root/panel", "B4_FM_-X_panel v1"),
            _cad_node("root/board", "B4_COM_board v19"),
            _cad_node("root/sensor", "B4_sensor_module v1"),
        ]},
    )
    assert result["candidates"] == []
    assert result["summary"]["unmatched_cad_node_count"] == 3


def test_duplicate_occurrences_are_preserved_as_distinct_paths_and_mark_object_ambiguous():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node("root/RW/occurrence-1", "B4_RW_Assembly v55", "root/RW", 1),
            _cad_node("root/RW/occurrence-2", "B4_RW_Assembly v55", "root/RW", 2),
        ]},
    )
    rows = result["candidates"]
    assert len(rows) == 2
    assert len({row["candidate_id"] for row in rows}) == 2
    assert all(row["multiple_cad_candidates_for_object"] for row in rows)
    assert all(row["ambiguous"] for row in rows)


def test_em_and_b3_variant_tags_require_identity_review():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node("root/B4_EM_RW", "B4_EM_RW_Assembly v1"),
            _cad_node("root/B3_RW", "B3_RW_Assembly v2"),
            _cad_node("root/B4_FM_RW", "B4_FM_RW_Assembly v3"),
        ]},
    )
    by_path = {row["cad_node_path"]: row for row in result["candidates"]}
    assert by_path["root/B4_EM_RW"]["variant_tags"] == ["em"]
    assert by_path["root/B3_RW"]["variant_tags"] == ["b3"]
    assert by_path["root/B4_FM_RW"]["variant_tags"] == ["fm"]
    assert by_path["root/B4_EM_RW"]["identity_review_required"] is True
    assert by_path["root/B3_RW"]["identity_review_required"] is True
    assert by_path["root/B4_FM_RW"]["identity_review_required"] is True
    assert by_path["root/B4_FM_RW"]["ambiguous"] is False


def test_external_context_and_relationship_nodes_are_not_equipment_targets():
    result = build_candidate_mapping(
        {"tree": {"object_id": "whole_spacecraft", "kind": "spacecraft", "children": [
            {"object_id": "component.ground_station", "kind": "external_object", "children": []},
            {"object_id": "component.link_budget", "kind": "relationship_object", "children": []},
            {"object_id": "component.power_sink", "kind": "context_object", "children": []},
        ]}},
        {"nodes": [_cad_node("root/antenna", "B4_antenna v1")]},
    )
    assert result["candidates"] == []
    assert result["source"]["engineering_equipment_category_count"] == 0


def test_rejects_duplicate_or_missing_cad_paths():
    engineering = _engineering_tree()
    try:
        build_candidate_mapping(engineering, {"nodes": [
            _cad_node("root/RW", "B4_RW_Assembly v1"),
            _cad_node("root/RW", "B4_RW_Assembly v2"),
        ]})
    except ValueError as error:
        assert "DUPLICATE_CAD_NODE_PATH" in str(error)
    else:
        raise AssertionError("duplicate CAD paths must fail closed")

    try:
        build_candidate_mapping(engineering, {"nodes": [{"source_node_name": "B4_RW_Assembly"}]})
    except ValueError as error:
        assert "REQUIRES_PATH_AND_NAME" in str(error)
    else:
        raise AssertionError("CAD node without a stable path must fail closed")


def test_mapping_is_deterministic_and_lexical_strength_is_not_probability():
    kwargs = {
        "engineering_tree": _engineering_tree(),
        "assembly_tree": {"nodes": [_cad_node("root/RW", "B4_RW_Assembly v55")]},
        "source_model": "BIRDS4_FM_Final v7.step",
        "source_revision": "abc123",
    }
    first = build_candidate_mapping(**kwargs)
    second = build_candidate_mapping(**kwargs)
    assert first == second
    assert first["semantics"]["lexical_strength_is_not_probability"] is True


def test_geometry_leaf_nodes_and_antenna_mounting_hardware_are_not_component_candidates():
    leaf = _cad_node("root/SNSS-M2-4:1", "SNSS-M2-4 v1:1")
    leaf["child_count"] = 0
    antenna_mount = _cad_node("root/B4_antenna_fixing_parts", "B4_antenna_fixing_parts v7")
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            leaf,
            antenna_mount,
            _cad_node("root/B4_RW_Assembly", "B4_RW_Assembly v55"),
        ]},
    )
    assert [row["engineering_object_id"] for row in result["candidates"]] == ["component.reaction_wheel"]
    assert result["summary"]["skipped_leaf_geometry_node_count"] == 1
    assert result["summary"]["render_eligible_candidate_count"] == 0
    assert result["candidates"][0]["node_granularity"] == "assembly_occurrence"


def test_perovskite_solar_cell_mission_is_payload_not_bus_solar_array():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node(
                "BIRDS4_FM_Final v7/B4_FM_-Y_panel v3/Perovskite_Solar_Cells v3",
                "Perovskite_Solar_Cells v3",
                "BIRDS4_FM_Final v7/B4_FM_-Y_panel v3",
            ),
        ]},
    )
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["engineering_object_id"] == "component.payload"
    assert candidate["match_evidence"][0]["reason"] == "perovskite_solar_cell_mission_payload_candidate"
    assert candidate["verification_status"] == "draft"
    assert candidate["render_eligible"] is False


def test_mixed_em_or_b3_nodes_in_full_reference_model_flag_all_candidates_for_identity_review():
    result = build_candidate_mapping(
        _engineering_tree(),
        {"nodes": [
            _cad_node("BIRDS4_FM_Final v7/B4_battery v1", "B4_battery v1"),
            _cad_node("BIRDS4_FM_Final v7/B4_EM_panel v19", "B4_EM_panel v19"),
            _cad_node("BIRDS4_FM_Final v7/B3_panel v26", "B3_panel v26"),
        ]},
    )
    assert result["source"]["source_contains_mixed_em_or_b3_nodes"] is True
    assert result["source"]["source_variant_node_counts"]["em"] == 1
    assert result["source"]["source_variant_node_counts"]["b3"] == 1
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["engineering_object_id"] == "component.battery"
    assert candidate["variant_tags"] == ["fm"]
    assert candidate["source_model_identity_review_required"] is True
    assert candidate["identity_review_required"] is True
