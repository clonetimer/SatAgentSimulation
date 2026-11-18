"""Public native thermal inputs survive normalization without losing intent."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.form_schema import task_spec_to_form_data
from sat_sim.task_models import CanonicalTaskSpec, canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec


CAPABILITY = "component.thermal_node.v1"


def thermal_legacy():
    return {
        "schema_version": "0.1.0", "task_id": "native-thermal-public-input",
        "task_type": "component", "capability_id": CAPABILITY,
        "target": {"level": "component", "name": "thermal_node", "mode": "nominal"},
        "simulation": {"duration_s": 1.0, "sample_s": 0.2, "step_s": 0.1},
        "parameters": {
            "physics_backend": "basilisk_native", "initial_temp_k": 300.0,
            "power_w": 10.0, "surface_area_m2": 0.01, "mass_kg": 1.0,
            "specific_heat_j_per_kg_k": 900.0, "absorptivity": 0.25,
            "emissivity": 0.8, "normal_b": [1.0, 0.0, 0.0],
            "sun_direction_n": [1.0, 0.0, 0.0], "illumination_factor": 0.0,
        },
        "outputs": {"plots": [], "qoi": ["thermal.node.final_temp_k"]},
    }


def thermal_form():
    legacy = thermal_legacy()
    return {
        "capability_id": CAPABILITY, "simulation": legacy["simulation"],
        "parameters": {"values": legacy["parameters"]}, "outputs": legacy["outputs"],
    }


def _set_time(spec, field, value):
    if field == "solver.step_s":
        spec["simulation"].setdefault("solver", {})["step_s"] = value
    else:
        spec["simulation"][field] = value


@pytest.mark.parametrize("path", ["duration_s", "sample_s", "step_s", "solver.step_s"])
@pytest.mark.parametrize("value", [0, None, True, "1"])
@pytest.mark.parametrize("kind", ["legacy", "canonical", "form"])
def test_native_times_reject_explicit_invalid_values_before_defaults(kind, path, value):
    spec = thermal_form() if kind == "form" else thermal_legacy()
    if kind == "canonical":
        spec = canonicalize_task_spec(spec)
    _set_time(spec, path, value)
    normalize = normalize_form_task_spec if kind == "form" else canonicalize_task_spec
    with pytest.raises(ValueError, match=path.replace(".", r"\.")):
        normalize(spec)


def test_native_legacy_keeps_standard_step_and_solver_fallback():
    spec = thermal_legacy()
    assert to_runtime_task_spec(canonicalize_task_spec(spec))["simulation"]["step_s"] == 0.1
    del spec["simulation"]["step_s"]
    spec["simulation"]["solver"] = {"step_s": 0.05}
    assert to_runtime_task_spec(canonicalize_task_spec(spec))["simulation"]["step_s"] == 0.05
    del spec["simulation"]["solver"]
    assert to_runtime_task_spec(canonicalize_task_spec(spec))["simulation"]["step_s"] == 0.2


def test_native_typed_binding_keeps_raw_time_check():
    spec = canonicalize_task_spec(thermal_legacy())
    parsed = CanonicalTaskSpec.model_validate(spec)
    assert CanonicalTaskSpec.model_validate({**spec, "simulation": parsed.simulation}) == parsed
    with pytest.raises(ValueError, match="step_s"):
        CanonicalTaskSpec.model_validate({
            **spec, "model": parsed.model, "parameters": parsed.parameters,
            "simulation": {**spec["simulation"], "step_s": True},
        })


@pytest.mark.parametrize("payload", [
    {"constraints": ["unsupported"]}, {"faults": ["unsupported"]},
    {"events": {"unknown": ["unsupported"]}}, {"modifiers": {"faults": ["unsupported"]}},
    {"task_type": "subsystem"}, {"target": {"level": "component", "name": "battery"}},
])
def test_native_legacy_rejects_information_migration_would_erase(payload):
    spec = thermal_legacy()
    spec.update(payload)
    with pytest.raises(ValueError):
        canonicalize_task_spec(spec)


@pytest.mark.parametrize("name", ["qoi", "record_fields"])
def test_native_legacy_rejects_reference_output_even_with_valid_other_alias(name):
    spec = thermal_legacy()
    other = "record_fields" if name == "qoi" else "qoi"
    spec["outputs"].update({name: ["thermal.node.effective_tau_s"], other: ["thermal.node.temp_k"]})
    with pytest.raises(ValueError, match="effective_tau_s"):
        canonicalize_task_spec(spec)


@pytest.mark.parametrize("kind", ["inline", "override"])
def test_native_legacy_rejects_conflicting_nested_parameter_envelope(kind):
    spec = thermal_legacy()
    spec["parameters"] = {"values": spec["parameters"]}
    if kind == "inline":
        spec["parameters"]["ambient_k"] = 300.0
    else:
        spec["parameters"]["overrides"] = [{"path": "power_w", "value": 20.0}]
    with pytest.raises(ValueError):
        canonicalize_task_spec(spec)


@pytest.fixture
def client(tmp_path):
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as result:
        yield result


def _parse(client, form):
    return client.post("/tasks/parse", json={"input_kind": "form", "form_data": form, "compile_if_valid": True})


@pytest.mark.parametrize("config_backed,edit_binding", [
    (False, ("parameters", "values")), (True, ("parameters", "values")), (True, ("model", "config")),
])
@pytest.mark.parametrize("field,value", [
    ("mass_kg", True), ("power_w", False), ("illumination_factor", False),
    ("normal_b", [True, 0.0, 0.0]), ("sun_direction_n", [1.0, False, 0.0]),
])
def test_projected_native_booleans_cannot_hide_as_unchanged_numbers(client, config_backed, edit_binding, field, value):
    legacy = thermal_legacy()
    legacy["parameters"]["power_w"] = 0.0
    spec = canonicalize_task_spec(legacy)
    if config_backed:
        spec["model"]["config"] = spec["parameters"]["values"]
        spec["parameters"]["values"] = {}
    form = task_spec_to_form_data(CAPABILITY, spec)["form_data"]
    form[edit_binding[0]][edit_binding[1]][field] = value
    response = _parse(client, form)
    assert response.status_code == 422, response.text
    assert ".".join((*edit_binding, field)) in response.json()["detail"]["message"]


def test_config_backed_public_edit_retains_backend_values_and_provenance(client):
    spec = canonicalize_task_spec(thermal_legacy())
    spec["model"]["config"] = spec["parameters"]["values"]
    spec["parameters"]["values"] = {}
    spec["model"]["config"]["mass_kg"] = 1.234
    spec["provenance"]["fields"]["model.config.mass_kg"] = {"source": "user_provided", "evidence": "Declared thermal mass"}
    spec = canonicalize_task_spec(spec)
    form = task_spec_to_form_data(CAPABILITY, spec)["form_data"]
    assert normalize_form_task_spec(form) == spec
    form["parameters"]["values"]["power_w"] = 20.0
    response = _parse(client, form)
    assert response.status_code == 200 and response.json()["ok"], response.text
    edited = response.json()["result"]["task_spec"]
    assert to_runtime_task_spec(edited)["parameters"] == {**spec["model"]["config"], "power_w": 20.0}
    assert edited["provenance"]["fields"]["model.config.mass_kg"] == spec["provenance"]["fields"]["model.config.mass_kg"]
    assert edited["outputs"]["plots"] == []


def test_inactive_config_does_not_override_native_values_or_get_revalidated():
    spec = canonicalize_task_spec(thermal_legacy())
    spec["model"]["config"] = {"physics_backend": "project_reference", "mass_kg": True, "normal_b": [True, False, 0]}
    original_config = deepcopy(spec["model"]["config"])
    form = task_spec_to_form_data(CAPABILITY, spec)["form_data"]
    form["parameters"]["values"]["power_w"] = 20.0
    edited = normalize_form_task_spec(form)
    assert edited["model"]["config"] == original_config
    assert to_runtime_task_spec(edited)["parameters"]["physics_backend"] == "basilisk_native"


@pytest.mark.parametrize("payload", [
    {"events": {"faults": ["unsupported"]}}, {"modifiers": {"faults": ["unsupported"]}},
    {"mode": "fault"}, {"record_fields": ["thermal.node.temp_k"]},
    {"parameter_values": {"ambient_k": 250}}, {"task_type": "subsystem"},
])
def test_raw_native_form_rejects_discarded_or_conflicting_request(payload):
    form = thermal_form()
    form.update(payload)
    with pytest.raises(ValueError):
        normalize_form_task_spec(form)


@pytest.mark.parametrize("alias", ["parameter_values", "inline"])
@pytest.mark.parametrize("field,value", [("mass_kg", True), ("normal_b", [True, False, False])])
def test_raw_native_form_rejects_booleans_in_duplicate_parameter_aliases(alias, field, value):
    form = thermal_form()
    duplicate = {**form["parameters"]["values"], field: value}
    if alias == "parameter_values":
        form[alias] = duplicate
    else:
        form["parameters"].update(duplicate)
    with pytest.raises(ValueError, match=field):
        normalize_form_task_spec(form)


def test_reference_thermal_time_migration_and_effect_inputs_remain_compatible():
    spec = thermal_legacy()
    spec["parameters"] = {"physics_backend": "project_reference", "ambient_k": 295.0, "tau_s": 12.0}
    spec["simulation"].update(duration_s=0, sample_s=0, step_s=None)
    canonical = canonicalize_task_spec(spec)
    assert canonical["simulation"]["duration_s"] == 300.0
    assert canonical["simulation"]["step_s"] == canonical["simulation"]["sample_s"] == 10.0
    assert canonical["parameters"]["values"] == spec["parameters"]
