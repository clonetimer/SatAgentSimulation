from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parameters.resource_paths import resolve_parameter_registry

from sat_sim.api import create_app
from sat_sim.capability_agent import CapabilityTemplateBackend
from sat_sim.capability_registry import clear_capability_cache, get_capability, list_capabilities
from sat_sim.experiments.basilisk_mc_adapter import build_basilisk_mc_plan, execute_basilisk_controller_variants
from sat_sim.experiments.monte_carlo_engine import expand_monte_carlo
from sat_sim.experiments.native_controller_registry import native_controller_capability_ids, native_controller_contract
from sat_sim.form_schema import capability_form_schema
from sat_sim.model_governance import capability_governance_matrix, validate_capability_governance
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates
from sat_sim.workbench_catalog import workbench_presentation_catalog

ADCS = "subsystem.adcs_unified_native.v1"
WHOLE = "whole_spacecraft.unified_native.v1"
FOUNDATION = "whole_spacecraft.bsksim_foundation.v1"
ADCS_BRIDGE = "subsystem.adcs_bsksim.v1"
WHOLE_BRIDGE = "whole_spacecraft.bsksim_coupled.v1"


def _short_template(template_id: str) -> dict:
    spec = instantiate_scenario_template(template_id)
    spec["simulation"].update({"duration_s": 6.0, "step_s": 0.5, "sample_s": 1.0})
    return spec


def test_all_capabilities_have_valid_lifecycle_and_governance() -> None:
    clear_capability_cache()
    assert len(list_capabilities()) == 60
    assert validate_capability_governance() == []
    matrix = capability_governance_matrix()
    assert matrix["count"] == 60
    assert matrix["recommended_capability_ids"] == [
        ADCS,
        "subsystem.comm_data.unified_native.v1",
        "subsystem.eps.unified_native.v1",
        "subsystem.propulsion.unified_native.v1",
        WHOLE,
    ]
    assert matrix["official_monte_carlo_capability_ids"] == [ADCS, FOUNDATION, WHOLE]
    registry_path = resolve_parameter_registry("demo_parameter_registry_v1.json")
    assert registry_path.is_file()


def test_compatibility_bridges_are_deprecated_with_replacements() -> None:
    adcs = get_capability(ADCS_BRIDGE)
    whole = get_capability(WHOLE_BRIDGE)
    assert adcs.lifecycle_status == "deprecated"
    assert adcs.product_tier == "compatibility"
    assert adcs.replacement_capability_id == ADCS
    assert adcs.exposed_to_agent is False
    assert whole.lifecycle_status == "deprecated"
    assert whole.product_tier == "compatibility"
    assert whole.replacement_capability_id == WHOLE
    assert whole.exposed_to_agent is False


def test_workbench_uses_unified_runtime_as_primary_path() -> None:
    catalog = workbench_presentation_catalog()
    objects = {item["object_id"]: item for item in catalog["objects"]}
    whole = objects["whole_spacecraft"]
    adcs = objects["subsystem.adcs"]
    assert whole["primary_capability_id"] == WHOLE
    assert adcs["primary_capability_id"] == ADCS
    assert whole["variants"][0]["recommended"] is True
    assert adcs["variants"][0]["recommended"] is True
    assert whole["variants"][0]["requires_allow_proxy"] is False


def test_agent_defaults_to_recommended_unified_paths() -> None:
    backend = CapabilityTemplateBackend()
    assert backend.select_template("建立一个ADCS闭环仿真", {}).capability_id == ADCS
    assert backend.select_template("创建一个整星电源轨道仿真", {}).capability_id == WHOLE
    assert backend.select_template("使用统一运行图创建整星仿真", {}).capability_id == WHOLE
    # A component request remains a component capability rather than being swallowed by the subsystem default.
    assert backend.select_template("反作用轮部件故障仿真", {}).capability_id == "component.reaction_wheel.v1"


def test_governance_api_exposes_product_and_runtime_boundaries(tmp_path: Path) -> None:
    client = TestClient(create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts"))
    response = client.get("/capabilities/governance")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["validation_issues"] == []
    records = {item["capability_id"]: item for item in payload["governance"]["records"]}
    assert records[ADCS]["runtime_origin"] == "basilisk_official_plus_project_sysmodels"
    assert records[WHOLE]["recommended"] is True
    assert records[ADCS_BRIDGE]["runtime_origin"] == "legacy_bridge"


def test_scenario_catalog_recommends_unified_and_not_bridges() -> None:
    payload = list_scenario_templates()
    templates = {item["template_id"]: item for item in payload["templates"]}
    assert templates["adcs_unified_native"]["recommended"] is True
    assert templates["whole_spacecraft_unified_native"]["recommended"] is True
    assert templates["adcs_bsksim_migration"]["recommended"] is False
    assert templates["bsksim_whole_spacecraft_strong_coupling"]["recommended"] is False


def test_native_controller_registry_paths_are_schema_exposed() -> None:
    assert native_controller_capability_ids() == (ADCS, FOUNDATION, WHOLE)
    for capability_id in native_controller_capability_ids():
        form_paths = {field["path"] for field in capability_form_schema(capability_id)["fields"]}
        registered = set(native_controller_contract(capability_id).path_map)
        assert registered <= form_paths


def test_official_controller_plan_rejects_bridge_and_accepts_native() -> None:
    path = "parameters.values.initial_pointing_error_deg"
    distribution = {path: {"distribution": "uniform", "min": 3.0, "max": 9.0}}
    native = _short_template("adcs_unified_native")
    plan = build_basilisk_mc_plan(native, distribution, sample_count=2, seed=77)
    assert plan.eligible is True
    assert plan.status == "runnable_official_controller"

    bridge = dict(native)
    bridge["model"] = dict(native["model"], capability_id=ADCS_BRIDGE)
    blocked = build_basilisk_mc_plan(bridge, distribution, sample_count=2, seed=77)
    assert blocked.eligible is False
    assert blocked.status == "not_eligible"


def test_official_controller_applies_adcs_initial_condition_to_recorder(tmp_path: Path) -> None:
    spec = _short_template("adcs_unified_native")
    path = "parameters.values.initial_pointing_error_deg"
    dispersions = {path: {"distribution": "uniform", "min": 3.0, "max": 9.0}}
    variants = expand_monte_carlo(spec, dispersions, sample_count=2, seed=77)
    report = execute_basilisk_controller_variants(spec, variants, archive_dir=tmp_path / "adcs", thread_count=1)
    check = report["parameter_application_checks"][path]
    assert report["controller_invoked"] is True
    assert report["failure_count"] == 0
    assert check["unique_requested_count"] == 2
    assert check["applied_to_case"] is True
    assert check["recorder_verified"] is True
    assert check["sensitivity_observed"] is True
    assert check["recorder_max_abs_error"] <= 1.0e-9


def test_official_controller_applies_whole_spacecraft_soc_to_recorder(tmp_path: Path) -> None:
    spec = _short_template("whole_spacecraft_unified_native")
    path = "parameters.values.initial_soc"
    dispersions = {path: {"distribution": "uniform", "min": 0.45, "max": 0.80}}
    variants = expand_monte_carlo(spec, dispersions, sample_count=2, seed=77)
    assert variants[0]["simulation_seed"] != variants[1]["simulation_seed"]
    report = execute_basilisk_controller_variants(spec, variants, archive_dir=tmp_path / "whole", thread_count=1)
    check = report["parameter_application_checks"][path]
    assert report["failure_count"] == 0
    assert check["unique_requested_count"] == 2
    assert check["applied"] is True
    assert check["recorder_verified"] is True
    assert check["sensitivity_observed"] is True
    assert "final_battery_soc" in report["output_statistics"]
