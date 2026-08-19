from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from sat_sim.a5r.resolution import resolve_subsystem_execution
from sat_sim.task_compiler import compile_task_spec
from sat_sim.task_spec import load_task_spec, TaskSpecError
from sat_sim.unified_execution import adapter_key_for_compiled, build_execution_request
from sat_sim_model_assets.subsystem_verticals import (
    COMM_BUNDLE,
    EPS_BUNDLE,
    PROPULSION_BUNDLE,
    SUBSYSTEM_BUNDLES,
    THERMAL_BUNDLE,
)


def _load(name: str) -> dict:
    return deepcopy(load_task_spec(Path("examples") / name).data)


def _thermal_spec() -> dict:
    return {
        "schema_version": "1.0.0",
        "task": {"id": "a5r_thermal_source_native", "name": "A5R thermal source native"},
        "simulation": {
            "level": "subsystem", "subsystem": "thermal", "duration_s": 30.0,
            "step_s": 5.0, "sample_s": 5.0, "backend": "python",
        },
        "parameters": {"profile": "demo", "values": {"initial_battery_temp_k": 290.0}},
        "outputs": {"output_root": "runs/a5r_thermal", "qoi": ["thermal.source_native.battery_temp_k"]},
        "assurance": {"parameter_profile": "demo", "allow_proxy": False},
        "model": {"capability_id": THERMAL_BUNDLE.capability_id, "target": {"level": "subsystem", "name": "thermal", "mode": "nominal"}},
    }


SPECS = {
    EPS_BUNDLE.adapter_key: lambda: _load("subsystem_eps_unified_native_nominal.yaml"),
    COMM_BUNDLE.adapter_key: lambda: _load("subsystem_comm_data_unified_native_nominal.yaml"),
    THERMAL_BUNDLE.adapter_key: _thermal_spec,
    PROPULSION_BUNDLE.adapter_key: lambda: _load("subsystem_propulsion_unified_native_nominal.yaml"),
}


@pytest.mark.parametrize("bundle", SUBSYSTEM_BUNDLES, ids=lambda b: b.capability_id)
def test_a5r_resolution_builds_auditable_bound_graph(bundle) -> None:  # noqa: ANN001
    spec = SPECS[bundle.adapter_key]()
    resolution = resolve_subsystem_execution(bundle.adapter_key, spec)
    assert not [item for item in resolution.diagnostics if item["severity"] == "error"]
    assert resolution.bound_graph.graph_ref == bundle.graph.graph_ref
    assert resolution.bound_graph.graph_sha256 == bundle.graph.content_sha256
    assert resolution.bound_graph.parameter_set_sha256 == bundle.parameter_set.content_sha256
    assert resolution.bound_graph.binding_set_sha256 == bundle.binding_set.content_sha256
    assert resolution.bound_graph.capability_projection_sha256 == bundle.projection.content_sha256
    assert set(bundle.required_outputs) <= set(resolution.bound_graph.requested_outputs)
    assert resolution.bound_graph.resolved_effects[0]["effect_id"] == "nominal"


@pytest.mark.parametrize("bundle", SUBSYSTEM_BUNDLES, ids=lambda b: b.capability_id)
def test_a5r_compiler_routes_subsystems_without_legacy(bundle) -> None:  # noqa: ANN001
    spec = SPECS[bundle.adapter_key]()
    compiled = compile_task_spec(spec)
    request = build_execution_request(compiled, spec, write_dataset=False)
    assert adapter_key_for_compiled(compiled) == bundle.adapter_key
    assert request.adapter_key == bundle.adapter_key
    assert request.legacy_payload is None
    assert request.metadata["legacy_route"] is False
    assert request.metadata["bound_model_graph"]["graph_sha256"] == bundle.graph.content_sha256


def test_a5r_focused_subsystem_events_fail_closed_before_runtime() -> None:
    spec = _load("subsystem_eps_unified_native_nominal.yaml")
    spec["events"] = {
        "faults": [{
            "id": "unsupported_fault", "target": "eps.battery", "effect": "open_circuit",
            "start_s": 1.0, "magnitude": 1.0,
        }],
        "degradations": [], "constraints": [],
    }
    resolution = resolve_subsystem_execution(EPS_BUNDLE.adapter_key, spec)
    errors = [item for item in resolution.diagnostics if item["severity"] == "error"]
    assert any(item["code"] == "A5R_EFFECT_NOT_SUPPORTED" for item in errors)


def test_a5r_declared_propulsion_fault_is_bound_for_runtime() -> None:
    spec = _load("subsystem_propulsion_unified_native_nominal.yaml")
    spec["target"]["mode"] = "fault"
    spec["modifiers"] = {
        "faults": [{
            "modifier_id": "feed_failure",
            "target": "propulsion",
            "fault_type": "thrust_or_feed_failure",
            "onset_time_s": 0.0,
            "severity": 1.0,
        }],
        "degradations": [],
        "constraints": [],
    }
    resolution = resolve_subsystem_execution(PROPULSION_BUNDLE.adapter_key, spec)
    errors = [item for item in resolution.diagnostics if item["severity"] == "error"]
    assert errors == []
    assert resolution.bound_graph.resolved_effects[0]["effect_id"] == "thrust_or_feed_failure"
