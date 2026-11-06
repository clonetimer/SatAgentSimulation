"""Synthetic-fixture contract tests; no synthetic traces are labelled as Basilisk evidence."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit_r83m_native_archive import (
    AuditError,
    EXPECTED_NATIVE_CHAIN,
    ROW_FIELDS,
    SCHEMA_VERSION,
    SOURCE_SCHEMA,
    audit_archive,
    audit_payload,
    expected_case_ids,
)


def synthetic_contract_fixture() -> dict:
    """Synthetic rows ONLY to exercise the read-only verifier's negative gates."""
    cases = {}
    paired = {}
    for wheel in range(3):
        for initial in range(3):
            group = f"rw{wheel}_initial{initial}"
            for mode in ("zero", "pulse05", "pulse09"):
                context = f"{group}_{mode}"
                for role in ("healthy", "loss75"):
                    case_id = f"{context}_{role}"
                    cap = 0.005 if role == "loss75" else 0.020
                    rows = []
                    for index in range(31):
                        row = {field: 0.0 for field in ROW_FIELDS}
                        row["time_s"] = 0.2 * index
                        for i, speed in enumerate((10.0, 20.0, 30.0)):
                            row[f"rw.native_speed_rad_s_{i}"] = speed
                        if index >= 13 and mode == "pulse09":
                            row["rw.native_target_applied_torque_nm_QA_ONLY"] = 0.006 if role == "healthy" else 0.004
                            if role == "loss75":
                                row[f"rw.native_speed_rad_s_{wheel}"] += 0.003
                        rows.append(row)
                    cases[case_id] = {
                        "arm": case_id,
                        "native_reference_mode": mode,
                        "native_independent_condition_id": group,
                        "native_reference_context_id": context,
                        "native_fault_capacity_mode_QA_ONLY": role,
                        "source_fault_target_index_QA_ONLY": wheel,
                        "source_initial_state_index": initial,
                        "native_module_chain": EXPECTED_NATIVE_CHAIN,
                        "model_inference_uses_truth": False,
                        "native_scheduler_was_advanced": True,
                        "reference_is_guidance_not_wheel_motor_telecommand": True,
                        "original_native_rw_hardware_limit_nm": 0.020,
                        "fault_native_rw_capacity_after_nm_QA_ONLY": cap,
                        "fault_onset_paused_scheduler_s_QA_ONLY": 2.4,
                        "true_native_actuator_capacity_derated": role == "loss75",
                        "reference_setpoint_event_receipts": [{
                            "rw_effector_c_backed_readback_nm": cap,
                            "physical_fault_target_rw_index_QA_ONLY": wheel,
                            "real_native_physical_change_not_feature_edit": True,
                        }],
                        "rows": rows,
                    }
                manifest = mode == "pulse09"
                paired[context + "_loss75"] = {
                    "context": context,
                    "reference_mode": mode,
                    "native_independent_condition_id": group,
                    "source_fault_target_index_QA_ONLY": wheel,
                    "fault_native_cap_after_nm": 0.005,
                    "pre_event_max_native_rw_speed_difference_rad_s": 0.0,
                    "post_event_max_native_rw_speed_difference_rad_s": 0.003 if manifest else 0.0,
                    "post_event_max_native_rw_applied_torque_difference_nm": 0.002 if manifest else 0.0,
                    "actual_physical_speed_manifestation": manifest,
                    "actual_physical_torque_manifestation": manifest,
                }
    return {
        "schema": SOURCE_SCHEMA,
        "execution_engine": "GENUINE_BASILISK_REACTIONWHEELSTATEEFFECTOR_AND_FSW",
        "source_native_cases": 54,
        "healthy_source_cases": 27,
        "paired_fault_vs_nominal_comparisons": 27,
        "independent_wheel_and_initial_condition_groups": 9,
        "fault_truth_only_for_offline_physics_QA": True,
        "fault_derating_verified_by_real_cpp_state": True,
        "actual_applied_motor_torque_verified_from_rwOutMsgs": True,
        "no_machine_learning_trained": True,
        "no_protected_validation_test_final_cases_generated": True,
        "stable_release_eligible": False,
        "not_a_generalizable_diagnostic_benchmark": True,
        "physical_source_cases": cases,
        "native_physical_paired_outcomes": paired,
        "max_original_native_allocator_request_nm": 0.0,
        "max_original_native_body_rate_rad_s": 0.0,
        "max_original_native_wheel_speed_rad_s": 30.003,
        "original_native_request_by_reference_mode": {
            m: {"peak_allocator_command_nm_all_rw": 0.0, "max_body_rate_rad_s": 0.0}
            for m in ("zero", "pulse05", "pulse09")
        },
    }


def test_synthetic_contract_fixture_covers_full_r83m_grid_without_claiming_physical_data():
    fixture = synthetic_contract_fixture()
    assert len(expected_case_ids()) == 54
    report = audit_payload(fixture)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["case_count"] == 54
    assert report["sample_rows"] == 1674
    assert report["formal_training_or_test_eligible"] is False
    assert report["physics_archive_verified"] is False
    assert report["wheel_initial_condition_groups"] == 9
    assert [report["observability_by_reference_mode"][m]["native_wheel_speed_effect_count"] for m in ("zero", "pulse05", "pulse09")] == [0, 0, 9]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("schema", "SOURCE_SCHEMA_MISMATCH"),
        ("case", "SOURCE_CASE_GRID_MISMATCH"),
        ("time", "SAMPLE_TIME_MISMATCH"),
        ("qa", "FAULT_CPP_READBACK_MISMATCH"),
        ("truth", "TRUTH_LEAKAGE_FLAG"),
        ("delta", "POSTFAULT_SPEED_SUMMARY_MISMATCH"),
        ("prefault", "PREFAULT_OBSERVABLE_MISMATCH"),
        ("cap", "NATIVE_TORQUE_CAP_VIOLATION"),
        ("summary", "SOURCE_MAXIMUM_MISMATCH"),
    ],
)
def test_synthetic_tampering_is_rejected_with_machine_readable_reason(mutation, reason):
    payload = synthetic_contract_fixture()
    if mutation == "schema":
        payload["schema"] = "unknown"
    elif mutation == "case":
        del payload["physical_source_cases"]["rw0_initial0_zero_healthy"]
    elif mutation == "time":
        payload["physical_source_cases"]["rw0_initial0_zero_healthy"]["rows"][1]["time_s"] = 9.0
    elif mutation == "qa":
        payload["physical_source_cases"]["rw0_initial0_pulse09_loss75"]["reference_setpoint_event_receipts"][0]["rw_effector_c_backed_readback_nm"] = 0.02
    elif mutation == "truth":
        payload["physical_source_cases"]["rw0_initial0_zero_loss75"]["model_inference_uses_truth"] = True
    elif mutation == "delta":
        payload["native_physical_paired_outcomes"]["rw0_initial0_pulse09_loss75"]["post_event_max_native_rw_speed_difference_rad_s"] = 0.01
    elif mutation == "prefault":
        payload["physical_source_cases"]["rw0_initial0_zero_loss75"]["rows"][3]["rw.native_speed_rad_s_0"] += 0.1
    elif mutation == "cap":
        payload["physical_source_cases"]["rw0_initial0_pulse09_loss75"]["rows"][13]["rw.native_target_applied_torque_nm_QA_ONLY"] = 0.01
    elif mutation == "summary":
        payload["max_original_native_wheel_speed_rad_s"] = 100.0
    with pytest.raises(AuditError, match=reason):
        audit_payload(payload)


def test_nonoriginal_archive_is_rejected_by_frozen_hash(tmp_path: Path):
    archive = tmp_path / "substituted.zip"
    archive.write_bytes(b"synthetic fixture is not an original Basilisk archive")
    with pytest.raises(AuditError, match="ARCHIVE_SHA256_MISMATCH"):
        audit_archive(archive)
