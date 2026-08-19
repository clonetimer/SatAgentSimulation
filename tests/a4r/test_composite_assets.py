from __future__ import annotations

from pathlib import Path

import yaml

from sat_sim_kernel import ConnectionKind
from sat_sim_model_assets.composite_digital_twin import (
    COMPOSITE_ADAPTER_KEY,
    COMPOSITE_ASSET_REGISTRY,
    COMPOSITE_BINDINGS,
    COMPOSITE_CAPABILITY_ID,
    COMPOSITE_GRAPH,
    COMPOSITE_IMPLEMENTATION_PROFILE,
    COMPOSITE_PARAMETER_SET,
    COMPOSITE_PROJECTION,
    COMPOSITE_SPACECRAFT_DEFINITION,
    EFFECT_IDS,
    REQUIRED_RUNTIME_COUPLINGS,
    coupling_ids_from_graph,
    parameter_contracts,
)


CAPABILITY_PATH = Path("src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml")
COUPLING_MATRIX_PATH = Path("configs/verification/whole_spacecraft_coupling_causality_v3.json")

EXPECTED_RUNTIME_COUPLING_PORTS = {
    "payload_to_comm_storage": ("payload.payload_generated_bps", "storage.generated_bps", ConnectionKind.SIGNAL, False),
    "payload_activity_to_eps_thermal": ("payload.payload_power_w", "eps.payload_power_w", ConnectionKind.RESOURCE, True),
    "orbit_sun_attitude_eclipse_to_eps_solar_power": ("orbit_environment.solar_power_available_w", "eps.solar_power_w", ConnectionKind.RESOURCE, False),
    "comm_native_odh_to_storage_drain": ("comm.removed_bps", "storage.removed_bps", ConnectionKind.SIGNAL, True),
    "eps_pdu_to_payload_activity": ("eps.pdu_payload_enabled", "payload.pdu_payload_enabled", ConnectionKind.RESOURCE, False),
    "adcs_pointing_to_payload_comm_gate": ("adcs.pointing_gate", "payload.pointing_gate", ConnectionKind.SIGNAL, False),
    "comm_activity_to_eps_thermal": ("comm.comm_power_w", "eps.comm_power_w", ConnectionKind.RESOURCE, True),
    "eps_pdu_to_comm_activity": ("eps.pdu_comm_enabled", "comm.pdu_comm_enabled", ConnectionKind.RESOURCE, False),
    "eps_loads_to_thermal_heat": ("eps.eps_heat_w", "thermal.eps_heat_w", ConnectionKind.RESOURCE, False),
    "comm_transmitter_to_storage_drain": ("comm.legacy_transmitter_removed_bps", "storage.legacy_removed_bps", ConnectionKind.SIGNAL, True),
    "adcs_control_effort_to_eps_thermal": ("adcs.adcs_control_power_w", "eps.adcs_power_w", ConnectionKind.RESOURCE, True),
    "adcs_spacecraft_to_orbit_environment": ("spacecraft_dynamics.spacecraft_position_x_m", "orbit_environment.spacecraft_position_x_m", ConnectionKind.SIGNAL, False),
    "eps_pdu_to_thermal_heaters": ("eps.pdu_heater_enabled", "thermal.pdu_heater_enabled", ConnectionKind.RESOURCE, False),
    "gravity_to_spacecraft": ("orbit_environment.gravity_accel_m_s2", "spacecraft_dynamics.gravity_accel_m_s2", ConnectionKind.STATE, True),
    "ground_access_rf_link_budget": ("orbit_environment.ground_slant_range_m", "comm.ground_slant_range_m", ConnectionKind.SIGNAL, False),
    "orbit_eclipse_to_thermal_shadow": ("orbit_environment.eclipse_shadow_factor", "thermal.eclipse_shadow_factor", ConnectionKind.SIGNAL, False),
    "propulsion_activity_to_eps_thermal": ("propulsion.propulsion_power_w", "eps.propulsion_power_w", ConnectionKind.RESOURCE, True),
    "propulsion_effector_to_spacecraft": ("propulsion.orbit_perturbation_m", "spacecraft_dynamics.propulsion_orbit_delta_m", ConnectionKind.STATE, False),
    "thermal_heaters_to_eps_load": ("thermal.heater_eps_load_w", "eps.heater_power_w", ConnectionKind.RESOURCE, True),
}


def _capability() -> dict:
    return yaml.safe_load(CAPABILITY_PATH.read_text(encoding="utf-8"))


def test_composite_asset_registry_validates() -> None:
    COMPOSITE_ASSET_REGISTRY.validate()
    inventory = COMPOSITE_ASSET_REGISTRY.inventory()
    assert inventory["definitions"]["spacecraft.composite_digital_twin@1"] == COMPOSITE_SPACECRAFT_DEFINITION.content_sha256
    assert inventory["graphs"]["spacecraft.composite_digital_twin@1"] == COMPOSITE_GRAPH.content_sha256
    assert inventory["parameter_sets"][str(COMPOSITE_PARAMETER_SET.parameter_set_id)] == COMPOSITE_PARAMETER_SET.content_sha256


def test_composite_model_definition_covers_public_capability_parameters() -> None:
    capability = _capability()
    declared = {str(item.name) for item in COMPOSITE_SPACECRAFT_DEFINITION.parameters}
    public = set(capability["parameters"])
    assert public <= declared
    assert set(COMPOSITE_PARAMETER_SET.values) <= declared
    assert set(COMPOSITE_PROJECTION.parameter_alias_map().values()) == declared


def test_composite_effects_cover_capability_operator_contract() -> None:
    capability = _capability()
    capability_effects = {item["effect_id"] for item in capability["operator"]["effects"]}
    model_effects = {str(item.effect_id) for item in COMPOSITE_SPACECRAFT_DEFINITION.effects}
    binding_effects = {str(item.effect_id) for item in COMPOSITE_BINDINGS.effects}
    assert capability_effects == set(EFFECT_IDS)
    assert capability_effects <= model_effects
    assert capability_effects <= binding_effects


def test_composite_graph_covers_required_runtime_couplings() -> None:
    import json

    matrix = json.loads(COUPLING_MATRIX_PATH.read_text(encoding="utf-8"))
    required = set(matrix["required_runtime_couplings"])
    assert required == set(REQUIRED_RUNTIME_COUPLINGS)
    assert required <= coupling_ids_from_graph()


def test_composite_graph_runtime_couplings_match_port_contract() -> None:
    by_id = {str(item.transform): item for item in COMPOSITE_GRAPH.connections if item.transform}

    assert set(EXPECTED_RUNTIME_COUPLING_PORTS) == set(REQUIRED_RUNTIME_COUPLINGS)
    for coupling_id, (source, target, kind, feedback) in EXPECTED_RUNTIME_COUPLING_PORTS.items():
        connection = by_id[coupling_id]
        assert str(connection.source) == source
        assert str(connection.target) == target
        assert connection.kind == kind
        assert connection.feedback is feedback


def test_composite_projection_is_non_legacy_and_schema_ready() -> None:
    contracts = parameter_contracts()
    form = COMPOSITE_PROJECTION.form_schema(contracts)
    api = COMPOSITE_PROJECTION.api_schema(contracts)
    agent = COMPOSITE_PROJECTION.agent_tool_schema(contracts)
    assert COMPOSITE_PROJECTION.capability_id == COMPOSITE_CAPABILITY_ID
    assert COMPOSITE_PROJECTION.adapter_key == COMPOSITE_ADAPTER_KEY
    assert COMPOSITE_IMPLEMENTATION_PROFILE.adapter_key == COMPOSITE_ADAPTER_KEY
    assert api["adapter_key"] == COMPOSITE_ADAPTER_KEY
    assert "class_path" not in form["properties"]
    assert "adapter_key" not in form["properties"]
    assert "eps_battery_capacity_loss" in " ".join(agent["effects"])
