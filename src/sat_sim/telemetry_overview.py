"""Memory-bounded, source-faithful full-span preview of finite-run telemetry.

This is a plot preview: selected rows always come from the original immutable
JSONL, including earliest/latest records and observed min/max of monitored
numeric channels. It NEVER replaces the complete dataset export or telemetry
pagination, and never interpolates missing values.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping


def _time(row: Mapping[str, Any]) -> float | None:
    t = row.get("time_s")
    if type(t) not in (int, float) or not math.isfinite(t):
        return None
    return float(t)


def _stream(source: Path, start_s: float | None, end_s: float | None) -> Iterator[dict[str, Any]]:
    with source.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"TELEMETRY_OVERVIEW_INVALID_JSON_LINE:{line_no}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"TELEMETRY_OVERVIEW_EXPECTED_OBJECT:{line_no}")
            t = _time(value)
            if t is None or start_s is not None and t < start_s or end_s is not None and t > end_s:
                continue
            yield value


def read_telemetry_overview(
    root: Path,
    *,
    max_points: int = 3000,
    fields: tuple[str, ...] = (),
    start_s: float | None = None,
    end_s: float | None = None,
) -> dict[str, Any]:
    """Two streamed passes, bounded memory regardless of original run length."""
    if type(max_points) is not int or max_points < 100 or max_points > 5000:
        raise ValueError("TELEMETRY_OVERVIEW_POINT_LIMIT")
    if any(v is not None and not math.isfinite(v) for v in (start_s, end_s)):
        raise ValueError("TELEMETRY_OVERVIEW_INVALID_TIME")
    if start_s is not None and end_s is not None and end_s < start_s:
        raise ValueError("TELEMETRY_OVERVIEW_REVERSED_TIME")
    if len(fields) > 16 or any(not isinstance(f, str) or not f or len(f) > 128 for f in fields):
        raise ValueError("TELEMETRY_OVERVIEW_FIELD_LIMIT")
    source = root / "results" / "telemetry.jsonl"
    if not source.is_file():
        return {
            "source": None, "total": 0, "count": 0, "rows": [],
            "truncated": False, "sampled": False, "sampling_policy": "actual_telemetry_no_interpolation",
            "extrema_fields": list(fields), "start_s": start_s, "end_s": end_s,
        }
    count = 0
    extrema: dict[str, dict[str, tuple[float, int]]] = {}
    field_list = list(dict.fromkeys(fields))
    ignored = {"time_s", "sample_index", "task_id", "case_id", "t_s", "utc", "run_id"}
    for row in _stream(source, start_s, end_s):
        if not fields and count == 0:
            field_list = [
                key for key, value in row.items()
                if key not in ignored and type(value) in (int, float) and math.isfinite(value)
            ][:16]
        for field in field_list:
            raw = row.get(field)
            if type(raw) not in (int, float) or not math.isfinite(raw):
                continue
            value = float(raw)
            pair = extrema.setdefault(field, {})
            if "min" not in pair or value < pair["min"][0]:
                pair["min"] = (value, count)
            if "max" not in pair or value > pair["max"][0]:
                pair["max"] = (value, count)
        count += 1
    if count == 0:
        indices: set[int] = set()
    elif count <= max_points:
        indices = set(range(count))
    else:
        must_keep = {0, count - 1}
        for pair in extrema.values():
            must_keep.update(record[1] for record in pair.values())
        indices = set(must_keep)
        remaining = max_points - len(indices)
        if remaining > 0:
            # Exact observed samples distributed throughout the entire run.
            steps = max(1, remaining - 1)
            indices.update(round(k * (count - 1) / steps) for k in range(remaining))
        if len(indices) > max_points:
            raise ValueError("TELEMETRY_OVERVIEW_INTERNAL_POINT_BOUND")
    rows: list[dict[str, Any]] = []
    if indices:
        for i, row in enumerate(_stream(source, start_s, end_s)):
            if i in indices:
                rows.append(row)
    return {
        "source": "results/telemetry.jsonl", "total": count,
        "count": len(rows), "rows": rows, "truncated": len(rows) < count,
        "sampled": len(rows) < count,
        "sampling_policy": "observed_uniform_indices_plus_global_channel_extrema",
        "extrema_fields": list(extrema),
        "start_s": start_s, "end_s": end_s,
        "note": "Original samples only; zoom on this overview may omit intermediate points. Download the Run dataset or paginate full telemetry for exact data.",
    }
