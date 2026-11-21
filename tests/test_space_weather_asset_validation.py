from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from integrated.orbit_environment import assets
from sat_sim.bsk_engine.space_weather import (
    inspect_space_weather_file,
    validate_space_weather_coverage,
)


FIXTURE = Path(__file__).parent / "fixtures" / "celestrak_space_weather_2026-03-04.csv"


def test_celestrak_space_weather_fixture_is_auditable():
    evidence = inspect_space_weather_file(FIXTURE)
    assert evidence["valid_row_count"] == 5
    assert evidence["valid_date_start"] == "2026-02-28"
    assert evidence["valid_date_end"] == "2026-03-04"
    assert len(evidence["sha256"]) == 64
    assert evidence["first_invalid_row"] is None
    assert evidence["provenance_manifest_status"] == "NOT_PROVIDED"
    assert evidence["source_url"] is None


def test_celestrak_space_weather_requires_three_day_lookback_and_run_end_coverage():
    epoch = datetime(2026, 3, 4, 21, 48, tzinfo=timezone.utc)
    evidence = validate_space_weather_coverage(FIXTURE, epoch, 3600.0)
    assert evidence["coverage_status"] == "PASS"
    assert evidence["coverage_start_utc_date"] == "2026-03-01"
    assert evidence["coverage_end_utc_date"] == "2026-03-04"
    assert evidence["stale_value_fallback_permitted"] is False


def test_celestrak_space_weather_fails_closed_when_end_date_is_missing():
    epoch = datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="SPACE_WEATHER_COVERAGE_INCOMPLETE"):
        validate_space_weather_coverage(FIXTURE, epoch, 1.0)


def test_celestrak_space_weather_fails_closed_when_required_lookback_is_missing(tmp_path):
    raw = FIXTURE.read_text(encoding="utf-8")
    # Remove March 1; the CSV remains structurally valid but cannot satisfy
    # Basilisk's four-day look-back for a March 4 start.
    lines = raw.splitlines()
    broken = "\n".join([lines[0], lines[1], *lines[3:]]) + "\n"
    path = tmp_path / "missing-lookback.csv"
    path.write_text(broken, encoding="utf-8")
    with pytest.raises(ValueError, match="SPACE_WEATHER_COVERAGE_INCOMPLETE"):
        validate_space_weather_coverage(
            path,
            datetime(2026, 3, 4, tzinfo=timezone.utc),
            1.0,
        )


def test_celestrak_space_weather_rejects_duplicate_observed_dates(tmp_path):
    raw = FIXTURE.read_text(encoding="utf-8")
    lines = raw.splitlines()
    duplicate = "\n".join([*lines, lines[-1]]) + "\n"
    path = tmp_path / "duplicate-date.csv"
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="SPACE_WEATHER_DUPLICATE_DATE"):
        inspect_space_weather_file(path)

def test_space_weather_asset_resolver_uses_explicit_offline_file(monkeypatch, tmp_path):
    target = tmp_path / "SW-Last5Years.csv"
    target.write_bytes(FIXTURE.read_bytes())
    monkeypatch.setenv("SAT_SIM_SPACE_WEATHER_PATH", str(target))
    asset, _searched = assets.resolve_space_weather_asset(strict=True)
    assert asset is not None
    assert Path(asset.path) == target.resolve()
    assert asset.sha256 == inspect_space_weather_file(target)["sha256"]


def test_space_weather_asset_resolver_fails_closed_without_local_asset(monkeypatch, tmp_path):
    monkeypatch.delenv("SAT_SIM_SPACE_WEATHER_PATH", raising=False)
    monkeypatch.delenv("SAT_SIM_ASSET_ROOT", raising=False)
    monkeypatch.setattr(assets, "_candidate_dirs", lambda *args, **kwargs: [tmp_path / "empty"])
    with pytest.raises(FileNotFoundError, match="SPACE_WEATHER_ASSET_NOT_FOUND"):
        assets.resolve_space_weather_asset(strict=True)

def test_offline_space_weather_setup_command_writes_manifest(tmp_path):
    target = tmp_path / "SW-Last5Years.csv"
    target.write_bytes(FIXTURE.read_bytes())
    script = Path(__file__).parents[1] / "scripts" / "fetch_space_weather_asset.py"
    subprocess.run(
        [
            sys.executable, str(script), "--offline",
            "--output", str(target),
            "--source-url", "test-fixture://celestrak-compatible-sample",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads((tmp_path / "space_weather_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["runtime_network_required"] is False
    assert manifest["source_url"] == "test-fixture://celestrak-compatible-sample"
    assert manifest["asset"]["sha256"] == inspect_space_weather_file(target)["sha256"]
    assert manifest["retrieval_time_unknown"] is True

def test_space_weather_provenance_manifest_hash_mismatch_fails_closed(tmp_path):
    target = tmp_path / "SW-Last5Years.csv"
    target.write_bytes(FIXTURE.read_bytes())
    manifest = {
        "schema_version": "sat-sim-space-weather-asset.v1",
        "status": "PASS",
        "source_url": "test-fixture://mismatched",
        "retrieved_at_utc": None,
        "retrieval_time_unknown": True,
        "asset": {"sha256": "0" * 64},
    }
    (tmp_path / "space_weather_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="SPACE_WEATHER_PROVENANCE_MANIFEST_HASH_MISMATCH"):
        inspect_space_weather_file(target)

def test_offline_asset_manifest_does_not_assume_default_celestrak_source(tmp_path):
    target = tmp_path / "SW-Last5Years.csv"
    target.write_bytes(FIXTURE.read_bytes())
    script = Path(__file__).parents[1] / "scripts" / "fetch_space_weather_asset.py"
    subprocess.run(
        [sys.executable, str(script), "--offline", "--output", str(target)],
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = inspect_space_weather_file(target)
    assert evidence["provenance_manifest_status"] == "HASH_BOUND_SOURCE_UNKNOWN"
    assert evidence["source_url"] is None
    assert evidence["retrieval_time_unknown"] is True
