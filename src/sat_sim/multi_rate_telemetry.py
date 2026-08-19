"""Deterministic multi-rate telemetry export.

The simulator records a fastest common base trace.  Each configured telemetry
stream is then selected at an integer multiple of the base cadence and written
as a separate stream.  This preserves recorder truth: no interpolation or
synthetic samples are introduced.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TelemetryStreamResult:
    stream_id: str
    sample_s: float
    format: str
    fields: tuple[str, ...]
    row_count: int
    file: str
    source_base_sample_s: float
    selection_policy: str = "exact_integer_multiple_no_interpolation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _match_field(row: Mapping[str, Any], pattern: str) -> list[str]:
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return [key for key in row if key.startswith(prefix)]
    return [pattern] if pattern in row else []


def expand_fields(rows: Sequence[Mapping[str, Any]], patterns: Sequence[str]) -> tuple[str, ...]:
    if not rows:
        return ("time_s",)
    ordered: list[str] = ["time_s"]
    first = rows[0]
    for pattern in patterns:
        for field in _match_field(first, str(pattern)):
            if field not in ordered:
                ordered.append(field)
    return tuple(ordered)


def validate_telemetry_streams(*, base_sample_s: float, streams: Sequence[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    if base_sample_s <= 0:
        return ["base sample_s must be positive"]
    for index, stream in enumerate(streams):
        sid = str(stream.get("stream_id") or "").strip()
        if not sid:
            errors.append(f"telemetry_streams[{index}].stream_id is required")
        elif sid in seen:
            errors.append(f"duplicate telemetry stream_id: {sid}")
        seen.add(sid)
        try:
            sample_s = float(stream.get("sample_s"))
        except (TypeError, ValueError):
            errors.append(f"telemetry_streams[{index}].sample_s must be numeric")
            continue
        if sample_s < base_sample_s - 1e-12:
            errors.append(f"telemetry stream {sid or index} sample_s must be >= simulation.sample_s")
            continue
        ratio = sample_s / base_sample_s
        if abs(ratio - round(ratio)) > 1e-9:
            errors.append(
                f"telemetry stream {sid or index} sample_s must be an integer multiple of simulation.sample_s"
            )
        fields = stream.get("fields")
        if not isinstance(fields, list) or not fields:
            errors.append(f"telemetry_streams[{index}].fields must be a non-empty list")
    return errors


def select_stream_rows(
    rows: Sequence[Mapping[str, Any]], *, base_sample_s: float, stream_sample_s: float, fields: Sequence[str]
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    expanded = expand_fields(rows, fields)
    if not rows:
        return [], expanded
    stride = max(1, int(round(stream_sample_s / base_sample_s)))
    selected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index % stride != 0 and index != len(rows) - 1:
            continue
        selected.append({field: row.get(field) for field in expanded})
    return selected, expanded


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def write_multi_rate_telemetry(
    *, output_root: Path, rows: Iterable[Mapping[str, Any]], base_sample_s: float, streams: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    row_list = _rows(rows)
    errors = validate_telemetry_streams(base_sample_s=base_sample_s, streams=streams)
    if errors:
        raise ValueError("; ".join(errors))
    results: list[TelemetryStreamResult] = []
    for stream in streams:
        sid = str(stream["stream_id"])
        sample_s = float(stream["sample_s"])
        fmt = str(stream.get("format") or "csv")
        selected, fields = select_stream_rows(
            row_list, base_sample_s=base_sample_s, stream_sample_s=sample_s, fields=stream.get("fields") or []
        )
        suffix = "jsonl" if fmt == "jsonl" else "csv"
        path = output_root / "telemetry" / "streams" / f"{sid}.{suffix}"
        if fmt == "jsonl":
            _write_jsonl(path, selected)
        else:
            _write_csv(path, selected, fields)
        results.append(
            TelemetryStreamResult(
                stream_id=sid,
                sample_s=sample_s,
                format=fmt,
                fields=fields,
                row_count=len(selected),
                file=str(path.relative_to(output_root)),
                source_base_sample_s=float(base_sample_s),
            )
        )
    manifest = {
        "schema_version": "sat-sim.multi-rate-telemetry.v1",
        "recorder_mode": "postprocess_integer_stride",
        "same_writer_task": False,
        "post_run_downsampling": True,
        "base_sample_s": float(base_sample_s),
        "stream_count": len(results),
        "interpolation_used": False,
        "streams": [item.to_dict() for item in results],
    }
    manifest_path = output_root / "telemetry" / "multi_rate_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_file"] = str(manifest_path.relative_to(output_root))
    return manifest


__all__ = [
    "TelemetryStreamResult",
    "expand_fields",
    "validate_telemetry_streams",
    "select_stream_rows",
    "write_multi_rate_telemetry",
    "write_native_multi_rate_telemetry",
]


def write_native_multi_rate_telemetry(
    *,
    output_root: Path,
    native_payload: Mapping[str, Any],
    streams: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write telemetry rows sampled by Basilisk recorder groups.

    ``native_payload`` is produced by capability adapters after the Basilisk
    simulation completes.  No stride selection or interpolation is performed
    here; this function only filters fields and serializes recorder-owned rows.
    """

    requested = {str(item.get("stream_id")): dict(item) for item in streams}
    native_streams = {
        str(item.get("stream_id")): dict(item)
        for item in (native_payload.get("streams") or [])
        if isinstance(item, Mapping)
    }
    results: list[TelemetryStreamResult] = []
    native_details: list[dict[str, Any]] = []
    for stream_id, spec in requested.items():
        if stream_id not in native_streams:
            raise ValueError(f"Basilisk recorder group missing for telemetry stream {stream_id}")
        native = native_streams[stream_id]
        row_list = _rows(native.get("rows") or [])
        fields = expand_fields(row_list, spec.get("fields") or [])
        selected = [{field: row.get(field) for field in fields} for row in row_list]
        fmt = str(spec.get("format") or native.get("format") or "csv")
        suffix = "jsonl" if fmt == "jsonl" else "csv"
        path = output_root / "telemetry" / "streams" / f"{stream_id}.{suffix}"
        if fmt == "jsonl":
            _write_jsonl(path, selected)
        else:
            _write_csv(path, selected, fields)
        result = TelemetryStreamResult(
            stream_id=stream_id,
            sample_s=float(spec.get("sample_s") or native.get("sample_s")),
            format=fmt,
            fields=fields,
            row_count=len(selected),
            file=str(path.relative_to(output_root)),
            source_base_sample_s=float(spec.get("sample_s") or native.get("sample_s")),
            selection_policy="basilisk_native_recorder_no_postprocess_sampling",
        )
        results.append(result)
        native_details.append({
            "stream_id": stream_id,
            "task_name": native.get("task_name"),
            "recorder_sources": list(native.get("recorder_sources") or []),
            "native_recorder": bool(native.get("native_recorder", True)),
        })
    manifest = {
        "schema_version": "sat-sim.multi-rate-telemetry.v2",
        "recorder_mode": str(native_payload.get("recorder_mode") or "basilisk_message_recorder_groups"),
        "same_writer_task": bool(native_payload.get("same_writer_task", True)),
        "base_sample_s": None,
        "stream_count": len(results),
        "interpolation_used": False,
        "post_run_downsampling": False,
        "streams": [item.to_dict() for item in results],
        "native_groups": native_details,
    }
    manifest_path = output_root / "telemetry" / "multi_rate_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["manifest_file"] = str(manifest_path.relative_to(output_root))
    return manifest
