from datetime import datetime, timezone
from pathlib import Path

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
