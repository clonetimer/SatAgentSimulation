from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema, output_label
from sat_sim.workbench_catalog import workbench_presentation_catalog
from sat_sim.workbench_object_tree import build_object_tree
from sat_sim.workbench_quantity_names import (
    DICTIONARY_VERSION,
    bind_quantity_dictionary,
    component_catalog,
    output_display_name,
    parameter_display_name,
)
from sat_sim.workbench_scene_catalog import build_scene_catalog

ROOT = Path(__file__).resolve().parents[1]


def test_all_component_contracts_are_bound_to_reviewed_names_units_and_source_blobs():
    catalog = component_catalog()
    assert len(catalog) == 24
    assert len({row["object_id"] for row in catalog}) == 24
    for row in catalog:
        source = ROOT / row["source_contract_path"]
        actual_blob = subprocess.run(
            ["git", "hash-object", str(source)], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert actual_blob == row["source_contract_blob"], row["object_id"]
        contract = yaml.safe_load(source.read_text(encoding="utf-8"))
        bound = bind_quantity_dictionary(contract)
        assert bound["unreviewed_fields"] == [], (row["object_id"], bound["unreviewed_fields"])
        assert bound["unreviewed_parameters"] == [], (row["object_id"], bound["unreviewed_parameters"])


def test_single_machine_plus_quantity_names_use_exact_telemetry_fields():
    assert output_display_name("adcs.reaction_wheel.speed_rad_s_0") == "反作用轮1 · 角速度"
    assert output_display_name("adcs.mtb.torque_nm_0") == "磁力矩器 · 输出力矩 X 分量"
    assert output_display_name("eps.battery.soc") == "蓄电池 · 荷电状态"
    assert output_display_name("adcs.star_tracker.sigma_x") == "星敏感器 · 姿态误差参数 X 分量"
    assert parameter_display_name("component.reaction_wheel.v1", "wheel_inertia_kg_m2") == "反作用轮组 · 逐轮转动惯量"
    assert parameter_display_name("component.battery.v1", "initial_soc") == "蓄电池 · 初始荷电状态"
    assert output_display_name("adcs.unreviewed_temperature_k") is None
    assert output_label("adcs.reaction_wheel.speed_rad_s_0") == "反作用轮1 · 角速度"


def test_object_tree_has_four_levels_and_keeps_non_spacecraft_objects_separate():
    scene = build_scene_catalog(workbench_presentation_catalog())
    schemas = {
        row["execution_capability_id"]: capability_form_schema(row["execution_capability_id"])
        for row in scene["objects"]
        if row["kind"] == "component" and row.get("execution_capability_id")
    }
    result = build_object_tree(scene, schemas)
    root = result["tree"]
    assert root["object_id"] == "whole_spacecraft"
    adcs = next(row for row in root["children"] if row["object_id"] == "subsystem.adcs")
    assert adcs["name_zh"] == "姿态控制分系统"
    wheel = next(row for row in adcs["children"] if row["object_id"] == "component.reaction_wheel")
    assert wheel["kind"] == "equipment"
    assert wheel["instance_semantics"] == "capability_model_not_vehicle_inventory"
    assert any(row["kind"] == "parameter" and row["display_name_zh"] == "反作用轮组 · 逐轮转动惯量" for row in wheel["children"])
    assert any(row["kind"] == "observable" and row["display_name_zh"] == "反作用轮1 · 角速度" for row in wheel["children"])
    assert any(row["kind"] == "summary" for row in wheel["children"])
    orbit = next(row for row in root["children"] if row["object_id"] == "orbit_environment")
    control = next(row for row in root["children"] if row["object_id"] == "orbit_control")
    assert orbit["object_id"] != control["object_id"]
    context = next(row for row in result["roots"] if row["object_id"] == "mission_context")
    context_ids = {row["object_id"] for row in context["children"]}
    assert {"component.ground_station", "component.link_budget", "component.data_queue"} <= context_ids
    assert all(row["object_id"] != "component.ground_station" for row in root["children"])
    assert result["semantics"]["component_counts_are_inventory"] is False


def test_scene_catalog_endpoint_is_read_only_and_available_without_realtime():
    for key in tuple(os.environ):
        if key.startswith("SAT_SIM_"):
            os.environ.pop(key, None)
    with TestClient(create_app(
        runs_root=ROOT / ".pytest-tmp" / "scene-runs",
        artifacts_root=ROOT / ".pytest-tmp" / "scene-artifacts",
        embedded_worker=False, auth_mode="disabled",
    )) as client:
        before = client.get("/health").json()
        response = client.get("/workbench/scene/catalog")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["quantity_dictionary_version"] == DICTIONARY_VERSION
        assert payload["tree"]["tree"]["object_id"] == "whole_spacecraft"
        after = client.get("/health").json()
        assert before["features"] == after["features"] == {"interactive": False}
