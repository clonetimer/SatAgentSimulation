from __future__ import annotations

from copy import deepcopy

from sat_sim.a3r.resolution import resolve_attitude_control_execution
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_spec import load_task_spec
from sat_sim.unified_execution import adapter_key_for_compiled, build_execution_request


def _nominal_spec() -> dict:
    spec = deepcopy(load_task_spec("examples/whole_spacecraft_unified_native_nominal.yaml").data)
    spec["task_id"] = "a3r_resolution_test"
    spec["simulation"].update({"duration_s": 4.0, "step_s": 0.2, "sample_s": 1.0})
    spec["outputs"]["output_root"] = "runs/a3r_resolution_test"
    return spec


def test_recommended_whole_spacecraft_capability_routes_to_new_adapter() -> None:
    compiled = compile_task_spec(_nominal_spec())
    assert adapter_key_for_compiled(compiled) == "basilisk.attitude_control_graph"
    assert compiled.metadata["model_asset_execution"]["model_ref"] == "spacecraft.demo_satellite@1"


def test_non_model_asset_capability_uses_registered_adapter() -> None:
    spec = deepcopy(load_task_spec("examples/component_battery_capability_nominal.yaml").data)
    compiled = compile_task_spec(spec)
    assert adapter_key_for_compiled(compiled) == "capability.registered_adapter"


def test_a3_execution_request_has_no_opaque_legacy_payload() -> None:
    spec = _nominal_spec()
    compiled = compile_task_spec(spec)
    request = build_execution_request(compiled, spec, write_dataset=False)
    assert request.adapter_key == "basilisk.attitude_control_graph"
    assert request.legacy_payload is None
    assert str(request.model_ref) == "spacecraft.demo_satellite@1"
    assert str(request.implementation_id) == "spacecraft.demo_satellite.basilisk_unified@1"
    assert request.metadata["legacy_route"] is False
    assert request.metadata["bound_model_graph"]["binding_set_sha256"]


def test_object_calls_resolve_through_action_and_effect_bindings() -> None:
    spec = deepcopy(load_task_spec("examples/whole_spacecraft_attitude_control_a3r_object_calls.yaml").data)
    resolution = resolve_attitude_control_execution(spec)
    assert resolution.bound_graph.resolved_actions[0]["target_input"] == "power_manager.safe_mode_request"
    assert resolution.bound_graph.resolved_effects[0]["effect_id"] == "rw_jamming"
    modifiers = resolution.runtime_task_spec["modifiers"]
    assert modifiers["constraints"][0]["constraint_type"] == "power_safe_mode_threshold"
    assert modifiers["faults"][0]["fault_type"] == "adcs_rw_jamming"


def test_unbound_object_call_is_audited_not_executed() -> None:
    spec = _nominal_spec()
    spec.setdefault("metadata", {})["object_calls"] = [{"id": "unknown", "ref": "spacecraft.attitude_control.unknown_action"}]
    resolution = resolve_attitude_control_execution(spec)
    assert not resolution.bound_graph.resolved_actions
    assert resolution.diagnostics[0]["code"] == "A3R_OBJECT_CALL_NOT_BOUND"
