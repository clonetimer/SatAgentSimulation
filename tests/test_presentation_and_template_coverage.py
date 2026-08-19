from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sat_sim import __version__
from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates
from sat_sim.workbench_catalog import workbench_presentation_catalog


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        embedded_worker=False,
        auth_mode="disabled",
    ))


def test_every_workbench_object_has_at_least_one_template() -> None:
    objects = workbench_presentation_catalog()["objects"]
    templates = list_scenario_templates()["templates"]
    object_ids = {item["object_id"] for item in templates}
    assert len(objects) == 32
    assert len(templates) >= len(objects)
    assert len({item["template_id"] for item in templates}) == len(templates)
    assert {"bsksim_foundation_orbit_attitude", "adcs_bsksim_migration", "bsksim_whole_spacecraft_strong_coupling"} <= {item["template_id"] for item in templates}
    assert not [item["object_id"] for item in objects if item["object_id"] not in object_ids]
    for item in templates:
        spec = instantiate_scenario_template(item["template_id"])
        assert spec["model"]["capability_id"] == item["capability_id"]
        assert spec["metadata"]["scenario_template"]["object_id"] == item["object_id"]


def test_subsystem_forms_group_all_component_parameters_and_keep_common_advanced_split() -> None:
    capabilities = [
        "subsystem.adcs_fidelity.v1",
        "subsystem.eps.basic.v1",
        "subsystem.thermal.basic_lumped.v1",
        "subsystem.comm.basic_ground_pass.v1",
        "subsystem.propulsion.source_native.v1",
        "subsystem.payload.source_native.v1",
    ]
    for capability_id in capabilities:
        schema = capability_form_schema(capability_id)
        parameter_fields = {f["path"] for f in schema["fields"] if f["path"].startswith("parameters.values.")}
        parameter_sections = [s for s in schema["sections"] if s.get("id", "").startswith("parameters_")]
        covered = {path for section in parameter_sections for path in section["field_paths"]}
        assert parameter_fields == covered
        assert len(parameter_sections) >= 2
        assert {f.get("importance") for f in schema["fields"] if f["path"] in parameter_fields} <= {"common", "advanced"}


def test_wheel_configuration_and_allocation_are_chinese_labeled_selects() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    geometry = next(item for item in schema["fields"] if item["path"] == "parameters.values.wheel_configuration")
    assert geometry["widget"] == "select"
    assert geometry["enum"] == ["orthogonal_3", "four_skew", "pyramid_4"]
    assert geometry["enum_labels"]["four_skew"].startswith("四轮斜装")
    assert geometry["enum_labels"]["pyramid_4"].startswith("四轮金字塔")
    allocation = next(item for item in schema["fields"] if item["path"] == "parameters.values.wheel_allocation_method")
    assert allocation["widget"] == "select"
    assert allocation["enum"] == ["weighted_pseudoinverse"]


def test_light_theme_and_static_asset_version_are_authoritative(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        html = client.get("/")
        css = client.get("/assets/styles.css")
        js = client.get("/assets/app.js")
    assert f"styles.css?v={__version__}" in html.text
    assert f'window.SAT_SIM_STATIC_VERSION = "{__version__}"' in html.text
    assert f"app.js?v={__version__}" in html.text
    assert "background: #ffffff !important" in css.text
    assert 'html[data-theme="light"] .dynamic-form .schema-section' in css.text
    assert html.headers["cache-control"].startswith("no-store")
    assert css.headers["cache-control"].startswith("no-store")
    assert "field.enum_labels" in js.text
    assert "metric_metadata" in js.text
    assert "版本不一致：界面" in js.text


def test_metrics_endpoint_returns_chinese_presentation_metadata(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "run_adcs"
    (run_root / "input").mkdir(parents=True)
    (run_root / "results").mkdir(parents=True)
    task = instantiate_scenario_template("adcs_pointing")
    (run_root / "input" / "task_spec.json").write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    (run_root / "results" / "metrics.json").write_text(json.dumps({
        "schema_version": "metrics.v1",
        "metrics": {
            "qoi.adcs.final_pointing_error_deg": 0.12,
            "qoi.adcs.max_pointing_error_deg": 12.0,
            "fidelity_level": "medium",
        },
    }, ensure_ascii=False), encoding="utf-8")
    with _client(tmp_path) as client:
        response = client.get("/runs/run_adcs/metrics")
    assert response.status_code == 200
    payload = response.json()
    metadata = payload["metric_metadata"]
    assert metadata["qoi.adcs.final_pointing_error_deg"]["label_zh"] == "最终姿态指向误差"
    assert metadata["qoi.adcs.final_pointing_error_deg"]["unit"] == "deg"
    assert "qoi.adcs.final_pointing_error_deg" in payload["requested_qoi"]


def test_scenario_template_api_filters_by_business_object(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/scenario-templates", params={"object_id": "component.star_tracker"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["templates"][0]["execution_scope"] == "direct"


def test_qoi_wildcard_expands_without_ambiguity() -> None:
    from sat_sim.execution_planner import plan_task_spec

    spec = instantiate_scenario_template("adcs_pointing")
    spec["outputs"]["qoi"] = ["qoi.*"]
    result = plan_task_spec(spec)
    assert result.ok is True
    bindings = result.resolved_spec.output_bindings if result.resolved_spec else []
    assert len(bindings) > 1
    assert all(binding.requested_field == "qoi.*" for binding in bindings)


def test_eps_summary_qoi_resolve_through_operator_contract() -> None:
    from sat_sim.execution_planner import plan_task_spec
    from sat_sim.release_closure import representative_release_forms
    from sat_sim.unified_agent import normalize_form_task_spec

    form = next(item["form"] for item in representative_release_forms() if item["case_id"] == "subsystem_nominal")
    spec = normalize_form_task_spec(form)
    result = plan_task_spec(spec)
    assert result.ok is True
    bindings = result.resolved_spec.output_bindings if result.resolved_spec else []
    assert {binding.requested_field for binding in bindings} == {
        "qoi.eps.battery.final_soc",
        "qoi.eps.power.min_margin_w",
    }
    assert all(binding.binding_status == "resolved" for binding in bindings)


def test_eps_natural_language_final_soc_and_min_margin_are_declared_qoi(tmp_path: Path) -> None:
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent

    result = run_unified_agent(UnifiedAgentRequest(
        request_text="Create an EPS subsystem nominal simulation for 30 seconds with 5 second sampling; output final battery SOC and minimum power margin.",
        output_dir=tmp_path / "eps",
        backend="template",
        local_backend="template",
    ))
    assert result.ok is True, result.planning.to_dict() if result.planning else result.validation.to_dict()
    assert result.task_spec["simulation"]["duration_s"] == 30.0
    assert result.task_spec["simulation"]["sample_s"] == 5.0
    qoi = set(result.task_spec["outputs"]["qoi"])
    assert "qoi.eps.battery.final_soc" in qoi
    assert "qoi.eps.power.min_margin_w" in qoi
    assert "battery_soc" not in qoi
    assert "battery_soc" not in set(result.task_spec["outputs"]["plots"])


def test_thermal_natural_language_uses_declared_temperature_field(tmp_path: Path) -> None:
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent

    result = run_unified_agent(UnifiedAgentRequest(
        request_text="建立热控分系统温度仿真",
        output_dir=tmp_path / "thermal",
        backend="template",
        local_backend="template",
    ))
    assert result.ok is True
    assert "thermal.node.bus_temp_c" in result.task_spec["outputs"]["qoi"]
    assert "thermal_temp_c" not in result.task_spec["outputs"]["qoi"]
