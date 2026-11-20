"""Validation helpers for offline CelesTrak space-weather assets.

The official Basilisk SpaceWeatherData module stops at the first invalid row and
retains stale values when a later UTC day is missing. These helpers preflight
the entire requested simulation interval so a run cannot silently use stale
Ap/F10.7 inputs.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS = (
    "DATE",
    "AP1", "AP2", "AP3", "AP4", "AP5", "AP6", "AP7", "AP8",
    "AP_AVG",
    "F10.7_OBS",
    "F10.7_OBS_CENTER81",
)
AP_COLUMNS = ("AP1", "AP2", "AP3", "AP4", "AP5", "AP6", "AP7", "AP8", "AP_AVG")
F107_COLUMNS = ("F10.7_OBS", "F10.7_OBS_CENTER81")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect(path: Path) -> tuple[list[date], dict[str, Any]]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SPACE_WEATHER_ASSET_MISSING:{path}")

    valid_dates: list[date] = []
    first_invalid: dict[str, Any] | None = None
    previous: date | None = None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                raise ValueError("SPACE_WEATHER_HEADER_MISSING")
            reader.fieldnames = [str(name).strip() for name in reader.fieldnames]
            missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
            if missing:
                raise ValueError(f"SPACE_WEATHER_REQUIRED_COLUMNS_MISSING:{','.join(missing)}")

            for row_number, row in enumerate(reader, start=2):
                if not row or not any(value not in (None, "") for value in row.values()):
                    continue
                raw_date = str(row.get("DATE", "") or "").strip()
                try:
                    row_date = date.fromisoformat(raw_date)
                except ValueError:
                    first_invalid = {"row": row_number, "date": raw_date or None, "reason": "invalid_date"}
                    break
                if previous is not None and row_date <= previous:
                    reason = "duplicate_date" if row_date == previous else "date_not_ascending"
                    raise ValueError(f"SPACE_WEATHER_{reason.upper()}:row={row_number}:date={row_date.isoformat()}")

                invalid_reason = None
                for column in (*AP_COLUMNS, *F107_COLUMNS):
                    raw = str(row.get(column, "") or "").strip()
                    try:
                        value = float(raw)
                    except (TypeError, ValueError):
                        invalid_reason = f"invalid_numeric:{column}"
                        break
                    if not math.isfinite(value):
                        invalid_reason = f"non_finite:{column}"
                        break
                    if column in AP_COLUMNS and value < 0.0:
                        invalid_reason = f"negative_ap:{column}"
                        break
                    if column in F107_COLUMNS and value <= 0.0:
                        invalid_reason = f"non_positive_f107:{column}"
                        break
                if invalid_reason:
                    first_invalid = {"row": row_number, "date": row_date.isoformat(), "reason": invalid_reason}
                    break

                valid_dates.append(row_date)
                previous = row_date
    except UnicodeDecodeError as exc:
        raise ValueError("SPACE_WEATHER_ENCODING_INVALID:expected_utf8") from exc

    if not valid_dates:
        raise ValueError("SPACE_WEATHER_NO_VALID_OBSERVED_ROWS")

    return valid_dates, {
        "path": str(path),
        "name": path.name,
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
        "valid_row_count": len(valid_dates),
        "valid_date_start": valid_dates[0].isoformat(),
        "valid_date_end": valid_dates[-1].isoformat(),
        "first_invalid_row": first_invalid,
        "required_columns": list(REQUIRED_COLUMNS),
        "parser": "Basilisk_SpaceWeatherData_CelesTrak_compatible",
    }


def inspect_space_weather_file(path: str | Path) -> dict[str, Any]:
    """Validate CSV structure and return immutable asset metadata.

    Rows after the first malformed/predicted row are ignored, matching the
    official Basilisk parser. Simulation-specific coverage is checked
    separately by validate_space_weather_coverage.
    """
    _, evidence = _inspect(Path(path))
    return evidence


def validate_space_weather_coverage(
    path: str | Path,
    epoch: datetime,
    duration_s: float,
) -> dict[str, Any]:
    """Require every UTC day needed by Basilisk's 3-day Ap look-back.

    Basilisk SpaceWeatherData requires the current day and three preceding
    days at every update. The valid observed rows must therefore cover
    [epoch.date()-3 days, (epoch+duration).date()] without gaps.
    """
    if epoch.tzinfo is None or epoch.utcoffset() is None:
        raise ValueError("SPACE_WEATHER_EPOCH_REQUIRES_TIMEZONE")
    if not math.isfinite(float(duration_s)) or float(duration_s) < 0.0:
        raise ValueError("SPACE_WEATHER_DURATION_INVALID")

    valid_dates, evidence = _inspect(Path(path))
    epoch_utc = epoch.astimezone(timezone.utc)
    end_utc = epoch_utc + timedelta(seconds=float(duration_s))
    required_start = epoch_utc.date() - timedelta(days=3)
    required_end = end_utc.date()
    valid_set = set(valid_dates)

    missing: list[str] = []
    cursor = required_start
    while cursor <= required_end:
        if cursor not in valid_set:
            missing.append(cursor.isoformat())
        cursor += timedelta(days=1)

    if missing:
        raise ValueError(
            "SPACE_WEATHER_COVERAGE_INCOMPLETE:"
            f"required={required_start.isoformat()}..{required_end.isoformat()}:"
            f"available={valid_dates[0].isoformat()}..{valid_dates[-1].isoformat()}:"
            f"missing={','.join(missing[:12])}"
        )

    evidence.update({
        "coverage_start_utc_date": required_start.isoformat(),
        "coverage_end_utc_date": required_end.isoformat(),
        "coverage_status": "PASS",
        "space_weather_interpolation": False,
        "stale_value_fallback_permitted": False,
    })
    return evidence
