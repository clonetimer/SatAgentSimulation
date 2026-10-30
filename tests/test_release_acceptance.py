from __future__ import annotations

import json
from pathlib import Path

from sat_sim.bsk_engine.event_manager import parse_bsk_events
from sat_sim.product_closure import product_capability_closure
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates
from sat_sim.task_models import to_runtime_task_spec


ROOT = Path(__file__).resolve().parents[1]


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_advanced_outputs_survive_template_to_canonical_normalization() -> None:
    for template_id in ("eps_unified_native", "comm_data_unified_native"):
        spec = instantiate_scenario_template(template_id, task_id=f"test_{template_id}")
        assert len(spec["outputs"]["telemetry_streams"]) == 2
        assert spec["outputs"]["fmea"] == {"enabled": True, "formats": ["csv", "json"]}


def test_composite_fault_template_uses_modifier_delivery_and_preserves_ids() -> None:
    spec = instantiate_scenario_template(
        "whole_spacecraft_unified_native_fault_chain",
        task_id="test_fault_chain",
    )
    faults = spec["events"]["faults"]
    assert [row["id"] for row in faults] == ["payload_off", "link_loss", "battery_loss"]
    assert all(row["delivery"] == "modifier" for row in faults)
    runtime = to_runtime_task_spec(spec)
    assert [event.event_id for event in parse_bsk_events(runtime)] == [
        "payload_off",
        "link_loss",
        "battery_loss",
    ]


def test_current_platform_claim_requires_complete_and_sealed_acceptance() -> None:
    manifest = _json("src/sat_sim/release_manifest.json")
    if manifest["target_revalidation_status"] == "NOT_REVALIDATED":
        assert manifest["status"] == "IN_PROGRESS"
        assert manifest["current_qualification"]["historical_evidence_inheritance"] == "DISALLOWED"
        return  # No current Linux/vLLM qualification is declared.
    acceptance = _json("reports/platform_acceptance/platform_acceptance_summary.json")
    assert acceptance["status"] == "PASS"
    assert acceptance["target_os"] == "Linux"
    assert acceptance["required_failed_count"] == 0
    checks = {item["name"]: item for item in acceptance["checks"]}
    for name in (
        "package_release_identity",
        "strict_doctor_12_of_12",
        "strict_release_check",
        "unified_native_run",
        "run_bundle_sealed",
        "run_bundle_integrity",
        "vllm_acceptance",
    ):
        assert checks[name]["passed"] is True


def test_current_rw_approval_claim_requires_reviewed_evidence() -> None:
    manifest = _json("src/sat_sim/release_manifest.json")
    if manifest["current_qualification"]["rw_diagnostic_approval"] == "NOT_REVALIDATED":
        assert manifest["engineering_closure_status"] == "IN_PROGRESS"
        assert manifest["current_qualification"]["historical_evidence_inheritance"] == "DISALLOWED"
        return  # Historical expert approval cannot qualify the current revision.
    readiness = _json("reports/rw_jam_readiness.json")
    index = _json("reports/rw_native_validation/astrograph_dataset_index.json")
    authorization = _json("reports/rw_a_level_approval_authorization.json")

    assert readiness["status"] == "READY_FOR_ASTROGRAPH_PROMOTION"
    assert readiness["blockers"] == []
    assert readiness["formal_training_ready"] is True
    assert index["training_ready_case_count"] == 18
    assert index["expert_approved_case_count"] == 18
    assert authorization["approval_scope"] == (
        "engineering_simulation_internal_training_and_evaluation_only"
    )
    assert authorization["hardware_representative"] is False
    assert authorization["flight_validated"] is False


def test_current_product_governance_and_release_manifest_are_consistent() -> None:
    closure = product_capability_closure()
    manifest = _json("src/sat_sim/release_manifest.json")

    assert len(closure["records"]) == 60
    assert len(closure["default_visible_capability_ids"]) == 43
    assert manifest["release_id"] == "SAT-SIM-0.7.8-ENGINEERING-BASELINE"
    assert manifest["release_version"] == "0.7.8"
    assert manifest["scenario_template_count"] == 58
    assert manifest["scenario_template_count"] == list_scenario_templates()["count"]
    assert "reports/final_acceptance_report.md" in manifest["required_documents"]
    assert "reports/platform_acceptance/platform_acceptance_summary.json" in manifest[
        "runtime_generated_evidence"
    ]
    assert "reports/platform_acceptance/platform_acceptance_summary.json" not in manifest[
        "required_documents"
    ]
