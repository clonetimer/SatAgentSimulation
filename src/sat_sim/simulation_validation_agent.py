"""Deterministic validation agent for paired satellite fault experiments.

The agent evaluates provenance, pairing, signal timing and template-defined
physical signatures.  It never edits telemetry and never promotes proxy data to
formal training.  Its output is a reviewable qualification recommendation.
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

from .fault_experiment_templates import FaultExperimentTemplate, get_fault_experiment_template

SIMULATION_PAIR_VALIDATION_SCHEMA_VERSION = "sat-sim.simulation-pair-validation.v1"
DATASET_QUALIFICATION_INDEX_SCHEMA_VERSION = "sat-sim.dataset-qualification-index.v1"


@dataclass(frozen=True)
class ValidationCheck:
    check_id: str
    status: str
    required: bool
    summary: str
    observed: Any = None
    expected: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status,
            "required": self.required,
            "summary": self.summary,
            "observed": self.observed,
            "expected": self.expected,
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
    out: dict[float, Mapping[str, Any]] = {}
    for row in rows:
        value = _number(row.get("time_s"))
        if value is not None:
            out[value] = row
    return out


def _series(rows: Sequence[Mapping[str, Any]], channel: str, *, start: float | None = None, before: float | None = None) -> list[float]:
    values: list[float] = []
    for row in rows:
        t = _number(row.get("time_s"))
        value = _number(row.get(channel))
        if t is None or value is None:
            continue
        if start is not None and t < start:
            continue
        if before is not None and t >= before:
            continue
        values.append(value)
    return values


def _paired_values(
    fault_rows: Sequence[Mapping[str, Any]],
    nominal_rows: Sequence[Mapping[str, Any]],
    channel: str,
    *,
    start: float | None = None,
    before: float | None = None,
) -> list[tuple[float, float]]:
    fault = _time_index(fault_rows)
    nominal = _time_index(nominal_rows)
    pairs: list[tuple[float, float]] = []
    for t in sorted(set(fault).intersection(nominal)):
        if start is not None and t < start:
            continue
        if before is not None and t >= before:
            continue
        fv = _number(fault[t].get(channel))
        nv = _number(nominal[t].get(channel))
        if fv is not None and nv is not None:
            pairs.append((fv, nv))
    return pairs


def _signature_check(
    signature: Mapping[str, Any],
    fault_rows: Sequence[Mapping[str, Any]],
    nominal_rows: Sequence[Mapping[str, Any]],
    onset: float,
) -> ValidationCheck:
    check_id = str(signature.get("check_id"))
    channel = str(signature.get("channel"))
    comparison = str(signature.get("comparison"))
    threshold = float(signature.get("threshold"))
    required = bool(signature.get("required", True))
    fault_post = _series(fault_rows, channel, start=onset)
    nominal_post = _series(nominal_rows, channel, start=onset)
    paired_post = _paired_values(fault_rows, nominal_rows, channel, start=onset)
    passed = False
    observed: Any = None
    if comparison == "post_fault_mean_minus_nominal_gt":
        if fault_post and nominal_post:
            observed = fmean(fault_post) - fmean(nominal_post)
            passed = observed > threshold
    elif comparison == "post_pair_max_abs_delta_gt":
        if paired_post:
            observed = max(abs(fv - nv) for fv, nv in paired_post)
            passed = observed > threshold
    elif comparison == "post_fault_max_le":
        if fault_post:
            observed = max(fault_post)
            passed = observed <= threshold
    elif comparison == "post_fault_to_nominal_mean_ratio_lt":
        if fault_post and nominal_post and abs(fmean(nominal_post)) > 1.0e-15:
            observed = fmean(fault_post) / fmean(nominal_post)
            passed = observed < threshold
    else:
        return ValidationCheck(check_id, "FAIL", required, f"unsupported comparison {comparison}", None, threshold)
    return ValidationCheck(
        check_id,
        "PASS" if passed else "FAIL",
        required,
        f"{channel}: {comparison}",
        observed,
        threshold,
    )


class SimulationValidationAgent:
    """Validate one fault/nominal pair against its governed experiment template."""

    def validate_pair(
        self,
        *,
        fault_root: str | Path,
        nominal_root: str | Path,
        template: FaultExperimentTemplate | None = None,
    ) -> dict[str, Any]:
        fault_root = Path(fault_root)
        nominal_root = Path(nominal_root)
        fault_contract = _read_json(fault_root / "astrograph" / "dataset_contract.json")
        nominal_contract = _read_json(nominal_root / "astrograph" / "dataset_contract.json")
        fault_gt = fault_contract.get("ground_truth") if isinstance(fault_contract.get("ground_truth"), Mapping) else {}
        nominal_gt = nominal_contract.get("ground_truth") if isinstance(nominal_contract.get("ground_truth"), Mapping) else {}
        fault_id = str(fault_gt.get("class_id") or "")
        template = template or get_fault_experiment_template(fault_id)
        fault_rows = _read_csv(fault_root / str((fault_contract.get("telemetry") or {}).get("file")))
        nominal_rows = _read_csv(nominal_root / str((nominal_contract.get("telemetry") or {}).get("file")))
        onset = _number(fault_gt.get("onset_time_s"))
        checks: list[ValidationCheck] = []

        pair_match = bool(fault_gt.get("pair_id")) and fault_gt.get("pair_id") == nominal_gt.get("pair_id")
        checks.append(ValidationCheck("pair_identity", "PASS" if pair_match else "FAIL", True, "fault and nominal pair IDs must match", fault_gt.get("pair_id"), nominal_gt.get("pair_id")))
        role_match = fault_gt.get("case_role") == "fault" and nominal_gt.get("case_role") == "nominal"
        checks.append(ValidationCheck("pair_roles", "PASS" if role_match else "FAIL", True, "pair roles must be fault/nominal", [fault_gt.get("case_role"), nominal_gt.get("case_role")], ["fault", "nominal"]))
        fault_sim = fault_contract.get("simulation") if isinstance(fault_contract.get("simulation"), Mapping) else {}
        nominal_sim = nominal_contract.get("simulation") if isinstance(nominal_contract.get("simulation"), Mapping) else {}
        seed_match = fault_sim.get("seed") == nominal_sim.get("seed")
        checks.append(ValidationCheck("paired_seed", "PASS" if seed_match else "FAIL", True, "paired cases must use the same seed", fault_sim.get("seed"), nominal_sim.get("seed")))
        onset_ok = onset is not None
        checks.append(ValidationCheck("fault_onset_present", "PASS" if onset_ok else "FAIL", True, "fault case must declare onset_time_s", onset, "finite time"))

        qualification = template.qualification
        min_pre = int(qualification.get("minimum_pre_onset_samples", 2))
        min_post = int(qualification.get("minimum_post_onset_samples", 2))
        tolerance = float(qualification.get("pre_onset_absolute_tolerance", 1.0e-8))
        if onset is not None:
            pre_times = sorted(t for t in set(_time_index(fault_rows)).intersection(_time_index(nominal_rows)) if t < onset)
            post_times = sorted(t for t in set(_time_index(fault_rows)).intersection(_time_index(nominal_rows)) if t >= onset)
            checks.append(ValidationCheck("pre_onset_sample_count", "PASS" if len(pre_times) >= min_pre else "FAIL", True, "enough paired samples must exist before injection", len(pre_times), min_pre))
            checks.append(ValidationCheck("post_onset_sample_count", "PASS" if len(post_times) >= min_post else "FAIL", True, "enough paired samples must exist after injection", len(post_times), min_post))
            max_pre_delta = 0.0
            missing_pre_channels: list[str] = []
            for channel in template.expected_response.get("pre_onset_pair_channels", []):
                values = _paired_values(fault_rows, nominal_rows, str(channel), before=onset)
                if not values:
                    missing_pre_channels.append(str(channel))
                    continue
                max_pre_delta = max(max_pre_delta, max(abs(fv - nv) for fv, nv in values))
            pre_ok = not missing_pre_channels and max_pre_delta <= tolerance
            checks.append(ValidationCheck(
                "pre_onset_pair_equivalence",
                "PASS" if pre_ok else "FAIL",
                True,
                "paired runs must be equivalent before fault onset",
                {"max_abs_delta": max_pre_delta, "missing_channels": missing_pre_channels},
                {"max_abs_delta": tolerance, "missing_channels": []},
            ))
            for signature in template.expected_response.get("signatures", []):
                checks.append(_signature_check(signature, fault_rows, nominal_rows, onset))

        fault_fidelity = (fault_sim.get("fidelity") or {}) if isinstance(fault_sim.get("fidelity"), Mapping) else {}
        nominal_fidelity = (nominal_sim.get("fidelity") or {}) if isinstance(nominal_sim.get("fidelity"), Mapping) else {}
        source_eligible = bool(fault_fidelity.get("formal_training_eligible")) and bool(nominal_fidelity.get("formal_training_eligible"))
        checks.append(ValidationCheck(
            "basilisk_source_eligibility",
            "PASS" if source_eligible else "REVIEW",
            False,
            "both cases need A-level Basilisk runtime evidence for formal qualification",
            [fault_fidelity.get("fidelity_level"), nominal_fidelity.get("fidelity_level")],
            ["A_ENGINEERING_BASILISK_NATIVE", "A_ENGINEERING_BASILISK_NATIVE"],
        ))

        required_failed = any(item.required and item.status == "FAIL" for item in checks)
        physics_passed = not required_failed
        if required_failed:
            decision = "REJECT"
            reason = "required_validation_check_failed"
        elif source_eligible:
            decision = "PASS"
            reason = "physics_signatures_and_basilisk_evidence_complete"
        else:
            decision = "REVIEW"
            reason = "physics_signatures_passed_but_formal_basilisk_evidence_missing"
        formal_qualified = decision == "PASS" and source_eligible
        return {
            "schema_version": SIMULATION_PAIR_VALIDATION_SCHEMA_VERSION,
            "validation_agent": "deterministic_simulation_validation_agent.v1",
            "pair_id": fault_gt.get("pair_id"),
            "fault_id": fault_id,
            "experiment_template_id": template.template_id,
            "fault_dataset_id": fault_contract.get("dataset_id"),
            "nominal_dataset_id": nominal_contract.get("dataset_id"),
            "decision": decision,
            "reason": reason,
            "physics_validation_passed": physics_passed,
            "formal_training_qualified": formal_qualified,
            "expert_review_required": True,
            "checks": [item.to_dict() for item in checks],
            "claim_boundary": {
                "flight_validated": False,
                "agent_may_modify_simulation": False,
                "agent_may_publish_knowledge": False,
                "qualification_is_candidate_only_until_expert_review": True,
            },
        }



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _update_experiment_record(root: Path, validation: Mapping[str, Any], *, validation_file: str) -> None:
    path = root / "astrograph" / "experiment_record.json"
    if not path.is_file():
        return
    record = _read_json(path)
    validation_path = root.parents[1] / validation_file
    contract_path = root / "astrograph" / "dataset_contract.json"
    record["qualification"] = {
        "physics_validation_decision": validation.get("decision"),
        "physics_validation_passed": bool(validation.get("physics_validation_passed")),
        "formal_training_qualified": bool(validation.get("formal_training_qualified")),
        "expert_review_status": "PENDING",
        "validation_report_file": validation_file,
    }
    artifacts = record.setdefault("artifacts", {})
    if validation_path.is_file():
        artifacts["simulation_validation"] = {
            "file": validation_file,
            "sha256": _sha256(validation_path),
        }
    if contract_path.is_file():
        artifacts["dataset_contract"] = {
            "file": "astrograph/dataset_contract.json",
            "sha256": _sha256(contract_path),
        }
    _write_json(path, record)

def _update_case_contract(root: Path, validation: Mapping[str, Any], *, validation_file: str) -> dict[str, Any]:
    path = root / "astrograph" / "dataset_contract.json"
    contract = _read_json(path)
    quality = contract.setdefault("quality", {})
    quality["physics_validation_status"] = validation.get("decision")
    quality["physics_validation_passed"] = bool(validation.get("physics_validation_passed"))
    quality["formal_training_qualified"] = bool(validation.get("formal_training_qualified"))
    quality["physics_candidate_ready"] = bool(
        quality.get("nonempty_trace")
        and quality.get("channel_ready")
        and quality.get("model_input_ready")
        and quality.get("formal_training_eligible")
        and quality.get("formal_training_qualified")
    )
    quality["candidate_training_ready"] = False
    quality["expert_review_status"] = "PENDING"
    quality["training_ready"] = False
    contract["simulation_validation_file"] = validation_file
    _write_json(path, contract)
    return contract


def validate_dataset_pairs(dataset_root: str | Path, contracts: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate every complete fault/nominal pair and update case contracts."""

    root = Path(dataset_root)
    by_pair: dict[str, dict[str, Mapping[str, Any]]] = {}
    for contract in contracts:
        pairing = contract.get("pairing") if isinstance(contract.get("pairing"), Mapping) else {}
        pair_id = str(pairing.get("pair_id") or "")
        role = str(pairing.get("case_role") or "")
        if pair_id and role:
            by_pair.setdefault(pair_id, {})[role] = contract

    updated_by_id: dict[str, dict[str, Any]] = {str(item.get("dataset_id")): dict(item) for item in contracts}
    reports: list[dict[str, Any]] = []
    agent = SimulationValidationAgent()
    for pair_id, pair in sorted(by_pair.items()):
        fault = pair.get("fault")
        nominal = pair.get("nominal")
        if not fault or not nominal:
            reports.append({
                "schema_version": SIMULATION_PAIR_VALIDATION_SCHEMA_VERSION,
                "pair_id": pair_id,
                "decision": "REJECT",
                "reason": "incomplete_pair",
                "physics_validation_passed": False,
                "formal_training_qualified": False,
                "expert_review_required": True,
                "checks": [],
            })
            continue
        fault_root = root / "cases" / str(fault.get("dataset_id"))
        nominal_root = root / "cases" / str(nominal.get("dataset_id"))
        report = agent.validate_pair(fault_root=fault_root, nominal_root=nominal_root)
        report_path = root / "validation" / "pairs" / f"{pair_id}.json"
        _write_json(report_path, report)
        relative = report_path.relative_to(root).as_posix()
        report["report_file"] = relative
        _write_json(report_path, report)
        reports.append(report)
        for case_root, contract in ((fault_root, fault), (nominal_root, nominal)):
            updated = _update_case_contract(case_root, report, validation_file=relative)
            _update_experiment_record(case_root, report, validation_file=relative)
            updated["contract_file"] = str(contract.get("contract_file") or "")
            updated_by_id[str(updated.get("dataset_id"))] = updated

    decision_counts: dict[str, int] = {}
    formal_pairs = 0
    for report in reports:
        decision = str(report.get("decision") or "UNKNOWN")
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        if report.get("formal_training_qualified"):
            formal_pairs += 1
    qualification_index = {
        "schema_version": DATASET_QUALIFICATION_INDEX_SCHEMA_VERSION,
        "pair_count": len(reports),
        "formal_training_qualified_pair_count": formal_pairs,
        "counts_by_decision": dict(sorted(decision_counts.items())),
        "reports": reports,
    }
    qualification_path = root / "dataset_qualification_index.json"
    _write_json(qualification_path, qualification_index)
    ordered = [updated_by_id[str(item.get("dataset_id"))] for item in contracts]
    return ordered, qualification_index


__all__ = [
    "DATASET_QUALIFICATION_INDEX_SCHEMA_VERSION",
    "SIMULATION_PAIR_VALIDATION_SCHEMA_VERSION",
    "SimulationValidationAgent",
    "ValidationCheck",
    "validate_dataset_pairs",
]
