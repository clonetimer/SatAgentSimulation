"""HF-1 deterministic time-system primitives.

The functions in this module deliberately avoid optional astronomy packages.
They provide the common epoch/grid metadata needed by later orbit, attitude,
and coupled-model work while keeping conversion formulas explicit and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
import math

UNIX_EPOCH_JD = 2440587.5
JULIAN_DAY_SECONDS = 86400.0
J2000_UTC = "2000-01-01T12:00:00Z"
HF_TIME_SCHEMA_VERSION = "hf1.time_systems.v1"


class TimeSystemError(ValueError):
    """Raised when a time-system value cannot be normalized."""


def ensure_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Naive datetimes are treated as UTC.  Aware datetimes are converted to UTC.
    """

    if not isinstance(value, datetime):
        raise TimeSystemError(f"expected datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_utc(value: str | datetime | None = None, *, default: str = J2000_UTC) -> datetime:
    """Parse a UTC timestamp from ISO-8601 text or datetime.

    ``Z`` suffixes are accepted.  If ``value`` is omitted, J2000 UTC is used so
    later orbit modules have a deterministic reference epoch.
    """

    if value is None or value == "":
        value = default
    if isinstance(value, datetime):
        return ensure_utc(value)
    if not isinstance(value, str):
        raise TimeSystemError(f"UTC epoch must be string or datetime, got {type(value).__name__}")
    text = value.strip()
    if not text:
        text = default
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except Exception as exc:
        raise TimeSystemError(f"invalid UTC timestamp {value!r}") from exc


def datetime_to_julian_date(value: str | datetime | None = None) -> float:
    """Convert UTC datetime/ISO text to Julian Date."""

    dt = parse_utc(value)
    return UNIX_EPOCH_JD + dt.timestamp() / JULIAN_DAY_SECONDS


def julian_date_to_datetime(julian_date: float) -> datetime:
    """Convert Julian Date to UTC datetime."""

    if isinstance(julian_date, bool) or not isinstance(julian_date, (int, float)) or not math.isfinite(float(julian_date)):
        raise TimeSystemError("julian_date must be a finite number")
    unix_seconds = (float(julian_date) - UNIX_EPOCH_JD) * JULIAN_DAY_SECONDS
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc)


def datetime_to_modified_julian_date(value: str | datetime | None = None) -> float:
    """Convert UTC datetime/ISO text to Modified Julian Date."""

    return datetime_to_julian_date(value) - 2400000.5


def modified_julian_date_to_datetime(modified_julian_date: float) -> datetime:
    """Convert Modified Julian Date to UTC datetime."""

    if isinstance(modified_julian_date, bool) or not isinstance(modified_julian_date, (int, float)):
        raise TimeSystemError("modified_julian_date must be a number")
    return julian_date_to_datetime(float(modified_julian_date) + 2400000.5)


def _positive_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise TimeSystemError(f"{name} must be a positive finite number")
    return float(value)


@dataclass(frozen=True)
class TimeGrid:
    """Deterministic simulation time grid and epoch metadata."""

    epoch_utc: datetime
    duration_s: float
    sample_s: float
    times_s: tuple[float, ...]
    time_system: str = "UTC"
    schema_version: str = HF_TIME_SCHEMA_VERSION

    @property
    def sample_count(self) -> int:
        return len(self.times_s)

    @property
    def monotonic(self) -> bool:
        return all(self.times_s[i] < self.times_s[i + 1] for i in range(len(self.times_s) - 1))

    @property
    def epoch_jd(self) -> float:
        return datetime_to_julian_date(self.epoch_utc)

    @property
    def epoch_mjd(self) -> float:
        return datetime_to_modified_julian_date(self.epoch_utc)

    def utc_at(self, elapsed_s: float) -> datetime:
        from datetime import timedelta

        if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)) or not math.isfinite(float(elapsed_s)):
            raise TimeSystemError("elapsed_s must be a finite number")
        return self.epoch_utc + timedelta(seconds=float(elapsed_s))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "time_system": self.time_system,
            "epoch_utc": self.epoch_utc.isoformat().replace("+00:00", "Z"),
            "epoch_jd": self.epoch_jd,
            "epoch_mjd": self.epoch_mjd,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "sample_count": self.sample_count,
            "times_s": list(self.times_s),
            "monotonic": self.monotonic,
            "include_endpoint": bool(self.times_s and abs(self.times_s[-1] - self.duration_s) <= 1e-9),
        }


def build_time_grid(
    *,
    duration_s: float,
    sample_s: float,
    epoch_utc: str | datetime | None = None,
    time_system: str = "UTC",
    include_endpoint: bool = True,
) -> TimeGrid:
    """Build a monotonic time grid from 0 to ``duration_s``.

    If ``duration_s`` is not an integer multiple of ``sample_s`` and
    ``include_endpoint`` is true, the final sample is appended exactly at
    duration_s.  This avoids silent truncation in coupled models.
    """

    duration = _positive_number(duration_s, "duration_s")
    sample = _positive_number(sample_s, "sample_s")
    if sample > duration:
        raise TimeSystemError("sample_s must not exceed duration_s")
    system = str(time_system or "UTC").strip()
    allowed = {"UTC", "JulianDate", "relative_seconds", "simulation_seconds"}
    if system not in allowed:
        raise TimeSystemError(f"unsupported time_system {time_system!r}; expected one of {sorted(allowed)}")
    epoch = parse_utc(epoch_utc)
    times: list[float] = []
    n_full = int(math.floor(duration / sample + 1e-12))
    for idx in range(n_full + 1):
        t = round(idx * sample, 12)
        if t <= duration + 1e-9:
            times.append(min(t, duration))
    if include_endpoint and abs(times[-1] - duration) > 1e-9:
        times.append(duration)
    # Deduplicate any floating-point endpoint collision while preserving order.
    clean: list[float] = []
    for value in times:
        if not clean or abs(clean[-1] - value) > 1e-10:
            clean.append(float(value))
    grid = TimeGrid(epoch_utc=epoch, duration_s=duration, sample_s=sample, times_s=tuple(clean), time_system=system)
    if not grid.monotonic:
        raise TimeSystemError("time grid must be strictly monotonic")
    return grid


def normalize_time_contract(simulation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return HF-1 time metadata derived from a TaskSpec simulation block."""

    sim = simulation if isinstance(simulation, Mapping) else {}
    duration = sim.get("duration_s", 300.0)
    sample = sim.get("sample_s", 10.0)
    epoch = sim.get("epoch_utc", sim.get("epoch", None))
    time_system = str(sim.get("time_system", sim.get("time_base", "UTC")) or "UTC")
    if time_system == "simulation_seconds":
        # Existing TaskSpecs use simulation_seconds; HF-1 still records a real epoch.
        time_system = "UTC"
    grid = build_time_grid(duration_s=float(duration), sample_s=float(sample), epoch_utc=epoch, time_system=time_system)
    payload = grid.to_dict()
    payload["contract_status"] = "implemented_hf1"
    payload["round_trip_evidence"] = {
        "utc_jd_mjd": "implemented",
        "monotonic_grid": grid.monotonic,
    }
    return payload


__all__ = [
    "HF_TIME_SCHEMA_VERSION",
    "J2000_UTC",
    "TimeGrid",
    "TimeSystemError",
    "build_time_grid",
    "datetime_to_julian_date",
    "datetime_to_modified_julian_date",
    "ensure_utc",
    "julian_date_to_datetime",
    "modified_julian_date_to_datetime",
    "normalize_time_contract",
    "parse_utc",
]
