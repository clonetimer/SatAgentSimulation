from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.fault_environment.fault_catalog import get_fault_contract
from sat_sim.form_schema import capability_form_schema


def _torque_loss_spec() -> dict:
    return {
        "schema_version": "0.1.0",
        "task_id": "phase3a_torque_authority_loss",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "degradation"},
        "simulation": {"duration_s": 8.0, "sample_s": 1.0, "solver": {"step_s": 0.2}},
        "parameters": {
            "wheel_configuration": "orthogonal_3",
            "max_wheel_torque_nm": 0.05,
            "initial_wheel_speed_rad_s": [60.0, 70.0, 80.0],
        },
        "modifiers": {
            "faults": [],
            "constraints": [],
            "degradations": [
                {
                    "modifier_id": "partial_torque_1",
                    "target": "adcs.reaction_wheel.1",
                    "degradation_type": "adcs_rw_torque_authority_loss",
                    "onset_time_s": 2.0,
                    "duration_s": 4.0,
                    "severity": 0.5,
                    "parameters": {"wheel_index": 1, "torque_scale": 0.4},
                }
            ],
        },
    }


def test_partial_torque_authority_is_a_formal_degradation() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}
    item = degradations["adcs_rw_torque_authority_loss"]
    assert item["magnitude_applicable"] is False
    assert [field["name"] for field in item["parameter_fields"]] == ["wheel_index", "torque_scale"]

    contract = get_fault_contract("adcs_rw_torque_authority_loss", "degradation")
    assert contract.category == "degradation"
    assert "effective_max_torque" in " ".join(contract.expected_observables)


def test_partial_torque_authority_changes_physical_evidence_without_fault_label() -> None:
    result = AdcsFidelityAdapter().run(_torque_loss_spec())
    active = [row for row in result.trace_rows if 2.0 <= float(row["time_s"]) < 6.0]
    assert active
    assert all(row["label.degradation_active"] is True for row in active)
    assert all(row["label.fault_active"] is False for row in active)
    assert all(float(row["adcs.rw.effective_max_torque_nm_1"]) == pytest.approx(0.02) for row in active)
    assert all(float(row["adcs.rw.effective_friction_torque_nm_1"]) == pytest.approx(0.0) for row in active)


def test_default_factory_plan_has_balanced_paired_splits() -> None:
    spec = build_fault_dataset_campaign(FaultDatasetFactoryConfig())
    base = spec["campaign"]["base_spec"]
    assert base["capability_id"] == "subsystem.adcs_unified_native.v1"
    assert base["simulation"]["backend"] == "basilisk"
    assert base["assurance"]["allow_proxy"] is False
    cases = spec["campaign"]["cases"]
    assert len(cases) == 90

    by_pair: dict[str, list[dict]] = {}
    split_counts: dict[str, int] = {}
    for case in cases:
        meta = case["overrides"]["metadata"]
        by_pair.setdefault(meta["astrograph_pair_id"], []).append(case)
        split = meta["astrograph_split"]
        split_counts[split] = split_counts.get(split, 0) + 1
    assert split_counts == {"train": 54, "validation": 18, "test": 18}
    assert len(by_pair) == 45
    for pair in by_pair.values():
        assert {item["overrides"]["metadata"]["astrograph_case_role"] for item in pair} == {"fault", "nominal"}
        assert len({item["overrides"]["simulation.seed"] for item in pair}) == 1
        assert len({json.dumps(item["overrides"]["parameters"], sort_keys=True) for item in pair}) == 1
        assert len({item["overrides"]["metadata"]["target"] for item in pair}) == 1


def test_pilot_campaign_writes_target_normalized_astrograph_contracts(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        counts=SplitCounts(train=1, validation=0, test=0),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
        output_root=str(tmp_path / "ignored"),
    )
    spec = build_fault_dataset_campaign(config)
    result = run_fault_dataset_campaign(spec, output_root=tmp_path / "pilot", continue_on_error=False)

    assert result.campaign.summary["success_count"] == 6
    assert result.dataset_index["case_count"] == 6
    assert result.dataset_index["training_ready_case_count"] == 0
    assert result.dataset_index["formal_training_eligible_case_count"] == 0
    assert result.dataset_index["counts_by_fidelity_level"] == {"C_TEST_PROXY_ONLY": 6}
    assert result.dataset_index["counts_by_class"] == {
        "ADCS_RW_FRICTION_INCREASE": 1,
        "ADCS_RW_JAM": 1,
        "ADCS_RW_TORQUE_AUTHORITY_LOSS": 1,
        "NORMAL": 3,
    }

    fault_contracts = [
        item for item in result.dataset_index["contracts"]
        if item["ground_truth"]["case_role"] == "fault"
    ]
    assert len(fault_contracts) == 3
    by_class = {item["ground_truth"]["class_id"]: item for item in fault_contracts}
    for contract in fault_contracts:
        channel = contract["telemetry"]["channel_contract"]
        assert channel["missing_required_channels"] == []
        assert channel["coverage_ratio"] == pytest.approx(1.0)
        assert channel["profile_id"] == "astrograph-rw-temporal-basilisk-5ch.v2"
        assert contract["quality"]["channel_ready"] is True
        assert contract["quality"]["formal_training_eligible"] is False
        assert contract["quality"]["training_ready"] is False
        assert contract["claim_boundary"]["level"] == "C_TEST_PROXY_ONLY"
        assert contract["claim_boundary"]["flight_validated"] is False
        claim_notes = " ".join(contract["claim_boundary"]["notes"])
        assert "rwMotorTorqueOutMsg" in claim_notes
        assert "not measured flight current" in claim_notes
        assert "excludes unvalidated motor-current synthesis" not in claim_notes
        assert contract["simulation_report_file"] == "astrograph/simulation_report.json"
        assert contract["metadata"]["canonical_target_wheel_index"] in {0, 1, 2}

        case_root = result.campaign.output_root / "cases" / contract["dataset_id"]
        telemetry = case_root / contract["telemetry"]["file"]
        rows = list(csv.DictReader(telemetry.open(encoding="utf-8")))
        assert rows
        assert "sensor.adcs.rw.speed_rad_s_0" in rows[0]
        assert "estimate.adcs.rw.actual_torque_nm_0" in rows[0]

    friction = by_class["ADCS_RW_FRICTION_INCREASE"]
    friction_root = result.campaign.output_root / "cases" / friction["dataset_id"]
    friction_rows = list(csv.DictReader((friction_root / friction["telemetry"]["file"]).open(encoding="utf-8")))
    active_friction = [row for row in friction_rows if row["label.degradation_active"] == "True"]
    assert active_friction
    assert any(float(row["estimate.adcs.rw.friction_torque_nm_0"]) > 0 for row in active_friction)

    authority = by_class["ADCS_RW_TORQUE_AUTHORITY_LOSS"]
    authority_root = result.campaign.output_root / "cases" / authority["dataset_id"]
    authority_rows = list(csv.DictReader((authority_root / authority["telemetry"]["file"]).open(encoding="utf-8")))
    active_authority = [row for row in authority_rows if row["label.degradation_active"] == "True"]
    assert active_authority
    assert any(float(row["estimate.adcs.rw.effective_max_torque_nm_0"]) < 0.05 for row in active_authority)
