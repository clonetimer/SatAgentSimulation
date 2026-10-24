"""Long finite-run chart must not be just the first 2000 telemetry rows."""
from __future__ import annotations

import json

import pytest

from sat_sim.telemetry_overview import read_telemetry_overview


def _bundle(tmp_path, rows):
    root = tmp_path / "run-001"
    source = root / "results" / "telemetry.jsonl"
    source.parent.mkdir(parents=True)
    with source.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, allow_nan=False) + "\n")
    return root


def test_bounded_overview_covers_full_span_and_retains_real_extrema(tmp_path):
    root = _bundle(tmp_path, (
        {"time_s": i * .25, "sample_index": i,
         "eps.battery_soc": -.75 if i == 4017 else (.55 if i == 129 else .7),
         "adcs.pointing_error_deg": 5. if i == 3901 else 1.,
         "other": None if i == 2000 else i / 6000}
        for i in range(6001)
    ))
    data = read_telemetry_overview(
        root, max_points=150, fields=("eps.battery_soc", "adcs.pointing_error_deg")
    )
    assert data["total"] == 6001
    assert data["sampled"] is True
    assert data["count"] <= 150
    indices = [row["sample_index"] for row in data["rows"]]
    assert indices == sorted(set(indices))
    assert indices[0] == 0 and indices[-1] == 6000
    assert {129, 3901, 4017} <= set(indices)
    assert next(row for row in data["rows"] if row["sample_index"] == 4017)["eps.battery_soc"] == -.75
    assert data["sampling_policy"] == "observed_uniform_indices_plus_global_channel_extrema"


def test_time_window_is_recomputed_on_original_data_and_not_proxy_interpolation(tmp_path):
    root = _bundle(tmp_path, (
        {"time_s": i, "sample_index": i, "x": i, "y": None if i % 2 else 0}
        for i in range(5000)
    ))
    sample = read_telemetry_overview(root, max_points=120, fields=("x",),
                                     start_s=3000, end_s=3040)
    assert sample["sampled"] is False and sample["total"] == 41
    assert sample["rows"][0]["time_s"] == 3000
    assert sample["rows"][-1]["time_s"] == 3040
    assert sample["rows"][1]["y"] is None
    with pytest.raises(ValueError, match="REVERSED_TIME"):
        read_telemetry_overview(root, start_s=8, end_s=3)
    with pytest.raises(ValueError, match="POINT_LIMIT"):
        read_telemetry_overview(root, max_points=30)


def test_malformed_original_fails_closed(tmp_path):
    root = _bundle(tmp_path, [{"time_s": 0, "x": 1}])
    with (root / "results" / "telemetry.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("{invalid}\n")
    with pytest.raises(ValueError, match="INVALID_JSON_LINE"):
        read_telemetry_overview(root)


def test_api_chart_endpoint_bounds_and_non_destructive(tmp_path):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app

    runs = tmp_path / "runs"
    root = _bundle(runs, (
        {"time_s": i, "sample_index": i, "x": -42 if i == 2300 else i}
        for i in range(3010)
    ))
    original = (root / "results" / "telemetry.jsonl").read_bytes()
    app = create_app(runs_root=runs, artifacts_root=tmp_path / "artifacts",
                     embedded_worker=False, auth_mode="disabled")
    with TestClient(app) as client:
        res = client.get("/runs/run-001/telemetry/overview",
                         params={"max_points": 110, "fields": "x"})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["total"] == 3010 and data["sampled"] is True
        assert any(row["sample_index"] == 2300 for row in data["rows"])
        assert data["rows"][-1]["sample_index"] == 3009
        assert client.get("/runs/run-001/telemetry/overview",
                          params={"start_s": 20, "end_s": 10}).status_code == 422
        assert client.get("/runs/missing/telemetry/overview").status_code == 404
    assert (root / "results" / "telemetry.jsonl").read_bytes() == original
