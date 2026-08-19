"""Human-controlled freeze gate for provisional diagnostic signatures."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .astrograph_dataset_contract import build_astrograph_dataset_index

DIAGNOSTIC_SIGNATURE_REVIEW_SCHEMA_VERSION = "sat-sim.diagnostic-signature-review.v1"
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


def _sha256_json(payload: Mapping[str, Any]) -> str:
    data = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def record_diagnostic_signature_review(
    dataset_root: str | Path,
    *,
    fault_id: str,
    decision: str,
    reviewer_id: str,
    reviewer_type: str,
    notes: str,
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    root = Path(dataset_root)
    decision = decision.strip().upper()
    if decision not in {"FREEZE", "REJECT"}:
        raise ValueError("decision must be FREEZE or REJECT")
    if reviewer_type not in _ALLOWED_REVIEWER_TYPES:
        raise ValueError(f"reviewer_type must be one of {sorted(_ALLOWED_REVIEWER_TYPES)}")
    if not reviewer_id.strip() or not notes.strip():
        raise ValueError("reviewer_id and notes are required")

    index_path = root / "diagnostic_qualification_index.json"
    index = _read(index_path)
    summaries = index.get("fault_summaries") if isinstance(index.get("fault_summaries"), list) else []
    matches = [item for item in summaries if isinstance(item, Mapping) and str(item.get("fault_id")) == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one diagnostic fault summary for {fault_id!r}")
    summary = dict(matches[0])
    if decision == "FREEZE" and summary.get("threshold_freeze_status") != "READY_FOR_EXPERT_REVIEW":
        raise ValueError("signature freeze cannot override insufficient or rejected Basilisk diagnostic evidence")

    reports = index.get("reports") if isinstance(index.get("reports"), list) else []
    fault_reports = [item for item in reports if isinstance(item, Mapping) and str(item.get("fault_id")) == fault_id]
    signature_hashes = sorted({str(item.get("signature_sha256")) for item in fault_reports if item.get("signature_sha256")})
    if decision == "FREEZE" and len(signature_hashes) != 1:
        raise ValueError("all reviewed pairs must use exactly one diagnostic signature revision")

    review = {
        "schema_version": DIAGNOSTIC_SIGNATURE_REVIEW_SCHEMA_VERSION,
        "fault_id": fault_id,
        "decision": decision,
        "reviewer_id": reviewer_id.strip(),
        "reviewer_type": reviewer_type,
        "reviewed_at_utc": _now_iso(),
        "notes": notes.strip(),
        "evidence_refs": list(evidence_refs),
        "reviewed_signature_sha256": signature_hashes[0] if len(signature_hashes) == 1 else None,
        "diagnostic_evidence_digest": _sha256_json({"fault_summary": summary, "reports": fault_reports}),
        "claim_boundary": {
            "flight_validated": False,
            "review_cannot_override_failed_diagnostic_checks": True,
            "freeze_applies_only_to_reviewed_signature_hash": True,
        },
    }
    review_path = root / "validation" / "signature_reviews" / f"{fault_id}.json"
    _write(review_path, review)
    relative_review = review_path.relative_to(root).as_posix()

    for item in summaries:
        if isinstance(item, dict) and str(item.get("fault_id")) == fault_id:
            item["threshold_freeze_status"] = "FROZEN" if decision == "FREEZE" else "REJECTED"
            item["signature_review_file"] = relative_review
    formal_count = 0
    for item in reports:
        if not isinstance(item, dict) or str(item.get("fault_id")) != fault_id:
            if isinstance(item, Mapping) and item.get("formal_diagnostic_qualified"):
                formal_count += 1
            continue
        qualified = decision == "FREEZE" and bool(item.get("basilisk_candidate_qualified"))
        item["formal_diagnostic_qualified"] = qualified
        item["signature_review_file"] = relative_review
        if qualified:
            formal_count += 1
        report_file = item.get("report_file")
        if report_file:
            _write(root / str(report_file), item)
    index["formal_diagnostic_qualified_pair_count"] = formal_count
    _write(index_path, index)

    dataset_index_path = root / "astrograph_dataset_index.json"
    dataset_index = _read(dataset_index_path)
    contracts = dataset_index.get("contracts") if isinstance(dataset_index.get("contracts"), list) else []
    updated: list[dict[str, Any]] = []
    affected_pairs = {
        str(item.get("pair_id"))
        for item in fault_reports
        if isinstance(item, Mapping) and item.get("pair_id")
    }
    qualified_pairs = {
        str(item.get("pair_id"))
        for item in reports
        if isinstance(item, Mapping) and item.get("formal_diagnostic_qualified")
    }
    for original in contracts:
        contract = dict(original)
        pairing = contract.get("pairing") if isinstance(contract.get("pairing"), Mapping) else {}
        pair_id = str(pairing.get("pair_id") or "")
        if pair_id in affected_pairs:
            quality = contract.setdefault("quality", {})
            formal_diagnostic = pair_id in qualified_pairs
            quality["formal_diagnostic_qualified"] = formal_diagnostic
            quality["signature_review_status"] = "FROZEN" if decision == "FREEZE" else "REJECTED"
            quality["signature_review_file"] = relative_review
            quality["candidate_training_ready"] = bool(
                quality.get("nonempty_trace")
                and quality.get("channel_ready")
                and quality.get("model_input_ready")
                and quality.get("formal_training_eligible")
                and quality.get("formal_training_qualified")
                and formal_diagnostic
            )
            quality["training_ready"] = False
            case_root = root / "cases" / str(contract.get("dataset_id"))
            persisted_path = case_root / "astrograph" / "dataset_contract.json"
            persisted = _read(persisted_path)
            persisted.setdefault("quality", {}).update(quality)
            _write(persisted_path, persisted)
            experiment_path = case_root / "astrograph" / "experiment_record.json"
            if experiment_path.is_file():
                experiment = _read(experiment_path)
                experiment.setdefault("qualification", {}).update({
                    "formal_diagnostic_qualified": formal_diagnostic,
                    "signature_review_status": quality["signature_review_status"],
                    "signature_review_file": relative_review,
                })
                _write(experiment_path, experiment)
        updated.append(contract)

    rebuilt = build_astrograph_dataset_index(updated)
    for key, value in dataset_index.items():
        if key not in rebuilt and key != "contracts":
            rebuilt[key] = value
    rebuilt["contracts"] = updated
    rebuilt["diagnostic_qualification"] = index
    _write(dataset_index_path, rebuilt)
    return {"review": review, "review_file": relative_review, "diagnostic_qualification": index, "dataset_index": rebuilt}


__all__ = ["DIAGNOSTIC_SIGNATURE_REVIEW_SCHEMA_VERSION", "record_diagnostic_signature_review"]
