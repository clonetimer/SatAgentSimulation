from __future__ import annotations

from pathlib import Path

from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig, SplitCounts, build_fault_dataset_campaign, run_fault_dataset_campaign,
)
from sat_sim.rw_fault_scientific_closure import build_feature_table, run_scientific_closure


def test_scientific_closure_generates_non_leaking_features_and_baselines(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig(
        campaign_id="phase3i_test",
        counts=SplitCounts(train=2, validation=1, test=1),
        duration_s=12.0, sample_s=1.0, solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1", simulation_backend="python", allow_test_proxy=True,
    )
    execution = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=tmp_path / "dataset", continue_on_error=False
    )
    table = build_feature_table(execution.campaign.output_root)
    assert set(table["label"]) == {
        "NORMAL", "ADCS_RW_FRICTION_INCREASE", "ADCS_RW_JAM", "ADCS_RW_TORQUE_AUTHORITY_LOSS"
    }
    model_columns = [column for column in table if column not in {
        "pair_id", "split", "fault_family", "fidelity_level", "onset_s", "dataset_id", "case_role", "label"
    } and not column.startswith("audit__")]
    assert model_columns
    assert not any("available" in column or "effective_max_torque" in column for column in model_columns)
    result = run_scientific_closure(execution.campaign.output_root, output_dir=tmp_path / "analysis")
    assert result.report["formal_diagnostic_claim_allowed"] is False
    assert result.report["selected_engineering_model"]
    assert result.feature_table_path.is_file()
    assert result.selected_model_path and result.selected_model_path.is_file()
    assert result.astrograph_manifest_path and result.astrograph_manifest_path.is_file()
