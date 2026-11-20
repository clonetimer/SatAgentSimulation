"""Whole-spacecraft declarations distinguish route scope from runtime evidence."""

from __future__ import annotations

from pathlib import Path

from sat_sim.capability_registry import (
    capability_summary_payload,
    get_capability,
    list_capabilities,
    validate_capability_integration_registry,
    validate_operator_registry,
)


GROUPS = {
    "native_product_runtime": {"unified_native", "composite_digital_twin"},
    "local_electrical_product_model": {"power_thermal_orbit_coupled", "comm_payload_mission_coupled"},
    "product_without_eps": {"orbit_adcs_fidelity", "maneuver_orbit_attitude"},
    "explicit_adapter_only": {"basilisk_6dof", "bsksim_foundation", "orbit_attitude_thermal"},
    "deprecated_direct_api": {"basic_power_orbit", "basic_power_attitude_orbit", "basic_power_thermal_orbit", "bsksim_coupled"},
}


def test_all_thirteen_whole_spacecraft_routes_declare_resource_scope_and_real_sources():
    contracts = {item.capability_id: item for item in list_capabilities() if item.capability_id.startswith("whole_spacecraft.")}
    expected = {f"whole_spacecraft.{name}.v1" for names in GROUPS.values() for name in names}
    assert set(contracts) == expected
    repository = Path(__file__).resolve().parents[1]
    for scope, names in GROUPS.items():
        for name in names:
            contract = contracts[f"whole_spacecraft.{name}.v1"]
            declaration = contract.data.get("resource_accounting")
            assert isinstance(declaration, dict), contract.capability_id
            assert declaration["route_scope"] == scope
            assert declaration["electrical_model"]
            assert declaration["decision"]
            assert declaration["evidence_scope"] == "capability_declaration_not_runtime_measurement"
            assert declaration["implementation_references"]
            assert all((repository / source).is_file() for source in declaration["implementation_references"])
            assert "provider_count" not in declaration
            if scope == "native_product_runtime":
                assert declaration["runtime_inventory_field"] == "power_authority_inventory"
            else:
                assert "runtime_inventory_field" not in declaration


def test_machine_capability_summary_exposes_declaration_without_relabeling_it_as_measurement():
    summaries = {item["capability_id"]: item for item in capability_summary_payload()}
    for names in GROUPS.values():
        for name in names:
            capability_id = f"whole_spacecraft.{name}.v1"
            contract = get_capability(capability_id)
            assert "resource_accounting" in summaries[capability_id]
            assert summaries[capability_id]["resource_accounting"] == contract.data["resource_accounting"]


def test_resource_declarations_preserve_active_local_models_and_explicit_compatibility_routes():
    for name in GROUPS["local_electrical_product_model"] | GROUPS["product_without_eps"]:
        contract = get_capability(f"whole_spacecraft.{name}.v1")
        assert contract.is_active and contract.exposed_to_agent
    for name in GROUPS["explicit_adapter_only"]:
        contract = get_capability(f"whole_spacecraft.{name}.v1")
        assert contract.is_active and not contract.exposed_to_agent
    for name in GROUPS["deprecated_direct_api"]:
        contract = get_capability(f"whole_spacecraft.{name}.v1")
        assert contract.lifecycle_status == "deprecated"
        assert contract.data["status"] == "deprecated_compatibility_only"
    stale = get_capability("whole_spacecraft.basic_power_attitude_orbit.v1")
    assert "planned for INT-1" not in stale.lifecycle["reason"]


def test_native_resource_evidence_is_advertised_in_both_machine_observability_views():
    fields = {"power_authority_inventory", "resource_closure_evidence"}
    native = get_capability("whole_spacecraft.unified_native.v1").data
    assert native["resource_accounting"]["runtime_resource_evidence_scope"] == "electrical authority census and measured internal-heat transfer"
    assert native["resource_accounting"]["runtime_battery_balance_audit"] == "not_included_in_this_evidence"
    assert fields | {"engineering_proxy_module_count"} <= set(native["outputs"]["qoi"])
    assert fields | {"engineering_proxy_module_count"} <= set(native["operator"]["observability"]["qoi"])
    assert not any("Resource activity-to-power" in item for item in native["integration_contract"]["limitations"])

    composite = get_capability("whole_spacecraft.composite_digital_twin.v1").data
    assert composite["resource_accounting"]["runtime_resource_evidence_scope"] == "electrical authority census, measured battery balance and actual internal-heat transfer"
    assert composite["resource_accounting"]["runtime_battery_balance_audit"] == "observed_native_message_interval"
    summary = {item["name"]: item for item in composite["outputs"]["summary"]}
    battery_fields = {
        "battery_evidence_status", "battery_evidence_sample_count", "battery_startup_excluded_sample_count",
        "battery_evidence_start_s", "battery_evidence_end_s",
    }
    assert fields | battery_fields <= summary.keys()
    assert fields | battery_fields <= set(composite["operator"]["observability"]["qoi"])
    assert all(summary[name]["dtype"] == "object" and summary[name]["unit"] == "json" for name in fields)
    assert summary["proxy_coupling_count"]["unit"] == "link_records"
    trace = {item["name"]: item for item in composite["outputs"]["trace"]}
    for name, unit in (("battery_capacity_j", "J"), ("battery_source_time_s", "s")):
        assert trace[name]["unit"] == unit
        assert name in composite["operator"]["observability"]["trace_fields"]


def test_composite_declares_actual_builder_resource_edges_and_preserves_pointing_aliases():
    contract = get_capability("whole_spacecraft.composite_digital_twin.v1")
    raw = set(contract.data["integration_contract"]["couplings"]["supported"])
    assert {"eps_loads_to_thermal_heat", "comm_activity_to_eps_thermal", "thermal_heaters_to_eps_load", "adcs_pointing_to_payload_comm_gate"} <= raw
    assert {"adcs_pointing_to_payload_gate", "adcs_pointing_to_comm_gate"} <= contract.supported_couplings
    errors = [item.to_dict() for item in validate_capability_integration_registry() if item.severity == "error"]
    errors += [item.to_dict() for item in validate_operator_registry() if item.severity == "error"]
    assert errors == []
