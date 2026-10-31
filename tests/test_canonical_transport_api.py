"""Canonical text must be consumed at every public TaskSpec HTTP boundary."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app, TaskCreateBody, ExperimentOptionsBody
from sat_sim.execution_planner import plan_task_spec
from sat_sim.task_spec import spec_sha256


def _original():
    path = Path(__file__).with_name("fixtures") / "form_projection/recorded_create17.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _js_object(value):
    if type(value) is float and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _js_object(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_js_object(child) for child in value]
    return value


@pytest.fixture
def client(tmp_path):
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "api", embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as result:
        yield result


def _pair(spec):
    return {"task_spec": _js_object(spec), "task_spec_json": json.dumps(spec, ensure_ascii=False, indent=2)}


def _assert_exact(spec, original):
    # Python dict equality alone hides 1.0 -> 1, the actual wire regression.
    assert spec_sha256(spec) == spec_sha256(original)


def _graph(capability):
    nodes = [{"id": kind, "type": kind, **({"capabilityId": capability} if kind == "model" else {})} for kind in ("model", "config", "outputs", "code", "run")]
    edges = [("model", "capability", "config", "capability"), ("config", "task", "outputs", "task"), ("outputs", "spec", "code", "spec"), ("outputs", "spec", "run", "spec")]
    return {"schema_version": "sat-sim.visual-graph.v1", "nodes": nodes, "edges": [{"id": str(i), "source": a, "sourcePort": ap, "target": b, "targetPort": bp} for i, (a, ap, b, bp) in enumerate(edges)]}


def test_public_validate_parse_project_graph_export_preserve_original_plan(client):
    original = _original()
    capability = original["model"]["capability_id"]
    assert spec_sha256(_js_object(original)) != spec_sha256(original)
    validated = client.post("/tasks/validate", json=_pair(original))
    assert validated.status_code == 200, validated.text
    _assert_exact(validated.json()["task_spec"], original)
    _assert_exact(json.loads(validated.json()["task_spec_json"]), original)
    parsed = client.post("/tasks/parse", json={"input_kind": "task_spec", **_pair(original)})
    assert parsed.status_code == 200 and parsed.json()["ok"], parsed.text
    _assert_exact(parsed.json()["result"]["task_spec"], original)
    projection = client.post(f"/forms/capabilities/{capability}/project", json=_pair(original))
    assert projection.status_code == 200, projection.text
    form = _js_object(projection.json()["projection"]["form_data"])
    graph = client.post("/tasks/compile-graph", json={"graph": _graph(capability), "form_data": form})
    assert graph.status_code == 200 and graph.json()["ok"], graph.text
    result = graph.json()["result"]
    _assert_exact(result["task_spec"], original)
    assert result["planning"]["execution_plan"]["plan_sha256"] == plan_task_spec(original).execution_plan.plan_sha256
    exported = client.post("/tasks/export-script", json={"task_spec": _js_object(result["task_spec"]), "task_spec_json": result["task_spec_json"]})
    assert exported.status_code == 200 and exported.json()["ok"], exported.text
    import ast
    assignment = next(node for node in ast.parse(exported.json()["code"]).body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "TASK_SPEC" for target in node.targets))
    _assert_exact(ast.literal_eval(assignment.value), original)


@pytest.mark.parametrize("path", ["/tasks/validate", "/tasks/export-script", "/forms/capabilities/whole_spacecraft.unified_native.v1/project", "/task-center/tasks", "/runs"])
@pytest.mark.parametrize("bad_text", [None, "", 17])
def test_explicit_bad_text_never_falls_back_to_an_otherwise_valid_object(client, path, bad_text):
    response = client.post(path, json={"task_spec": _original(), "task_spec_json": bad_text})
    assert response.status_code == 422, response.text
    assert "CANONICAL_TRANSPORT_" in response.text


def test_binding_is_checked_before_schema_can_coerce_boolean_or_string(client):
    original = _original()
    for different in (True, "12"):
        companion = deepcopy(original)
        companion["simulation"]["duration_s"] = different
        response = client.post("/tasks/validate", json={"task_spec": companion, "task_spec_json": json.dumps(original)})
        assert response.status_code == 422
        assert "CANONICAL_TRANSPORT_" in response.text


def test_task_center_save_load_versions_preserve_text_and_explicit_new_identity(client):
    original = _original()
    saved = client.post("/task-center/tasks", json={**_pair(original), "task_id": "wire-copy", "status": "READY"})
    assert saved.status_code == 200, saved.text
    record = saved.json()["task"]
    assert record["task_spec"]["task"]["id"] == "wire-copy"
    assert record["task_spec"]["provenance"]["fields"]["task.id"]["source"] == "resolver_derived"
    expected = deepcopy(original)
    expected["task"]["id"] = "wire-copy"
    expected["provenance"]["fields"]["task.id"] = record["task_spec"]["provenance"]["fields"]["task.id"]
    _assert_exact(record["task_spec"], expected)
    loaded = client.get("/task-center/tasks/wire-copy").json()["task"]
    _assert_exact(json.loads(loaded["task_spec_json"]), expected)
    updated = client.put("/task-center/tasks/wire-copy", json={"task_spec": _js_object(expected), "task_spec_json": loaded["task_spec_json"], "force_new_version": True})
    assert updated.status_code == 200, updated.text
    version = client.get("/task-center/tasks/wire-copy/versions/2").json()["version"]
    _assert_exact(json.loads(version["task_spec_json"]), expected)


def test_prepare_and_history_use_raw_document_but_execute_remains_hash_bound(client):
    original = _original()
    prepared = client.post("/runs", json={**_pair(original), "run_id": "wire-prepared-only", "execute": False})
    assert prepared.status_code == 200, prepared.text
    history = client.get("/runs/wire-prepared-only")
    assert history.status_code == 200
    document = history.json()["run"]
    _assert_exact(document["task_spec"], original)
    _assert_exact(json.loads(document["task_spec_json"]), original)
    rejection = client.post("/runs/wire-prepared-only/execute-async", json={"execution_plan_sha256": plan_task_spec(original).execution_plan.plan_sha256, **_pair(original)})
    assert rejection.status_code == 422  # Cannot replace a prepared document at execution time.


def test_refinement_and_experiment_bases_use_the_same_authoritative_raw_binding():
    original = _original()
    text = json.dumps(original)
    refined = TaskCreateBody.model_validate({"input_kind": "natural_language", "request_text": "change magnitude", "base_task_spec": _js_object(original), "base_task_spec_json": text})
    _assert_exact(refined.base_task_spec, original)
    experimental = ExperimentOptionsBody.model_validate({"base_task_spec": _js_object(original), "base_task_spec_json": text})
    _assert_exact(experimental.base_task_spec, original)


def test_dictionary_dispersion_options_endpoint_consumes_the_same_raw_base(client, monkeypatch):
    import sat_sim.api as module

    original = _original()
    received = []
    monkeypatch.setattr(module, "dispersion_parameter_options", lambda spec: received.append(spec) or [])
    response = client.post("/experiments/dispersion-options", json={"base_task_spec": _js_object(original), "base_task_spec_json": json.dumps(original)})
    assert response.status_code == 200
    _assert_exact(received[0], original)
    text_only = client.post("/experiments/dispersion-options", json={"base_task_spec_json": json.dumps(original)})
    assert text_only.status_code == 200
    _assert_exact(received[1], original)
    bad = client.post("/experiments/dispersion-options", json={"base_task_spec": original, "base_task_spec_json": None})
    assert bad.status_code == 422 and "CANONICAL_TRANSPORT_" in bad.text


def test_response_drops_a_stale_sidecar_for_an_invalid_draft(client):
    @client.app.get("/wire-test-invalid-draft")
    def invalid_draft():
        return {"ok": False, "task_spec": {"schema_version": "1.0.0"}, "task_spec_json": "stale"}

    response = client.get("/wire-test-invalid-draft")
    assert response.status_code == 200
    assert "task_spec_json" not in response.json()


def test_partial_interactive_contract_is_not_relabelled_as_a_canonical_document(client):
    contract = {"simulation": {"duration_s": 2., "solver": {"step_s": .1}}, "parameters": {"values": {"initial_soc": .7}}}

    @client.app.get("/wire-test-partial-interactive")
    def partial_contract():
        return {"task_spec": contract}

    response = client.get("/wire-test-partial-interactive")
    assert response.status_code == 200
    assert response.json() == {"task_spec": contract}


def test_readonly_diagnostic_fragment_keeps_its_existing_non_executable_contract():
    from sat_sim.api import VisualDiagnoseBody

    fragment = {"model": {"capability_id": "component.reaction_wheel.v1"}}
    body = VisualDiagnoseBody.model_validate({"error_text": "runtime failed", "task_spec": fragment})
    assert body.task_spec == fragment
    assert "simulation" not in body.task_spec
    with pytest.raises(ValueError):
        VisualDiagnoseBody.model_validate({"error_text": "runtime failed", "task_spec": fragment, "task_spec_json": None})


def test_response_transport_limits_fail_instead_of_returning_a_lossy_object(client):
    original = _original()
    original["metadata"]["oversized"] = "x" * (2 * 1024 * 1024)

    @client.app.get("/wire-test-oversized-document")
    def oversized():
        return {"ok": True, "task_spec": original}

    response = client.get("/wire-test-oversized-document")
    assert response.status_code == 422
    assert "CANONICAL_TRANSPORT_" in response.text


def test_json_editor_new_numeric_text_is_a_new_binding_and_legacy_object_still_works(client):
    original = _original()
    edited = deepcopy(original)
    assert type(edited["parameters"]["values"]["orbit_radius_m"]) is float
    edited["parameters"]["values"]["orbit_radius_m"] = int(edited["parameters"]["values"]["orbit_radius_m"])
    response = client.post("/tasks/validate", json=_pair(edited))
    assert response.status_code == 200
    _assert_exact(response.json()["task_spec"], edited)
    assert plan_task_spec(response.json()["task_spec"]).execution_plan.plan_sha256 != plan_task_spec(original).execution_plan.plan_sha256
    legacy = client.post("/tasks/validate", json={"task_spec": original})
    assert legacy.status_code == 200 and legacy.json()["ok"]
