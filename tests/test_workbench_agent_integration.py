from __future__ import annotations

from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.workbench_integration import (
    agent_tool_surface,
    experiment_design_options,
    list_workbench_fault_models,
    preview_experiment_plan,
)


def _adcs_spec():
    return instantiate_scenario_template("adcs_pointing")


def test_fault_models_are_grouped_for_agent_and_workbench():
    payload = list_workbench_fault_models(capability_id="subsystem.adcs_bsksim.v1")
    assert payload["ok"] is True
    categories = {item["category"] for item in payload["fault_models"]}
    assert {"fault", "degradation", "constraint"}.issubset(categories)
    assert any(item["effect"] == "adcs_reaction_wheel_speed_limit" for item in payload["fault_models"])
    assert all("ui_group" in item and item["expected_observables"] for item in payload["fault_models"])


def test_experiment_options_and_preview_are_schema_backed():
    spec = _adcs_spec()
    options = experiment_design_options(spec)
    assert options["ok"] is True
    assert options["sweep_options"]
    chosen = next((item for item in options["sweep_options"] if item["path"] == "simulation.duration_s"), options["sweep_options"][0])
    preview = preview_experiment_plan(
        base_task_spec=spec,
        experiment_type="sweep",
        sweep={chosen["path"]: [20, 40]},
        assertions=[{"metric": "status", "operator": "==", "value": "PASS"}],
    )
    assert preview["variant_count"] == 2
    assert preview["preview"][0]["parameters"][chosen["path"]] == 20
    assert preview["assertions"]


def test_agent_tool_surface_forbids_unconstrained_generation():
    surface = agent_tool_surface()
    names = {item["name"] for item in surface["tools"]}
    assert {"list_fault_models", "create_monte_carlo_plan", "validate_experiment_plan"}.issubset(names)
    assert "invent_fault_model" in surface["forbidden"]


def test_api_exposes_fault_catalog_and_experiment_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("SAT_SIM_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    client = TestClient(create_app())
    spec = _adcs_spec()
    fault = client.get("/fault-environment/catalog", params={"capability_id": "subsystem.adcs_bsksim.v1"})
    assert fault.status_code == 200
    assert fault.json()["count"] >= 3
    tools = client.get("/agent/tools")
    assert tools.status_code == 200
    opts = client.post("/experiments/options", json={"base_task_spec": spec})
    assert opts.status_code == 200
    path = next(item["path"] for item in opts.json()["sweep_options"] if item["path"] == "simulation.duration_s")
    plan = client.post("/experiments/plan", json={"base_task_spec": spec, "experiment_type": "sweep", "sweep": {path: [10, 20]}})
    assert plan.status_code == 200
    assert plan.json()["variant_count"] == 2
