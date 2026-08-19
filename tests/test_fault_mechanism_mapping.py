from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from sat_sim.diagnostic_mapping_bundle import build_astrograph_diagnostic_mapping_bundle
from sat_sim.diagnostic_pipeline_registry import list_diagnostic_pipelines
from sat_sim.fault_mechanism_library import list_fault_mechanisms
from sat_sim.telemetry_feature_contract_library import list_telemetry_feature_contracts

ROOT = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "src/sat_sim/schemas" / name).read_text(encoding="utf-8"))


def test_phase3e_contract_libraries_are_schema_valid_and_cross_linked() -> None:
    mechanisms = list_fault_mechanisms()
    features = list_telemetry_feature_contracts()
    pipelines = list_diagnostic_pipelines()
    expected = {
        "ADCS_RW_FRICTION_INCREASE", "ADCS_RW_JAM", "ADCS_RW_TORQUE_AUTHORITY_LOSS",
        "EPS_BATTERY_CAPACITY_LOSS",
    }
    assert {item.fault_id for item in mechanisms} == expected
    assert {item.fault_id for item in features} == expected
    assert {item.fault_id for item in pipelines} == expected
    for item in mechanisms:
        jsonschema.validate(item.to_dict(), _schema("fault_mechanism.schema.json"))
    for item in features:
        jsonschema.validate(item.to_dict(), _schema("telemetry_feature_contract.schema.json"))
    for item in pipelines:
        jsonschema.validate(item.to_dict(), _schema("diagnostic_pipeline.schema.json"))
    bundle = build_astrograph_diagnostic_mapping_bundle()
    jsonschema.validate(bundle, _schema("diagnostic_mapping_bundle.schema.json"))
    assert bundle["mapping_count"] == 4
    assert len(bundle["bundle_sha256"]) == 64


def test_simulator_truth_is_excluded_from_all_model_inputs() -> None:
    bundle = build_astrograph_diagnostic_mapping_bundle()
    for mapping in bundle["mappings"]:
        raw = mapping["feature_contract"]["raw_features"]
        truth = {item["channel"] for item in raw if item["evidence_role"] == "simulator_truth"}
        assert not truth.intersection(mapping["astrograph_model_input_channels"])
        assert mapping["formal_model_binding_enabled"] is False


def test_three_rw_faults_are_active_but_model_binding_remains_disabled() -> None:
    bundle = build_astrograph_diagnostic_mapping_bundle()
    by_fault = {item["fault_id"]: item for item in bundle["mappings"]}
    for fault_id in ("ADCS_RW_FRICTION_INCREASE", "ADCS_RW_JAM", "ADCS_RW_TORQUE_AUTHORITY_LOSS"):
        item = by_fault[fault_id]
        assert item["status"] == "active_provisional"
        assert item["diagnostic_signature"] is not None
        assert item["pipeline"]["model_policy"]["binding_status"] == "disabled_pending_benchmark"
        assert item["astrograph_model_input_channels"]


def test_eps_capacity_loss_contract_is_honestly_blocked() -> None:
    bundle = build_astrograph_diagnostic_mapping_bundle()
    item = next(value for value in bundle["mappings"] if value["fault_id"] == "EPS_BATTERY_CAPACITY_LOSS")
    assert item["status"] == "blocked_pending_basilisk_fault_migration"
    assert item["diagnostic_signature"] is None
    assert item["pipeline"]["model_policy"]["binding_status"] == "blocked_by_simulation_capability"
    assert item["formal_model_binding_enabled"] is False
    assert item["mechanism"]["claim_boundary"]["formal_training_allowed"] is False


def test_current_astrograph_primary_channel_gaps_are_explicit() -> None:
    bundle = build_astrograph_diagnostic_mapping_bundle()
    by_fault = {item["fault_id"]: item for item in bundle["mappings"]}
    assert by_fault["ADCS_RW_FRICTION_INCREASE"]["missing_required_model_channels"] == []
    assert by_fault["ADCS_RW_JAM"]["missing_required_model_channels"] == []
    assert by_fault["ADCS_RW_JAM"]["astrograph_interface_ready"] is True
    assert by_fault["ADCS_RW_TORQUE_AUTHORITY_LOSS"]["missing_required_model_channels"] == []
    assert set(by_fault["EPS_BATTERY_CAPACITY_LOSS"]["missing_required_model_channels"]) == {
        "estimate.eps.battery_capacity_wh", "estimate.eps.battery_soh"
    }
    assert {item["fault_id"] for item in bundle["mappings"] if item["astrograph_interface_ready"]} == {"ADCS_RW_FRICTION_INCREASE", "ADCS_RW_JAM", "ADCS_RW_TORQUE_AUTHORITY_LOSS"}
