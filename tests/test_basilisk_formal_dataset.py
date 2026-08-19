from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.campaign import expand_campaign_spec
from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.form_schema import capability_form_schema
from sat_sim.simulation_fidelity import BasiliskRuntimeStatus, classify_simulation_fidelity
from sat_sim.task_validator import validate_task_spec


def test_proxy_dataset_requires_explicit_test_opt_in() -> None:
    with pytest.raises(ValueError, match="formal AstroGraph datasets"):
        FaultDatasetFactoryConfig(
            capability_id="subsystem.adcs_fidelity.v1",
            simulation_backend="test_proxy",
        )


def test_signal_gate_is_exactly_eighteen_basilisk_cases() -> None:
    config = FaultDatasetFactoryConfig.from_json(
        Path("configs/integration/rw_native_validation.json")
    )
    spec = build_fault_dataset_campaign(config)
    assert len(spec["campaign"]["cases"]) == 18
    base = spec["campaign"]["base_spec"]
    assert base["capability_id"] == "subsystem.adcs_unified_native.v1"
    assert base["simulation"]["backend"] == "basilisk"
    assert base["assurance"]["allow_proxy"] is False
    assert spec["metadata"]["formal_dataset_path"] is True



def test_signal_gate_children_pass_strict_taskspec_validation(tmp_path: Path) -> None:
    config = FaultDatasetFactoryConfig.from_json(
        Path("configs/integration/rw_native_validation.json")
    )
    spec = build_fault_dataset_campaign(config)
    plan = expand_campaign_spec(spec, output_root=tmp_path / "plan")
    failures: dict[str, list[str]] = {}
    for case in plan.cases:
        result = validate_task_spec(case.spec)
        if not result.ok:
            failures[case.case_id] = [f"{issue.path}: {issue.message}" for issue in result.errors]
    assert failures == {}


def test_campaign_top_level_does_not_require_capability_id() -> None:
    spec = {
        "schema_version": "0.1.0",
        "task_id": "campaign_validation_no_top_level_capability",
        "task_type": "campaign",
        "simulation": {"duration_s": 2.0, "sample_s": 1.0, "seed": 7},
        "outputs": {"output_root": "runs", "trace_format": "csv"},
        "campaign": {
            "sampling": "manual",
            "count": 1,
            "base_spec": {
                "schema_version": "0.1.0",
                "task_id": "battery_child",
                "task_type": "component",
                "capability_id": "component.battery.v1",
                "target": {"level": "component", "name": "battery", "mode": "nominal"},
                "simulation": {"duration_s": 2.0, "sample_s": 1.0},
                "parameters": {
                    "capacity_wh": 160.0,
                    "initial_soc": 0.6,
                    "net_power_w": -20.0,
                    "charge_efficiency": 0.98,
                    "discharge_efficiency": 0.97,
                    "min_soc": 0.0,
                    "max_soc": 1.0,
                },
                "faults": [],
                "modifiers": {"faults": [], "degradations": []},
                "outputs": {"output_root": "runs", "trace_format": "csv"},
            },
            "cases": [{"case_id": "battery_nominal", "overrides": {}}],
        },
    }

    result = validate_task_spec(spec)

    assert result.ok

def test_unified_native_catalog_exposes_partial_torque_authority_loss() -> None:
    schema = capability_form_schema("subsystem.adcs_unified_native.v1")
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}
    assert "adcs_rw_torque_authority_loss" in degradations


def test_fidelity_classifier_fails_closed_without_runtime_evidence() -> None:
    incomplete = classify_simulation_fidelity(
        capability_id="subsystem.adcs_unified_native.v1",
        backend="basilisk",
        bsk_version="2.11.0",
        runtime_summary={},
        runtime_evidence={},
    )
    assert incomplete["formal_training_eligible"] is False
    assert incomplete["fidelity_level"] == "B_BASILISK_UNVERIFIED_OR_INCOMPLETE"

    complete = classify_simulation_fidelity(
        capability_id="subsystem.adcs_unified_native.v1",
        backend="basilisk",
        bsk_version="2.11.0",
        runtime_summary={"runtime_truth_status": "instantiated_connected_recorded_unified"},
        runtime_evidence={"runtime_manifest": {"instantiated_modules": [{"tag": "spacecraft"}]}},
    )
    assert complete["formal_training_eligible"] is True
    assert complete["fidelity_level"] == "A_ENGINEERING_BASILISK_NATIVE"


def test_formal_execution_requires_local_basilisk_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = BasiliskRuntimeStatus(
        installed=False,
        importable=False,
        version=None,
        accepted_version=False,
        ready=False,
        reason="test_runtime_unavailable",
    )
    monkeypatch.setattr(
        "sat_sim.simulation_fidelity.inspect_basilisk_runtime",
        lambda: unavailable,
    )
    config = FaultDatasetFactoryConfig(
        counts=SplitCounts(train=0, validation=1, test=0),
        duration_s=2.0,
        sample_s=1.0,
        solver_step_s=0.2,
    )
    spec = build_fault_dataset_campaign(config)
    with pytest.raises(RuntimeError, match="formal Basilisk dataset execution requires"):
        run_fault_dataset_campaign(spec, output_root=tmp_path / "blocked", continue_on_error=False)
