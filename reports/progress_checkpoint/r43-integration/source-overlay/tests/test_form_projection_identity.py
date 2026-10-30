"""Canonical form projection is an editable view, never a provenance re-creation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sat_sim.api import create_app
from sat_sim.execution_planner import plan_task_spec
from sat_sim.form_schema import capability_form_schema, task_spec_to_form_data
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.unified_agent import normalize_form_task_spec

FIXTURES = Path(__file__).with_name("fixtures") / "form_projection"


def _spec(name):
    if name in {"recorded_create17", "offline_sentinel"}:
        return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))
    form = deepcopy(capability_form_schema(name)["default_form"])
    spec = normalize_form_task_spec(form)
    spec["metadata"]["projection_sentinel"] = {"opaque": [1, {"x": "retained"}]}
    spec["model"]["template"] = "CUSTOM_NON_FORM_TEMPLATE"
    return spec


@pytest.mark.parametrize("name", ["recorded_create17", "offline_sentinel", "component.battery.v1", "component.reaction_wheel.v1"])
def test_no_edit_projection_preserves_full_canonical_and_plan(name):
    spec = canonicalize_task_spec(_spec(name))
    projection = task_spec_to_form_data(spec["model"]["capability_id"], spec)
    rebuilt = normalize_form_task_spec(projection["form_data"])
    assert rebuilt == spec
    assert plan_task_spec(rebuilt).execution_plan == plan_task_spec(spec).execution_plan
    assert "_canonical_projection" not in projection["form_data"]["_canonical_projection"]["task_spec"]


def test_actual_form_edit_only_refreshes_changed_field_and_replans():
    spec = canonicalize_task_spec(_spec("offline_sentinel"))
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["parameters"]["values"]["spacecraft_mass_kg"] = 137.
    rebuilt = normalize_form_task_spec(form)
    assert rebuilt["parameters"]["values"]["spacecraft_mass_kg"] == 137.
    assert rebuilt["simulation"] == spec["simulation"]
    assert rebuilt["metadata"] == spec["metadata"]
    assert rebuilt["provenance"]["pending_confirmations"] == spec["provenance"]["pending_confirmations"]
    fields = rebuilt["provenance"]["fields"]
    assert fields["parameters.values.spacecraft_mass_kg"]["source"] == "user_explicit"
    for path, record in spec["provenance"]["fields"].items():
        if path != "parameters.values.spacecraft_mass_kg":
            assert fields[path] == record
    old_assumption = rebuilt["provenance"]["assumptions"][0]
    assert old_assumption["value"] == 100 and old_assumption["status"] == "rejected"
    assert plan_task_spec(rebuilt).execution_plan != plan_task_spec(spec).execution_plan


def test_form_step_edit_synchronizes_existing_solver_step_only():
    spec = canonicalize_task_spec(_spec("offline_sentinel"))
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["simulation"]["step_s"] = .05
    rebuilt = normalize_form_task_spec(form)
    assert rebuilt["simulation"]["step_s"] == rebuilt["simulation"]["solver"]["step_s"] == .05
    assert {k: v for k, v in rebuilt["simulation"]["solver"].items() if k != "step_s"} == {k: v for k, v in spec["simulation"]["solver"].items() if k != "step_s"}
    assert rebuilt["provenance"]["fields"]["simulation.step_s"]["source"] == "user_explicit"
    assert rebuilt["provenance"]["fields"]["simulation.solver.step_s"]["source"] == "resolver_derived"


def test_fresh_form_retains_explicit_solver_without_canonical_context():
    form = deepcopy(capability_form_schema("component.battery.v1")["default_form"])
    solver = {"method": "rk4", "step_s": .1, "rtol": 2e-7, "atol": 3e-10, "include_endpoint": False, "deterministic_seed": 41}
    form["simulation"].update(solver=solver, step_s=.1, time_base="relative_seconds", time_system="relative_seconds")
    spec = normalize_form_task_spec(form)
    assert spec["simulation"]["solver"] == solver
    assert spec["simulation"]["time_base"] == spec["simulation"]["time_system"] == "relative_seconds"


def _graph(capability):
    nodes = [{"id": name, "type": name, **({"capabilityId": capability} if name == "model" else {})} for name in ("model", "config", "outputs", "code", "run")]
    connections = [("model", "capability", "config", "capability"), ("config", "task", "outputs", "task"), ("outputs", "spec", "code", "spec"), ("outputs", "spec", "run", "spec")]
    return {"schema_version": "sat-sim.visual-graph.v1", "nodes": nodes,
            "edges": [{"id": str(i), "source": a, "sourcePort": ap, "target": b, "targetPort": bp} for i, (a, ap, b, bp) in enumerate(connections)]}


def test_public_graph_and_export_keep_recorded_model_identity(tmp_path):
    spec = canonicalize_task_spec(_spec("recorded_create17"))
    capability = spec["model"]["capability_id"]
    app = create_app(runs_root=tmp_path / "runs", artifacts_root=tmp_path / "api", embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client:
        projection = client.post(f"/forms/capabilities/{capability}/project", json={"task_spec": spec}).json()["projection"]
        response = client.post("/tasks/compile-graph", json={"form_data": projection["form_data"], "graph": _graph(capability)})
        assert response.status_code == 200
        result = response.json()
        assert result["ok"], result
        assert result["result"]["task_spec"] == spec
        expected = plan_task_spec(spec).execution_plan.model_dump(mode="json")
        assert result["result"]["planning"]["execution_plan"] == expected
        exported = client.post("/tasks/export-script", json={"task_spec": result["result"]["task_spec"]})
        assert exported.status_code == 200 and exported.json()["ok"]
        compile(exported.json()["code"], "recorded_gui_export.py", "exec")


def test_mismatched_or_invalid_context_is_rejected_without_fallback():
    spec = _spec("recorded_create17")
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["capability_id"] = form["model"]["capability_id"] = "component.battery.v1"
    with pytest.raises(ValueError, match="FORM_PROJECTION_CAPABILITY_MISMATCH"):
        normalize_form_task_spec(form)
    form = deepcopy(capability_form_schema("component.battery.v1")["default_form"])
    form["_canonical_projection"] = {"schema_version": "bad", "task_spec": spec}
    with pytest.raises(ValueError, match="FORM_PROJECTION_CONTEXT_INVALID"):
        normalize_form_task_spec(form)


def test_solver_step_edit_synchronizes_form_step_and_conflicts_are_rejected():
    spec = _spec("offline_sentinel")
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["simulation"]["solver"]["step_s"] = .05
    rebuilt = normalize_form_task_spec(form)
    assert rebuilt["simulation"]["step_s"] == rebuilt["simulation"]["solver"]["step_s"] == .05
    assert rebuilt["provenance"]["fields"]["simulation.step_s"]["source"] == "resolver_derived"
    form["simulation"]["step_s"] = .04
    with pytest.raises(ValueError, match="FORM_PROJECTION_STEP_CONFLICT"):
        normalize_form_task_spec(form)


def test_context_is_validated_instead_of_trusting_a_client_validation_claim():
    spec = _spec("recorded_create17")
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["_canonical_projection"]["task_spec"]["simulation"]["duration_s"] = -1
    with pytest.raises(ValueError):
        normalize_form_task_spec(form)


def test_recursive_or_extra_context_fields_are_rejected():
    spec = _spec("recorded_create17")
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["_canonical_projection"]["form_data"] = {}
    with pytest.raises(ValueError, match="FORM_PROJECTION_CONTEXT_INVALID"):
        normalize_form_task_spec(form)


def test_explicit_request_overrides_preserve_unrelated_canonical_context():
    spec = canonicalize_task_spec(_spec("offline_sentinel"))
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    rebuilt = normalize_form_task_spec(form, task_id="explicit-new-id", output_root="datasets/new-explicit")
    assert rebuilt["task"]["id"] == "explicit-new-id"
    assert rebuilt["outputs"]["output_root"] == "datasets/new-explicit"
    assert rebuilt["simulation"] == spec["simulation"]
    assert rebuilt["provenance"]["pending_confirmations"] == spec["provenance"]["pending_confirmations"]
    assert rebuilt["provenance"]["fields"]["task.id"]["source"] == "user_explicit"


def _event_spec():
    spec = _spec("recorded_create17")
    event = deepcopy(spec["events"]["faults"][0])
    event.update(id="second-event", start_s=8., magnitude=.33)
    spec["events"]["faults"].append(event)
    for index, item in enumerate(spec["events"]["faults"]):
        for field in ("magnitude", "start_s"):
            spec["provenance"]["fields"][f"events.faults[{index}].{field}"] = {
                "source": "agent_inferred", "evidence": f"Original {item['id']} {field} {item[field]}", "note": "Indexed sentinel"}
            spec["provenance"]["assumptions"].append({"path": f"events.faults[{index}].{field}", "value": item[field],
                "reason": f"Original {item['id']} {field} assumption", "status": "pending_confirmation"})
    return canonicalize_task_spec(spec)


def test_indexed_event_edit_refreshes_only_its_changed_leaf():
    spec = _event_spec()
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["events"]["faults"][0]["magnitude"] = .42
    rebuilt = normalize_form_task_spec(form)
    fields = rebuilt["provenance"]["fields"]
    assert rebuilt["events"]["faults"][0]["magnitude"] == .42
    assert fields["events.faults[0].magnitude"]["source"] == "user_explicit"
    for path in ("events.faults[0].start_s", "events.faults[1].magnitude", "events.faults[1].start_s"):
        assert fields[path] == spec["provenance"]["fields"][path]
    assumptions = {item["path"]: item for item in rebuilt["provenance"]["assumptions"]}
    assert assumptions["events.faults[0].magnitude"]["status"] == "rejected"
    assert assumptions["events.faults[0].magnitude"]["value"] == .17
    assert assumptions["events.faults[0].start_s"]["status"] == "pending_confirmation"


@pytest.mark.parametrize("operation", ["insert", "delete", "reorder", "reorder_and_edit"])
def test_event_structure_changes_relocate_origins_by_unique_event_identity(operation):
    spec = _event_spec()
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    events = form["events"]["faults"]
    if operation == "insert":
        inserted = deepcopy(events[0])
        inserted.update(id="inserted-event", magnitude=.55, start_s=2.)
        events.insert(0, inserted)
    elif operation == "delete":
        events.pop(0)
    else:
        events.reverse()
        if operation == "reorder_and_edit":
            events[1]["magnitude"] = .42
    rebuilt = normalize_form_task_spec(form)
    fields = rebuilt["provenance"]["fields"]
    assumptions = {item["path"]: item for item in rebuilt["provenance"]["assumptions"]}
    old_indices = {item["id"]: index for index, item in enumerate(spec["events"]["faults"])}
    for index, event in enumerate(rebuilt["events"]["faults"]):
        if event["id"] not in old_indices:
            assert fields[f"events.faults[{index}].magnitude"]["source"] == "user_explicit"
            continue
        old_index = old_indices[event["id"]]
        assert fields[f"events.faults[{index}].start_s"] == spec["provenance"]["fields"][f"events.faults[{old_index}].start_s"]
        assert assumptions[f"events.faults[{index}].start_s"]["value"] == event["start_s"]
        assert assumptions[f"events.faults[{index}].start_s"]["status"] == "pending_confirmation"
        if operation == "reorder_and_edit" and event["id"] == "add_fault":
            assert fields[f"events.faults[{index}].magnitude"]["source"] == "user_explicit"
            assert assumptions[f"events.faults[{index}].magnitude"]["value"] == .17
            assert assumptions[f"events.faults[{index}].magnitude"]["status"] == "rejected"
        else:
            assert fields[f"events.faults[{index}].magnitude"] == spec["provenance"]["fields"][f"events.faults[{old_index}].magnitude"]
    if operation == "delete":
        assert not any("Original add_fault" in (record.get("evidence") or "") for record in fields.values())
        retired = [item for item in rebuilt["provenance"]["assumptions"] if "Original add_fault" in item["reason"]]
        assert retired and all(item["path"].startswith("removed:") and item["status"] == "rejected" for item in retired)


@pytest.mark.parametrize("index_style", ["bracket", "dot"])
def test_numeric_array_changes_preserve_unmodified_index_evidence(index_style):
    spec = _spec("recorded_create17")
    spec["metadata"]["sentinel_vector"] = [1., 2., 3.]
    paths = [f"metadata.sentinel_vector[{i}]" if index_style == "bracket" else f"metadata.sentinel_vector.{i}" for i in range(3)]
    for index, path in enumerate(paths):
        spec["provenance"]["fields"][path] = {"source": "agent_inferred", "evidence": f"Original vector {index}"}
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form.setdefault("metadata", {})["sentinel_vector"] = [1., 4., 3.]
    rebuilt = normalize_form_task_spec(form)
    fields = rebuilt["provenance"]["fields"]
    assert fields["metadata.sentinel_vector[1]"]["source"] == "user_explicit"
    assert fields[paths[0]] == spec["provenance"]["fields"][paths[0]]
    assert fields[paths[2]] == spec["provenance"]["fields"][paths[2]]
    if index_style == "dot":
        assert paths[1] not in fields


def test_context_history_cannot_supply_a_cached_execution_plan_or_approval(tmp_path):
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent

    spec = _spec("recorded_create17")
    forged_hash = "f" * 64
    spec["metadata"]["v24_planning"] = {"execution_plan_sha256": forged_hash, "status": "PASS"}
    spec["metadata"]["approval"] = {"approved": True, "task_spec_sha256": forged_hash}
    form = task_spec_to_form_data(spec["model"]["capability_id"], spec)["form_data"]
    form["simulation"]["duration_s"] = 13.
    result = run_unified_agent(UnifiedAgentRequest(input_kind="form", form_data=form, output_dir=tmp_path, compile_if_valid=True))
    assert result.ok and result.compiled is not None
    actual = result.planning.execution_plan.plan_sha256
    assert actual != forged_hash
    assert result.compiled.metadata["v24_planning"]["execution_plan_sha256"] == actual
    form["simulation"]["duration_s"] = -1
    with pytest.raises(ValueError):
        normalize_form_task_spec(form)
