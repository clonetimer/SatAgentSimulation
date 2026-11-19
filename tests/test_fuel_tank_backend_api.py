"""Verify public FuelTank form schema selection and template projection."""
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def test_fuel_tank_native_template_projects_to_selected_public_schema(tmp_path):
    capability_id = "component.fuel_tank.v1"
    template_id = "component_fuel_tank_basilisk_native"
    form_path = f"/forms/capabilities/{capability_id}"
    field_prefix = "propulsion.fuel_tank."
    app = create_app(
        runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
        embedded_worker=False, auth_mode="disabled",
    )

    with TestClient(app) as client:
        response = client.get(form_path)
        assert response.status_code == 200, response.text
        reference = response.json()["form_schema"]
        response = client.get(form_path, params={"physics_backend": "basilisk_native"})
        assert response.status_code == 200, response.text
        native = response.json()["form_schema"]
        assert reference["default_form"]["parameters"]["values"]["physics_backend"] == "project_reference"
        assert field_prefix + "pressure_pa" in reference["outputs"]["trace_fields"]
        assert set(native["default_form"]["parameters"]["values"]) == {
            "physics_backend", "initial_mass_kg", "capacity_kg", "mass_flow_kg_s",
        }
        assert field_prefix + "final_pressure_pa" not in native["outputs"]["summary_qoi"]
        assert field_prefix + "pressure_pa" not in native["outputs"]["trace_fields"]
        assert {field_prefix + "mass_rate_kg_s", field_prefix + "max_mass_kg"} <= set(native["outputs"]["trace_fields"])

        response = client.get("/scenario-templates", params={"capability_id": capability_id})
        assert response.status_code == 200, response.text
        assert template_id in {item["template_id"] for item in response.json()["templates"]}
        response = client.post(f"/scenario-templates/{template_id}/instantiate", json={})
        assert response.status_code == 200, response.text
        task_spec = response.json()["task_spec"]
        assert task_spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
        response = client.post(form_path + "/project", json={"task_spec": task_spec})
        assert response.status_code == 200, response.text
        projection = response.json()["projection"]
        assert projection["projection_is_lossless"] is True
        assert projection["form_schema"] == native

        response = client.post("/tasks/parse", json={
            "input_kind": "form", "form_data": projection["form_data"], "compile_if_valid": True,
        })
        assert response.status_code == 200, response.text
        round_trip = response.json()
        assert round_trip["ok"], round_trip
        assert round_trip["result"]["task_spec"] == task_spec
