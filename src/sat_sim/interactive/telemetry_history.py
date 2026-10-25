"""Exact, original-sample time-window retrieval for paused/terminal live sessions.

Provenance is the append-only, hash-validated SessionWorkspace journal, not
a short in-memory WebSocket replay buffer. It deliberately refuses live
RUNNING reads, which could race with a segment rotation or append.
No interpolation, truncation, normalization, or invented measurements.
"""
from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

from .workspace import SessionWorkspace


def exact_telemetry_window(
    path: Path, *, session_id: str, stream: str,
    start_s: float, end_s: float,
    fields: tuple[str, ...], max_rows: int = 3000,
) -> dict[str, Any]:
    if not isfinite(start_s) or not isfinite(end_s) or start_s < 0 or end_s <= start_s:
        raise ValueError("HISTORY_INVALID_TIME_WINDOW")
    if not isinstance(max_rows, int) or not 1 <= max_rows <= 5000:
        raise ValueError("HISTORY_INVALID_MAX_ROWS")
    if (len(fields) > 6 or len(set(fields)) != len(fields)
            or any(not isinstance(x, str) or not x or len(x) > 128 for x in fields)):
        raise ValueError("HISTORY_INVALID_FIELDS")
    path = Path(path)
    if not path.is_dir() or path.name != session_id:
        raise ValueError("HISTORY_WORKSPACE_NOT_FOUND")
    rows: list[dict[str, Any]] = []
    scanned = 0
    for event in SessionWorkspace.iter_telemetry(path):
        frame = event.get("frame", {})
        if not isinstance(frame, dict) or frame.get("stream") != stream:
            continue
        scanned += 1
        value = frame.get("sim_time_s")
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError("HISTORY_INVALID_STORED_TIMESTAMP")
        if float(value) < start_s or float(value) > end_s:
            continue
        if len(rows) >= max_rows:
            raise ValueError("HISTORY_TOO_MANY_SAMPLES_NARROW_WINDOW")
        raw = frame.get("values")
        if not isinstance(raw, dict):
            raise ValueError("HISTORY_INVALID_STORED_VALUES")
        selected = {key: raw.get(key) for key in (fields or tuple(raw.keys()))}
        rows.append({
            "sequence": frame["sequence"],
            "sim_time_s": value,
            "quality": frame.get("quality", "GOOD"),
            "values": selected,
        })
    gaps = [
        {"after_sequence": older["sequence"], "next_sequence": newer["sequence"]}
        for older, newer in zip(rows, rows[1:])
        if newer["sequence"] != older["sequence"] + 1
    ]
    return {
        "schema_version": "sat-sim.durable-tm-history.v1",
        "session_id": session_id, "stream": stream,
        "start_s": start_s, "end_s": end_s,
        "frames": rows, "frame_count": len(rows),
        "scanned_stream_frames": scanned, "gaps": gaps,
        "source": "hash_verified_append_only_workspace",
        "exact_original_samples": True,
        "decimated": False, "interpolated": False,
        "limit": max_rows,
        "note": "Full hash chain verified; no frame limit is silently applied. If too many records exist, request a narrower time window.",
    }
