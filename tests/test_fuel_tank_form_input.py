"""Regression coverage for native FuelTank form bindings and explicit inputs."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec


CAPABILITY = "component.fuel_tank.v1"
NATIVE = "basilisk_native"


def _native_form():
    form = capability_form_schema(CAPABILITY, physics_backend=NATIVE)["default_form"]
    form["simulation"].update(duration_s=1, sample_s=1, step_s=1)
    return form


@pytest.fixture
def client(tmp_path):
    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )
    with TestClient(app) as test_client:
        yield test_client


def _parse_form(client, form):
    return client.post("/tasks/parse", json={
        "input_kind": "form", "form_data": form, "compile_if_valid": True,
    })


def _config_backed_spec():
    spec = normalize_form_task_spec(_native_form())
    spec["model"]["config"] = spec["parameters"]["values"]
    spec["parameters"]["values"] = {}
    spec["model"]["config"].update(initial_mass_kg=1.234, capacity_kg=2.5)
    spec["provenance"]["fields"]["model.config.initial_mass_kg"] = {
        "source": "user_provided", "evidence": "Explicit config mass",
    }
    return canonicalize_task_spec(spec)


def _project_form(client, spec):
    response = client.post(f"/forms/capabilities/{CAPABILITY}/project", json={"task_spec": spec})
    assert response.status_code == 200, response.text
    return response.json()["projection"]["form_data"]


def test_config_backed_projection_keeps_effective_values_after_first_edit(client):
    spec = _config_backed_spec()
    form = _project_form(client, spec)
    assert form["parameters"]["values"] == spec["model"]["config"]
    response = _parse_form(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    assert response.json()["result"]["task_spec"] == spec

    form["parameters"]["values"]["mass_flow_kg_s"] = 0.002
    response = _parse_form(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    result = response.json()["result"]
    edited = result["task_spec"]
    assert to_runtime_task_spec(edited)["parameters"] == {
        **spec["model"]["config"], "mass_flow_kg_s": 0.002,
    }
    assert edited["provenance"]["fields"]["model.config.initial_mass_kg"] == spec["provenance"]["fields"]["model.config.initial_mass_kg"]
    assert edited["provenance"]["fields"]["model.config.mass_flow_kg_s"]["source"] == "user_explicit"
    assert result["planning"]["resolved_spec"]["implementation_class"] == "official_basilisk_native"


def test_config_backed_projection_preserves_shared_inputs_reopening_reference(client):
    form = _project_form(client, _config_backed_spec())
    reference = capability_form_schema(CAPABILITY)["default_form"]["parameters"]["values"]
    form["parameters"]["values"] = {
        **reference, **form["parameters"]["values"], "physics_backend": "project_reference",
    }
    form["model"]["config"]["physics_backend"] = "project_reference"
    form["outputs"]["plots"] = ["propulsion.fuel_tank.propellant_mass_kg"]
    response = _parse_form(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    result = response.json()["result"]
    assert result["planning"]["resolved_spec"]["implementation_class"] == "project_deterministic_model"
    reference_spec = result["task_spec"]

    reopened = _project_form(client, reference_spec)
    assert reopened["parameters"]["values"] == form["parameters"]["values"]
    assert normalize_form_task_spec(reopened) == reference_spec
    reopened["parameters"]["values"]["mass_flow_kg_s"] = 0.002
    assert to_runtime_task_spec(normalize_form_task_spec(reopened))["parameters"] == {
        **form["parameters"]["values"], "mass_flow_kg_s": 0.002,
    }


@pytest.mark.parametrize("stale_mass", [0.5, True])
def test_projection_uses_parameter_values_over_stale_model_config(client, stale_mass):
    spec = normalize_form_task_spec(_native_form())
    spec["parameters"]["values"]["initial_mass_kg"] = 1.234
    spec["model"]["config"].update(physics_backend="project_reference", initial_mass_kg=stale_mass)
    spec = canonicalize_task_spec(spec)
    form = _project_form(client, spec)
    assert form["parameters"]["values"] == spec["parameters"]["values"]
    assert normalize_form_task_spec(form) == spec
    form["parameters"]["values"]["mass_flow_kg_s"] = 0.002
    rebuilt = normalize_form_task_spec(form)
    assert rebuilt["model"]["config"] == spec["model"]["config"]
    assert to_runtime_task_spec(rebuilt)["parameters"] == form["parameters"]["values"]


def test_native_projected_form_rejects_boolean_time_equal_to_base_number(client):
    form = _project_form(client, _config_backed_spec())
    form["simulation"]["step_s"] = True
    response = _parse_form(client, form)
    assert response.status_code == 422, response.text
    assert "step_s" in response.json()["detail"]["message"]


@pytest.mark.parametrize(("config_backed", "edit_path"), [
    (False, ("parameters", "values")),
    (True, ("parameters", "values")),
    (True, ("model", "config")),
])
@pytest.mark.parametrize(("field", "value"), [
    ("capacity_kg", True), ("initial_mass_kg", True), ("mass_flow_kg_s", False),
])
def test_native_projected_form_rejects_boolean_quantity_equal_to_base_number(client, config_backed, edit_path, field, value):
    spec = normalize_form_task_spec(_native_form())
    quantities = {"capacity_kg": 1.0, "initial_mass_kg": 1.0, "mass_flow_kg_s": 0.0}
    spec["parameters"]["values"].update(quantities)
    if config_backed:
        spec["model"]["config"] = spec["parameters"]["values"]
        spec["parameters"]["values"] = {}
    form = _project_form(client, canonicalize_task_spec(spec))
    group, binding = edit_path
    form[group][binding][field] = value

    response = _parse_form(client, form)
    assert response.status_code == 422, response.json().get("ok")
    assert ".".join((*edit_path, field)) in response.json()["detail"]["message"]


def test_raw_native_form_selects_explicit_config_when_values_are_empty(client):
    form = _native_form()
    form["model"]["config"] = form["parameters"]["values"]
    form["parameters"]["values"] = {}
    form["model"]["config"]["initial_mass_kg"] = 1.234
    response = _parse_form(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    spec = response.json()["result"]["task_spec"]
    assert to_runtime_task_spec(spec)["parameters"] == form["model"]["config"]


@pytest.mark.parametrize("field", ["duration_s", "sample_s", "step_s"])
@pytest.mark.parametrize("value", [0, None, True, "1"])
def test_raw_native_form_rejects_explicit_invalid_time_before_defaulting(field, value):
    form = _native_form()
    form["simulation"][field] = value
    with pytest.raises(ValueError, match=field):
        normalize_form_task_spec(form)


@pytest.mark.parametrize(("section", "field", "value"), [
    ((), "capability_id", "component.reaction_wheel.v1"),
    (("model",), "capability_id", "component.reaction_wheel.v1"),
    ((), "task_type", "subsystem"),
    (("model", "target"), "level", "subsystem"),
    (("events",), "faults", ["leak"]),
    (("events",), "transients", [{"effect": "leak"}]),
    (("outputs",), "telemetry_streams", ["pressure"]),
    (("outputs",), "record_fields", ["propulsion.fuel_tank.final_pressure_pa"]),
    ((), "qoi", ["propulsion.fuel_tank.final_pressure_pa"]),
    (("parameters",), "full_pressure_pa", 2500000),
    (("parameters",), "overrides", [{"path": "full_pressure_pa", "value": 2500000}]),
])
def test_raw_native_form_rejects_inputs_that_would_be_dropped(client, section, field, value):
    form = _native_form()
    container = form
    for key in section:
        container = container[key]
    container[field] = deepcopy(value)
    response = _parse_form(client, form)
    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]["message"]


def test_raw_native_form_uses_explicit_solver_step_if_standard_step_omitted(client):
    form = _native_form()
    del form["simulation"]["step_s"]
    form["simulation"]["solver"] = {"step_s": 0.25}
    response = _parse_form(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    simulation = response.json()["result"]["task_spec"]["simulation"]
    assert simulation["step_s"] == simulation["solver"]["step_s"] == 0.25
