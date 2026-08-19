from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from sat_sim.diagnostic_signature_library import list_diagnostic_signatures
from sat_sim.diagnostic_signature_review import record_diagnostic_signature_review
from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.fault_experiment_templates import get_fault_experiment_template

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _proxy_config(*, campaign_id: str = "phase3d_proxy") -> FaultDatasetFactoryConfig:
    return FaultDatasetFactoryConfig(
        campaign_id=campaign_id,
        counts=SplitCounts(train=1, validation=0, test=0),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )


def test_diagnostic_signature_library_is_schema_valid_and_lineage_complete() -> None:
    schema = _read(ROOT / "src/sat_sim/schemas/diagnostic_signature.schema.json")
    signatures = list_diagnostic_signatures()
    assert {item.fault_id for item in signatures} == {
        "ADCS_RW_FRICTION_INCREASE",
        "ADCS_RW_JAM",
        "ADCS_RW_TORQUE_AUTHORITY_LOSS",
    }
    for signature in signatures:
        jsonschema.validate(signature.to_dict(), schema)
        template = get_fault_experiment_template(signature.fault_id)
        assert signature.source_experiment_template_id == template.template_id
        assert signature.qualification["threshold_status"] == "provisional_until_basilisk_18_case"


def test_fault_severity_models_are_schema_valid_and_jam_is_binary() -> None:
    schema = _read(ROOT / "src/sat_sim/schemas/fault_severity_model.schema.json")
    for fault_id in (
        "ADCS_RW_FRICTION_INCREASE",
        "ADCS_RW_JAM",
        "ADCS_RW_TORQUE_AUTHORITY_LOSS",
    ):
        template = get_fault_experiment_template(fault_id)
        jsonschema.validate(template.severity_model, schema)
    jam = get_fault_experiment_template("ADCS_RW_JAM")
    assert jam.severity_model["mechanism_kind"] == "binary_failure"
    assert jam.severity_model["fixed_normalized_severity"] == 1.0


def test_campaign_embeds_physically_bound_severity_and_binary_jam_is_not_randomized() -> None:
    config = FaultDatasetFactoryConfig(
        counts=SplitCounts(train=0, validation=1, test=0),
        duration_s=12.0,
        sample_s=1.0,
        solver_step_s=0.2,
    )
    campaign = build_fault_dataset_campaign(config)
    cases = campaign["campaign"]["cases"]
    fault_cases = [item for item in cases if item["overrides"]["metadata"]["astrograph_case_role"] == "fault"]
    by_fault = {item["overrides"]["metadata"]["astrograph_fault_id"]: item for item in fault_cases}
    jam_meta = by_fault["ADCS_RW_JAM"]["overrides"]["metadata"]
    assert jam_meta["severity"] == 1.0
    assert jam_meta["fault_severity"]["mechanism_kind"] == "binary_failure"
    assert jam_meta["fault_severity"]["severity_level"] == "critical"
    friction_meta = by_fault["ADCS_RW_FRICTION_INCREASE"]["overrides"]["metadata"]
    assert friction_meta["fault_severity"]["physically_applied"] is True
    assert friction_meta["fault_severity"]["simulator_parameter"] == "drag_nms"
    torque_meta = by_fault["ADCS_RW_TORQUE_AUTHORITY_LOSS"]["overrides"]["metadata"]
    assert torque_meta["fault_severity"]["physically_applied"] is True
    assert torque_meta["fault_severity"]["simulator_parameter"] == "torque_scale"


def test_proxy_dataset_produces_diagnostic_reports_but_no_formal_candidate(tmp_path: Path) -> None:
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(_proxy_config()),
        output_root=tmp_path / "dataset",
        continue_on_error=False,
    )
    diagnostic = result.dataset_index["diagnostic_qualification"]
    assert diagnostic["pair_count"] == 3
    assert diagnostic["counts_by_decision"] == {"REJECT": 1, "REVIEW": 2}
    assert diagnostic["basilisk_candidate_qualified_pair_count"] == 0
    assert diagnostic["formal_diagnostic_qualified_pair_count"] == 0
    assert result.dataset_index["training_ready_case_count"] == 0
    schema = _read(ROOT / "src/sat_sim/schemas/diagnostic_pair_validation.schema.json")
    for report in diagnostic["reports"]:
        jsonschema.validate(report, schema)
        if report["fault_id"] == "ADCS_RW_TORQUE_AUTHORITY_LOSS":
            assert report["diagnostic_validation_passed"] is False
            assert report["decision"] == "REJECT"
        else:
            assert report["diagnostic_validation_passed"] is True
        assert report["formal_diagnostic_qualified"] is False
        assert report["leakage_guard"]["simulator_truth_contributing_count"] == 0
    jam_contract = next(
        item for item in result.dataset_index["contracts"]
        if (item.get("ground_truth") or {}).get("class_id") == "ADCS_RW_JAM"
    )
    descriptor = jam_contract["ground_truth"]["severity_descriptor"]
    jsonschema.validate(descriptor, _read(ROOT / "src/sat_sim/schemas/fault_severity_descriptor.schema.json"))
    assert descriptor["normalized_severity"] == 1.0
    feature_policy = jam_contract["telemetry"]["channel_contract"]["training_feature_policy"]
    assert feature_policy["target_leakage_guard"] is True
    assert "status.adcs.rw.available_0" in feature_policy["physics_validation_only_channels"]
    assert "estimate.adcs.rw.effective_max_torque_nm_0" in feature_policy["physics_validation_only_channels"]
    assert "sensor.adcs.rw.speed_rad_s_0" in feature_policy["allowed_model_input_channels"]


def test_signature_freeze_cannot_override_proxy_or_insufficient_evidence(tmp_path: Path) -> None:
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(_proxy_config(campaign_id="phase3d_freeze_reject")),
        output_root=tmp_path / "dataset",
        continue_on_error=False,
    )
    with pytest.raises(ValueError, match="cannot override"):
        record_diagnostic_signature_review(
            result.campaign.output_root,
            fault_id="ADCS_RW_FRICTION_INCREASE",
            decision="FREEZE",
            reviewer_id="diagnostic-expert-001",
            reviewer_type="llm_assisted_human",
            notes="Proxy evidence cannot freeze a formal signature.",
        )


def test_named_review_freezes_exact_signature_revision_after_candidate_gate(tmp_path: Path) -> None:
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(_proxy_config(campaign_id="phase3d_freeze_fixture")),
        output_root=tmp_path / "dataset",
        continue_on_error=False,
    )
    root = result.campaign.output_root
    index_path = root / "diagnostic_qualification_index.json"
    diagnostic = _read(index_path)
    target = "ADCS_RW_FRICTION_INCREASE"
    target_reports = [item for item in diagnostic["reports"] if item["fault_id"] == target]
    assert len(target_reports) == 1
    # Governance-mechanics fixture: emulate three independent A-level candidate reports.
    original = target_reports[0]
    diagnostic["reports"] = [item for item in diagnostic["reports"] if item["fault_id"] != target]
    for index in range(3):
        clone = dict(original)
        clone["pair_id"] = f"fixture-friction-{index}"
        clone["basilisk_candidate_qualified"] = True
        clone["decision"] = "PASS"
        clone["formal_diagnostic_qualified"] = False
        clone["report_file"] = f"validation/diagnostics/fixture-friction-{index}.json"
        diagnostic["reports"].append(clone)
        path = root / clone["report_file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(clone, indent=2) + "\n", encoding="utf-8")
    for summary in diagnostic["fault_summaries"]:
        if summary["fault_id"] == target:
            summary["pair_count"] = 3
            summary["candidate_pass_pair_count"] = 3
            summary["rejected_pair_count"] = 0
            summary["threshold_freeze_status"] = "READY_FOR_EXPERT_REVIEW"
    diagnostic["pair_count"] += 2
    diagnostic["basilisk_candidate_qualified_pair_count"] = 3
    diagnostic["counts_by_decision"] = {"PASS": 3, "REVIEW": 2}
    index_path.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")

    reviewed = record_diagnostic_signature_review(
        root,
        fault_id=target,
        decision="FREEZE",
        reviewer_id="diagnostic-expert-001",
        reviewer_type="human",
        notes="Reviewed three independent Basilisk candidate pairs and froze this exact signature revision.",
        evidence_refs=("diagnostic_qualification_index.json",),
    )
    jsonschema.validate(
        reviewed["review"],
        _read(ROOT / "src/sat_sim/schemas/diagnostic_signature_review.schema.json"),
    )
    assert reviewed["review"]["reviewed_signature_sha256"] == original["signature_sha256"]
    summary = next(item for item in reviewed["diagnostic_qualification"]["fault_summaries"] if item["fault_id"] == target)
    assert summary["threshold_freeze_status"] == "FROZEN"
    assert reviewed["diagnostic_qualification"]["formal_diagnostic_qualified_pair_count"] == 3
    # Pair approval remains a separate gate, so no case is training-ready here.
    assert reviewed["dataset_index"]["training_ready_case_count"] == 0
