"""FMEA table generation from TaskSpec events and registered effect contracts.

Risk scores are never invented.  Severity/occurrence/detectability are exported
only when explicitly supplied by the TaskSpec.  RPN is computed only when all
three ratings are available.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fault_environment import get_fault_contract


@dataclass(frozen=True)
class FmeaRow:
    fmea_row_id: str
    event_id: str
    episode_id: str
    category: str
    failure_mode: str
    target: str
    local_effect: str
    system_effect: str
    detection_method: str
    expected_observables: str
    prevention_or_control: str
    severity: int | None
    occurrence: int | None
    detectability: int | None
    rpn: int | None
    rating_status: str
    evidence_policy: str
    start_s: float
    end_s: float | None
    traceability_link_id: str = ""
    evidence_status: str = "not_evaluated"
    physical_effect_verified: bool = False
    telemetry_stream_ids: str = ""
    changed_observables: str = ""
    evidence_api_path: str = ""
    validation_result: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _score(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= 10 else None


def _event_rows(task_spec: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    events = task_spec.get("events") if isinstance(task_spec.get("events"), Mapping) else {}
    out: list[tuple[str, Mapping[str, Any]]] = []
    for key, category in (("faults", "fault"), ("degradations", "degradation"), ("constraints", "constraint")):
        for item in events.get(key, []) if isinstance(events, Mapping) else []:
            if isinstance(item, Mapping):
                out.append((category, item))
    # Legacy compatibility.
    modifiers = task_spec.get("modifiers") if isinstance(task_spec.get("modifiers"), Mapping) else {}
    for key, category in (("faults", "fault"), ("degradations", "degradation"), ("constraints", "constraint")):
        for item in modifiers.get(key, []) if isinstance(modifiers, Mapping) else []:
            if isinstance(item, Mapping):
                out.append((category, item))
    return out


def build_fmea_rows(task_spec: Mapping[str, Any]) -> list[FmeaRow]:
    rows: list[FmeaRow] = []
    seen: set[tuple[str, str]] = set()
    for category, event in _event_rows(task_spec):
        event_id = str(event.get("id") or event.get("fault_id") or event.get("effect") or "event")
        effect = str(event.get("effect") or event.get("fault_type") or "unknown")
        key = (category, event_id)
        if key in seen:
            continue
        seen.add(key)
        contract = get_fault_contract(effect, category)
        parameters = event.get("parameters") if isinstance(event.get("parameters"), Mapping) else {}
        risk = parameters.get("fmea") if isinstance(parameters.get("fmea"), Mapping) else {}
        severity = _score(risk.get("severity"))
        occurrence = _score(risk.get("occurrence"))
        detectability = _score(risk.get("detectability"))
        rpn = severity * occurrence * detectability if None not in (severity, occurrence, detectability) else None
        rows.append(
            FmeaRow(
                fmea_row_id=f"fmea::{event_id}",
                event_id=event_id,
                episode_id=f"episode::{event_id}",
                category=category,
                failure_mode=contract.display_name_zh,
                target=str(event.get("target") or contract.target_kind),
                local_effect=str(risk.get("local_effect") or contract.trigger_semantics),
                system_effect=str(risk.get("system_effect") or "待任务级分析"),
                detection_method=str(risk.get("detection_method") or "Recorder观测与效果合同"),
                expected_observables=",".join(contract.expected_observables),
                prevention_or_control=str(risk.get("prevention_or_control") or "待工程评审"),
                severity=severity,
                occurrence=occurrence,
                detectability=detectability,
                rpn=rpn,
                rating_status="rated" if rpn is not None else "unrated_no_fabricated_score",
                evidence_policy=contract.evidence_policy,
                start_s=float(event.get("start_s") or 0.0),
                end_s=float(event["end_s"]) if event.get("end_s") is not None else None,
            )
        )
    return rows


def write_fmea_table(
    *,
    output_root: Path,
    task_spec: Mapping[str, Any],
    formats: Sequence[str],
    traceability: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = build_fmea_rows(task_spec)
    link_map = {
        str(item.get("event_id")): item
        for item in (traceability or {}).get("links", [])
        if isinstance(item, Mapping)
    }
    enriched: list[FmeaRow] = []
    for row in rows:
        link = link_map.get(row.event_id)
        if not isinstance(link, Mapping):
            enriched.append(row)
            continue
        telemetry_ids = ",".join(
            str(item.get("stream_id")) for item in (link.get("telemetry_evidence") or []) if isinstance(item, Mapping)
        )
        enriched.append(replace(
            row,
            traceability_link_id=str(link.get("link_id") or ""),
            evidence_status=str(link.get("evidence_status") or "not_evaluated"),
            physical_effect_verified=bool(link.get("physical_effect_verified")),
            telemetry_stream_ids=telemetry_ids,
            changed_observables=",".join(str(item) for item in (link.get("changed_physical_fields") or [])),
            evidence_api_path=f"/runs/{(traceability or {}).get('run_id')}/fault-traceability/{row.event_id}",
            validation_result=str(link.get("validation_result") or ""),
        ))
    rows = enriched
    output_root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    if "csv" in formats:
        path = output_root / "fmea.csv"
        fieldnames = list(FmeaRow.__annotations__.keys())
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row.to_dict())
        files["csv"] = str(path.name)
    if "json" in formats:
        path = output_root / "fmea.json"
        payload = {
            "schema_version": "sat-sim.fmea.v2",
            "row_count": len(rows),
            "risk_score_policy": "compute_rpn_only_from_explicit_1_to_10_ratings",
            "rows": [row.to_dict() for row in rows],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files["json"] = str(path.name)
    manifest = {
        "schema_version": "sat-sim.fmea-manifest.v2",
        "row_count": len(rows),
        "rated_row_count": sum(1 for row in rows if row.rpn is not None),
        "unrated_row_count": sum(1 for row in rows if row.rpn is None),
        "files": files,
        "traceability_linked_row_count": sum(1 for row in rows if row.traceability_link_id),
        "physical_effect_verified_row_count": sum(1 for row in rows if row.physical_effect_verified),
        "warning": "Occurrence/detectability and RPN are absent unless explicitly provided; no risk score is fabricated.",
    }
    manifest_path = output_root / "fmea_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_file"] = str(manifest_path.name)
    return manifest


__all__ = ["FmeaRow", "build_fmea_rows", "write_fmea_table"]
