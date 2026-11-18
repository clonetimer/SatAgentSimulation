"""Native thermal selection and the final exported data share one contract."""
import ast
import csv
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.execution_planner import plan_task_spec
from sat_sim.script_exporter import export_runner_script
from sat_sim.task_models import to_runtime_task_spec


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY = "component.thermal_node.v1"
PREFIX = "thermal.node."
EXAMPLE = ROOT / "examples/component_thermal_node_basilisk_native.yaml"


def _spec():
    return yaml.safe_load(EXAMPLE.read_text())


def test_public_native_template_uses_selected_schema_and_lossless_projection(tmp_path):
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "artifacts", embedded_worker=False, auth_mode="disabled")
    path = f"/forms/capabilities/{CAPABILITY}"
    with TestClient(app) as client:
        reference = client.get(path).json()["form_schema"]
        native = client.get(path, params={"physics_backend": "basilisk_native"}).json()["form_schema"]
        assert "surface_area_m2" not in reference["default_form"]["parameters"]["values"]
        assert "ambient_k" in reference["default_form"]["parameters"]["values"]
        assert set(native["default_form"]["parameters"]["values"]) == set(_spec()["parameters"]["values"])
        assert PREFIX + "effective_tau_s" not in native["outputs"]["trace_fields"]
        assert PREFIX + "measured_temp_k" not in native["outputs"]["trace_fields"]
        assert PREFIX + "input_power_w" in native["outputs"]["trace_fields"]
        response = client.post("/scenario-templates/component_thermal_node_basilisk_native/instantiate", json={})
        assert response.status_code == 200, response.text
        spec = response.json()["task_spec"]
        assert spec["parameters"]["values"] == _spec()["parameters"]["values"]
        response = client.post(path + "/project", json={"task_spec": spec})
        assert response.status_code == 200, response.text
        projection = response.json()["projection"]
        assert projection["projection_is_lossless"] and projection["form_schema"] == native
        response = client.post("/tasks/parse", json={"input_kind": "form", "form_data": projection["form_data"], "compile_if_valid": True})
        assert response.status_code == 200 and response.json()["ok"], response.text
        assert response.json()["result"]["task_spec"] == spec


@pytest.mark.parametrize("legacy", [False, True], ids=["canonical-example", "legacy-solver-step"])
def test_sealed_bundle_keeps_actual_temperature_channels_and_selected_contract(tmp_path, legacy):
    pytest.importorskip("Basilisk")
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle

    spec = _spec()
    spec["simulation"]["sample_s"] = 0.2
    spec["outputs"]["telemetry_streams"] = [{"stream_id": "temperature", "sample_s": 0.4, "fields": [PREFIX + "temp_*"], "format": "csv"}]
    if legacy:
        spec = to_runtime_task_spec(spec)
        spec["simulation"].pop("step_s", None)
        spec["simulation"]["solver"] = {"step_s": 0.1}
    planning = plan_task_spec(spec)
    assert planning.ok, planning.validation.to_dict()
    prepared = prepare_run(spec, output_root=tmp_path)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED", result.run_record
    assert result.validation.result.value == "PASS" and result.run_record.sealed
    assert verify_run_bundle(prepared.bundle_root)["ok"]
    root = Path(prepared.bundle_root)
    metadata = result.summary["adapter_metadata"]
    assert metadata["native_provenance"] == "Basilisk.simulation.sensorThermal.SensorThermal"
    assert metadata["native_recorder_samples"] == 101 and metadata["native_step_s"] == 0.1
    assert len(result.trace_rows) == 51
    assert result.summary["qoi"][PREFIX + "initial_temp_k"] == 300.0
    assert result.summary["qoi"][PREFIX + "final_temp_k"] == pytest.approx(300.06961637618775, abs=2e-12)
    contract = json.loads((root / "runtime/capability_contract.json").read_text())["contract"]
    assert contract["selected_physics_backend"] == "basilisk_native"
    assert set(contract["parameters"]) == set(_spec()["parameters"]["values"])
    assert {row["name"] for row in contract["outputs"]["summary"]} == set(result.summary["qoi"])
    assert PREFIX + "effective_tau_s" not in {row["name"] for row in contract["outputs"]["trace"]}
    with (root / "results/telemetry.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 51
    for actual, recorded in zip(rows, result.trace_rows):
        assert float(actual["time_s"]) == recorded["time_s"]
        assert float(actual[PREFIX + "temp_c"]) == recorded[PREFIX + "temp_c"]
        assert float(actual[PREFIX + "temp_k"]) == float(actual[PREFIX + "temp_c"]) + 273.15
        assert float(actual[PREFIX + "input_power_w"]) == 10.0
    telemetry = root / "results/dataset/telemetry"
    manifest = json.loads((telemetry / "multi_rate_manifest.json").read_text())
    assert set(manifest["streams"][0]["fields"]) == {"time_s", PREFIX + "temp_c", PREFIX + "temp_k"}
    assert manifest["interpolation_used"] is False
    with (telemetry / "streams/temperature.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected = result.trace_rows[::2]
    assert len(rows) == len(expected) == 26
    for actual, recorded in zip(rows, expected):
        for field in ("time_s", PREFIX + "temp_c", PREFIX + "temp_k"):
            assert float(actual[field]) == recorded[field]


@pytest.mark.parametrize("field", [PREFIX + "effective_tau_s", PREFIX + "measured_temp_k", PREFIX + "temp_?"])
def test_telemetry_rejects_unavailable_fields_or_unsupported_globs(field):
    spec = _spec()
    spec["outputs"]["telemetry_streams"] = [{"stream_id": "bad", "sample_s": 0.2, "fields": [field]}]
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any("telemetry_streams" in issue.path for issue in planning.validation.errors)


@pytest.mark.parametrize("legacy", [False, True])
def test_exported_python_keeps_native_binding_parameters_and_recorded_temperature(tmp_path, legacy):
    pytest.importorskip("Basilisk")
    spec = _spec()
    if legacy:
        spec = to_runtime_task_spec(spec)
    spec_path = tmp_path / "task.json"
    spec_path.write_text(json.dumps(spec))
    dataset = tmp_path / "dataset"
    exported = export_runner_script(spec_path, tmp_path / "thermal.py", kind="capability-python", output_root=dataset)
    assignments = {node.targets[0].id: node.value for node in ast.parse(exported.output_path.read_text()).body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
    binding = ast.literal_eval(assignments["SOURCE_BINDING"])
    assert binding["basilisk_required"] is True
    assert binding["mode"] == "basilisk_native"
    embedded = ast.literal_eval(assignments["TASK_SPEC"])
    assert set(embedded["parameters"]["values"]) == set(_spec()["parameters"]["values"])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT))), "PYTHONUTF8": "1"}
    completed = subprocess.run([sys.executable, str(exported.output_path)], cwd=tmp_path, env=env,
                               text=True, capture_output=True, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads((dataset / "summary.json").read_text())
    assert summary["physics_backend"] == "basilisk_native"
    assert summary["qoi"][PREFIX + "final_temp_k"] == pytest.approx(300.06961637618775, abs=2e-12)
    with (dataset / "trace.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 101
    assert float(rows[-1][PREFIX + "temp_k"]) == float(rows[-1][PREFIX + "temp_c"]) + 273.15
