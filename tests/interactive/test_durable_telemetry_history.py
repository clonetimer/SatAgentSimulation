"""Durable interactive telemetry history: exact windows, integrity and hard bounds."""
from __future__ import annotations

from pathlib import Path

import pytest

from sat_sim.interactive.models import InteractiveSessionSpec, TelemetryFrame
from sat_sim.interactive.workspace import SessionWorkspace
from sat_sim.interactive.telemetry_history import exact_telemetry_window
from sat_sim.interactive import telemetry_segments


def _workspace(tmp_path: Path) -> SessionWorkspace:
    spec = InteractiveSessionSpec(
        session_id="hist-1", capability_id="whole_spacecraft.unified_native.v1",
        task_spec={}, continuous=True, telemetry_segment_max_bytes=0,
        telemetry_min_free_disk_bytes=0,
    )
    return SessionWorkspace(tmp_path, spec)


def test_hash_verified_exact_window_preserves_samples_null_and_time(tmp_path):
    ws = _workspace(tmp_path)
    for i in range(10):
        ws.append_telemetry(TelemetryFrame(
            session_id="hist-1", stream="whole.continuous", sequence=i,
            sim_time_s=i * .5, quality="SUSPECT" if i == 4 else "GOOD",
            values={"orbit.radius_m": 7_000_000 + i,
                    "eps.battery_soc": None if i == 3 else .7 - i / 100},
        ))
    result = exact_telemetry_window(
        ws.path, session_id="hist-1", stream="whole.continuous",
        start_s=1., end_s=3., max_rows=5,
        fields=("orbit.radius_m", "eps.battery_soc"),
    )
    assert result["exact_original_samples"] is True
    assert result["source"] == "hash_verified_append_only_workspace"
    assert result["decimated"] is False and result["interpolated"] is False
    assert result["frame_count"] == 5
    assert [r["sequence"] for r in result["frames"]] == [2, 3, 4, 5, 6]
    assert result["frames"][1]["values"]["eps.battery_soc"] is None
    assert result["frames"][2]["quality"] == "SUSPECT"
    assert result["gaps"] == []


def test_overlong_window_fails_without_silent_truncation(tmp_path):
    ws = _workspace(tmp_path)
    for i in range(9):
        ws.append_telemetry(TelemetryFrame(
            session_id="hist-1", stream="whole.continuous", sequence=i,
            sim_time_s=float(i), values={"x": i},
        ))
    with pytest.raises(ValueError, match="TOO_MANY_SAMPLES"):
        exact_telemetry_window(ws.path, session_id="hist-1", stream="whole.continuous",
                               start_s=0., end_s=8., max_rows=3, fields=("x",))
    smaller = exact_telemetry_window(ws.path, session_id="hist-1", stream="whole.continuous",
                                     start_s=4., end_s=6., max_rows=3, fields=("x",))
    assert smaller["frame_count"] == 3 and smaller["frames"][-1]["values"]["x"] == 6


def test_closed_rotated_and_compressed_journal_is_verified(tmp_path):
    ws = _workspace(tmp_path)
    for i in range(4):
        ws.append_telemetry(TelemetryFrame(
            session_id="hist-1", stream="whole.continuous", sequence=i,
            sim_time_s=float(i), values={"x": i},
        ))
    telemetry_segments.roll_active(ws.path)
    telemetry_segments.compress_closed(ws.path)
    for i in range(4, 7):
        ws.append_telemetry(TelemetryFrame(
            session_id="hist-1", stream="whole.continuous", sequence=i,
            sim_time_s=float(i), values={"x": i},
        ))
    window = exact_telemetry_window(
        ws.path, session_id="hist-1", stream="whole.continuous",
        start_s=2., end_s=6., fields=("x",),
    )
    assert [r["values"]["x"] for r in window["frames"]] == [2, 3, 4, 5, 6]


def test_invalid_window_bad_fields_and_corrupted_log_fail_closed(tmp_path):
    ws = _workspace(tmp_path)
    ws.append_telemetry(TelemetryFrame(
        session_id="hist-1", stream="whole.continuous", sequence=0,
        sim_time_s=1., values={"x": 1},
    ))
    with pytest.raises(ValueError, match="INVALID_TIME_WINDOW"):
        exact_telemetry_window(ws.path, session_id="hist-1", stream="whole.continuous",
                               start_s=2., end_s=1., fields=("x",))
    with pytest.raises(ValueError, match="INVALID_FIELDS"):
        exact_telemetry_window(ws.path, session_id="hist-1", stream="whole.continuous",
                               start_s=0., end_s=2., fields=tuple("abcdefg"))
    src = ws.telemetry_path
    original = src.read_bytes()
    src.write_bytes(original.replace(b'"sim_time_s": 1.0', b'"sim_time_s": 9.0'))
    assert original != src.read_bytes()
    with pytest.raises(ValueError, match="TELEMETRY_HASH_CHAIN_INVALID"):
        exact_telemetry_window(ws.path, session_id="hist-1", stream="whole.continuous",
                               start_s=0., end_s=2., fields=("x",))
