"""Verify drag source quantities survive public form selection and TaskSpec projection."""
from fastapi.testclient import TestClient

from sat_sim.api import create_app


def test_drag_density_outputs_survive_form_round_trip(tmp_path):
    fields = {"orbit.drag_input_density_kg_m3": "kg/m^3", "orbit.drag_density_source_time_s": "s"}
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    path = "/forms/capabilities/whole_spacecraft.unified_native.v1"
    with TestClient(app) as client:
        response = client.get(path)
        response.raise_for_status()
        outputs = response.json()["form_schema"]["outputs"]
        options = {item["name"]: item for item in outputs["trace_options"]}
        for name, unit in fields.items():
            assert name in outputs["trace_fields"]
            assert options[name]["unit"] == unit
            assert options[name]["dtype"] == "number"
            assert options[name]["plottable"]

        response = client.post("/scenario-templates/whole_spacecraft_unified_native/instantiate", json={})
        response.raise_for_status()
        task = response.json()["task_spec"]
        task["parameters"]["values"]["atmosphere_model"] = "nrlmsise00"
        task["outputs"]["plots"] = list(fields)
        response = client.post(path + "/project", json={"task_spec": task})
        response.raise_for_status()
        projection = response.json()["projection"]
        assert projection["projection_is_lossless"]
        response = client.post("/tasks/parse", json={
            "input_kind": "form", "form_data": projection["form_data"], "compile_if_valid": True,
        })
        response.raise_for_status()
        parsed = response.json()
        assert parsed["ok"], parsed
        assert parsed["result"]["task_spec"]["outputs"]["plots"] == list(fields)
