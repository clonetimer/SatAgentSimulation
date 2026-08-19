from __future__ import annotations

import json
import shutil
from pathlib import Path

import jsonschema

from sat_sim.campaign import expand_campaign_spec
from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.fault_experiment_templates import list_fault_experiment_templates
from sat_sim.simulation_validation_agent import SimulationValidationAgent

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fault_experiment_templates_are_complete_and_schema_valid() -> None:
    schema = _read(ROOT / "src/sat_sim/schemas/fault_experiment_template.schema.json")
    templates = list_fault_experiment_templates()
    assert {item.fault_id for item in templates} == {
        "ADCS_RW_FRICTION_INCREASE",
        "ADCS_RW_JAM",
        "ADCS_RW_TORQUE_AUTHORITY_LOSS",
    }
    assert len({item.template_id for item in templates}) == 3
    for template in templates:
        jsonschema.validate(template.to_dict(), schema)
        assert template.capability_id == "subsystem.adcs_unified_native.v1"
        assert template.simulation_backend == "basilisk"


def test_fault_nominal_pairs_preserve_seed_and_template_lineage(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        counts=SplitCounts(train=0, validation=1, test=0),
        duration_s=10.0,
        sample_s=1.0,
        solver_step_s=0.2,
    )
    plan = expand_campaign_spec(build_fault_dataset_campaign(config), output_root=tmp_path / "plan")
    assert len(plan.cases) == 6
    by_pair: dict[str, list] = {}
    for case in plan.cases:
        metadata = case.spec["metadata"]
        by_pair.setdefault(metadata["astrograph_pair_id"], []).append(case)
        assert metadata["experiment_template_id"].startswith("adcs.rw_")
        assert metadata["campaign_preserve_seed"] is True
    for cases in by_pair.values():
        assert len(cases) == 2
        assert len({case.spec["simulation"]["seed"] for case in cases}) == 1
        assert len({json.dumps(case.spec["parameters"], sort_keys=True) for case in cases}) == 1


def test_proxy_campaign_emits_experiment_records_and_review_only_qualification(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        campaign_id="phase3c_proxy",
        counts=SplitCounts(train=1, validation=0, test=0),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "dataset", continue_on_error=False
    )
    assert result.dataset_index["case_count"] == 6
    assert result.dataset_index["training_ready_case_count"] == 0
    assert result.dataset_index["formal_training_qualified_case_count"] == 0
    qualification = result.dataset_index["qualification"]
    assert qualification["pair_count"] == 3
    assert qualification["counts_by_decision"] == {"REVIEW": 3}

    record_schema = _read(ROOT / "src/sat_sim/schemas/experiment_record.schema.json")
    validation_schema = _read(ROOT / "src/sat_sim/schemas/simulation_pair_validation.schema.json")
    for contract in result.dataset_index["contracts"]:
        case_root = result.campaign.output_root / "cases" / contract["dataset_id"]
        record = _read(case_root / contract["experiment_record_file"])
        jsonschema.validate(record, record_schema)
        assert record["lineage"]["experiment_template_id"]
        assert record["lineage"]["dataset_id"] == contract["dataset_id"]
        assert contract["quality"]["physics_validation_status"] == "REVIEW"
        assert contract["quality"]["training_ready"] is False
    for report in qualification["reports"]:
        jsonschema.validate(report, validation_schema)
        assert report["physics_validation_passed"] is True
        assert report["formal_training_qualified"] is False


def test_validation_agent_can_sign_a_physically_valid_a_level_pair(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        campaign_id="phase3c_friction",
        counts=SplitCounts(train=1, validation=0, test=0),
        fault_ids=("ADCS_RW_FRICTION_INCREASE",),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "source", continue_on_error=False
    )
    pair = result.dataset_index["qualification"]["reports"][0]
    fault_root = result.campaign.output_root / "cases" / pair["fault_dataset_id"]
    nominal_root = result.campaign.output_root / "cases" / pair["nominal_dataset_id"]
    signed_fault = tmp_path / "signed_fault"
    signed_nominal = tmp_path / "signed_nominal"
    shutil.copytree(fault_root, signed_fault)
    shutil.copytree(nominal_root, signed_nominal)
    for root in (signed_fault, signed_nominal):
        contract_path = root / "astrograph/dataset_contract.json"
        contract = _read(contract_path)
        fidelity = contract["simulation"]["fidelity"]
        fidelity["formal_training_eligible"] = True
        fidelity["fidelity_level"] = "A_ENGINEERING_BASILISK_NATIVE"
        fidelity["simulation_engine"] = "Basilisk"
        fidelity["reason"] = "test_fixture_complete_runtime_evidence"
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    report = SimulationValidationAgent().validate_pair(fault_root=signed_fault, nominal_root=signed_nominal)
    assert report["decision"] == "PASS"
    assert report["physics_validation_passed"] is True
    assert report["formal_training_qualified"] is True


def test_validation_agent_rejects_missing_fault_signature(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        campaign_id="phase3c_reject",
        counts=SplitCounts(train=1, validation=0, test=0),
        fault_ids=("ADCS_RW_FRICTION_INCREASE",),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "source", continue_on_error=False
    )
    pair = result.dataset_index["qualification"]["reports"][0]
    fault_root = result.campaign.output_root / "cases" / pair["fault_dataset_id"]
    nominal_root = result.campaign.output_root / "cases" / pair["nominal_dataset_id"]
    shutil.copyfile(nominal_root / "astrograph/telemetry.csv", fault_root / "astrograph/telemetry.csv")
    report = SimulationValidationAgent().validate_pair(fault_root=fault_root, nominal_root=nominal_root)
    assert report["decision"] == "REJECT"
    assert report["physics_validation_passed"] is False


def test_expert_review_cannot_override_proxy_technical_gate(tmp_path: Path) -> None:
    from sat_sim.dataset_expert_review import record_dataset_pair_review

    config = FaultDatasetFactoryConfig(
        campaign_id="phase3c_review_proxy",
        counts=SplitCounts(train=1, validation=0, test=0),
        fault_ids=("ADCS_RW_FRICTION_INCREASE",),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "source", continue_on_error=False
    )
    pair_id = result.dataset_index["qualification"]["reports"][0]["pair_id"]
    import pytest
    with pytest.raises(ValueError, match="cannot override"):
        record_dataset_pair_review(
            result.campaign.output_root,
            pair_id=pair_id,
            decision="APPROVE",
            reviewer_id="expert@example",
            reviewer_type="llm_assisted_human",
            notes="Reviewed proxy evidence; technical gate is not A-level.",
        )


def test_expert_approval_enables_only_technically_qualified_pair(tmp_path: Path) -> None:
    from sat_sim.dataset_expert_review import record_dataset_pair_review

    config = FaultDatasetFactoryConfig(
        campaign_id="phase3c_review_approve",
        counts=SplitCounts(train=1, validation=0, test=0),
        fault_ids=("ADCS_RW_FRICTION_INCREASE",),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "source", continue_on_error=False
    )
    root = result.campaign.output_root
    qualification_path = root / "dataset_qualification_index.json"
    qualification = _read(qualification_path)
    report = qualification["reports"][0]
    report["decision"] = "PASS"
    report["formal_training_qualified"] = True
    qualification_path.write_text(json.dumps(qualification, indent=2) + "\n", encoding="utf-8")
    index_path = root / "astrograph_dataset_index.json"
    index = _read(index_path)
    for contract in index["contracts"]:
        contract["quality"]["formal_training_eligible"] = True
        contract["quality"]["formal_training_qualified"] = True
        contract["quality"]["candidate_training_ready"] = True
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    import pytest
    with pytest.raises(ValueError, match="diagnostic signature gate"):
        record_dataset_pair_review(
            root,
            pair_id=report["pair_id"],
            decision="APPROVE",
            reviewer_id="human-expert-001",
            reviewer_type="llm_assisted_human",
            notes="Physics qualification alone is no longer sufficient.",
            evidence_refs=(report["report_file"],),
        )
