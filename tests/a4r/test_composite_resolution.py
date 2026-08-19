from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from sat_sim.a4r.resolution import COMPOSITE_REQUIRED_OUTPUTS, resolve_composite_digital_twin_execution
from sat_sim.parameter_consumption import audit_parameter_consumption
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_BINDINGS,
    COMPOSITE_GRAPH,
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_PARAMETER_SET,
    COMPOSITE_PROJECTION,
    COMPOSITE_SPACECRAFT_REF,
    REQUIRED_RUNTIME_COUPLINGS,
    coupling_ids_from_graph,
)


ROOT = Path(__file__).resolve().parents[2]


def _example(name: str) -> dict:
    return yaml.safe_load((ROOT / "examples" / name).read_text(encoding="utf-8"))


def test_nominal_resolution_binds_composite_model_graph_without_legacy_payload() -> None:
    resolution = resolve_composite_digital_twin_execution(
        _example("whole_spacecraft_composite_digital_twin_nominal.yaml"),
        requested_outputs=("custom.metric",),
    )
    bound = resolution.bound_graph

    assert bound.graph_ref == COMPOSITE_GRAPH.graph_ref
    assert bound.root_model_ref == COMPOSITE_SPACECRAFT_REF
    assert bound.implementation_id == COMPOSITE_IMPLEMENTATION
    assert bound.parameter_set_id == COMPOSITE_PARAMETER_SET.parameter_set_id
    assert bound.binding_set_id == COMPOSITE_BINDINGS.binding_set_id
    assert bound.capability_projection_id == COMPOSITE_PROJECTION.projection_id
    assert bound.resolved_effects[0]["effect_id"] == "nominal"
    assert bound.resolved_parameters["initial_soc"] == 0.62
    assert bound.resolved_parameters["native_downlink_packet_size_bits"] == 1000.0
    assert bound.requested_outputs[0] == "custom.metric"
    assert set(COMPOSITE_REQUIRED_OUTPUTS).issubset(bound.requested_outputs)
    assert resolution.diagnostics == ()


def test_fault_modifier_scenario_resolves_to_governed_effect() -> None:
    resolution = resolve_composite_digital_twin_execution(_example("whole_spacecraft_composite_digital_twin_fault.yaml"))

    assert resolution.bound_graph.resolved_effects[0]["effect_id"] == "payload_instrument_off"
    assert resolution.bound_graph.resolved_effects[0]["kind"] == "fault"
    assert resolution.runtime_task_spec["modifiers"]["faults"][0]["fault_type"] == "instrument_off"
    assert resolution.diagnostics == ()


def test_degradation_modifier_scenario_resolves_to_governed_effect() -> None:
    resolution = resolve_composite_digital_twin_execution(_example("whole_spacecraft_composite_digital_twin_degradation.yaml"))

    assert resolution.bound_graph.resolved_effects[0]["effect_id"] == "multi_subsystem_end_of_life"
    assert resolution.bound_graph.resolved_effects[0]["kind"] == "degradation"
    assert resolution.diagnostics == ()


def test_mixed_capacity_fade_alias_resolves_to_governed_degradation_effect() -> None:
    resolution = resolve_composite_digital_twin_execution(_example("whole_spacecraft_composite_digital_twin_mixed.yaml"))
    effects = {item["effect_id"] for item in resolution.bound_graph.resolved_effects}

    assert "comm_data_downlink_link_loss" in effects
    assert "battery_capacity_loss_30pct" in effects
    assert resolution.diagnostics == ()


def test_capacity_fade_parameter_contract_is_consumed_for_composite_agent_specs() -> None:
    spec = _example("whole_spacecraft_composite_digital_twin_mixed.yaml")
    spec["modifiers"]["degradations"][0]["parameters"]["capacity_loss_pct"] = 30.0

    audit = audit_parameter_consumption(spec)

    assert audit.ok is True
    assert not audit.unknown_paths
    assert any(path.endswith("parameters.capacity_loss_pct") for path in audit.consumed_paths)


def test_unbound_modifier_is_audited_without_claiming_nominal() -> None:
    spec = deepcopy(_example("whole_spacecraft_composite_digital_twin_nominal.yaml"))
    spec["modifiers"]["faults"] = [{
        "modifier_id": "unknown_001",
        "target": "payload.instrument",
        "fault_type": "not_in_contract",
        "onset_time_s": 1.0,
        "duration_s": -1.0,
        "severity": 1.0,
        "parameters": {},
    }]

    resolution = resolve_composite_digital_twin_execution(spec)

    assert resolution.bound_graph.resolved_effects == ()
    assert resolution.diagnostics[0]["severity"] == "error"
    assert resolution.diagnostics[0]["code"] == "A4R_EFFECT_NOT_BOUND"
    assert resolution.diagnostics[0]["effect_id"] == "not_in_contract"


def test_resolution_exports_a4r_adapter_key_detector() -> None:
    from sat_sim.a4r.resolution import is_a4_capability_metadata

    assert is_a4_capability_metadata({"model_asset_execution": {"adapter_key": COMPOSITE_ADAPTER_KEY}})
    assert not is_a4_capability_metadata({"model_asset_execution": {"adapter_key": "legacy.capability"}})


def test_resolution_covers_all_required_runtime_couplings() -> None:
    assert set(REQUIRED_RUNTIME_COUPLINGS) == coupling_ids_from_graph()
