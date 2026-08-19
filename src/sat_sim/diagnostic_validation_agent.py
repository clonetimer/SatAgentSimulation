"""Deterministic diagnostic-signature validation for paired experiments.

This layer is intentionally separate from physics validation.  A simulator can
apply a fault correctly while producing a weak or non-distinguishing telemetry
signature.  The agent evaluates detectability and persistence without changing
telemetry or approving a dataset for training.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from .diagnostic_signature_library import DiagnosticSignature, get_diagnostic_signature

DIAGNOSTIC_PAIR_VALIDATION_SCHEMA_VERSION = "sat-sim.diagnostic-pair-validation.v1"
DIAGNOSTIC_QUALIFICATION_INDEX_SCHEMA_VERSION = "sat-sim.diagnostic-qualification-index.v1"


@dataclass(frozen=True)
class DiagnosticCriterionResult:
    criterion_id: str
    status: str
    required: bool
    weight: float
    channel: str
    metric: str
    evidence_role: str
    contributes_to_diagnostic_score: bool
    observed: Any
    expected: Any
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "status": self.status,
            "required": self.required,
            "weight": self.weight,
            "channel": self.channel,
            "metric": self.metric,
            "evidence_role": self.evidence_role,
            "contributes_to_diagnostic_score": self.contributes_to_diagnostic_score,
            "observed": self.observed,
            "expected": self.expected,
            "summary": self.summary,
        }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes"}:
            return 1.0
        if text in {"false", "no"}:
            return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _time_index(rows: Sequence[Mapping[str, Any]]) -> dict[float, Mapping[str, Any]]:
    output: dict[float, Mapping[str, Any]] = {}
    for row in rows:
        time = _number(row.get("time_s"))
        if time is not None:
            output[time] = row
    return output


def _series(rows: Sequence[Mapping[str, Any]], channel: str, *, start: float) -> list[float]:
    values: list[float] = []
    for row in rows:
        time = _number(row.get("time_s"))
        value = _number(row.get(channel))
        if time is not None and value is not None and time >= start:
            values.append(value)
    return values


def _paired_series(
    fault_rows: Sequence[Mapping[str, Any]], nominal_rows: Sequence[Mapping[str, Any]], channel: str, *, start: float
) -> list[tuple[float, float, float]]:
    fault = _time_index(fault_rows)
    nominal = _time_index(nominal_rows)
    values: list[tuple[float, float, float]] = []
    for time in sorted(set(fault).intersection(nominal)):
        if time < start:
            continue
        fault_value = _number(fault[time].get(channel))
        nominal_value = _number(nominal[time].get(channel))
        if fault_value is not None and nominal_value is not None:
            values.append((time, fault_value, nominal_value))
    return values


def _evaluate_criterion(
    criterion: Mapping[str, Any],
    *,
    fault_rows: Sequence[Mapping[str, Any]],
    nominal_rows: Sequence[Mapping[str, Any]],
    onset: float,
) -> DiagnosticCriterionResult:
    criterion_id = str(criterion.get("criterion_id"))
    channel = str(criterion.get("channel"))
    metric = str(criterion.get("metric"))
    threshold = float(criterion.get("threshold"))
    required = bool(criterion.get("required", True))
    weight = float(criterion.get("weight", 1.0))
    evidence_role = str(criterion.get("evidence_role") or "operational_estimate")
    contributes = bool(criterion.get("contributes_to_diagnostic_score", evidence_role != "simulator_truth"))
    fault_post = _series(fault_rows, channel, start=onset)
    nominal_post = _series(nominal_rows, channel, start=onset)
    paired = _paired_series(fault_rows, nominal_rows, channel, start=onset)
    observed: Any = None
    passed = False

    if metric == "post_fault_mean_minus_nominal_gt":
        if fault_post and nominal_post:
            observed = fmean(fault_post) - fmean(nominal_post)
            passed = observed > threshold
    elif metric == "post_pair_max_abs_delta_gt":
        if paired:
            observed = max(abs(fault - nominal) for _, fault, nominal in paired)
            passed = observed > threshold
    elif metric == "post_pair_rms_delta_gt":
        if paired:
            observed = math.sqrt(fmean([(fault - nominal) ** 2 for _, fault, nominal in paired]))
            passed = observed > threshold
    elif metric == "post_fault_max_le":
        if fault_post:
            observed = max(fault_post)
            passed = observed <= threshold
    elif metric == "post_fault_to_nominal_mean_ratio_lt":
        if fault_post and nominal_post and abs(fmean(nominal_post)) > 1.0e-15:
            observed = fmean(fault_post) / fmean(nominal_post)
            passed = observed < threshold
    elif metric == "post_fault_fraction_le_gte":
        value_threshold = float(criterion.get("value_threshold", 0.0))
        if fault_post:
            observed = sum(value <= value_threshold for value in fault_post) / len(fault_post)
            passed = observed >= threshold
    elif metric == "post_pair_threshold_persistence_gte":
        delta_threshold = float(criterion.get("absolute_delta_threshold", 0.0))
        if paired:
            observed = sum(abs(fault - nominal) >= delta_threshold for _, fault, nominal in paired) / len(paired)
            passed = observed >= threshold
    elif metric == "paired_delta_onset_latency_le":
        delta_threshold = float(criterion.get("absolute_delta_threshold", 0.0))
        hits = [time - onset for time, fault, nominal in paired if abs(fault - nominal) >= delta_threshold]
        if hits:
            observed = min(hits)
            passed = observed <= threshold

    return DiagnosticCriterionResult(
        criterion_id=criterion_id,
        status="PASS" if passed else "FAIL",
        required=required,
        weight=weight,
        channel=channel,
        metric=metric,
        evidence_role=evidence_role,
        contributes_to_diagnostic_score=contributes,
        observed=observed,
        expected={"threshold": threshold, **({"absolute_delta_threshold": criterion.get("absolute_delta_threshold")} if "absolute_delta_threshold" in criterion else {})},
        summary=f"{channel}: {metric}",
    )


def _sha256_text(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DiagnosticValidationAgent:
    """Evaluate whether a fault/nominal pair contains a governed diagnostic signature."""

    def validate_pair(
        self,
        *,
        fault_root: str | Path,
        nominal_root: str | Path,
        signature: DiagnosticSignature | None = None,
    ) -> dict[str, Any]:
        fault_root = Path(fault_root)
        nominal_root = Path(nominal_root)
        fault_contract = _read_json(fault_root / "astrograph" / "dataset_contract.json")
        nominal_contract = _read_json(nominal_root / "astrograph" / "dataset_contract.json")
        fault_gt = fault_contract.get("ground_truth") if isinstance(fault_contract.get("ground_truth"), Mapping) else {}
        fault_id = str(fault_gt.get("class_id") or "")
        signature = signature or get_diagnostic_signature(fault_id)
        if signature.source_experiment_template_id != str((fault_contract.get("metadata") or {}).get("experiment_template_id")):
            raise ValueError("diagnostic signature and experiment template lineage do not match")
        onset = _number(fault_gt.get("onset_time_s"))
        telemetry = fault_contract.get("telemetry") if isinstance(fault_contract.get("telemetry"), Mapping) else {}
        nominal_telemetry = nominal_contract.get("telemetry") if isinstance(nominal_contract.get("telemetry"), Mapping) else {}
        fault_rows = _read_csv(fault_root / str(telemetry.get("file")))
        nominal_rows = _read_csv(nominal_root / str(nominal_telemetry.get("file")))

        available_fault = set(fault_rows[0]) if fault_rows else set()
        available_nominal = set(nominal_rows[0]) if nominal_rows else set()
        missing_channels = sorted(set(signature.required_channels) - available_fault.intersection(available_nominal))
        criteria: list[DiagnosticCriterionResult] = []
        if onset is not None and not missing_channels:
            criteria = [
                _evaluate_criterion(item, fault_rows=fault_rows, nominal_rows=nominal_rows, onset=onset)
                for item in signature.criteria
            ]

        required_failed = bool(missing_channels) or onset is None or any(
            item.required and item.status != "PASS" for item in criteria
        )
        total_weight = sum(max(0.0, item.weight) for item in criteria if item.contributes_to_diagnostic_score)
        passed_weight = sum(
            max(0.0, item.weight) for item in criteria
            if item.contributes_to_diagnostic_score and item.status == "PASS"
        )
        score = passed_weight / total_weight if total_weight > 0 else 0.0
        minimum_score = float(signature.qualification.get("minimum_diagnostic_score", 0.0))
        minimum_observable = int(signature.qualification.get("minimum_observable_criteria_passed", 1))
        observable_passed = sum(
            item.status == "PASS" and item.evidence_role in {"observable_telemetry", "operational_estimate"}
            for item in criteria
        )
        signature_passed = (
            not required_failed and score >= minimum_score and observable_passed >= minimum_observable
        )

        fault_quality = fault_contract.get("quality") if isinstance(fault_contract.get("quality"), Mapping) else {}
        nominal_quality = nominal_contract.get("quality") if isinstance(nominal_contract.get("quality"), Mapping) else {}
        physics_formal = bool(fault_quality.get("formal_training_qualified")) and bool(
            nominal_quality.get("formal_training_qualified")
        )
        if not signature_passed:
            decision = "REJECT"
            reason = "diagnostic_signature_requirements_failed"
        elif physics_formal:
            decision = "PASS"
            reason = "diagnostic_signature_candidate_passed_on_formal_basilisk_pair"
        else:
            decision = "REVIEW"
            reason = "diagnostic_signature_passed_but_formal_basilisk_physics_gate_missing"

        basilisk_candidate = decision == "PASS" and physics_formal
        return {
            "schema_version": DIAGNOSTIC_PAIR_VALIDATION_SCHEMA_VERSION,
            "validation_agent": "deterministic_diagnostic_validation_agent.v1",
            "pair_id": fault_gt.get("pair_id"),
            "fault_id": fault_id,
            "signature_id": signature.signature_id,
            "signature_sha256": _sha256_text(signature.to_dict()),
            "source_experiment_template_id": signature.source_experiment_template_id,
            "fault_dataset_id": fault_contract.get("dataset_id"),
            "nominal_dataset_id": nominal_contract.get("dataset_id"),
            "decision": decision,
            "reason": reason,
            "diagnostic_validation_passed": signature_passed,
            "diagnostic_score": score,
            "minimum_diagnostic_score": minimum_score,
            "observable_criteria_passed": observable_passed,
            "minimum_observable_criteria_passed": minimum_observable,
            "missing_required_channels": missing_channels,
            "basilisk_candidate_qualified": basilisk_candidate,
            "signature_threshold_status": signature.qualification.get("threshold_status"),
            "formal_diagnostic_qualified": False,
            "signature_expert_freeze_required": True,
            "criteria": [item.to_dict() for item in criteria],
            "leakage_guard": {
                "simulator_truth_criteria_count": sum(item.evidence_role == "simulator_truth" for item in criteria),
                "simulator_truth_contributing_count": sum(
                    item.evidence_role == "simulator_truth" and item.contributes_to_diagnostic_score for item in criteria
                ),
                "target_leakage_blocked": True,
            },
            "claim_boundary": {
                "flight_validated": False,
                "thresholds_are_provisional": signature.qualification.get("threshold_status") != "frozen",
                "agent_may_modify_thresholds": False,
                "agent_may_approve_training": False,
            },
        }


def _update_case_contract(root: Path, report: Mapping[str, Any], *, report_file: str) -> dict[str, Any]:
    path = root / "astrograph" / "dataset_contract.json"
    contract = _read_json(path)
    quality = contract.setdefault("quality", {})
    quality.update({
        "diagnostic_validation_status": report.get("decision"),
        "diagnostic_validation_passed": bool(report.get("diagnostic_validation_passed")),
        "diagnostic_score": report.get("diagnostic_score"),
        "basilisk_diagnostic_candidate": bool(report.get("basilisk_candidate_qualified")),
        "formal_diagnostic_qualified": bool(report.get("formal_diagnostic_qualified")),
        "candidate_training_ready": False,
        "training_ready": False,
    })
    contract["diagnostic_validation_file"] = report_file
    _write_json(path, contract)
    return contract


def _update_experiment_record(root: Path, report: Mapping[str, Any], *, report_file: str) -> None:
    path = root / "astrograph" / "experiment_record.json"
    if not path.is_file():
        return
    record = _read_json(path)
    qualification = record.setdefault("qualification", {})
    qualification.update({
        "diagnostic_validation_decision": report.get("decision"),
        "diagnostic_validation_passed": bool(report.get("diagnostic_validation_passed")),
        "diagnostic_score": report.get("diagnostic_score"),
        "basilisk_diagnostic_candidate": bool(report.get("basilisk_candidate_qualified")),
        "formal_diagnostic_qualified": False,
        "signature_review_status": "PENDING",
        "diagnostic_validation_report_file": report_file,
    })
    _write_json(path, record)


def validate_dataset_diagnostics(
    dataset_root: str | Path, contracts: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = Path(dataset_root)
    by_pair: dict[str, dict[str, Mapping[str, Any]]] = {}
    for contract in contracts:
        pairing = contract.get("pairing") if isinstance(contract.get("pairing"), Mapping) else {}
        pair_id = str(pairing.get("pair_id") or "")
        role = str(pairing.get("case_role") or "")
        if pair_id and role:
            by_pair.setdefault(pair_id, {})[role] = contract

    updated_by_id = {str(item.get("dataset_id")): dict(item) for item in contracts}
    reports: list[dict[str, Any]] = []
    agent = DiagnosticValidationAgent()
    for pair_id, pair in sorted(by_pair.items()):
        fault = pair.get("fault")
        nominal = pair.get("nominal")
        if not fault or not nominal:
            reports.append({
                "schema_version": DIAGNOSTIC_PAIR_VALIDATION_SCHEMA_VERSION,
                "pair_id": pair_id,
                "decision": "REJECT",
                "reason": "incomplete_pair",
                "diagnostic_validation_passed": False,
                "basilisk_candidate_qualified": False,
                "formal_diagnostic_qualified": False,
                "criteria": [],
            })
            continue
        fault_root = root / "cases" / str(fault.get("dataset_id"))
        nominal_root = root / "cases" / str(nominal.get("dataset_id"))
        report = agent.validate_pair(fault_root=fault_root, nominal_root=nominal_root)
        report_path = root / "validation" / "diagnostics" / f"{pair_id}.json"
        report["report_file"] = report_path.relative_to(root).as_posix()
        _write_json(report_path, report)
        reports.append(report)
        for case_root, original in ((fault_root, fault), (nominal_root, nominal)):
            updated = _update_case_contract(case_root, report, report_file=report["report_file"])
            _update_experiment_record(case_root, report, report_file=report["report_file"])
            updated["contract_file"] = str(original.get("contract_file") or "")
            updated_by_id[str(updated.get("dataset_id"))] = updated

    counts: dict[str, int] = {}
    fault_summaries: dict[str, dict[str, Any]] = {}
    for report in reports:
        decision = str(report.get("decision") or "UNKNOWN")
        counts[decision] = counts.get(decision, 0) + 1
        fault_id = str(report.get("fault_id") or "UNKNOWN")
        summary = fault_summaries.setdefault(fault_id, {
            "fault_id": fault_id,
            "pair_count": 0,
            "candidate_pass_pair_count": 0,
            "rejected_pair_count": 0,
            "minimum_independent_basilisk_pairs_for_freeze": get_diagnostic_signature(fault_id).qualification.get(
                "minimum_independent_basilisk_pairs_for_freeze", 3
            ) if fault_id != "UNKNOWN" else 3,
            "threshold_freeze_status": "PENDING_BASILISK_EVIDENCE",
            "signature_review_file": None,
        })
        summary["pair_count"] += 1
        if report.get("basilisk_candidate_qualified"):
            summary["candidate_pass_pair_count"] += 1
        if decision == "REJECT":
            summary["rejected_pair_count"] += 1
    for summary in fault_summaries.values():
        minimum = int(summary["minimum_independent_basilisk_pairs_for_freeze"])
        if summary["candidate_pass_pair_count"] >= minimum and summary["rejected_pair_count"] == 0:
            summary["threshold_freeze_status"] = "READY_FOR_EXPERT_REVIEW"

    index = {
        "schema_version": DIAGNOSTIC_QUALIFICATION_INDEX_SCHEMA_VERSION,
        "pair_count": len(reports),
        "basilisk_candidate_qualified_pair_count": sum(bool(item.get("basilisk_candidate_qualified")) for item in reports),
        "formal_diagnostic_qualified_pair_count": 0,
        "counts_by_decision": dict(sorted(counts.items())),
        "fault_summaries": [fault_summaries[key] for key in sorted(fault_summaries)],
        "reports": reports,
        "claim_boundary": {
            "thresholds_require_named_expert_freeze": True,
            "candidate_pass_is_not_training_approval": True,
        },
    }
    _write_json(root / "diagnostic_qualification_index.json", index)
    return [updated_by_id[str(item.get("dataset_id"))] for item in contracts], index


__all__ = [
    "DIAGNOSTIC_PAIR_VALIDATION_SCHEMA_VERSION",
    "DIAGNOSTIC_QUALIFICATION_INDEX_SCHEMA_VERSION",
    "DiagnosticCriterionResult",
    "DiagnosticValidationAgent",
    "validate_dataset_diagnostics",
]
