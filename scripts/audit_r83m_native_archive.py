"""Read-only audit of the SHA-pinned, 54-case R83m original Basilisk archive.

The source artifact remains immutable; this program does not synthesize traces,
change physics, label an unobservable fault as healthy, or export ML features.
Unit tests use *synthetic fixtures*, never substitute them for archived evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import sys
from typing import Any, Mapping
from zipfile import BadZipFile, ZipFile

SCHEMA_VERSION = "sat-sim.r83m.original-native-artifact-audit.v1"
SOURCE_SCHEMA = "sat-sim.r83m.real-native-guidance-threshold-sweep.v1"
ARCHIVE_SHA256 = "db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796"
ORIGINAL_JSON_NAME = "r83m_genuine_native_54_original.json"
ORIGINAL_JSON_SHA256 = "635fd4e4cd36da39a15ba1289003ba84aaf4551b02de70c3d063ada878209502"
COMPAT_NAME = "compat_payload_review.json"
COMPAT_SHA256 = "3f279f92c4e424ce0625083a190dda1b9ab183727fb789eb976abded67b9cdb9"
PAIRS_PER_MODE = 9
SPEED_MANIFESTATION_THRESHOLD = 1e-8  # Original R83m reporting convention, not a detection threshold.
MODES = ("zero", "pulse05", "pulse09")
ROLES = ("healthy", "loss75")
EXPECTED_NATIVE_CHAIN = ["inertial3D", "attTrackingError", "mrpFeedback", "rwMotorTorque", "reactionWheelStateEffector", "spacecraft"]
CASE_PATTERN = re.compile(r"rw([0-2])_initial([0-2])_(zero|pulse05|pulse09)_(healthy|loss75)\Z")
ROW_FIELDS = frozenset(
    ["time_s", "rw.native_target_applied_torque_nm_QA_ONLY"]
    + [f"{name}_{i}" for name in (
        "fsw.body_request_nm", "fsw.native_reference_mrp", "hub.omega_bn_b_rad_s",
        "hub.sigma_bn", "rw.native_allocator_request_nm", "rw.native_speed_rad_s",
    ) for i in range(3)]
)
QA_FIELD = "rw.native_target_applied_torque_nm_QA_ONLY"


class AuditError(ValueError):
    """Invalid original archive, or failure to satisfy the frozen research contract."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AuditError(code)


def same_number(actual: Any, expected: float, *, atol: float = 1e-11) -> bool:
    return (type(actual) in (int, float) and math.isfinite(actual)
            and math.isclose(float(actual), expected, rel_tol=1e-10, abs_tol=atol))


def expected_case_ids() -> set[str]:
    return {f"rw{wheel}_initial{initial}_{mode}_{role}" for wheel in range(3)
            for initial in range(3) for mode in MODES for role in ROLES}


def audit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute archived paired effects from actual case rows, not just source summaries."""
    require(payload.get("schema") == SOURCE_SCHEMA, "SOURCE_SCHEMA_MISMATCH")
    require(payload.get("execution_engine") == "GENUINE_BASILISK_REACTIONWHEELSTATEEFFECTOR_AND_FSW", "EXECUTION_ENGINE_MISMATCH")
    for name, expected in (
        ("source_native_cases", 54), ("healthy_source_cases", 27),
        ("paired_fault_vs_nominal_comparisons", 27),
        ("independent_wheel_and_initial_condition_groups", 9),
    ):
        require(type(payload.get(name)) is int and payload[name] == expected, f"SOURCE_COUNT_MISMATCH:{name}")
    for name, expected in (
        ("fault_truth_only_for_offline_physics_QA", True),
        ("fault_derating_verified_by_real_cpp_state", True),
        ("actual_applied_motor_torque_verified_from_rwOutMsgs", True),
        ("no_machine_learning_trained", True),
        ("no_protected_validation_test_final_cases_generated", True),
        ("stable_release_eligible", False),
        ("not_a_generalizable_diagnostic_benchmark", True),
    ):
        require(payload.get(name) is expected, f"ASSURANCE_FLAG_MISMATCH:{name}")
    cases = payload.get("physical_source_cases")
    paired = payload.get("native_physical_paired_outcomes")
    require(isinstance(cases, dict) and set(cases) == expected_case_ids(), "SOURCE_CASE_GRID_MISMATCH")
    expected_pairs = {name for name in cases if name.endswith("_loss75")}
    require(isinstance(paired, dict) and set(paired) == expected_pairs, "PAIR_GRID_MISMATCH")

    all_modes: Counter[str] = Counter()
    unique_groups: Counter[str] = Counter()
    global_max_speed = 0.0
    global_max_request = 0.0
    global_max_body_rate = 0.0
    mode_max_request: dict[str, float] = {m: 0.0 for m in MODES}
    mode_max_body_rate: dict[str, float] = {m: 0.0 for m in MODES}
    for case_id, case in sorted(cases.items()):
        require(isinstance(case, dict), f"INVALID_CASE:{case_id}")
        match = CASE_PATTERN.fullmatch(case_id)
        require(match is not None, f"INVALID_CASE_ID:{case_id}")
        wheel, initial, mode, role = match.groups()
        group, context = f"rw{wheel}_initial{initial}", f"rw{wheel}_initial{initial}_{mode}"
        expected_cap = 0.005 if role == "loss75" else 0.02
        for k, v in (
            ("arm", case_id), ("native_reference_mode", mode),
            ("native_independent_condition_id", group),
            ("native_reference_context_id", context),
            ("native_fault_capacity_mode_QA_ONLY", role),
        ):
            require(case.get(k) == v, f"CASE_METADATA_CONFLICT:{case_id}:{k}")
        require(case.get("source_fault_target_index_QA_ONLY") == int(wheel), f"FAULT_TARGET_MISMATCH:{case_id}")
        require(case.get("source_initial_state_index") == int(initial), f"INITIAL_STATE_MISMATCH:{case_id}")
        require(case.get("native_module_chain") == EXPECTED_NATIVE_CHAIN, f"NATIVE_CHAIN_MISMATCH:{case_id}")
        require(case.get("model_inference_uses_truth") is False, f"TRUTH_LEAKAGE_FLAG:{case_id}")
        require(case.get("native_scheduler_was_advanced") is True, f"NATIVE_SCHEDULER_MISSING:{case_id}")
        require(case.get("reference_is_guidance_not_wheel_motor_telecommand") is True, f"REFERENCE_PROVENANCE_MISSING:{case_id}")
        require(same_number(case.get("original_native_rw_hardware_limit_nm"), 0.02), f"NOMINAL_CAP_MISMATCH:{case_id}")
        require(same_number(case.get("fault_native_rw_capacity_after_nm_QA_ONLY"), expected_cap), f"FAULT_CAP_MISMATCH:{case_id}")
        require(same_number(case.get("fault_onset_paused_scheduler_s_QA_ONLY"), 2.4), f"FAULT_ONSET_MISMATCH:{case_id}")
        require(case.get("true_native_actuator_capacity_derated") is (role == "loss75"), f"DERATING_FLAG_MISMATCH:{case_id}")
        receipts = case.get("reference_setpoint_event_receipts")
        require(isinstance(receipts, list), f"NATIVE_RECEIPTS_MISSING:{case_id}")
        fault_receipts = [x for x in receipts if isinstance(x, dict) and "rw_effector_c_backed_readback_nm" in x]
        require(len(fault_receipts) == 1, f"FAULT_READBACK_MISSING:{case_id}")
        receipt = fault_receipts[0]
        require(receipt.get("physical_fault_target_rw_index_QA_ONLY") == int(wheel), f"FAULT_READBACK_TARGET_MISMATCH:{case_id}")
        require(same_number(receipt.get("rw_effector_c_backed_readback_nm"), expected_cap), f"FAULT_CPP_READBACK_MISMATCH:{case_id}")
        require(receipt.get("real_native_physical_change_not_feature_edit") is True, f"FAULT_PHYSICS_PROVENANCE_MISSING:{case_id}")
        require(isinstance(case.get("rows"), list) and len(case["rows"]) == 31, f"SAMPLE_COUNT_MISMATCH:{case_id}")
        for index, row in enumerate(case["rows"]):
            require(isinstance(row, dict) and set(row) == ROW_FIELDS, f"ROW_FIELDS_MISMATCH:{case_id}:{index}")
            require(all(type(v) in (int, float) and math.isfinite(v) for v in row.values()), f"ROW_NONFINITE:{case_id}:{index}")
            require(same_number(row["time_s"], 0.2 * index), f"SAMPLE_TIME_MISMATCH:{case_id}:{index}")
            actual_torque = abs(row[QA_FIELD])
            if index >= 13:  # First native sample after the 2.4 s paused-scheduler event.
                require(actual_torque <= expected_cap + 1e-10, f"NATIVE_TORQUE_CAP_VIOLATION:{case_id}:{index}")
            for i in range(3):
                global_max_speed = max(global_max_speed, abs(row[f"rw.native_speed_rad_s_{i}"]))
                request = abs(row[f"rw.native_allocator_request_nm_{i}"])
                global_max_request = max(global_max_request, request)
                mode_max_request[mode] = max(mode_max_request[mode], request)
                rate = abs(row[f"hub.omega_bn_b_rad_s_{i}"])
                global_max_body_rate = max(global_max_body_rate, rate)
                mode_max_body_rate[mode] = max(mode_max_body_rate[mode], rate)
        all_modes[mode] += 1
        unique_groups[group] += 1
    require(set(unique_groups) == {f"rw{i}_initial{j}" for i in range(3) for j in range(3)}
            and all(v == 6 for v in unique_groups.values()), "INDEPENDENT_GROUPS_MISMATCH")
    require(all(all_modes[m] == 18 for m in MODES), "REFERENCE_MODE_BALANCE_MISMATCH")
    for name, actual in (
        ("max_original_native_allocator_request_nm", global_max_request),
        ("max_original_native_body_rate_rad_s", global_max_body_rate),
        ("max_original_native_wheel_speed_rad_s", global_max_speed),
    ):
        require(same_number(payload.get(name), actual), f"SOURCE_MAXIMUM_MISMATCH:{name}")
    mode_summaries = payload.get("original_native_request_by_reference_mode")
    require(isinstance(mode_summaries, dict) and set(mode_summaries) == set(MODES), "MODE_SUMMARY_MISSING")
    for mode in MODES:
        summary = mode_summaries[mode]
        require(isinstance(summary, dict), f"MODE_SUMMARY_INVALID:{mode}")
        require(same_number(summary.get("peak_allocator_command_nm_all_rw"), mode_max_request[mode]), f"MODE_TORQUE_REQUEST_MISMATCH:{mode}")
        require(same_number(summary.get("max_body_rate_rad_s"), mode_max_body_rate[mode]), f"MODE_BODY_RATE_MISMATCH:{mode}")

    positive_speed: Counter[str] = Counter()
    positive_torque: Counter[str] = Counter()
    speeds: dict[str, list[float]] = {m: [] for m in MODES}
    torques: dict[str, list[float]] = {m: [] for m in MODES}
    nonqa_different_modes: Counter[str] = Counter()
    for pair_id, outcome in sorted(paired.items()):
        require(isinstance(outcome, dict), f"PAIR_INVALID:{pair_id}")
        context = pair_id.removesuffix("_loss75")
        healthy, fault = cases[context + "_healthy"], cases[pair_id]
        mode = fault["native_reference_mode"]
        wheel = fault["source_fault_target_index_QA_ONLY"]
        require(outcome.get("context") == context and outcome.get("reference_mode") == mode,
                f"PAIR_METADATA_CONFLICT:{pair_id}")
        require(outcome.get("native_independent_condition_id") == fault["native_independent_condition_id"], f"PAIR_GROUP_CONFLICT:{pair_id}")
        require(outcome.get("source_fault_target_index_QA_ONLY") == wheel, f"PAIR_TARGET_CONFLICT:{pair_id}")
        require(same_number(outcome.get("fault_native_cap_after_nm"), 0.005), f"PAIR_CAP_MISMATCH:{pair_id}")
        before_speed = 0.0
        after_speed = 0.0
        after_torque = 0.0
        nonqa_different = False
        for a, b in zip(healthy["rows"], fault["rows"]):
            require(same_number(a["time_s"], b["time_s"]), f"PAIRED_TIME_MISMATCH:{pair_id}")
            speed_delta = abs(a[f"rw.native_speed_rad_s_{wheel}"] - b[f"rw.native_speed_rad_s_{wheel}"])
            if a["time_s"] <= 2.4:
                before_speed = max(before_speed, speed_delta)
                require(all(a[key] == b[key] for key in ROW_FIELDS if key != QA_FIELD), f"PREFAULT_OBSERVABLE_MISMATCH:{pair_id}")
            else:
                after_speed = max(after_speed, speed_delta)
                after_torque = max(after_torque, abs(a[QA_FIELD] - b[QA_FIELD]))
            if any(a[key] != b[key] for key in ROW_FIELDS if key not in ("time_s", QA_FIELD)):
                nonqa_different = True
        require(same_number(outcome.get("pre_event_max_native_rw_speed_difference_rad_s"), before_speed), f"PREFAULT_SPEED_SUMMARY_MISMATCH:{pair_id}")
        require(same_number(outcome.get("post_event_max_native_rw_speed_difference_rad_s"), after_speed), f"POSTFAULT_SPEED_SUMMARY_MISMATCH:{pair_id}")
        require(same_number(outcome.get("post_event_max_native_rw_applied_torque_difference_nm"), after_torque), f"POSTFAULT_TORQUE_SUMMARY_MISMATCH:{pair_id}")
        speed_manifest = after_speed > SPEED_MANIFESTATION_THRESHOLD
        torque_manifest = after_torque > SPEED_MANIFESTATION_THRESHOLD
        require(outcome.get("actual_physical_speed_manifestation") is speed_manifest, f"SPEED_MANIFESTATION_MISMATCH:{pair_id}")
        require(outcome.get("actual_physical_torque_manifestation") is torque_manifest, f"TORQUE_MANIFESTATION_MISMATCH:{pair_id}")
        positive_speed[mode] += int(speed_manifest)
        positive_torque[mode] += int(torque_manifest)
        nonqa_different_modes[mode] += int(nonqa_different)
        speeds[mode].append(after_speed)
        torques[mode].append(after_torque)

    # These numbers are the original fixed-design research result, NOT a detector gate.
    require(all(positive_speed[m] == (9 if m == "pulse09" else 0) and
                positive_torque[m] == (9 if m == "pulse09" else 0) for m in MODES), "SOURCE_OBSERVABILITY_PATTERN_CHANGED")
    require(all(nonqa_different_modes[m] == (9 if m == "pulse09" else 0) for m in MODES), "SOURCE_NONQA_DIFFERENCE_PATTERN_CHANGED")
    summary = {}
    for mode in MODES:
        speed_values = speeds[mode]
        torque_values = torques[mode]
        require(len(speed_values) == PAIRS_PER_MODE, f"PAIR_COUNT_MISMATCH:{mode}")
        summary[mode] = {
            "pairs": len(speed_values),
            "native_wheel_speed_effect_count": positive_speed[mode],
            "native_applied_torque_effect_count": positive_torque[mode],
            "nonqa_source_channel_effect_count": nonqa_different_modes[mode],
            "speed_max_delta_rad_s_min": min(speed_values),
            "speed_max_delta_rad_s_median": median(speed_values),
            "speed_max_delta_rad_s_max": max(speed_values),
            "applied_torque_max_delta_nm_min": min(torque_values),
            "applied_torque_max_delta_nm_max": max(torque_values),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "physics_archive_verified": False,  # Only audit_archive can establish SHA-pinned provenance.
        "case_count": len(cases),
        "paired_count": len(paired),
        "wheel_initial_condition_groups": len(unique_groups),  # Not independently manufactured spacecraft.
        "sample_rows": sum(len(case["rows"]) for case in cases.values()),
        "observability_by_reference_mode": summary,
        "model_feature_matrix_generated": False,  # QA-only input audit, not a feature exporter.
        "formal_training_or_test_eligible": False,
        "formal_eligibility_reason": "Native engineering physics source lacks realistic sensor measurement/noise and frozen independent evaluation cohorts.",
        "requested_torque_max_nm": global_max_request,
        "native_wheel_speed_max_rad_s": global_max_speed,
        "native_body_rate_max_rad_s": global_max_body_rate,
        "source_is_immutable": False,  # Only true once original ZIP hash has been verified.
    }


def audit_archive(path: Path) -> dict[str, Any]:
    """Accept only the exact archived original; never rewrite or silently repair it."""
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA256, "ARCHIVE_SHA256_MISMATCH")
    try:
        with ZipFile(path, "r") as archive:
            files = archive.infolist()
            require({item.filename for item in files} == {ORIGINAL_JSON_NAME, COMPAT_NAME} and len(files) == 2, "ARCHIVE_FILES_MISMATCH")
            require(all(item.file_size <= 3_000_000 for item in files), "ARCHIVE_MEMBER_TOO_LARGE")
            require(archive.testzip() is None, "ARCHIVE_CRC_MISMATCH")
            original = archive.read(ORIGINAL_JSON_NAME)
            compat = archive.read(COMPAT_NAME)
    except (BadZipFile, KeyError, OSError) as exc:
        raise AuditError("ARCHIVE_DECODE_FAILED") from exc
    require(hashlib.sha256(original).hexdigest() == ORIGINAL_JSON_SHA256, "ORIGINAL_JSON_SHA256_MISMATCH")
    require(hashlib.sha256(compat).hexdigest() == COMPAT_SHA256, "COMPAT_SHA256_MISMATCH")
    try:
        payload = json.loads(original)
        compat_payload = json.loads(compat)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuditError("ARCHIVE_JSON_INVALID") from exc
    require(isinstance(payload, dict), "ORIGINAL_JSON_NOT_OBJECT")
    require(isinstance(compat_payload, dict) and compat_payload.get("status") == "PASS", "COMPAT_REVIEW_INVALID")
    results = compat_payload.get("results")
    require(isinstance(results, list) and len(results) == 1, "COMPAT_RESULTS_INVALID")
    require(results[0].get("payload_bytes_unchanged") is True and results[0].get("payload_file_count") == 880,
            "BASILISK_PAYLOAD_COMPAT_MISMATCH")
    report = audit_payload(payload)
    report["physics_archive_verified"] = True
    report["source_is_immutable"] = True
    report["archive_sha256"] = ARCHIVE_SHA256
    report["archive_bytes"] = len(raw)
    report["source_json_sha256"] = ORIGINAL_JSON_SHA256
    report["compat_sha256"] = COMPAT_SHA256
    report["artifact_source"] = "clonetimer/SatAgentSimulation Actions 40160788578 artifact 12586915899"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Original unmodified GitHub Actions artifact ZIP")
    parser.add_argument("--report", type=Path, help="Optional JSON report path (does not alter source archive)")
    args = parser.parse_args(argv)
    try:
        result = audit_archive(args.archive)
    except (AuditError, OSError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "ok": False, "reason_code": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
