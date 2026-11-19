"""Selected physics backends bind only parameters and observables they implement."""
from copy import deepcopy
import json

import pytest

from sat_sim.capability_registry import get_capability
from sat_sim.execution_planner import plan_task_spec
from sat_sim.form_schema import capability_form_schema, task_spec_to_form_data
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec

CID = "component.fuel_tank.v1"
PREFIX = "propulsion.fuel_tank."


def _native_spec():
    form = deepcopy(capability_form_schema(CID)["default_form"])
    form["simulation"].update(duration_s=1.0, sample_s=.1, step_s=.01)
    form["parameters"]["values"] = {
        "physics_backend": "basilisk_native", "capacity_kg": 3.,
        "initial_mass_kg": 2., "mass_flow_kg_s": .001,
    }
    form["outputs"].update(qoi=[], plots=[])
    return normalize_form_task_spec(form)


def test_native_default_planning_binds_mass_without_pressure():
    planning = plan_task_spec(_native_spec())
    assert planning.ok, planning.validation.to_dict()
    fields = {item.bound_field for item in planning.resolved_spec.output_bindings}
    assert fields == {PREFIX + name for name in ("initial_mass_kg", "final_mass_kg", "consumed_mass_kg")}
    assert planning.resolved_spec.implementation_class == "official_basilisk_native"


def test_selected_forms_preserve_cached_reference_contract():
    before = deepcopy(get_capability(CID).data)
    native = capability_form_schema(CID, physics_backend="basilisk_native")
    reference = capability_form_schema(CID)
    assert "full_pressure_pa" not in native["default_form"]["parameters"]["values"]
    assert PREFIX + "pressure_pa" not in native["outputs"]["trace_fields"]
    assert PREFIX + "mass_rate_kg_s" in native["outputs"]["trace_fields"]
    assert native["default_form"]["parameters"]["values"]["physics_backend"] == "basilisk_native"
    assert PREFIX + "pressure_pa" in reference["outputs"]["trace_fields"]
    assert get_capability(CID).data == before
    assert native["event_catalog"]["faults"] == []


@pytest.mark.parametrize("section,field", [("qoi", "final_pressure_pa"), ("plots", "pressure_pa"), ("plots", "*pressure*")])
def test_native_rejects_pressure_outputs_before_execution(section, field):
    spec = _native_spec()
    spec["outputs"][section] = [PREFIX + field]
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any("OUTPUT" in issue.code.upper() or "QOI" in issue.code.upper() for issue in planning.validation.errors)


@pytest.mark.parametrize("key,value", [("full_pressure_pa", 2500000.), ("dry_pressure_pa", 150000.)])
def test_native_does_not_mark_reference_pressure_parameters_consumed(key, value):
    spec = _native_spec()
    spec["parameters"]["values"][key] = value
    planning = plan_task_spec(spec)
    assert not planning.ok
    assert any(issue.code == "PARAMETER_PATH_UNCONSUMED" and key in issue.path for issue in planning.validation.errors)


def test_profile_selection_uses_effective_runtime_parameters_and_keeps_form_identity():
    from sat_sim.capability_backends import get_task_capability
    spec = _native_spec()
    spec["model"]["config"] = {"physics_backend": "project_reference"}
    assert "full_pressure_pa" not in get_task_capability(spec).data["parameters"]
    projection = task_spec_to_form_data(CID, spec)
    assert "parameters.values.full_pressure_pa" not in projection["projected_field_paths"]
    assert normalize_form_task_spec(projection["form_data"]) == spec


def test_unknown_backend_fails_and_existing_capability_contract_is_unchanged():
    from sat_sim.capability_backends import select_capability_backend
    with pytest.raises(ValueError, match="backend"):
        select_capability_backend(get_capability(CID), "typo")
    rw = get_capability("component.reaction_wheel.v1")
    assert select_capability_backend(rw, "basilisk_native").data == rw.data


def test_native_contract_is_preserved_in_compilation_and_bundle_snapshot(tmp_path):
    from sat_sim.run_bundle import prepare_run
    prepared = prepare_run(_native_spec(), output_root=tmp_path)
    from pathlib import Path
    root = Path(prepared.bundle_root)
    contract = json.loads((root / "runtime/capability_contract.json").read_text())["contract"]
    assert "full_pressure_pa" not in contract["parameters"]
    assert PREFIX + "pressure_pa" not in {row["name"] for row in contract["outputs"]["trace"]}
    assert contract["selected_physics_backend"] == "basilisk_native"
    compiled = json.loads((root / "runtime/compiled_task.json").read_text())
    assert compiled["metadata"]["source_binding"]["basilisk_required"] is True


def test_native_standard_and_solver_steps_remain_visible_for_conflict_validation():
    spec = _native_spec()
    spec["simulation"]["solver"] = {"step_s": .02}
    runtime = to_runtime_task_spec(spec)
    assert runtime["simulation"]["step_s"] == .01
    assert runtime["simulation"]["solver"]["step_s"] == .02
    assert not plan_task_spec(spec).ok


def test_native_template_uses_selected_backend_defaults():
    from sat_sim.scenario_templates import instantiate_scenario_template
    spec = instantiate_scenario_template("component_fuel_tank_basilisk_native")
    assert spec["parameters"]["values"]["physics_backend"] == "basilisk_native"
    assert "full_pressure_pa" not in spec["parameters"]["values"]
    assert not any("pressure" in name for section in ("qoi", "plots") for name in spec["outputs"][section])
    assert plan_task_spec(spec).ok


def test_native_rejects_unavailable_telemetry_stream_and_conflicting_flat_qoi():
    from sat_sim.capability_backends import validate_backend_output_requests
    runtime = to_runtime_task_spec(_native_spec())
    runtime["outputs"].update(qoi=[PREFIX + "final_mass_kg"], record_fields=[PREFIX + "final_pressure_pa"])
    assert validate_backend_output_requests(runtime)
    spec = _native_spec()
    spec["outputs"]["telemetry_streams"] = [{"stream_id": "pressure", "sample_s": .1, "fields": [PREFIX + "pressure_pa"], "format": "csv"}]
    assert not plan_task_spec(spec).ok


def test_native_dictionary_audits_all_profiles_without_silencing_unit_conflicts():
    from sat_sim.workbench_quantity_names import bind_quantity_dictionary
    contract = deepcopy(get_capability(CID).data)
    bound = bind_quantity_dictionary(contract)
    assert not bound["unreviewed_fields"] and not bound["unreviewed_parameters"]
    native = contract["backend_profiles"]["basilisk_native"]["outputs"]["trace"]
    native[0]["unit"] = "g"
    with pytest.raises(ValueError, match="BACKEND_FIELD_CONFLICT"):
        bind_quantity_dictionary(contract)


@pytest.mark.parametrize("outputs", [[], {"plots": "pressure"}, {"qoi": 2}, {"telemetry_streams": [None]}, {"telemetry_streams": [{"fields": "pressure"}]}])
def test_direct_backend_output_validation_returns_structured_errors(outputs):
    from sat_sim.capability_backends import validate_backend_output_requests
    runtime = to_runtime_task_spec(_native_spec())
    runtime["outputs"] = outputs
    issues = validate_backend_output_requests(runtime)
    assert issues and all(issue.severity == "error" for issue in issues)


def test_native_does_not_consume_unapplied_parameter_override():
    spec = _native_spec()
    spec["parameters"]["overrides"] = [{"path": "parameters.values.mass_flow_kg_s", "value": .002}]
    assert not plan_task_spec(spec).ok


@pytest.mark.parametrize("solver", [None, {"step_s": .15}])
def test_legacy_native_fuel_preserves_explicit_standard_step(solver):
    legacy = to_runtime_task_spec(_native_spec())
    legacy["simulation"] = {"duration_s": .3, "sample_s": .3, "step_s": .1}
    if solver is not None:
        legacy["simulation"]["solver"] = solver
    canonical = canonicalize_task_spec(legacy)
    assert canonical["simulation"]["step_s"] == .1
    assert to_runtime_task_spec(canonical)["simulation"]["step_s"] == .1
    assert plan_task_spec(legacy).ok is (solver is None)


def test_legacy_explicit_step_reaches_the_sealed_native_recorder(tmp_path):
    from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
    legacy = to_runtime_task_spec(_native_spec())
    legacy["simulation"] = {"duration_s": .3, "sample_s": .3, "step_s": .1}
    prepared = prepare_run(legacy, output_root=tmp_path)
    result = execute_prepared_run(prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256)
    assert result.run_record.status.value == "SUCCEEDED"
    assert result.validation.result.value == "PASS" and result.run_record.sealed
    assert verify_run_bundle(prepared.bundle_root)["ok"]
    assert result.summary["adapter_metadata"]["native_step_s"] == .1
    assert result.summary["adapter_metadata"]["native_recorder_samples"] == 4
    assert len(result.trace_rows) == 2


@pytest.mark.parametrize("mutate", [
    lambda spec: spec.update(task_type="subsystem"),
    lambda spec: spec["target"].update(level="subsystem"),
    lambda spec: spec.update(events={"faults": [{"effect": "leak"}]}),
    lambda spec: spec.update(constraints=[{"constraint_type": "mass_limit"}]),
    lambda spec: spec.update(faults=["leak"]),
    lambda spec: spec["parameters"].update(overrides=[{"path": "mass_flow_kg_s", "value": .002}]),
])
def test_legacy_native_inputs_cannot_be_erased_into_valid_requests(mutate):
    legacy = to_runtime_task_spec(_native_spec())
    mutate(legacy)
    assert not plan_task_spec(legacy).ok
