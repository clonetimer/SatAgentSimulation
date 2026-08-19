"""Expert approval gate for technically qualified simulation datasets."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .astrograph_dataset_contract import build_astrograph_dataset_index

EXPERT_REVIEW_SCHEMA_VERSION = "sat-sim.dataset-expert-review.v1"
_ALLOWED_DECISIONS = {"APPROVE", "REJECT"}
_ALLOWED_REVIEWER_TYPES = {"human", "llm_assisted_human"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_dataset_pair_review(
    dataset_root: str | Path,
    *,
    pair_id: str,
    decision: str,
    reviewer_id: str,
    reviewer_type: str,
    notes: str,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    """Record a human-controlled review and update training readiness.

    LLM output may support the review, but the accepted reviewer types require a
    human identity.  An approval cannot override a failed technical gate.
    """

    root = Path(dataset_root)
    decision = decision.strip().upper()
    if decision not in _ALLOWED_DECISIONS:
        raise ValueError(f"decision must be one of {sorted(_ALLOWED_DECISIONS)}")
    if reviewer_type not in _ALLOWED_REVIEWER_TYPES:
        raise ValueError(f"reviewer_type must be one of {sorted(_ALLOWED_REVIEWER_TYPES)}")
    if not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    if not notes.strip():
        raise ValueError("review notes are required")

    qualification_path = root / "dataset_qualification_index.json"
    qualification = _read(qualification_path)
    reports = qualification.get("reports") if isinstance(qualification.get("reports"), list) else []
    matches = [item for item in reports if isinstance(item, Mapping) and str(item.get("pair_id")) == pair_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one qualification report for pair {pair_id!r}")
    technical = dict(matches[0])
    diagnostic_path = root / "diagnostic_qualification_index.json"
    diagnostic = None
    if diagnostic_path.is_file():
        diagnostic_index = _read(diagnostic_path)
        diagnostic_reports = diagnostic_index.get("reports") if isinstance(diagnostic_index.get("reports"), list) else []
        diagnostic_matches = [
            item for item in diagnostic_reports
            if isinstance(item, Mapping) and str(item.get("pair_id")) == pair_id
        ]
        if len(diagnostic_matches) != 1:
            raise KeyError(f"expected exactly one diagnostic report for pair {pair_id!r}")
        diagnostic = dict(diagnostic_matches[0])
    if decision == "APPROVE" and not technical.get("formal_training_qualified"):
        raise ValueError("expert approval cannot override a failed or incomplete technical qualification gate")
    if decision == "APPROVE" and diagnostic is not None and not diagnostic.get("formal_diagnostic_qualified"):
        raise ValueError("expert approval cannot override an unfrozen or failed diagnostic signature gate")

    review = {
        "schema_version": EXPERT_REVIEW_SCHEMA_VERSION,
        "pair_id": pair_id,
        "decision": decision,
        "reviewer_id": reviewer_id.strip(),
        "reviewer_type": reviewer_type,
        "reviewed_at_utc": _now_iso(),
        "notes": notes.strip(),
        "evidence_refs": list(evidence_refs),
        "technical_qualification": {
            "decision": technical.get("decision"),
            "formal_training_qualified": bool(technical.get("formal_training_qualified")),
            "report_file": technical.get("report_file"),
        },
        "diagnostic_qualification": ({
            "decision": diagnostic.get("decision"),
            "formal_diagnostic_qualified": bool(diagnostic.get("formal_diagnostic_qualified")),
            "signature_id": diagnostic.get("signature_id"),
            "report_file": diagnostic.get("report_file"),
        } if diagnostic is not None else None),
        "claim_boundary": {
            "review_does_not_create_flight_validation": True,
            "review_does_not_override_technical_failure": True,
        },
    }
    review_path = root / "validation" / "expert_reviews" / f"{pair_id}.json"
    _write(review_path, review)
    relative_review = review_path.relative_to(root).as_posix()

    index_path = root / "astrograph_dataset_index.json"
    index = _read(index_path)
    contracts = index.get("contracts") if isinstance(index.get("contracts"), list) else []
    updated: list[dict[str, Any]] = []
    for item in contracts:
        contract = dict(item)
        pairing = contract.get("pairing") if isinstance(contract.get("pairing"), Mapping) else {}
        if str(pairing.get("pair_id")) == pair_id:
            quality = contract.setdefault("quality", {})
            quality["expert_review_status"] = "APPROVED" if decision == "APPROVE" else "REJECTED"
            quality["expert_review_file"] = relative_review
            quality["training_ready"] = bool(
                quality.get("candidate_training_ready")
                and quality.get("formal_diagnostic_qualified", diagnostic is None)
                and decision == "APPROVE"
            )
            case_root = root / "cases" / str(contract.get("dataset_id"))
            contract_path = case_root / "astrograph" / "dataset_contract.json"
            persisted = _read(contract_path)
            persisted_quality = persisted.setdefault("quality", {})
            persisted_quality.update({
                "expert_review_status": quality["expert_review_status"],
                "expert_review_file": relative_review,
                "training_ready": quality["training_ready"],
            })
            _write(contract_path, persisted)
            experiment_path = case_root / "astrograph" / "experiment_record.json"
            if experiment_path.is_file():
                experiment = _read(experiment_path)
                qualification_block = experiment.setdefault("qualification", {})
                qualification_block.update({
                    "expert_review_status": quality["expert_review_status"],
                    "expert_review_file": relative_review,
                    "training_ready": quality["training_ready"],
                })
                _write(experiment_path, experiment)
        updated.append(contract)

    rebuilt = build_astrograph_dataset_index(updated)
    for key, value in index.items():
        if key not in rebuilt and key != "contracts":
            rebuilt[key] = value
    rebuilt["contracts"] = updated
    _write(index_path, rebuilt)
    return {"review": review, "review_file": relative_review, "dataset_index": rebuilt}


__all__ = ["EXPERT_REVIEW_SCHEMA_VERSION", "record_dataset_pair_review"]
