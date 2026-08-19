"""Traceability links between FMEA rows, fault episodes and telemetry evidence.

The link layer is deterministic.  It never upgrades an episode evidence status
and it never invents telemetry coverage.  It only indexes artifacts that were
already produced by the simulation runtime and the dataset writer.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fault_environment import get_fault_contract


@dataclass(frozen=True)
class TelemetryEvidenceRef:
    stream_id: str
    sample_s: float
    file: str
    matched_fields: tuple[str, ...]
    window_start_s: float
    window_end_s: float
    row_count: int
    first_time_s: float | None
    last_time_s: float | None
    api_path: str

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["matched_fields"] = list(self.matched_fields)
        return out


@dataclass(frozen=True)
class FaultTraceabilityLink:
    link_id: str
    event_id: str
    fmea_row_id: str
    episode_id: str
    category: str
    effect: str
    target: str
    start_s: float
    end_s: float | None
    evidence_status: str
    physical_effect_verified: bool
    expected_observables: tuple[str, ...]
    physical_observable_fields: tuple[str, ...]
    changed_physical_fields: tuple[str, ...]
    telemetry_evidence: tuple[TelemetryEvidenceRef, ...]
    telemetry_coverage_status: str
    validation_result: str | None
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("expected_observables", "physical_observable_fields", "changed_physical_fields"):
            out[key] = list(getattr(self, key))
        out["telemetry_evidence"] = [item.to_dict() for item in self.telemetry_evidence]
        out["artifacts"] = dict(self.artifacts)
        return out


def _events(task_spec: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for container_name in ("events", "modifiers"):
        container = task_spec.get(container_name)
        if not isinstance(container, Mapping):
            continue
        for key, category in (("faults", "fault"), ("degradations", "degradation"), ("constraints", "constraint")):
            raw = container.get(key)
            if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
                continue
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                event = dict(item)
                event_id = str(event.get("id") or event.get("fault_id") or event.get("effect") or "event")
                dedupe = (category, event_id)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                out.append((category, event))
    return out


def _episodes(fault_environment: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(fault_environment, Mapping):
        return []
    return [item for item in (fault_environment.get("episodes") or []) if isinstance(item, Mapping)]


def _find_episode(
    episodes: Sequence[Mapping[str, Any]], *, episode_id: str, effect: str, target: str, start_s: float
) -> Mapping[str, Any]:
    for episode in episodes:
        if str(episode.get("episode_id") or "") == episode_id:
            return episode
    candidates = [
        episode for episode in episodes
        if str(episode.get("effect") or "") == effect
        and str(episode.get("target") or "") == target
        and abs(float(episode.get("start_s") or 0.0) - start_s) <= 1e-9
    ]
    return candidates[0] if len(candidates) == 1 else {}


def _field_matches(field: str, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(field, pattern)
    return field == pattern


def _stream_matches(stream_fields: Sequence[str], evidence_fields: Sequence[str]) -> tuple[str, ...]:
    matches: list[str] = []
    for field in stream_fields:
        text = str(field)
        if text == "time_s":
            continue
        if any(_field_matches(text, str(pattern)) or _field_matches(str(pattern), text) for pattern in evidence_fields):
            if text not in matches:
                matches.append(text)
    return tuple(matches)


def _read_stream_times(dataset_root: Path, stream: Mapping[str, Any], start_s: float, end_s: float) -> tuple[int, float | None, float | None]:
    path = dataset_root / str(stream.get("file") or "")
    if not path.is_file():
        return 0, None, None
    times: list[float] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    value = float(row.get("time_s"))
                except (TypeError, ValueError, json.JSONDecodeError, AttributeError):
                    continue
                if start_s - 1e-9 <= value <= end_s + 1e-9:
                    times.append(value)
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    value = float(row.get("time_s"))
                except (TypeError, ValueError):
                    continue
                if start_s - 1e-9 <= value <= end_s + 1e-9:
                    times.append(value)
    return len(times), (times[0] if times else None), (times[-1] if times else None)


def build_fault_traceability(
    *,
    task_spec: Mapping[str, Any],
    dataset_root: Path,
    fault_environment: Mapping[str, Any] | None,
    multi_rate_manifest: Mapping[str, Any] | None,
    validation_outcome: Mapping[str, Any] | None,
    run_id: str,
) -> dict[str, Any]:
    episodes = _episodes(fault_environment)
    streams = [dict(item) for item in (multi_rate_manifest or {}).get("streams", []) if isinstance(item, Mapping)]
    duration = float(((task_spec.get("simulation") or {}).get("duration_s") or task_spec.get("duration_s") or 0.0))
    validation_result = str((validation_outcome or {}).get("result") or "") or None
    links: list[FaultTraceabilityLink] = []

    for category, event in _events(task_spec):
        event_id = str(event.get("id") or event.get("fault_id") or event.get("effect") or "event")
        effect = str(event.get("effect") or event.get("fault_type") or "unknown")
        episode_id = f"episode::{event_id}"
        contract = get_fault_contract(effect, category)
        start_s = float(event.get("start_s") or 0.0)
        episode = _find_episode(
            episodes, episode_id=episode_id, effect=effect,
            target=str(event.get("target") or contract.target_kind), start_s=start_s,
        )
        evidence_summary = episode.get("evidence_summary") if isinstance(episode.get("evidence_summary"), Mapping) else {}
        expected = tuple(str(item) for item in (evidence_summary.get("expected_observables") or contract.expected_observables))
        physical = tuple(str(item) for item in (evidence_summary.get("physical_observable_fields") or []))
        changed = tuple(str(item) for item in (evidence_summary.get("changed_physical_fields") or []))
        evidence_fields = tuple(dict.fromkeys([*expected, *physical, *changed]))
        raw_end = event.get("end_s")
        end_s = float(raw_end) if raw_end is not None else None
        effective_end = end_s if end_s is not None else duration
        telemetry_refs: list[TelemetryEvidenceRef] = []
        for stream in streams:
            fields = tuple(str(item) for item in (stream.get("fields") or []))
            matched = _stream_matches(fields, evidence_fields)
            if not matched:
                continue
            sample_s = float(stream.get("sample_s") or 0.0)
            window_start = max(0.0, start_s - sample_s)
            window_end = min(duration, effective_end + sample_s) if duration > 0 else effective_end + sample_s
            count, first_time, last_time = _read_stream_times(dataset_root, stream, window_start, window_end)
            query = (
                f"/runs/{run_id}/telemetry-streams/{stream.get('stream_id')}"
                f"?start_s={window_start:g}&end_s={window_end:g}"
            )
            telemetry_refs.append(
                TelemetryEvidenceRef(
                    stream_id=str(stream.get("stream_id")),
                    sample_s=sample_s,
                    file=str(stream.get("file")),
                    matched_fields=matched,
                    window_start_s=window_start,
                    window_end_s=window_end,
                    row_count=count,
                    first_time_s=first_time,
                    last_time_s=last_time,
                    api_path=query,
                )
            )
        if telemetry_refs and all(item.row_count > 0 for item in telemetry_refs):
            coverage = "covered"
        elif telemetry_refs:
            coverage = "stream_matched_window_empty"
        else:
            coverage = "no_matching_multi_rate_stream"
        links.append(
            FaultTraceabilityLink(
                link_id=f"trace::{event_id}",
                event_id=event_id,
                fmea_row_id=f"fmea::{event_id}",
                episode_id=episode_id,
                category=category,
                effect=effect,
                target=str(event.get("target") or contract.target_kind),
                start_s=start_s,
                end_s=end_s,
                evidence_status=str(episode.get("evidence_status") or "episode_not_available"),
                physical_effect_verified=bool(evidence_summary.get("physical_effect_verified")),
                expected_observables=expected,
                physical_observable_fields=physical,
                changed_physical_fields=changed,
                telemetry_evidence=tuple(telemetry_refs),
                telemetry_coverage_status=coverage,
                validation_result=validation_result,
                artifacts={
                    "fmea": "results/dataset/fmea/fmea.json",
                    "fault_episodes": "results/dataset/traceability/fault_episodes.json",
                    "multi_rate_manifest": "results/dataset/telemetry/multi_rate_manifest.json",
                    "validation_outcome": "validation/validation_outcome.json",
                    "events": "results/events.json",
                    "bundle_manifest": "bundle_manifest.json",
                },
            )
        )

    linked = sum(1 for item in links if item.evidence_status != "episode_not_available")
    covered = sum(1 for item in links if item.telemetry_coverage_status == "covered")
    verified = sum(1 for item in links if item.physical_effect_verified)
    return {
        "schema_version": "sat-sim.fault-traceability.v1",
        "run_id": run_id,
        "link_count": len(links),
        "episode_linked_count": linked,
        "telemetry_covered_count": covered,
        "physical_effect_verified_count": verified,
        "validation_result": validation_result,
        "links": [item.to_dict() for item in links],
    }


def write_fault_traceability(*, output_root: Path, payload: Mapping[str, Any], fault_environment: Mapping[str, Any] | None) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    traceability_path = output_root / "fault_traceability.json"
    traceability_path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    episodes_path = output_root / "fault_episodes.json"
    episodes_payload = dict(fault_environment or {"schema_version": "fault-environment.v1", "episode_count": 0, "episodes": []})
    episodes_path.write_text(json.dumps(episodes_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = output_root / "fault_traceability.csv"
    fieldnames = [
        "link_id", "event_id", "fmea_row_id", "episode_id", "category", "effect", "target",
        "start_s", "end_s", "evidence_status", "physical_effect_verified", "telemetry_coverage_status",
        "telemetry_stream_ids", "changed_physical_fields", "validation_result",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in payload.get("links") or []:
            if not isinstance(item, Mapping):
                continue
            writer.writerow({
                "link_id": item.get("link_id"),
                "event_id": item.get("event_id"),
                "fmea_row_id": item.get("fmea_row_id"),
                "episode_id": item.get("episode_id"),
                "category": item.get("category"),
                "effect": item.get("effect"),
                "target": item.get("target"),
                "start_s": item.get("start_s"),
                "end_s": item.get("end_s"),
                "evidence_status": item.get("evidence_status"),
                "physical_effect_verified": item.get("physical_effect_verified"),
                "telemetry_coverage_status": item.get("telemetry_coverage_status"),
                "telemetry_stream_ids": ",".join(str(ref.get("stream_id")) for ref in item.get("telemetry_evidence") or []),
                "changed_physical_fields": ",".join(str(field) for field in item.get("changed_physical_fields") or []),
                "validation_result": item.get("validation_result"),
            })
    return {
        "fault_traceability_json": str(traceability_path.name),
        "fault_traceability_csv": str(csv_path.name),
        "fault_episodes_json": str(episodes_path.name),
    }


__all__ = [
    "TelemetryEvidenceRef",
    "FaultTraceabilityLink",
    "build_fault_traceability",
    "write_fault_traceability",
]
